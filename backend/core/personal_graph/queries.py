"""個人知識ネットワーク用の SQL 読みプリミティブ（設計書 §4/§5）。

``core.personal_graph`` パッケージの中で DB を直接知るのはこのファイルのみ。
``core.postgres.get_session`` を直読みし、必ず try/finally で ``session.close()`` する
（開発ルール4）。FastAPI / routes / services / core.llm は import しない。

テーブル定義の正本: ``backend/db/020_interest_trace.sql``（interest_traces）、
``backend/db/036_reconstruction_loop.sql``（learner_reconstructions）、
``backend/db/init.sql``（learning_courses）。
"""

from __future__ import annotations

import json

from sqlalchemy import text as sa_text

from core.course_data import iter_all_topics
from core.postgres import get_session as _pg_session


def _to_iso(value) -> str:
    """timestamptz 列を ISO 文字列へ正規化する（NULL は空文字。versioning 系と同じ扱い）。"""
    return value.isoformat() if value else ""


def _payload_dict(raw) -> dict:
    """JSONB 列を dict に正規化する（psycopg2 は通常 dict を返すが、文字列で来た場合も防御する）。"""
    if isinstance(raw, dict):
        return raw
    if raw:
        return json.loads(raw)
    return {}


def fetch_traces(user_id: str, course_id: str) -> list[dict]:
    """interest_traces から本人・コースの tension/question 行を読む。

    本人確定の絞り込み（TENSION_OWNED_STATUSES・superseded 除外等）は行わない
    （derive.py の責務。ここは生データの読み取りに徹する）。
    """
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, kind, status, topic_id, payload, created_at
                FROM interest_traces
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                  AND kind IN ('tension', 'question')
                ORDER BY created_at, id
            """),
            {"user_id": user_id, "course_id": course_id},
        ).fetchall()
    finally:
        session.close()
    return [
        {
            "id": str(r[0]),
            "kind": r[1],
            "status": r[2],
            "topic_id": r[3],
            "payload": _payload_dict(r[4]),
            "created_at": _to_iso(r[5]),
        }
        for r in rows
    ]


def fetch_reconstructions(user_id: str, course_id: str) -> list[dict]:
    """learner_reconstructions から本人・コースの行を読む。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, item_id, claim_id, machine_verdict, self_check,
                       descended_to_symbol, revision_of, created_at
                FROM learner_reconstructions
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                ORDER BY created_at, id
            """),
            {"user_id": user_id, "course_id": course_id},
        ).fetchall()
    finally:
        session.close()
    return [
        {
            "id": str(r[0]),
            "item_id": str(r[1]),
            "claim_id": str(r[2]),
            "machine_verdict": r[3],
            "self_check": r[4],
            "descended_to_symbol": bool(r[5]),
            "revision_of": str(r[6]) if r[6] else None,
            "created_at": _to_iso(r[7]),
        }
        for r in rows
    ]


def fetch_topic_atlas_binding(course_id: str) -> dict[str, str]:
    """コースの topics[].atlas_node_id が非空のものだけ {topic_id: atlas_node_id} で返す。

    ``core.course_data.iter_all_topics`` 経由で読む（フラット topics[] + 章ネスト
    chapters[].topics[] の両方を走査する。course_data.py への素の dict アクセス禁止ルール）。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT data FROM learning_courses WHERE id = :course_id"),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return {}
    data = _payload_dict(row[0])
    binding: dict[str, str] = {}
    for topic in iter_all_topics(data):
        topic_id = topic.get("id")
        atlas_node_id = topic.get("atlas_node_id")
        if topic_id and atlas_node_id:
            binding[str(topic_id)] = str(atlas_node_id)
    return binding
