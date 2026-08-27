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

_started = False
_lock = threading.Lock()


def _enabled() -> bool:
    return os.getenv(ENV_ENABLED, "1") in ("1", "true", "True", "yes")


def _interval_seconds() -> int:
    raw = os.getenv(ENV_INTERVAL, str(DEFAULT_INTERVAL_SECONDS)) or str(DEFAULT_INTERVAL_SECONDS)
    try:
        return max(5, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS


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


def process_item(item: dict) -> bool:
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
        fetched = url_fetch.fetch_source_from_url(source_url, allowed)
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

    try:
        result = _accept_material_source(
            source_bytes=fetched.content,
            filename=fetched.filename or f"{item.get('arxiv_id') or 'paper'}.pdf",
            source_kind=fetched.source_kind,
            analyze_images=bool(item.get("analyze_images")),
            models_option=item.get("models") or None,
            current_user={"id": requested_by},
            source_url=source_url,
        )
    except HTTPException as exc:
        logger.warning("ingest worker: acceptance failed for item %s: %s", item_id, exc.detail)
        _finish(item_id, detail=str(exc.detail))
        return False
    except Exception:
        logger.exception("ingest worker: unexpected acceptance failure for item %s", item_id)
        _finish(item_id, detail=DETAIL_UNEXPECTED)
        return False

    _finish(item_id, accepted=dict(result or {}))
    logger.info(
        "ingest worker: accepted %s (item=%s)", item.get("arxiv_id"), item_id
    )
    return True


# ---------------------------------------------------------------------------
# ループ
# ---------------------------------------------------------------------------


def drain_once(*, max_items: int = MAX_ITEMS_PER_CYCLE, sleep=time.sleep) -> int:
    """キューを一巡処理する。戻り値は処理した件数（成功・失敗の合計）。

    アイテム間には :data:`INTER_ITEM_SLEEP_SECONDS` 秒の間隔を置く（PD7 の同族）。
    """
    processed = 0
    while processed < max(1, int(max_items)):
        try:
            item = _claim_next()
        except Exception:
            logger.exception("ingest worker: failed to claim next item")
            break
        if not item:
            break
        if processed:
            sleep(INTER_ITEM_SLEEP_SECONDS)
        process_item(item)
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
