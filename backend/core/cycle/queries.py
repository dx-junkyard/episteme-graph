"""理解サイクル（UCサイクル）Phase 1 の SQL 読みプリミティブ。

``core.personal_graph.queries`` の流儀（sqlalchemy 遅延 import・
``core.postgres.get_session`` を直読み・try/finally で close・行を dict に正規化して
返す）を踏襲する。FastAPI / routes / services は import しない。

対象 kind（``intention`` / ``anchor_mark``）は tension/anchor worker・
personal_graph 導出・問いの軌跡・教員向け集約のいずれからも読まれない構造的除外の
対になる読み取り専用モジュール（設計書 §4.1/§11-1）。
"""

from __future__ import annotations

import json


def _payload_dict(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if raw:
        return json.loads(raw)
    return {}


def _to_iso(value) -> str:
    return value.isoformat() if value else ""


def fetch_active_carryover(user_id: str, course_id: str) -> dict | None:
    """本人×コースの active（``status='open'``）な carryover を最新1件返す。

    carryover は常に最大1件（新しい carryover を書いたら旧行を superseded に遷移
    させるのは ``services.record_cycle_intention`` の責務。ここは読むだけ）。
    """
    from sqlalchemy import text as sa_text

    from core.postgres import get_session as _pg_session

    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT id, payload, created_at
                FROM interest_traces
                WHERE user_id = CAST(:uid AS uuid) AND course_id = :cid
                  AND kind = 'intention'
                  AND payload->>'role' = 'carryover_question'
                  AND status = 'open'
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"uid": user_id, "cid": course_id},
        ).fetchone()
    finally:
        session.close()
    if row is None:
        return None
    payload = _payload_dict(row[1])
    return {
        "id": str(row[0]),
        "text": payload.get("text", ""),
        "created_at": _to_iso(row[2]),
        "source_trace_id": payload.get("source_trace_id"),
    }


def fetch_intentions(user_id: str, course_id: str) -> list[dict]:
    """本人×コースの intention 行を存在チェック用に返す（role のみ）。

    OPEN の初回/再訪判定は「当該コースに intention 痕跡が一件でもあるか」で行う
    （設計書 §5.1）。件数や status の意味は問わない — 呼び出し側は
    ``bool(fetch_intentions(...))`` として使うだけで十分（存在チェック）。
    """
    from sqlalchemy import text as sa_text

    from core.postgres import get_session as _pg_session

    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, payload->>'role' AS role
                FROM interest_traces
                WHERE user_id = CAST(:uid AS uuid) AND course_id = :cid
                  AND kind = 'intention'
                LIMIT 20
            """),
            {"uid": user_id, "cid": course_id},
        ).fetchall()
    finally:
        session.close()
    return [{"id": str(r[0]), "role": r[1] or ""} for r in rows]


def fetch_recent_traces_since(user_id: str, course_id: str, since: str) -> list[dict]:
    """``since``（carryover の created_at, ISO文字列）以降に本人が確定した
    引っかかり（articulated tension / active anchor_mark / 本人確定 question）を返す。

    REVISIT の差分事実文（``core.cycle.derive.build_revisit_facts``）の入力。
    """
    from sqlalchemy import text as sa_text

    from core.postgres import get_session as _pg_session

    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, kind, status, payload, created_at
                FROM interest_traces
                WHERE user_id = CAST(:uid AS uuid) AND course_id = :cid
                  AND created_at > CAST(:since AS timestamptz)
                  AND (
                    (kind = 'tension' AND status = 'articulated')
                    OR (kind = 'anchor_mark' AND payload->'structure_anchor'->>'status' = 'active')
                    OR (kind = 'question'
                        AND payload->'structure_anchor'->>'attribution_source'
                            IN ('learner_selected', 'confirmed'))
                  )
                ORDER BY created_at DESC
                LIMIT 20
            """),
            {"uid": user_id, "cid": course_id, "since": since},
        ).fetchall()
    except Exception:
        rows = []
    finally:
        session.close()
    return [
        {
            "id": str(r[0]),
            "kind": r[1],
            "status": r[2],
            "payload": _payload_dict(r[3]),
            "created_at": _to_iso(r[4]),
        }
        for r in rows
    ]


def fetch_landing_candidates(user_id: str, course_id: str, since_hours: int = 24) -> list[dict]:
    """LEAVE（持ち越す問いの選択リスト）候補の生データを返す（設計書 §5.5）。

    「当日セッション」の近似として直近 ``since_hours``（既定24時間）の本人痕跡を対象に
    する（§13-2 未決事項: セッション粒度の厳密化は実測後）。並び替え・件数上限は
    ``core.cycle.derive.build_landing_candidates`` の責務（ここは生データを返すだけ）。
    """
    from sqlalchemy import text as sa_text

    from core.postgres import get_session as _pg_session

    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, kind, status, payload, created_at
                FROM interest_traces
                WHERE user_id = CAST(:uid AS uuid) AND course_id = :cid
                  AND created_at > now() - (:hours || ' hours')::interval
                  AND (
                    (kind = 'tension' AND status = 'articulated')
                    OR (kind = 'anchor_mark' AND payload->'structure_anchor'->>'status' = 'active')
                    OR (kind = 'question'
                        AND payload->'structure_anchor'->>'attribution_source'
                            IN ('learner_selected', 'confirmed'))
                  )
                ORDER BY created_at DESC
                LIMIT 20
            """),
            {"uid": user_id, "cid": course_id, "hours": since_hours},
        ).fetchall()
    except Exception:
        rows = []
    finally:
        session.close()
    return [
        {
            "id": str(r[0]),
            "kind": r[1],
            "status": r[2],
            "payload": _payload_dict(r[3]),
            "created_at": _to_iso(r[4]),
        }
        for r in rows
    ]
