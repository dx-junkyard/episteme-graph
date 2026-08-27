"""取り込みキューの読み書き（migration 072 / Phase 2）。

設計正本: ``docs/features/paper_discovery_design.md`` §5（Phase 2）。

このモジュールが持つのは**キュー行の状態遷移だけ**である。論文の取得（許可リスト照合・
SSRF ガード）と受理（既存アップロードパイプラインへの合流）は API 層の責務で、
ここからは呼ばない — core は FastAPI も HTTP クライアントも LLM も import しない
（PD1 のガードレールが構造として固定する。「発見層のどこかが勝手に論文を取りに行く」
経路を作らない）。

不変条項:

- **行削除の SQL を書かない**（P4）。失敗は ``status='failed'`` + ``detail`` で保持し、
  再試行は教員の明示操作（:func:`retry_item`）だけが ``queued`` へ戻す。
- **キュー行は教員の明示操作でしか生まれない**（PD1）。:func:`enqueue_items` の
  呼び出し元は API 層の「取り込みバッチ」エンドポイントのみで、検索やスケジューラから
  積む経路を作らない。
- ``commit`` / ``close`` は呼び出し側（API 層 / worker）の責務
  （``store.py`` と同じ流儀）。
- 数値スコア・類似度を持たない（PD4）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Optional

from sqlalchemy import text as sa_text

from core.paper_discovery.schema import normalize_arxiv_id, pdf_url_for

logger = logging.getLogger(__name__)

#: 取得待ち（worker が claim する対象）。
STATUS_QUEUED = "queued"
#: worker が取得・受理を実行中。
STATUS_FETCHING = "fetching"
#: 既存アップロードパイプラインへ受理済み（以後の進捗は教材一覧の status が正本）。
STATUS_ACCEPTED = "accepted"
#: 失敗（行は消さず detail に事実文を残す）。
STATUS_FAILED = "failed"

#: migration 072 の CHECK 制約と一致させる語彙（ガードレールが突き合わせる）。
INGEST_STATUSES = (STATUS_QUEUED, STATUS_FETCHING, STATUS_ACCEPTED, STATUS_FAILED)

#: 既定の分野キー（分野を指定せず取り込む場合の名前空間）。
DEFAULT_DOMAIN_KEY = "arxiv"

# 積まなかった理由（日本語の事実文。内部情報を載せない — UF6 継承）。
SKIP_INVALID_ID = "arXiv ID として解釈できませんでした。"
SKIP_ALREADY_INGESTED = "この論文は既に取り込み済みです。"
SKIP_ALREADY_QUEUED = "この論文は既にキューに登録されています。"

_ITEM_COLUMNS = """
    id::text,
    domain_key,
    arxiv_id,
    source_url,
    title,
    requested_by::text,
    analyze_images,
    models,
    status,
    detail,
    attempts,
    material_id,
    task_id,
    requested_at,
    started_at,
    finished_at
"""


def _iso(value: Any) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _as_dict(value: Any) -> Optional[dict]:
    """JSONB 列の値を dict へ（ドライバが str を返す場合も吸収する）。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    return value if isinstance(value, dict) else None


def _row_to_item(row) -> dict:
    return {
        "item_id": str(row[0] or ""),
        "domain_key": str(row[1] or ""),
        "arxiv_id": str(row[2] or ""),
        "source_url": str(row[3] or ""),
        "title": str(row[4] or ""),
        "requested_by": str(row[5]) if row[5] else "",
        "analyze_images": bool(row[6]),
        "models": _as_dict(row[7]),
        "status": str(row[8] or ""),
        "detail": str(row[9] or ""),
        "attempts": int(row[10] or 0),
        "material_id": str(row[11] or ""),
        "task_id": str(row[12] or ""),
        "requested_at": _iso(row[13]),
        "started_at": _iso(row[14]),
        "finished_at": _iso(row[15]),
    }


# ---------------------------------------------------------------------------
# 投入（教員の明示操作だけが呼ぶ — PD1）
# ---------------------------------------------------------------------------


def _active_arxiv_ids(session) -> set[str]:
    """まだ処理待ち（``queued`` / ``fetching``）の arXiv ID 集合。"""
    rows = session.execute(
        sa_text(
            """
            SELECT arxiv_id
              FROM paper_discovery_ingest_items
             WHERE status IN ('queued', 'fetching')
            """
        )
    ).fetchall()
    return {str(row[0]) for row in rows if row and row[0]}


