"""Stage 2(B) の非同期バッチワーカー（tension worker と同じ threading.Thread 方式）。

- トリガー: (a) セッション終了（最終質問から20分無活動）、(b) 進行中でも未帰属の
  問い累積3件、のいずれか早い方。
- 冪等性: interest_traces.analyzed_at 列は tension が使用しているため、こちらは
  payload.anchor_analyzed_at（JSONB キー）で管理する。structure_anchor 既存
  （方法Aの learner_selected を含む）の行は最初から対象外。
- コスト上限: 1セッションあたり LLM コール上限（ANCHOR_MAX_CALLS_PER_SESSION、既定3）、
  1ユーザー1日上限（ANCHOR_MAX_CALLS_PER_DAY、既定10）。tension のカウンタとは独立。
  モデルは fast tier 既定（ANCHOR_LLM_MODEL で上書き可）。
- 提示上限はダイジェスト取得側（services.get_anchor_digest）で適用する:
  confidence >= 0.55・新しい順に最大3件。残りは llm_candidate のまま DB に保持する（P4）。

このモジュールは backend/core に属するため api/services.py には依存しない
（永続化はここで直接 SQL を発行する）。
"""

from __future__ import annotations

import datetime
import json
import logging
import threading

from sqlalchemy import text as sa_text

from core.config import get_settings
from core.course_data import course_chapters, course_topics
from core.llm_usage.context import bind_usage_context
from core.llm_worker.cost_gate import CostGate, InMemoryCounterGate
from core.postgres import get_session as _pg_session
from core.structure_anchor.agent import StructureAnchorAgent
from core.structure_anchor.schema import (
    AnchorContext,
    QuestionRecord,
    candidate_to_anchor_payload,
)

logger = logging.getLogger(__name__)

# トリガー閾値
QUESTION_BATCH_THRESHOLD = 3      # 進行中でも未帰属の問い累積3件で起動
SESSION_IDLE_MINUTES = 20         # 最終質問から20分無活動でセッション終了扱い

# ダイジェスト提示条件（取得側 services.get_anchor_digest と共有する定数）
DIGEST_MIN_CONFIDENCE = 0.55
DIGEST_MAX_ITEMS = 3

# 候補ブロックの取得上限（入力肥大の防止。input_builder 側でも文字数キャップあり）
_MAX_CLAIM_BLOCKS = 40
_MAX_CONCEPT_BLOCKS = 30

# コスト上限のプロセス内カウンタ（tension worker と同方式・独立のカウンタ）。
# API サーバーは単一プロセス運用のため in-memory で足りる。
# 実装は core/llm_worker/cost_gate.py の CostGate/InMemoryCounterGate に共通化済み
# （session_call_counts / daily_call_counts / confirm_prompt_counts は同じ dict
# オブジェクトへのエイリアス。既存テストの直接操作と互換）。
_cost_gate = CostGate()
_session_call_counts: dict[tuple, int] = _cost_gate.session_counts
_daily_call_counts: dict[tuple, int] = _cost_gate.daily_counts
# 方法C（回答末尾の帰属確認プロンプト）のセッション内提示カウンタ（P7: 毎回出さない）
_confirm_gate = InMemoryCounterGate()
_confirm_prompt_counts: dict[tuple, int] = _confirm_gate.counts


def _today() -> str:
    return datetime.date.today().isoformat()


def _check_and_count_llm_call(user_id: str, course_id: str, topic_id: str) -> bool:
    """コスト上限内なら True を返しカウントを進める。上限超過なら False。"""
    settings = get_settings()
    per_session = int(getattr(settings, "anchor_max_calls_per_session", 3))
    per_day = int(getattr(settings, "anchor_max_calls_per_day", 10))
    skey = (user_id, course_id, topic_id, _today())
    dkey = (user_id, _today())
    ok = _cost_gate.check_and_count(
        session_limit=per_session, session_key=skey,
        daily_limit=per_day, daily_key=dkey,
    )
    if not ok:
        logger.info("anchor mining skipped: cost cap reached (session=%s, daily=%s)", skey, dkey)
    return ok


def check_and_count_confirm_prompt(user_id: str, course_id: str, topic_id: str | None) -> bool:
    """方法C の確認プロンプトを出してよければ True（セッション内上限まで。P7）。"""
    settings = get_settings()
    limit = int(getattr(settings, "anchor_confirm_max_per_session", 3))
    key = (user_id, course_id, topic_id or "", _today())
    return _confirm_gate.check_and_count(key, limit)


def _fetch_pending_questions(session, user_id: str, course_id: str, topic_id: str | None) -> list:
    """未帰属（structure_anchor 無し・anchor_analyzed_at 無し）の問い痕跡を古い順に返す。"""
    return session.execute(
        sa_text("""
            SELECT id, payload, created_at
            FROM interest_traces
            WHERE user_id = CAST(:uid AS uuid) AND course_id = :cid
              AND (topic_id = :tid OR (:tid IS NULL AND topic_id IS NULL))
              AND kind = 'question'
              AND payload->'structure_anchor' IS NULL
              AND payload->>'anchor_analyzed_at' IS NULL
            ORDER BY created_at ASC
        """),
        {"uid": user_id, "cid": course_id, "tid": topic_id},
    ).fetchall()


