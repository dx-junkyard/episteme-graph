"""論文ディスカバリー層 Phase 2 — 取り込みキューの非同期 worker。

設計正本: ``docs/features/paper_discovery_design.md`` §5（Phase 2）。

``paper_discovery_ingest_items``（migration 072）に**教員が積んだ**行を1件ずつ取り出し、
既存の URL 取得（``core.url_fetch``、UF1〜UF6）→ 既存アップロード受理
（``routes.admin._accept_material_source``）へ流すだけの薄いループ。V層の削除猶予
スイーパ（``core/versioning/worker.py``）と同型の ``threading.Thread`` daemon で、
``main.py`` の lifespan から起動する。

**なぜ core ではなく api 層にあるか**: 取得（許可リスト照合つき）と受理は API 層の
関数であり、``core/paper_discovery/`` は FastAPI も HTTP クライアントも import しない
規約（PD1 のガードレール）を持つ。キュー行の状態遷移だけが core
（``core.paper_discovery.ingest_queue``）にあり、「取りに行く」側はここに閉じる。

不変条項として構造で守るもの:

- **PD1 発見は自動、取り込みは教員の明示承認のみ**: この worker は
  ``core.paper_discovery.arxiv_client`` を **import しない**。arXiv を検索せず、
  自分で候補を作らない。処理するのは教員がキューに積んだ行だけである。
- **PD2 取得は既存経路のみ**: 許可ドメインは**毎回読み直して**
  ``fetch_source_from_url`` に渡す（教員が許可リストを直した直後から流れる／
  外した直後から止まる）。ディスカバリー専用の取得経路を作らない。
- **PD7 の同族（外部 API への行儀）**: アイテム間に
  :data:`INTER_ITEM_SLEEP_SECONDS` 秒の間隔を置く。
- **429 停止規則（P-0007）**: 取得先が HTTP 429 を返したら、その周の取り出しを止め、
  ``ARXIV_RATE_LIMIT_COOLDOWN_SECONDS``（既定 600 秒）のあいだ次の周でも取り出さない。
  止まっている間の行は ``queued`` のまま（状態を書き換えない）。
- **P4 情報を落とさない**: 失敗は行を消さず ``status='failed'`` + 日本語の事実文で
  残す。再試行は教員の明示操作だけ（worker は自動リトライしない）。
- ``detail`` にスタックトレース・解決した IP 等の内部情報を入れない（UF6 継承）。
- LLM を呼ばない（発見層は LLM 0回。解析パイプラインは受理後に既存経路が起動する）。
"""

from __future__ import annotations

import logging
import os
import threading
import time

from fastapi import HTTPException

from core import url_fetch
from core.paper_discovery import ingest_queue
from core.postgres import get_session

# 取得形式の解決（TeX → PDF の同期フォールバック・最大 2 回・429 では 2 回目を取らない）。
import source_resolution

# PD2: 受理は既存アップロード経路をそのまま呼ぶ（専用の教材種別を作らない）。
from routes.admin import _accept_material_source

logger = logging.getLogger(__name__)

#: worker の有効化（既定 on）。V層スイーパの ``VERSION_SWEEPER_ENABLED`` と同じ流儀。
ENV_ENABLED = "PAPER_DISCOVERY_WORKER_ENABLED"

#: キューが空だったときの待ち時間（秒）。
ENV_INTERVAL = "PAPER_DISCOVERY_WORKER_INTERVAL_SECONDS"

#: ``ENV_INTERVAL`` の既定値（秒）。
DEFAULT_INTERVAL_SECONDS = 30

#: アイテム間に置く間隔（秒）。arXiv への行儀 — PD7 の同族。
INTER_ITEM_SLEEP_SECONDS = 3

#: 起動時に「置き去りの ``fetching``」とみなす経過時間（分）。
STALE_FETCHING_MINUTES = 30

#: 1周で処理する上限（許可リストの再読込・停止指示の反映を確実にするための区切り）。
MAX_ITEMS_PER_CYCLE = 50

#: 想定外の失敗に付ける事実文（内部情報を載せない — UF6 継承）。
DETAIL_UNEXPECTED = "取り込み処理に失敗しました。時間をおいて再試行してください。"

#: 429 を受けたあとキューを取り出さない時間（秒）の既定値。正本は env
#: ``ARXIV_RATE_LIMIT_COOLDOWN_SECONDS``（``core.config`` — 検索クライアントの抑制窓と同じ
#: 設定を読む）。worker は検索クライアントを import しない（PD1）ので、既定値だけを
#: ここに同じ値で置く（``test_paper_discovery_worker`` が一致を固定）。
DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 600.0

