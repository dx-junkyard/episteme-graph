"""Stage 1 の非同期バッチワーカー（lecture_studio の _batch_*_worker と同じ
threading.Thread 方式。新規キュー基盤は入れない）。

- トリガー: (a) セッション終了（最終メッセージから20分無活動）、(b) 進行中でも
  tension_hint 累積5件、のいずれか早い方。
- 冪等性: 処理済みヒント痕跡は analyzed_at（既存列）で管理。未解析ヒントが無ければ
  同一窓の再実行はスキップされる。
- コスト上限: 1セッションあたり LLM コール上限（TENSION_MAX_CALLS_PER_SESSION、既定3）、
  1ユーザー1日上限（TENSION_MAX_CALLS_PER_DAY、既定10）。モデルは fast tier 既定
  （TENSION_LLM_MODEL で上書き可）。
- 提示上限はダイジェスト取得側（services.get_tension_digest）で適用する:
  confidence 降順・1セッション最大3件・confidence >= 0.55。残りは candidate のまま
  DB に保持する（P4）。

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
from core.llm_worker.cost_gate import CostGate
from core.postgres import get_session as _pg_session
from core.tension.agent import DEFAULT_MAX_CANDIDATES, TensionMiningAgent
from core.tension.input_builder import select_window_turns, turns_from_history
from core.tension.schema import (
    DETECTOR_VERSION,
    ConversationWindow,
    candidate_to_payload,
)

logger = logging.getLogger(__name__)

# トリガー閾値（設計書 §8）
HINT_BATCH_THRESHOLD = 5          # 進行中でも hint 累積5件で起動
SESSION_IDLE_MINUTES = 20         # 最終メッセージから20分無活動でセッション終了扱い

# ダイジェスト提示条件（§8/§9。取得側と共有する定数）
DIGEST_MIN_CONFIDENCE = 0.55
DIGEST_MAX_ITEMS = 3

# コスト上限のプロセス内カウンタ（(user_id, course_id, topic_id, date) / (user_id, date)）。
# API サーバーは単一プロセス運用のため in-memory で足りる。再起動でリセットされるが
# 上限は安全側の防波堤であり厳密な会計ではない。
# 実装は core/llm_worker/cost_gate.py の CostGate に共通化済み（session_call_counts /
# daily_call_counts は同じ dict オブジェクトへのエイリアス。既存テストの直接操作と互換）。
_cost_gate = CostGate()
_session_call_counts: dict[tuple, int] = _cost_gate.session_counts
_daily_call_counts: dict[tuple, int] = _cost_gate.daily_counts


def _today() -> str:
    return datetime.date.today().isoformat()


def _check_and_count_llm_call(user_id: str, course_id: str, topic_id: str) -> bool:
    """コスト上限内なら True を返しカウントを進める。上限超過なら False。"""
    settings = get_settings()
    per_session = int(getattr(settings, "tension_max_calls_per_session", 3))
    per_day = int(getattr(settings, "tension_max_calls_per_day", 10))
    skey = (user_id, course_id, topic_id, _today())
    dkey = (user_id, _today())
    ok = _cost_gate.check_and_count(
        session_limit=per_session, session_key=skey,
        daily_limit=per_day, daily_key=dkey,
    )
    if not ok:
        logger.info("tension mining skipped: cost cap reached (session=%s, daily=%s)", skey, dkey)
    return ok


def _fetch_pending_hints(session, user_id: str, course_id: str, topic_id: str) -> list:
    """未解析（analyzed_at IS NULL）の tension_hint 付き raw 痕跡を古い順に返す。"""
    return session.execute(
        sa_text("""
            SELECT id, payload, created_at
            FROM interest_traces
            WHERE user_id = CAST(:uid AS uuid) AND course_id = :cid
              AND (topic_id = :tid OR (:tid IS NULL AND topic_id IS NULL))
              AND payload->>'tension_hint' = 'true'
              AND analyzed_at IS NULL
              AND status <> 'superseded'  -- 機能3: 書き直し/削除で差し替えられた痕跡は解析しない
            ORDER BY created_at ASC
        """),
        {"uid": user_id, "cid": course_id, "tid": topic_id},
    ).fetchall()


def maybe_schedule_tension_mining(
    user_id: str,
    course_id: str,
    topic_id: str | None,
    session_end_check: bool = False,
) -> bool:
    """トリガー条件を満たしていればバックグラウンドスレッドで分析を起動する。

    best-effort: 例外はログのみ（チャット応答・ダイジェスト取得を止めない。P6）。
    戻り値はスレッドを起動したかどうか。
    """
    try:
        session = _pg_session()
        try:
            rows = _fetch_pending_hints(session, user_id, course_id, topic_id)
        finally:
            session.close()
        if not rows:
            return False
        should_run = len(rows) >= HINT_BATCH_THRESHOLD
        if not should_run and session_end_check:
            last_created = rows[-1][2]
            if last_created is not None:
                now = datetime.datetime.now(getattr(last_created, "tzinfo", None))
                idle = now - last_created
                should_run = idle >= datetime.timedelta(minutes=SESSION_IDLE_MINUTES)
        if not should_run:
            return False
        threading.Thread(
            target=run_tension_mining,
            args=(user_id, course_id, topic_id),
            daemon=True,
        ).start()
        return True
    except Exception as exc:
        logger.warning("maybe_schedule_tension_mining failed: %s", exc)
        return False


def run_tension_mining(user_id: str, course_id: str, topic_id: str | None) -> int:
    """1トピック分の未解析ヒントを1つの会話窓として分析し、候補を永続化する。

    戻り値は保存した candidate 行数。冪等性: 処理対象のヒント痕跡に analyzed_at を
    先に付けてから LLM を呼ぶ（同一窓の並行再実行をスキップ）。
    """
    bind_usage_context("learning:tension", user_id=str(user_id), course_id=str(course_id))
    session = _pg_session()
    try:
        hints = _fetch_pending_hints(session, user_id, course_id, topic_id)
        if not hints:
            return 0
        hint_ids = [str(r[0]) for r in hints]
        # 冪等化: 先に analyzed_at を立てる（並行スレッドの二重分析を防ぐ）
        claimed = session.execute(
            sa_text("""
                UPDATE interest_traces
                SET analyzed_at = now()
                WHERE id::text = ANY(:ids) AND analyzed_at IS NULL
                RETURNING id
            """),
            {"ids": hint_ids},
        ).fetchall()
        session.commit()
        if not claimed:
            return 0

        if not _check_and_count_llm_call(user_id, course_id, topic_id or ""):
            return 0

        history_row = session.execute(
            sa_text("""
                SELECT history FROM learning_chat_history
                WHERE user_id = CAST(:uid AS uuid) AND course_id = :cid AND topic_id = :tid
            """),
            {"uid": user_id, "cid": course_id, "tid": topic_id},
        ).fetchone()
        course_row = session.execute(
            sa_text("SELECT data FROM learning_courses WHERE id = :cid"),
            {"cid": course_id},
        ).fetchone()
    except Exception as exc:
        session.rollback()
        logger.warning("run_tension_mining setup failed: %s", exc)
        return 0
    finally:
        session.close()

    history = history_row[0] if history_row else []
    if isinstance(history, str):
        try:
            history = json.loads(history)
        except Exception:
            history = []
    if not history:
        return 0

    topic_title, context_label = _topic_labels(course_row[0] if course_row else None, topic_id)
    hint_payloads = [r[1] if isinstance(r[1], dict) else json.loads(r[1] or "{}") for r in hints]
    hint_texts = [p.get("text", "") for p in hint_payloads if p.get("text")]
    overall_tier = next(
        (p.get("overall_tier", "") for p in reversed(hint_payloads) if p.get("overall_tier")), "",
    )

    turns = turns_from_history(history, hint_texts=hint_texts)
    window = ConversationWindow(
        course_id=course_id,
        topic_id=topic_id or "",
        topic_title=topic_title,
        turns=select_window_turns(turns),
        components=[],  # 窓内で参照が確定している component/chunk のみ渡す（現状は未収集）
        chunks=[],
    )

    agent = TensionMiningAgent()
    result = agent.run(window, max_candidates=DEFAULT_MAX_CANDIDATES)

    saved = 0
    session = _pg_session()
    try:
        if result.repair_failed:
            # 2回失敗してもバッチは破棄せず unclassified の1行を残す（P4）。
            # confidence=0.0 なのでダイジェストには出ない（閾値で自然に落ちる）。
            payload = {
                "text": (hint_texts[-1] if hint_texts else "")[:500],
                "context_label": context_label,
                "tension_type": "unclassified",
                "paraphrase": "",
                "learner_text": "",
                "turn_ids": [],
                "target_refs": {"component_ids": [], "chunk_ids": [], "topic_id": topic_id or "", "edge_ids": []},
                "confidence": 0.0,
                "reason": "; ".join(result.warnings)[:500],
                "detector_version": DETECTOR_VERSION,
                "overall_tier_at_detection": overall_tier,
                "repair_failed": True,
            }
            _insert_candidate(session, user_id, course_id, topic_id, payload)
            saved = 1
        else:
            for cand in result.candidates:
                payload = candidate_to_payload(cand, context_label=context_label, overall_tier=overall_tier)
                _insert_candidate(session, user_id, course_id, topic_id, payload)
                saved += 1
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("run_tension_mining persist failed: %s", exc)
        return 0
    finally:
        session.close()
    logger.info(
        "tension mining done: user=%s course=%s topic=%s saved=%d repair_failed=%s",
        user_id, course_id, topic_id, saved, result.repair_failed,
    )
    return saved


def _insert_candidate(session, user_id: str, course_id: str, topic_id: str | None, payload: dict) -> None:
    """候補1件を kind='tension' / status='candidate' で保存する（P1: 確定は本人のみ）。"""
    session.execute(
        sa_text("""
            INSERT INTO interest_traces (id, user_id, course_id, topic_id, kind, payload, status, analyzed_at)
            VALUES (gen_random_uuid(), CAST(:uid AS uuid), :cid, :tid, 'tension',
                    CAST(:payload AS jsonb), 'candidate', now())
        """),
        {
            "uid": user_id, "cid": course_id, "tid": topic_id,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )


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
