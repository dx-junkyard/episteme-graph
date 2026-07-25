"""配信中マニュアルの content-hash 監査記帳（設計書 §2-3、温存・Phase 3 任意実装）。

**二元台帳の宣言**: 「誰が書いたか」（PRレビュー・blame）は git が正本、
「いつどの版が学習者・教員に配信されていたか」は本モジュールが ``theory_review_events``
に記帳する DB 側の記録が正本。コンテナ内には git のコミットメタデータが無く、
実際に変更した人物を取得できないため、``changed_by`` は常に ``NULL`` にする
（**取れない帰属を偽装記帳しない**）。

記帳は content-hash が前回記帳値から**変化したときのみ**行う（冪等・毎起動
再実行しても行が増殖しない）。``entity_type`` は必ず ``core.schema.AUDIT_ENTITY_MANUAL``
を使う（生リテラル禁止）。

``core/help_kb/`` は FastAPI を import しない規約のモジュール群であり、API 層の
``services.record_review_event`` を呼べない（core → api の import は禁止方向）。
そのため本モジュールは CLAUDE.md が認める例外パターン
（``core/document_pipeline/persistence.py`` と同型 — 呼び出し元トランザクションに
同乗する core 層からの直接 INSERT）として ``theory_review_events`` へ直接書き込む。

全経路 fail-open: DB 未接続・テーブル未整備・その他の例外はすべて捕捉し、
呼び出し側の起動シーケンスを止めない（False を返すのみ）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from sqlalchemy import text as sa_text

from core.schema import AUDIT_ENTITY_MANUAL

from . import manual as _manual

logger = logging.getLogger(__name__)

# help-kb refresh 監査（admin_assistant.py の _MANUAL_REFRESH_ENTITY_ID = "help_kb"）とは
# 意図的に別の entity_id を使う（「操作」記録と「配信版の同定」記録を混同しない）。
_SNAPSHOT_ENTITY_ID = "help_kb_snapshot"


def manual_snapshot() -> dict:
    """配信中の全 audience 分マニュアルから決定論的なスナップショットを構築する。

    節の並びは (audience, file, anchor) で正規化ソートしてから hash 化するため、
    索引構築時の辞書順・ファイル探索順に左右されず、同一内容には常に同一の
    ``content_hash`` が得られる。
    """
    sections = _manual._sections_for_audience("system_admin")
    normalized = sorted(
        (
            sec.get("audience", ""),
            sec.get("file", ""),
            sec.get("anchor", ""),
            sec.get("title", ""),
            sec.get("body", ""),
        )
        for sec in sections
    )
    canonical = json.dumps(normalized, ensure_ascii=False)
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    files = sorted({f"{aud}/{file}" for aud, file, _anchor, _title, _body in normalized})

    excluded = sorted(
        f"{exc.get('audience', '')}/{exc.get('file', '')}#{exc.get('anchor', '')}"
        for exc in _manual.excluded_sections()
    )

    return {
        "content_hash": content_hash,
        "files": files,
        "excluded_sections": excluded,
    }


def _previous_content_hash(session: Any) -> Optional[str]:
    row = session.execute(
        sa_text(
            """
            SELECT metadata->>'content_hash' AS content_hash
            FROM theory_review_events
            WHERE entity_type = :entity_type AND entity_id = :entity_id
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"entity_type": AUDIT_ENTITY_MANUAL, "entity_id": _SNAPSHOT_ENTITY_ID},
    ).fetchone()
    if row is None:
        return None
    return row[0] if not isinstance(row, dict) else row.get("content_hash")


def record_snapshot_if_changed(session: Optional[Any] = None) -> bool:
    """content-hash が前回記帳値と異なるときのみ監査行を1件追記する。

    ``session`` を省略すると ``core.postgres.get_session()`` を都度取得し、
    最後に必ず close する（他の監査ヘルパーと同型の try/finally 規約）。
    テストからはフェイクセッションを注入できるよう明示引数で受け取る。

    戻り値は「記帳したかどうか」。DB 障害・テーブル不在など、どの段階で
    例外が起きても記録を諦めて False を返す（fail-open）。呼び出し側の
    起動シーケンスや API 応答を巻き込んで失敗させない。
    """
    owns_session = session is None
    if owns_session:
        try:
            from core.postgres import get_session as _get_session

            session = _get_session()
        except Exception:
            logger.warning("help_kb.audit: failed to acquire a session", exc_info=True)
            return False

    try:
        snapshot = manual_snapshot()
        previous_hash = _previous_content_hash(session)
        if previous_hash == snapshot["content_hash"]:
            return False

        session.execute(
            sa_text(
                """
                INSERT INTO theory_review_events
                    (entity_type, entity_id, old_status, new_status, changed_by, metadata)
                VALUES
                    (:entity_type, :entity_id, '', '', NULL, CAST(:metadata AS jsonb))
                """
            ),
            {
                "entity_type": AUDIT_ENTITY_MANUAL,
                "entity_id": _SNAPSHOT_ENTITY_ID,
                "metadata": json.dumps(snapshot, ensure_ascii=False),
            },
        )
        session.commit()
        return True
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        logger.warning("help_kb.audit: failed to record manual content-hash snapshot", exc_info=True)
        return False
    finally:
        if owns_session:
            try:
                session.close()
            except Exception:
                pass