def maybe_schedule_anchor_mining(
    user_id: str,
    course_id: str,
    topic_id: str | None,
    session_end_check: bool = False,
) -> bool:
    """トリガー条件を満たしていればバックグラウンドスレッドで帰属分析を起動する。

    best-effort: 例外はログのみ（チャット応答・ダイジェスト取得を止めない。P6）。
    戻り値はスレッドを起動したかどうか。
    """
    try:
        session = _pg_session()
        try:
            rows = _fetch_pending_questions(session, user_id, course_id, topic_id)
        finally:
            session.close()
        if not rows:
            return False
        should_run = len(rows) >= QUESTION_BATCH_THRESHOLD
        if not should_run and session_end_check:
            last_created = rows[-1][2]
            if last_created is not None:
                now = datetime.datetime.now(getattr(last_created, "tzinfo", None))
                idle = now - last_created
                should_run = idle >= datetime.timedelta(minutes=SESSION_IDLE_MINUTES)
        if not should_run:
            return False
        threading.Thread(
            target=run_anchor_mining,
            args=(user_id, course_id, topic_id),
            daemon=True,
        ).start()
        return True
    except Exception as exc:
        logger.warning("maybe_schedule_anchor_mining failed: %s", exc)
        return False


def _collect_context_blocks(session, course_id: str, cited_chunk_ids: list[str]) -> dict:
    """アンカー候補ブロックを best-effort で収集する（無い構造は空のまま＝縮退。§5）。"""
    blocks: dict = {"claims": [], "equations": [], "derivation_steps": [], "concepts": [], "chunks": []}
    if cited_chunk_ids:
        try:
            rows = session.execute(
                sa_text("""
                    SELECT id, LEFT(COALESCE(NULLIF(display_text, ''), text), 200)
                    FROM chunks WHERE id::text = ANY(:ids)
                """),
                {"ids": cited_chunk_ids},
            ).fetchall()
            blocks["chunks"] = [{"id": str(r[0]), "head": r[1] or ""} for r in rows]
        except Exception as exc:
            logger.info("anchor context: chunk lookup skipped: %s", exc)
        try:
            rows = session.execute(
                sa_text("""
                    SELECT id, claim_type, LEFT(text, 200)
                    FROM theory_claims WHERE chunk_id::text = ANY(:ids)
                    LIMIT :lim
                """),
                {"ids": cited_chunk_ids, "lim": _MAX_CLAIM_BLOCKS},
            ).fetchall()
            for r in rows:
                item = {"id": str(r[0]), "text": r[2] or ""}
                ctype = str(r[1] or "")
                if ctype == "equation":
                    blocks["equations"].append(item)
                elif ctype == "derivation_step":
                    blocks["derivation_steps"].append(item)
                else:
                    blocks["claims"].append(item)
        except Exception as exc:
            logger.info("anchor context: theory_claims lookup skipped: %s", exc)
    try:
        rows = session.execute(
            sa_text("SELECT id, name FROM theory_components WHERE course_id = :cid LIMIT :lim"),
            {"cid": course_id, "lim": _MAX_CONCEPT_BLOCKS},
        ).fetchall()
        blocks["concepts"] = [{"id": str(r[0]), "label": r[1] or ""} for r in rows]
    except Exception as exc:
        logger.info("anchor context: theory_components lookup skipped: %s", exc)
    return blocks


