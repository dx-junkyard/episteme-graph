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
import logging

# 本人が引き受けた tension status の正本（candidate / dismissed / superseded を含まない）。
# リテラル再掲しない（return_door_design.md §2.1 / 発注仕様）。
from core.tension.schema import TENSION_OWNED_STATUSES

logger = logging.getLogger(__name__)


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


def fetch_active_leave_note(user_id: str, course_id: str) -> dict | None:
    """本人×コースの active（``status='open'``）な書き置き（leave_note）を最新1件返す。

    帰還の扉（return_door_design.md §2.1）の読み。leave_note は carryover と同じ
    「常に最大1件」規約（新規記録時の supersede は ``services.record_cycle_intention``
    の責務。ここは読むだけ）。
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
                  AND payload->>'role' = 'leave_note'
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
    }


def fetch_last_owned_tension(user_id: str, course_id: str) -> dict | None:
    """本人×コースで「最後に確定した tension」を最新1件返す（帰還の扉 §2.1）。

    対象 status は ``core.tension.schema.TENSION_OWNED_STATUSES``（本人が引き受けた
    確定分。candidate / dismissed / superseded を構造的に含まない）をそのまま使う
    — リテラル再掲しない。
    """
    from sqlalchemy import text as sa_text

    from core.postgres import get_session as _pg_session

    placeholders = ", ".join(f":st_{i}" for i in range(len(TENSION_OWNED_STATUSES)))
    params: dict = {"uid": user_id, "cid": course_id}
    for i, status in enumerate(TENSION_OWNED_STATUSES):
        params[f"st_{i}"] = status

    session = _pg_session()
    try:
        row = session.execute(
            sa_text(f"""
                SELECT id, status, payload, created_at
                FROM interest_traces
                WHERE user_id = CAST(:uid AS uuid) AND course_id = :cid
                  AND kind = 'tension'
                  AND status IN ({placeholders})
                ORDER BY created_at DESC
                LIMIT 1
            """),
            params,
        ).fetchone()
    finally:
        session.close()
    if row is None:
        return None
    payload = _payload_dict(row[2])
    return {
        "id": str(row[0]),
        "status": row[1] or "",
        "text": payload.get("learner_text") or payload.get("text") or "",
        "created_at": _to_iso(row[3]),
    }


def fetch_todays_user_words(user_id: str, course_id: str, limit: int = 30) -> list[dict]:
    """直近24時間のやり取りに含まれる本人発話（user ロールのみ）の逐語を返す
    （「今日のあなたの言葉」トレイ, return_door_design.md §2.2）。

    **「当日」フィルタは直近24時間窓 × 行 ``updated_at`` による近似**:
    ``learning_chat_history`` は (user, course, topic) ごとに履歴全体を 1 行の JSONB で
    持ち、各メッセージにタイムスタンプが無い。そのため「当日 ≒ 行の ``updated_at`` が
    直近24時間（＝直近にやり取りのあったトピック）」で近似し、当該行の履歴に含まれる
    それ以前のメッセージも混ざりうる（各要素の ``created_at`` も行の ``updated_at`` の
    近似値）。``CURRENT_DATE``（DB タイムゾーンの暦日）でなく ``now() - interval
    '24 hours'`` の相対窓なのは、DB=UTC のとき JST 等の学習者の朝の発話が同日昼に
    消えるのを避けるため（TZ 非依存。``fetch_landing_candidates`` と同型）。

    role フィルタは SQL 内で ``'user'`` に固定する — assistant ロール行を返す経路を
    作らない（RD1。二重防御として ``derive.build_todays_words`` でも再検査する）。
    空白のみの発話は SQL 段階（``btrim``）で除外し、``limit + 1`` 方式の truncated
    判定を正確にする（derive 側の空文字スキップは二重防御として残る）。
    並びは新しい順（行 ``updated_at`` 降順 → 行内の後方メッセージ優先）。truncated
    判定のため ``limit + 1`` 件まで返す（切り詰めは derive 側の責務）。
    """
    from sqlalchemy import text as sa_text

    from core.postgres import get_session as _pg_session

    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT h.topic_id,
                       m.msg->>'role' AS role,
                       m.msg->>'content' AS content,
                       h.updated_at
                FROM learning_chat_history h
                CROSS JOIN LATERAL jsonb_array_elements(h.history)
                    WITH ORDINALITY AS m(msg, ord)
                WHERE h.user_id = CAST(:uid AS uuid) AND h.course_id = :cid
                  AND h.updated_at >= now() - interval '24 hours'
                  AND m.msg->>'role' = 'user'
                  AND btrim(coalesce(m.msg->>'content', '')) <> ''
                ORDER BY h.updated_at DESC, m.ord DESC
                LIMIT :lim
            """),
            {"uid": user_id, "cid": course_id, "lim": int(limit) + 1},
        ).fetchall()
    except Exception:
        logger.warning(
            "fetch_todays_user_words failed (fail-open to empty tray)", exc_info=True
        )
        rows = []
    finally:
        session.close()
    return [
        {
            "topic_id": r[0] or "",
            "role": r[1] or "",
            "text": r[2] or "",
            "created_at": _to_iso(r[3]),
        }
        for r in rows
    ]


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