_started = False
_lock = threading.Lock()

#: 429 を受けて止まっている期限（``time.monotonic()`` の値）。``None`` は止まっていない。
#: 行には書かない（キュー行の状態は変えない — 止まっている間は claim しないだけ）。
_backoff_until: float | None = None


def _enabled() -> bool:
    return os.getenv(ENV_ENABLED, "1") in ("1", "true", "True", "yes")


def _interval_seconds() -> int:
    raw = os.getenv(ENV_INTERVAL, str(DEFAULT_INTERVAL_SECONDS)) or str(DEFAULT_INTERVAL_SECONDS)
    try:
        return max(5, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS


def _rate_limit_backoff_seconds() -> float:
    """429 のあと取り出しを止める秒数（0 以下なら止めない）。設定を読めなければ既定値。"""
    try:
        from core.config import get_settings

        value = float(get_settings().arxiv_rate_limit_cooldown_seconds)
    except Exception:  # noqa: BLE001 — 読めなければ抑制側に倒す（安全側）
        return DEFAULT_RATE_LIMIT_BACKOFF_SECONDS
    return max(0.0, value)


def _begin_backoff(clock=time.monotonic) -> None:
    """429 を受けた事実を記録し、以後しばらくキューを取り出さない（M6）。"""
    global _backoff_until
    seconds = _rate_limit_backoff_seconds()
    _backoff_until = clock() + seconds if seconds > 0 else None


def _backoff_active(clock=time.monotonic) -> bool:
    global _backoff_until
    if _backoff_until is None:
        return False
    if clock() >= _backoff_until:
        _backoff_until = None
        return False
    return True


# ---------------------------------------------------------------------------
# キュー操作（1操作 = 1セッション。長時間トランザクションを持たない）
# ---------------------------------------------------------------------------


def _claim_next() -> dict | None:
    session = get_session()
    try:
        item = ingest_queue.claim_next(session)
        session.commit()
        return item
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _allowed_domains() -> list[str]:
    """取得のたびに許可リストを読み直す（UF1 — 判定はサーバ側で毎回強制する）。"""
    session = get_session()
    try:
        return [row["domain"] for row in url_fetch.list_url_fetch_domains(session)]
    finally:
        session.close()


def _finish(item_id: str, *, accepted: dict | None = None, detail: str = "") -> None:
    session = get_session()
    try:
        if accepted is not None:
            ingest_queue.mark_accepted(
                session,
                item_id,
                material_id=str(accepted.get("material_id") or ""),
                task_id=str(accepted.get("task_id") or ""),
            )
        else:
            ingest_queue.mark_failed(session, item_id, detail=detail or DETAIL_UNEXPECTED)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("ingest worker: failed to record outcome for item %s", item_id)
    finally:
        session.close()


def requeue_stale() -> int:
    """プロセス再起動で置き去りになった ``fetching`` を ``queued`` へ戻す。

    起動時に1回だけ呼ぶ（行は消さない — P4）。失敗しても worker は続行する。
    """
    session = get_session()
    try:
        count = ingest_queue.requeue_stale_fetching(
            session, older_than_minutes=STALE_FETCHING_MINUTES
        )
        session.commit()
        if count:
            logger.info("ingest worker: requeued %d stale fetching item(s)", count)
        return count
    except Exception:
        session.rollback()
        logger.exception("ingest worker: failed to requeue stale items")
        return 0
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 1件の処理
# ---------------------------------------------------------------------------


def process_item(item: dict, *, clock=time.monotonic) -> bool:
    """1件を取得 → 受理する。成功なら True。

    例外はすべてここで捕捉し、``failed`` + 事実文に落とす（1件の失敗が worker を
    止めない）。``models`` の妥当性は投入時（API 層）に検証済みなので再検証しない。
    """
    item_id = str(item.get("item_id") or "")
    source_url = str(item.get("source_url") or "")
    requested_by = str(item.get("requested_by") or "")

    try:
        allowed = _allowed_domains()
    except Exception:
        logger.exception("ingest worker: failed to read allowed domains")
        _finish(item_id, detail=DETAIL_UNEXPECTED)
        return False

    try:
        # キュー行の source_url には投入時の形式が畳まれている（/src/ = TeX・/pdf/ = PDF）。
        # URL のパスから形式を読み、TeX が使えなければ同じ1件の処理の中で PDF に倒す。
        resolved = source_resolution.fetch_url_source(
            source_url, source_format=None, allowed_domains=allowed,
        )
    except url_fetch.RateLimitedError as exc:
        # arXiv からアクセスを制限されている。この1件は事実文で failed に残し、
        # 以後しばらくキューを取り出さない（制限中に叩き続けるとブロックが延びる — M6）。
        logger.info("ingest worker: rate limited by the source for item %s; backing off", item_id)
        _begin_backoff(clock)
        _finish(item_id, detail=str(exc))
        return False
    except url_fetch.UrlFetchError as exc:
        # 許可リスト未設定 / 未許可ドメイン / 形式不一致 等。サーバの事実文をそのまま
        # 残す（独自文で上書きしない — UF6）。再試行は教員の明示操作のみ。
        logger.info(
            "ingest worker: fetch rejected (%s) for item %s", type(exc).__name__, item_id
        )
        _finish(item_id, detail=str(exc))
        return False
    except Exception:
        logger.exception("ingest worker: unexpected fetch failure for item %s", item_id)
        _finish(item_id, detail=DETAIL_UNEXPECTED)
        return False

    fetched = resolved.fetched
    try:
        result = _accept_material_source(
            source_bytes=fetched.content,
            filename=fetched.filename or f"{item.get('arxiv_id') or 'paper'}.pdf",
            source_kind=fetched.source_kind,
            analyze_images=bool(item.get("analyze_images")),
            models_option=item.get("models") or None,
            current_user={"id": requested_by},
            # 実際にバイト列を返した URL を出所として記帳する（フォールバック時は /pdf/）。
            source_url=resolved.source_url,
        )
    except HTTPException as exc:
        logger.warning("ingest worker: acceptance failed for item %s: %s", item_id, exc.detail)
        _finish(item_id, detail=str(exc.detail))
        return False
    except Exception:
        logger.exception("ingest worker: unexpected acceptance failure for item %s", item_id)
        _finish(item_id, detail=DETAIL_UNEXPECTED)
        return False

    accepted = dict(result or {})
    accepted["effective_format"] = resolved.effective_format
    accepted["fell_back"] = bool(resolved.fell_back)
    _finish(item_id, accepted=accepted)
    logger.info(
        "ingest worker: accepted %s (item=%s format=%s fell_back=%s)",
        item.get("arxiv_id"), item_id, resolved.effective_format, resolved.fell_back,
    )
    return True


# ---------------------------------------------------------------------------
# ループ
# ---------------------------------------------------------------------------


def drain_once(
    *, max_items: int = MAX_ITEMS_PER_CYCLE, sleep=time.sleep, clock=time.monotonic,
) -> int:
    """キューを一巡処理する。戻り値は処理した件数（成功・失敗の合計）。

    アイテム間には :data:`INTER_ITEM_SLEEP_SECONDS` 秒の間隔を置く（PD7 の同族）。
    429 を受けたらその周はそこで止め、``ARXIV_RATE_LIMIT_COOLDOWN_SECONDS`` 秒は
    次の周でも取り出さない（行は ``queued`` のまま残る — M6）。
    """
    processed = 0
    while processed < max(1, int(max_items)):
        if _backoff_active(clock):
            break
        try:
            item = _claim_next()
        except Exception:
            logger.exception("ingest worker: failed to claim next item")
            break
        if not item:
            break
        if processed:
            sleep(INTER_ITEM_SLEEP_SECONDS)
        process_item(item, clock=clock)
        processed += 1
    return processed


def run_forever(interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> None:
    requeue_stale()
    while True:
        processed = 0
        try:
            processed = drain_once()
        except Exception:  # noqa: BLE001 — 1周の失敗で worker を落とさない
            logger.exception("ingest worker iteration failed")
        if processed == 0:
            time.sleep(max(5, interval_seconds))
        else:
            time.sleep(INTER_ITEM_SLEEP_SECONDS)


def start_background_worker() -> None:
    """起動時に1度だけ daemon worker を開始する（``PAPER_DISCOVERY_WORKER_ENABLED`` で無効化可）。"""
    global _started
    if not _enabled():
        logger.info("paper discovery ingest worker disabled by %s", ENV_ENABLED)
        return
    with _lock:
        if _started:
            return
        interval = _interval_seconds()
        thread = threading.Thread(
            target=run_forever,
            kwargs={"interval_seconds": interval},
            name="paper-discovery-ingest",
            daemon=True,
        )
        thread.start()
        _started = True
        logger.info("paper discovery ingest worker started (interval=%ds)", interval)