def run_anchor_mining(user_id: str, course_id: str, topic_id: str | None) -> int:
    """1トピック分の未帰属の問いを1バッチとして帰属し、候補を payload に書き戻す。

    戻り値は候補を書き込んだ行数。冪等性: 処理対象の問い痕跡に
    payload.anchor_analyzed_at を先に付けてから LLM を呼ぶ（並行再実行をスキップ）。
    """
    bind_usage_context("learning:structure_anchor", user_id=str(user_id), course_id=str(course_id))
    session = _pg_session()
    try:
        pending = _fetch_pending_questions(session, user_id, course_id, topic_id)
        if not pending:
            return 0
        pending_ids = [str(r[0]) for r in pending]
        # 冪等化: 先に anchor_analyzed_at を立てる（並行スレッドの二重分析を防ぐ）
        claimed = session.execute(
            sa_text("""
                UPDATE interest_traces
                SET payload = payload || jsonb_build_object('anchor_analyzed_at', to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SSOF'))
                WHERE id::text = ANY(:ids) AND payload->>'anchor_analyzed_at' IS NULL
                RETURNING id
            """),
            {"ids": pending_ids},
        ).fetchall()
        session.commit()
        if not claimed:
            return 0
        claimed_ids = {str(r[0]) for r in claimed}

        if not _check_and_count_llm_call(user_id, course_id, topic_id or ""):
            return 0

        course_row = session.execute(
            sa_text("SELECT data FROM learning_courses WHERE id = :cid"),
            {"cid": course_id},
        ).fetchone()

        questions: list[QuestionRecord] = []
        segments: dict[str, str] = {}
        all_cited: list[str] = []
        for r in pending:
            if str(r[0]) not in claimed_ids:
                continue
            payload = r[1] if isinstance(r[1], dict) else json.loads(r[1] or "{}")
            pos = payload.get("position_anchor") or {}
            seg = pos.get("segment_id")
            seg_id = int(seg) if isinstance(seg, (int, float, str)) and str(seg).lstrip("-").isdigit() else None
            cited = [str(c) for c in (payload.get("cited_chunk_ids") or []) if c]
            all_cited.extend(c for c in cited if c not in all_cited)
            questions.append(QuestionRecord(
                trace_id=str(r[0]),
                text=payload.get("text", ""),
                context_label=payload.get("context_label", ""),
                segment_id=seg_id,
                cited_chunk_ids=cited,
            ))
            if seg_id is not None:
                segments.setdefault(
                    f"seg_{seg_id}", payload.get("context_label", "") or (topic_id or ""),
                )

        blocks = _collect_context_blocks(session, course_id, all_cited)
    except Exception as exc:
        session.rollback()
        logger.warning("run_anchor_mining setup failed: %s", exc)
        return 0
    finally:
        session.close()

    if not questions:
        return 0

    topic_title, _context_label = _topic_labels(course_row[0] if course_row else None, topic_id)
    context = AnchorContext(
        course_id=course_id,
        topic_id=topic_id or "",
        topic_title=topic_title,
        questions=questions,
        claims=blocks["claims"],
        equations=blocks["equations"],
        derivation_steps=blocks["derivation_steps"],
        concepts=blocks["concepts"],
        chunks=blocks["chunks"],
        segments=[{"id": k, "label": v} for k, v in segments.items()],
    )

    agent = StructureAnchorAgent()
    result = agent.run(context)

    saved = 0
    session = _pg_session()
    try:
        if result.repair_failed:
            # 2回失敗してもバッチは破棄せず、対象痕跡に印だけ残す（P4）。
            # structure_anchor は書かない（誤帰属を残さない）ので再訪キューには影響しない。
            session.execute(
                sa_text("""
                    UPDATE interest_traces
                    SET payload = payload || jsonb_build_object('anchor_repair_failed', true)
                    WHERE id::text = ANY(:ids)
                """),
                {"ids": sorted(claimed_ids)},
            )
        else:
            for cand in result.candidates:
                anchor = candidate_to_anchor_payload(cand)
                row = session.execute(
                    sa_text("""
                        UPDATE interest_traces
                        SET payload = jsonb_set(payload, '{structure_anchor}', CAST(:anchor AS jsonb))
                        WHERE id = CAST(:tid AS uuid) AND user_id = CAST(:uid AS uuid)
                          AND payload->'structure_anchor' IS NULL
                        RETURNING id
                    """),
                    {
                        "anchor": json.dumps(anchor, ensure_ascii=False),
                        "tid": cand.trace_id, "uid": user_id,
                    },
                ).fetchone()
                if row is not None:
                    saved += 1
            # 帰属不能は理由のみ payload に残す（P4: 落とさない・誤帰属もしない）
            for un in result.unattributed:
                session.execute(
                    sa_text("""
                        UPDATE interest_traces
                        SET payload = payload || jsonb_build_object('anchor_unattributed_reason', CAST(:reason AS text))
                        WHERE id = CAST(:tid AS uuid) AND user_id = CAST(:uid AS uuid)
                    """),
                    {"reason": (un.reason or "")[:300], "tid": un.trace_id, "uid": user_id},
                )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("run_anchor_mining persist failed: %s", exc)
        return 0
    finally:
        session.close()
    logger.info(
        "anchor mining done: user=%s course=%s topic=%s saved=%d repair_failed=%s",
        user_id, course_id, topic_id, saved, result.repair_failed,
    )
    return saved


def _topic_labels(course_data, topic_id: str | None) -> tuple[str, str]:
    """course_data からトピック表示名と context_label（章·トピック）を引く。"""
    data = course_data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = None
    if not isinstance(data, dict) or not topic_id:
        return topic_id or "", topic_id or ""
    topic_title = topic_id
    chapter_title = ""
    chapters = course_chapters(data)
    for t in course_topics(data):
        if isinstance(t, dict) and str(t.get("id")) == str(topic_id):
            topic_title = t.get("title") or topic_id
            ci = t.get("chapter_index")
            if isinstance(ci, int) and 0 <= ci < len(chapters) and isinstance(chapters[ci], dict):
                chapter_title = chapters[ci].get("title") or ""
            break
    context_label = " · ".join([s for s in [chapter_title, topic_title] if s])
    return topic_title, context_label or topic_title
