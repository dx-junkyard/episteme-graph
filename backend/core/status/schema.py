"""ライフサイクル語彙の正本（S1: 状態は投影、保存するのは遷移のみ）。

`projector.py` が導出する現在状態の語彙、`watcher.py` が発火する遷移イベント種別、
`notification_rules.py` が配送する通知種別（v1 は 6 種に限定, S2/S4 準拠）をここに集約する。
FastAPI / LLM クライアントを import しない（S3）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 教材（MaterialStatus）
# ---------------------------------------------------------------------------

MATERIAL_STATE_UPLOADED = "uploaded"
MATERIAL_STATE_CHUNKING = "chunking"
MATERIAL_STATE_ANALYZING = "analyzing"
MATERIAL_STATE_ANALYZED = "analyzed"
MATERIAL_STATE_ANALYSIS_FAILED = "analysis_failed"
MATERIAL_STATE_UNKNOWN = "unknown"

MATERIAL_STATES = (
    MATERIAL_STATE_UPLOADED,
    MATERIAL_STATE_CHUNKING,
    MATERIAL_STATE_ANALYZING,
    MATERIAL_STATE_ANALYZED,
    MATERIAL_STATE_ANALYSIS_FAILED,
    MATERIAL_STATE_UNKNOWN,
)


@dataclass
class MaterialStatus:
    """教材の現在状態の投影結果（保存しない・毎回導出, S1）。"""

    document_id: str
    material_id: str
    state: str = MATERIAL_STATE_UNKNOWN
    stage: str = ""
    reason: str = ""
    run_id: str | None = None
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "material_id": self.material_id,
            "state": self.state,
            "stage": self.stage,
            "reason": self.reason,
            "run_id": self.run_id,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# コース（CourseStatus）— チェックポイント集合（順序を強制しない）
# ---------------------------------------------------------------------------

SCRIPT_STATUS_DRAFT = "draft"
SCRIPT_STATUS_PARTIAL = "partial"
SCRIPT_STATUS_GENERATED = "generated"
SCRIPT_STATUSES = (SCRIPT_STATUS_DRAFT, SCRIPT_STATUS_PARTIAL, SCRIPT_STATUS_GENERATED)

AUDIO_STATUS_NONE = "none"
AUDIO_STATUS_PARTIAL = "partial"
AUDIO_STATUS_GENERATED = "generated"
AUDIO_STATUSES = (AUDIO_STATUS_NONE, AUDIO_STATUS_PARTIAL, AUDIO_STATUS_GENERATED)


@dataclass
class CourseStatus:
    """コースの現在状態はチェックポイント集合として投影する（単線の順序ではない）。"""

    course_id: str
    registered: bool = True
    script_status: str = SCRIPT_STATUS_DRAFT
    audio_status: str = AUDIO_STATUS_NONE
    atlas_bound: bool = False
    published: bool = False
    shared: bool = False
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "course_id": self.course_id,
            "registered": self.registered,
            "script_status": self.script_status,
            "audio_status": self.audio_status,
            "atlas_bound": self.atlas_bound,
            "published": self.published,
            "shared": self.shared,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# 遷移イベント種別（status_events.event_kind — watcher が発火。通知専用ではない）
# ---------------------------------------------------------------------------

EVENT_MATERIAL_ANALYSIS_COMPLETED = "material.analysis_completed"
EVENT_MATERIAL_ANALYSIS_FAILED = "material.analysis_failed"
EVENT_COURSE_SCRIPT_GENERATED = "course.script_generated"
EVENT_COURSE_SCRIPT_GENERATION_FAILED = "course.script_generation_failed"
EVENT_COURSE_AUDIO_GENERATED = "course.audio_generated"
EVENT_COURSE_AUDIO_GENERATION_FAILED = "course.audio_generation_failed"

EVENT_KINDS = (
    EVENT_MATERIAL_ANALYSIS_COMPLETED,
    EVENT_MATERIAL_ANALYSIS_FAILED,
    EVENT_COURSE_SCRIPT_GENERATED,
    EVENT_COURSE_SCRIPT_GENERATION_FAILED,
    EVENT_COURSE_AUDIO_GENERATED,
    EVENT_COURSE_AUDIO_GENERATION_FAILED,
)

ENTITY_TYPE_MATERIAL = "material"
ENTITY_TYPE_COURSE = "course"

SOURCE_TABLE_ANALYSIS_RUNS = "document_analysis_runs"
SOURCE_TABLE_BACKGROUND_TASKS = "background_tasks"

WATCHED_SOURCE_TABLES = (SOURCE_TABLE_ANALYSIS_RUNS, SOURCE_TABLE_BACKGROUND_TASKS)


# ---------------------------------------------------------------------------
# 通知種別（user_notifications.kind — v1 は完了・失敗の6種に限定, 段階登録）
# ---------------------------------------------------------------------------

NOTIF_MATERIAL_ANALYSIS_COMPLETED = "material_analysis_completed"
NOTIF_MATERIAL_ANALYSIS_FAILED = "material_analysis_failed"
NOTIF_COURSE_SCRIPT_COMPLETED = "course_script_completed"
NOTIF_COURSE_SCRIPT_FAILED = "course_script_failed"
NOTIF_COURSE_AUDIO_COMPLETED = "course_audio_completed"
NOTIF_COURSE_AUDIO_FAILED = "course_audio_failed"

NOTIFICATION_KINDS = (
    NOTIF_MATERIAL_ANALYSIS_COMPLETED,
    NOTIF_MATERIAL_ANALYSIS_FAILED,
    NOTIF_COURSE_SCRIPT_COMPLETED,
    NOTIF_COURSE_SCRIPT_FAILED,
    NOTIF_COURSE_AUDIO_COMPLETED,
    NOTIF_COURSE_AUDIO_FAILED,
)

# event_kind -> notification kind（v1 の fan-out 対象はこの4対のみ, S2 冪等 UNIQUE と併せて
# 「通知過多は通知が無いのと同じ」の段階登録を構造的に強制する）
EVENT_TO_NOTIFICATION_KIND = {
    EVENT_MATERIAL_ANALYSIS_COMPLETED: NOTIF_MATERIAL_ANALYSIS_COMPLETED,
    EVENT_MATERIAL_ANALYSIS_FAILED: NOTIF_MATERIAL_ANALYSIS_FAILED,
    EVENT_COURSE_SCRIPT_GENERATED: NOTIF_COURSE_SCRIPT_COMPLETED,
    EVENT_COURSE_SCRIPT_GENERATION_FAILED: NOTIF_COURSE_SCRIPT_FAILED,
    EVENT_COURSE_AUDIO_GENERATED: NOTIF_COURSE_AUDIO_COMPLETED,
    EVENT_COURSE_AUDIO_GENERATION_FAILED: NOTIF_COURSE_AUDIO_FAILED,
}

assert set(EVENT_TO_NOTIFICATION_KIND.values()) == set(NOTIFICATION_KINDS)
