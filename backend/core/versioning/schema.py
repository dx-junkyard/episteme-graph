"""V層の語彙・定数の正本。"""
from __future__ import annotations

# オブジェクト種別（ポリモーフィック: course=TEXT id, document=UUID）
OBJECT_TYPE_COURSE = "course"
OBJECT_TYPE_DOCUMENT = "document"
OBJECT_TYPES = (OBJECT_TYPE_COURSE, OBJECT_TYPE_DOCUMENT)

# shared_version_state.lifecycle
LIFECYCLE_ACTIVE = "active"
LIFECYCLE_PENDING_DELETION = "pending_deletion"
LIFECYCLE_PURGED = "purged"

# shared_version_subscriptions.status
SUB_ACTIVE = "active"
SUB_UNSUBSCRIBED = "unsubscribed"

# share_notifications.kind
NOTIF_VERSION_PUBLISHED = "version_published"
NOTIF_DELETION_SCHEDULED = "deletion_scheduled"
NOTIF_DELETION_CANCELLED = "deletion_cancelled"
NOTIF_DELETED = "deleted"
NOTIFICATION_KINDS = (
    NOTIF_VERSION_PUBLISHED,
    NOTIF_DELETION_SCHEDULED,
    NOTIF_DELETION_CANCELLED,
    NOTIF_DELETED,
)

# theory_review_events.entity_type（監査）
AUDIT_RELEASE = "shared_release"
AUDIT_DELETION = "shared_deletion"
AUDIT_SUBSCRIPTION = "shared_subscription"

# 削除猶予の既定日数（所有者が予約時に上書き可能）
DEFAULT_GRACE_DAYS = 14


class VersioningError(Exception):
    """V層の操作エラー基底（API 層で HTTP ステータスへマップする）。"""


class PurgedError(VersioningError):
    """purge 済みオブジェクトへの操作（API では 410 Gone）。"""


class PendingDeletionError(VersioningError):
    """削除予約中のため発行できない（API では 409 Conflict）。"""


class AdoptConflictError(VersioningError):
    """楽観ロック衝突。expected_pinned_release_id が現在値と一致しない（API では 409）。"""


def is_valid_object_type(object_type: str) -> bool:
    return object_type in OBJECT_TYPES