def _ingested_arxiv_ids(session) -> set[str]:
    """``documents.source_url`` 由来の取り込み済み ID 集合（PD5 の読み時導出）。

    ``search.ingested_arxiv_ids`` と同じ判定だが、こちらは投入時の重複回避だけに使う。
    手動アップロードされた同一論文は判定できない（偽装しない — 設計書 §8）。
    """
    rows = session.execute(
        sa_text(
            """
            SELECT source_url
              FROM documents
             WHERE source_url IS NOT NULL
               AND source_url <> ''
            """
        )
    ).fetchall()
    out: set[str] = set()
    for row in rows:
        normalized = normalize_arxiv_id(row[0])
        if normalized:
            out.add(normalized)
    return out


def enqueue_items(
    session,
    items: Iterable[Any],
    *,
    domain_key: str = DEFAULT_DOMAIN_KEY,
    requested_by: Any = None,
    analyze_images: bool = False,
    models: Optional[dict] = None,
) -> dict:
    """取り込み対象をまとめてキューへ積む。

    ``items`` の各要素は ``{"arxiv_id": ..., "title": ...}`` の dict、または
    ``arxiv_id`` 文字列。返り値は::

        {"queued": [{"item_id": ..., "arxiv_id": ..., "title": ...}],
         "skipped": [{"arxiv_id": ..., "detail": "..."}]}

    積まない条件は3つだけで、いずれも**事実文つきで正直に返す**（黙って落とさない）:

    1. arXiv ID として正規化できない
    2. ``documents.source_url`` 由来で既に取り込み済み
    3. 同一 arXiv ID の ``queued`` / ``fetching`` が既にある（二重投入）

    ``failed`` / ``accepted`` 済みの ID は**新規行として積める**（失敗の再挑戦を
    キュー投入からもできるようにする。行は上書きせず履歴を残す — P4）。

    ``models`` の妥当性検証は呼び出し側（API 層）の責務。ここでは JSON として
    保存するだけで、worker は再検証しない。
    """
    key = str(domain_key or "").strip() or DEFAULT_DOMAIN_KEY
    payload = json.dumps(models, ensure_ascii=False) if models else None

    already_ingested = _ingested_arxiv_ids(session)
    pending = _active_arxiv_ids(session)

    queued: list[dict] = []
    skipped: list[dict] = []
    seen_in_request: set[str] = set()

    for entry in items or ():
        if isinstance(entry, dict):
            raw_id = entry.get("arxiv_id", "")
            title = str(entry.get("title") or "").strip()
        else:
            raw_id = getattr(entry, "arxiv_id", entry)
            title = str(getattr(entry, "title", "") or "").strip()

        raw_text = str(raw_id or "")
        arxiv_id = normalize_arxiv_id(raw_text)
        if not arxiv_id:
            skipped.append({"arxiv_id": raw_text, "detail": SKIP_INVALID_ID})
            continue
        if arxiv_id in already_ingested:
            skipped.append({"arxiv_id": arxiv_id, "detail": SKIP_ALREADY_INGESTED})
            continue
        if arxiv_id in pending or arxiv_id in seen_in_request:
            skipped.append({"arxiv_id": arxiv_id, "detail": SKIP_ALREADY_QUEUED})
            continue

        row = session.execute(
            sa_text(
                """
                INSERT INTO paper_discovery_ingest_items
                    (domain_key, arxiv_id, source_url, title, requested_by,
                     analyze_images, models, status)
                VALUES
                    (:domain_key, :arxiv_id, :source_url, :title,
                     CAST(:requested_by AS uuid), :analyze_images,
                     CAST(:models AS jsonb), 'queued')
             RETURNING id::text
                """
            ),
            {
                "domain_key": key,
                "arxiv_id": arxiv_id,
                "source_url": pdf_url_for(arxiv_id),
                "title": title or None,
                "requested_by": str(requested_by) if requested_by else None,
                "analyze_images": bool(analyze_images),
                "models": payload,
            },
        ).fetchone()

        seen_in_request.add(arxiv_id)
        queued.append(
            {
                "item_id": str(row[0]) if row else "",
                "arxiv_id": arxiv_id,
                "title": title,
            }
        )

    return {"queued": queued, "skipped": skipped}


# ---------------------------------------------------------------------------
# 読み取り
# ---------------------------------------------------------------------------


