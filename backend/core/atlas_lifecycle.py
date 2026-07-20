"""分野の地図 — ドメインライフサイクル横断ヘルパー (migration 057)。

正本: docs/features/atlas_binding_lifecycle_design.md
  §3.2 (凍結時の影響プレビュー) / §3.4 (freeze / retire の通知) / §4.4 (freeze-impact API)。

`backend/api/routes/atlas.py`（別担当）から呼ばれる薄い core 層。FastAPI を import
しない（core/ 共通ルール）。本モジュール自体は宛先解決・差分計算という「読み取り + 通知」
だけを行い、`atlas_domain_meta` / `atlas_skeletons` への書き込みは持たない
（書き込みは `core/atlas_store.py::retire_domain` / `restore_domain` 等の責務）。
"""

from __future__ import annotations

import logging

from sqlalchemy import text as sa_text

from core import atlas as atlas_module
from core import course_data
from core.notification_recipients import (
    atlas_bound_course_owner_ids,
    atlas_skeleton_editor_ids,
)
from core.schema import AUDIT_ENTITY_ATLAS_SKELETON
from core.status import cross_layer_notify

logger = logging.getLogger(__name__)


def notify_atlas_event(
    session,
    domain_key: str,
    kind: str,
    payload: dict | None = None,
    *,
    actor_id: str | None = None,
) -> int:
    """freeze / retire の通知を関係教員へ配送する (best-effort, §3.4)。

    宛先 = 「バインド中コースの所有者」∪「骨格の編集履歴のある教員」の和から、
    ``actor_id``（操作を行った本人）を除外する。entity_type は
    ``AUDIT_ENTITY_ATLAS_SKELETON``、entity_id は ``domain_key``。

    ``session`` は宛先解決 (SELECT) にのみ使う — 実際の INSERT は
    ``cross_layer_notify.notify_user`` が自前セッションで行い、1名ごとに
    独立してコミットする（1名の配送失敗が他の宛先への配送を止めない）。

    例外は握りつぶし ``logger.warning`` のみに留める（呼び出し側の本処理
    ＝凍結・retire 自体を通知の成否で失敗させない）。返り値は配送成功件数。
    """
    try:
        owner_ids = atlas_bound_course_owner_ids(session, domain_key)
        editor_ids = atlas_skeleton_editor_ids(session, domain_key)
        recipients = {str(r) for r in (*owner_ids, *editor_ids) if r}
        if actor_id:
            recipients.discard(str(actor_id))
    except Exception:  # noqa: BLE001
        logger.warning(
            "atlas lifecycle notify: recipient resolution failed for domain=%s kind=%s",
            domain_key, kind, exc_info=True,
        )
        return 0

    delivered = 0
    for recipient_id in recipients:
        try:
            ok = cross_layer_notify.notify_user(
                recipient_id, kind, AUDIT_ENTITY_ATLAS_SKELETON, domain_key, payload,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "atlas lifecycle notify: delivery failed for domain=%s kind=%s recipient=%s",
                domain_key, kind, recipient_id, exc_info=True,
            )
            continue
        if ok:
            delivered += 1
    return delivered


def _affected_courses_for_domain(session, domain_key: str, removed_ids: set[str]) -> list[dict]:
    """domain にバインド中のコースのうち、``removed_ids`` を指す topic を持つものを列挙する。"""
    if not removed_ids or session is None:
        return []
    rows = session.execute(
        sa_text(
            "SELECT id, title, data FROM learning_courses WHERE data->>'cartridge_id' = :domain_key"
        ),
        {"domain_key": domain_key},
    ).mappings().fetchall()
    affected: list[dict] = []
    for row in rows:
        data = row["data"] if isinstance(row["data"], dict) else {}
        hit_topics: list[dict] = []
        for topic in course_data.course_topics(data):
            node_id = topic.get("atlas_node_id")
            if node_id and node_id in removed_ids:
                hit_topics.append({
                    "topic_id": str(topic.get("id") or ""),
                    "title": str(topic.get("title") or ""),
                    "atlas_node_id": str(node_id),
                })
        if hit_topics:
            affected.append({
                "course_id": row["id"],
                "title": row["title"] or row["id"],
                "topics": hit_topics,
            })
    return affected


def compute_freeze_impact(
    session,
    domain_key: str,
    draft_skeleton: atlas_module.AtlasSkeleton | None,
    frozen_skeleton: atlas_module.AtlasSkeleton | None,
) -> dict:
    """凍結前後の node_id 差分 + 影響コースを決定論的に計算する (§4.4)。

    ``draft_skeleton`` は比較の新側（draft レビュー中は draft、freeze 実行後の
    レスポンスに使う場合は新しく凍結された版）。``frozen_skeleton`` は比較の
    旧側（現行の凍結版。無ければ初回凍結 = removed は必ず空）。

    node id 集合は ``skeleton.concept_ids() + skeleton.region_ids()``。
    ``draft_skeleton`` が None（draft 自体が存在しない）の場合は全て空で返す
    （呼び出し側の 404 判定はこの関数の責務外）。
    """
    if draft_skeleton is None:
        return {
            "cartridge_id": domain_key,
            "frozen_version": frozen_skeleton.version if frozen_skeleton else "",
            "removed_node_ids": [],
            "added_node_ids": [],
            "affected_courses": [],
        }

    draft_ids = set(draft_skeleton.concept_ids()) | set(draft_skeleton.region_ids())

    if frozen_skeleton is None:
        # 初回凍結: 比較対象が無いため常に無影響 (removed は必ず空)。
        # draft 側の全 id は「参考情報」として added に載せる。
        removed_ids: set[str] = set()
        added_ids = draft_ids
        frozen_version = ""
    else:
        frozen_ids = set(frozen_skeleton.concept_ids()) | set(frozen_skeleton.region_ids())
        removed_ids = frozen_ids - draft_ids
        added_ids = draft_ids - frozen_ids
        frozen_version = frozen_skeleton.version

    affected_courses = _affected_courses_for_domain(session, domain_key, removed_ids)

    return {
        "cartridge_id": domain_key,
        "frozen_version": frozen_version,
        "removed_node_ids": sorted(removed_ids),
        "added_node_ids": sorted(added_ids),
        "affected_courses": affected_courses,
    }
