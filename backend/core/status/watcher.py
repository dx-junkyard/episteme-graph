"""遷移検知（watermark scan）— 書き込みパス計装（outbox）を使わず、既存テーブル自体が
持つ遷移の証跡（status ∈ {completed, failed} + 完了時刻）を周期スキャンして
status_events に append する（S2: 冪等・S3: 非LLM・A〜V層非改変）。

watermark は「status_events の MAX(occurred_at) per source_table」を再開点にする
（専用 watermark テーブルは持たない）。UNIQUE(source_table, source_id, event_kind) が
冪等性を担保するため、再スキャンしても二重 emit しない。

同期パスに入れない。起動時に daemon スレッドで回す（STATUS_WATCH_ENABLED で制御）。
"""
from __future__ import annotations

import logging
import os
import threading
import time

from sqlalchemy import text as sa_text

from core.postgres import get_session

from . import notification_rules, schema

logger = logging.getLogger(__name__)

_started = False
_lock = threading.Lock()


def _watermark(session, source_table: str):
    return session.execute(
        sa_text("SELECT MAX(occurred_at) FROM status_events WHERE source_table = :t"),
        {"t": source_table},
    ).scalar()


def _emit(session, *, event_kind: str, entity_type: str, entity_id: str, from_state: str,
          to_state: str, source_table: str, source_id: str, payload: dict, occurred_at) -> str | None:
    """1件のイベントを冪等に emit する。新規挿入できれば status_events.id を返す（else None）。"""
    import json

    row = session.execute(
        sa_text("""
            INSERT INTO status_events
                (event_kind, entity_type, entity_id, from_state, to_state,
                 source_table, source_id, payload, occurred_at)
            VALUES (:event_kind, :entity_type, :entity_id, :from_state, :to_state,
                    :source_table, :source_id, CAST(:payload AS jsonb), :occurred_at)
            ON CONFLICT (source_table, source_id, event_kind) DO NOTHING
            RETURNING id::text
        """),
        {
            "event_kind": event_kind, "entity_type": entity_type, "entity_id": entity_id,
            "from_state": from_state, "to_state": to_state, "source_table": source_table,
            "source_id": source_id, "payload": json.dumps(payload, ensure_ascii=False),
            "occurred_at": occurred_at,
        },
    ).fetchone()
    return row[0] if row else None


def _scan_analysis_runs(session) -> int:
    watermark = _watermark(session, schema.SOURCE_TABLE_ANALYSIS_RUNS)
    where_wm = "AND COALESCE(completed_at, updated_at) > :wm" if watermark else ""
    rows = session.execute(
        sa_text(f"""
            SELECT id::text, document_id, material_id, status, current_stage, error_message,
                   COALESCE(completed_at, updated_at) AS occurred_at
            FROM document_analysis_runs
            WHERE status IN ('completed', 'failed') {where_wm}
            ORDER BY COALESCE(completed_at, updated_at) ASC
        """),
        {"wm": watermark} if watermark else {},
    ).mappings().fetchall()

    emitted = 0
    for row in rows:
        entity_id = row["document_id"] or row["material_id"] or ""
        if not entity_id:
            continue
        if row["status"] == "completed":
            event_kind = schema.EVENT_MATERIAL_ANALYSIS_COMPLETED
        else:
            event_kind = schema.EVENT_MATERIAL_ANALYSIS_FAILED
        payload = {
            "material_id": row["material_id"] or "", "stage": row["current_stage"] or "",
            "reason": row["error_message"] or "",
        }
        event_id = _emit(
            session, event_kind=event_kind, entity_type=schema.ENTITY_TYPE_MATERIAL,
            entity_id=entity_id, from_state="", to_state=row["status"],
            source_table=schema.SOURCE_TABLE_ANALYSIS_RUNS, source_id=row["id"],
            payload=payload, occurred_at=row["occurred_at"],
        )
        session.commit()
        if event_id:
            emitted += 1
            notification_rules.fan_out_event(event_id, event_kind, schema.ENTITY_TYPE_MATERIAL, entity_id, payload)
    return emitted


_TASK_TYPE_TO_EVENTS = {
    "script_generation": (schema.EVENT_COURSE_SCRIPT_GENERATED, schema.EVENT_COURSE_SCRIPT_GENERATION_FAILED),
    "audio_generation": (schema.EVENT_COURSE_AUDIO_GENERATED, schema.EVENT_COURSE_AUDIO_GENERATION_FAILED),
}


def _scan_background_tasks(session) -> int:
    watermark = _watermark(session, schema.SOURCE_TABLE_BACKGROUND_TASKS)
    where_wm = "AND updated_at > :wm" if watermark else ""
    task_types = ", ".join(f"'{t}'" for t in _TASK_TYPE_TO_EVENTS)
    rows = session.execute(
        sa_text(f"""
            SELECT id, task_type, status, result_data, updated_at
            FROM background_tasks
            WHERE status IN ('completed', 'failed') AND task_type IN ({task_types}) {where_wm}
            ORDER BY updated_at ASC
        """),
        {"wm": watermark} if watermark else {},
    ).mappings().fetchall()

    emitted = 0
    for row in rows:
        result_data = row["result_data"] if isinstance(row["result_data"], dict) else {}
        course_id = result_data.get("course_id") or ""
        if not course_id:
            # course_id が result_data に無いタスクは投影できない（S5: 何もしない）
            continue
        completed_event, failed_event = _TASK_TYPE_TO_EVENTS[row["task_type"]]
        event_kind = completed_event if row["status"] == "completed" else failed_event
        payload = {"task_type": row["task_type"]}
        event_id = _emit(
            session, event_kind=event_kind, entity_type=schema.ENTITY_TYPE_COURSE,
            entity_id=course_id, from_state="", to_state=row["status"],
            source_table=schema.SOURCE_TABLE_BACKGROUND_TASKS, source_id=row["id"],
            payload=payload, occurred_at=row["updated_at"],
        )
        session.commit()
        if event_id:
            emitted += 1
            notification_rules.fan_out_event(event_id, event_kind, schema.ENTITY_TYPE_COURSE, course_id, payload)
    return emitted


def scan_once() -> int:
    """1巡スキャンする。戻り値は新規に emit したイベント件数。"""
    session = get_session()
    try:
        emitted = 0
        emitted += _scan_analysis_runs(session)
        emitted += _scan_background_tasks(session)
        return emitted
    except Exception:  # noqa: BLE001 — 1回のスキャン失敗が watcher を止めないように
        session.rollback()
        logger.exception("status watcher scan failed")
        return 0
    finally:
        session.close()


def run_forever(interval_seconds: int = 60) -> None:
    while True:
        try:
            scan_once()
        except Exception:  # noqa: BLE001
            logger.exception("status watcher iteration failed")
        time.sleep(max(10, interval_seconds))


def start_watcher() -> None:
    """起動時に1度だけ daemon watcher を開始する（STATUS_WATCH_ENABLED で無効化可）。"""
    global _started
    if os.getenv("STATUS_WATCH_ENABLED", "1") not in ("1", "true", "True", "yes"):
        logger.info("status watcher disabled by STATUS_WATCH_ENABLED")
        return
    with _lock:
        if _started:
            return
        interval = int(os.getenv("STATUS_WATCH_INTERVAL", "60") or "60")
        thread = threading.Thread(
            target=run_forever, kwargs={"interval_seconds": interval},
            name="status-watcher", daemon=True,
        )
        thread.start()
        _started = True
        logger.info("status watcher started (interval=%ds)", interval)