def list_items(session, *, domain_key: Optional[str] = None, limit: int = 50) -> list[dict]:
    """キュー行を新しい順に返す（``detail`` / ``material_id`` 込み）。"""
    capped = max(1, min(500, int(limit or 50)))
    key = str(domain_key or "").strip()
    params: dict = {"limit": capped}
    where = ""
    if key:
        where = "WHERE domain_key = :domain_key"
        params["domain_key"] = key

    rows = session.execute(
        sa_text(
            f"""
            SELECT {_ITEM_COLUMNS}
              FROM paper_discovery_ingest_items
              {where}
             ORDER BY requested_at DESC
             LIMIT :limit
            """
        ),
        params,
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def get_item(session, item_id: str) -> Optional[dict]:
    """1件を返す（存在しなければ ``None``）。"""
    ident = str(item_id or "").strip()
    if not ident:
        return None
    row = session.execute(
        sa_text(
            f"""
            SELECT {_ITEM_COLUMNS}
              FROM paper_discovery_ingest_items
             WHERE id = CAST(:item_id AS uuid)
             LIMIT 1
            """
        ),
        {"item_id": ident},
    ).fetchone()
    return _row_to_item(row) if row else None


# ---------------------------------------------------------------------------
# worker の状態遷移
# ---------------------------------------------------------------------------


def claim_next(session) -> Optional[dict]:
    """最古の ``queued`` を1件 ``fetching`` へアトミックに遷移させて返す。

    ``FOR UPDATE SKIP LOCKED`` を使うので、worker が多重起動していても同じ行を
    2回処理しない（プロセスが増えても安全に）。対象が無ければ ``None``。
    """
    row = session.execute(
        sa_text(
            f"""
            UPDATE paper_discovery_ingest_items
               SET status     = 'fetching',
                   started_at = now(),
                   attempts   = attempts + 1
             WHERE id = (
                   SELECT id
                     FROM paper_discovery_ingest_items
                    WHERE status = 'queued'
                    ORDER BY requested_at ASC
                    LIMIT 1
                      FOR UPDATE SKIP LOCKED
             )
         RETURNING {_ITEM_COLUMNS}
            """
        )
    ).fetchone()
    return _row_to_item(row) if row else None


def mark_accepted(
    session, item_id: str, *, material_id: str = "", task_id: str = ""
) -> Optional[dict]:
    """受理成功を記録する（``accepted``）。以後の進捗は教材一覧の status が正本。"""
    ident = str(item_id or "").strip()
    if not ident:
        return None
    row = session.execute(
        sa_text(
            f"""
            UPDATE paper_discovery_ingest_items
               SET status      = 'accepted',
                   detail      = NULL,
                   material_id = :material_id,
                   task_id     = :task_id,
                   finished_at = now()
             WHERE id = CAST(:item_id AS uuid)
         RETURNING {_ITEM_COLUMNS}
            """
        ),
        {
            "item_id": ident,
            "material_id": str(material_id or "") or None,
            "task_id": str(task_id or "") or None,
        },
    ).fetchone()
    return _row_to_item(row) if row else None


def mark_failed(session, item_id: str, *, detail: str) -> Optional[dict]:
    """失敗を記録する（``failed``）。行は消さず事実文を残す（P4）。

    ``detail`` には日本語の事実文だけを入れる（スタックトレース・解決した IP 等の
    内部情報を入れない — UF6 継承。呼び出し側が守る）。
    """
    ident = str(item_id or "").strip()
    if not ident:
        return None
    row = session.execute(
        sa_text(
            f"""
            UPDATE paper_discovery_ingest_items
               SET status      = 'failed',
                   detail      = :detail,
                   finished_at = now()
             WHERE id = CAST(:item_id AS uuid)
         RETURNING {_ITEM_COLUMNS}
            """
        ),
        {"item_id": ident, "detail": str(detail or "")},
    ).fetchone()
    return _row_to_item(row) if row else None


def retry_item(session, item_id: str) -> Optional[dict]:
    """失敗した行を ``queued`` へ戻す（教員の明示操作のみ — P4 / PD1）。

    ``failed`` **以外**は何もせず ``None`` を返す（呼び出し側が 422 にする）。
    ``detail`` は消さない — 前回何が起きたかの履歴を残したまま再挑戦する。
    """
    ident = str(item_id or "").strip()
    if not ident:
        return None
    row = session.execute(
        sa_text(
            f"""
            UPDATE paper_discovery_ingest_items
               SET status      = 'queued',
                   started_at  = NULL,
                   finished_at = NULL
             WHERE id = CAST(:item_id AS uuid)
               AND status = 'failed'
         RETURNING {_ITEM_COLUMNS}
            """
        ),
        {"item_id": ident},
    ).fetchone()
    return _row_to_item(row) if row else None


def requeue_stale_fetching(session, *, older_than_minutes: int = 30) -> int:
    """プロセス再起動で置き去りになった ``fetching`` を ``queued`` へ戻す。

    worker の起動時に1回だけ呼ぶ想定。戻した件数を返す。行は消さない（P4）。
    """
    minutes = max(1, int(30 if older_than_minutes is None else older_than_minutes))
    rows = session.execute(
        sa_text(
            """
            UPDATE paper_discovery_ingest_items
               SET status     = 'queued',
                   started_at = NULL
             WHERE status = 'fetching'
               AND (started_at IS NULL
                    OR started_at < now() - make_interval(mins => :minutes))
         RETURNING id::text
            """
        ),
        {"minutes": minutes},
    ).fetchall()
    return len(rows)
