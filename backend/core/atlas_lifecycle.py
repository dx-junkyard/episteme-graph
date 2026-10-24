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


#: 版から消える項目があるときに必ず添える事実文（件数・煽り表現を書かない）。
#: 正本: docs/features/category_gap_candidates_design.md §5.5（gap 経路に限らず
#: removed node のある freeze 全般の改善として入れる）。
FACT_REMOVED_NODES_HIDE_LEARNER_TRACES = (
    "この版に存在しない項目を参照する学習者の記録は、"
    "その版からは表示されなくなります（記録自体は残ります）。"
)

#: 新しい項目を足しても、既存の論文の配置は再解析するまで変わらないという事実
#: （「カテゴリを足したのに論文が載らない」という空振りの期待を作らないため。§5.5）。
FACT_ADDED_NODES_NEED_REANALYSIS = (
    "追加した項目に既存の論文が並ぶのは、その論文をもう一度解析したときです。"
)


#: 版から消える項目が無いことの肯定の事実文（IK-0851。「影響が無い」と「影響を確かめ
#: られなかった」を同じ空で表さない）。消える項目が無ければ対応が外れるコースも無い
#: （コースの影響は消える項目からしか生じない）。件数は書かない。
FACT_NO_REMOVED_NODES = "この凍結で削除される概念・対応が外れるコースはありません。"

#: 下書きの項目構成が現行の公開版と同じ（追加も削除も無い）ことの事実文（IK-0851）。
#: IK-0885: 関係（辺）も同じときだけ出す（関係を足した下書きに「同じです」と言わない）。
FACT_SAME_NODE_SET = "下書きの領域・概念・関係は、現在の公開版と同じです。"

#: 項目は同じで関係だけが変わるときの事実文（IK-0885）。
FACT_SAME_NODES_EDGES_CHANGED = "下書きの領域・概念は現在の公開版と同じで、関係（辺）が変わります。"

#: 関係の増減の列挙（IK-0885。「A — B（依存）」をラベルで並べる・件数なし）。
FACT_ADDED_EDGES_TEMPLATE = "次版で加わる関係: {edges}"
FACT_REMOVED_EDGES_TEMPLATE = "次版で外れる関係: {edges}"

#: 凍結を止める条件の列挙（IK-0885。freeze と freeze-impact が同じ判定を読む）。
FACT_BLOCKED_BY_GAPS_TEMPLATE = (
    "採用済みでまだ次版の下書きに入っていない候補があるため、このままでは公開できません: {labels}"
    "（「論文の解析から見つかった候補」で取り込むか、候補へ戻してください）"
)
FACT_BLOCKED_BY_EDGES_TEMPLATE = (
    "採用済みでまだ次版の下書きに入っていない関係があるため、このままでは公開できません: {labels}"
    "（「関係（辺）の候補」で下書きへ反映するか、候補へ戻してください）"
)
FACT_READY = "公開を止める条件はありません。"


def _node_labels(*skeletons) -> dict[str, str]:
    labels: dict[str, str] = {}
    for skeleton in skeletons:
        for region in getattr(skeleton, "regions", ()) or ():
            for concept in getattr(region, "concepts", ()) or ():
                labels.setdefault(concept.id, concept.label or concept.id)
            labels.setdefault(region.id, region.label or region.id)
    return labels


def _edge_set(skeleton) -> dict[tuple[str, str], str]:
    """骨格の辺 → ``{(無向ペア): kind}``（同じペアが重複すれば最初の種別）。"""
    out: dict[tuple[str, str], str] = {}
    for edge in getattr(skeleton, "edges", ()) or ():
        a, b = str(edge.from_id or ""), str(edge.to_id or "")
        if not a or not b:
            continue
        key = (a, b) if a <= b else (b, a)
        out.setdefault(key, str(getattr(edge, "kind", "") or ""))
    return out


def _edge_directed(skeleton) -> dict[tuple[str, str], tuple[str, str]]:
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for edge in getattr(skeleton, "edges", ()) or ():
        a, b = str(edge.from_id or ""), str(edge.to_id or "")
        if not a or not b:
            continue
        key = (a, b) if a <= b else (b, a)
        out.setdefault(key, (a, b))
    return out


def edge_change_label(from_label: str, to_label: str, kind: str) -> str:
    """「A — B（依存）」（種別は ``label_vocab.EDGE_KIND_LABELS`` の表示語）。"""
    from core.label_vocab import EDGE_KIND_LABELS

    kind_label = EDGE_KIND_LABELS.get(str(kind or ""), "")
    text = f"{from_label} — {to_label}"
    return f"{text}（{kind_label}）" if kind_label else text


def edge_changes(draft_skeleton, frozen_skeleton) -> dict[str, list[str]]:
    """下書きと現行凍結版の関係の増減（無向で比較・種別の変更は外れて加わる扱い）。"""
    if draft_skeleton is None:
        return {"added_edges": [], "removed_edges": []}
    labels = _node_labels(draft_skeleton, frozen_skeleton)
    new_edges = _edge_set(draft_skeleton)
    old_edges = _edge_set(frozen_skeleton) if frozen_skeleton is not None else {}
    new_dir = _edge_directed(draft_skeleton)
    old_dir = _edge_directed(frozen_skeleton) if frozen_skeleton is not None else {}

    def _label(key, kind, directed):
        a, b = directed.get(key, key)
        return edge_change_label(labels.get(a, a), labels.get(b, b), kind)

    added = [
        _label(k, kind, new_dir)
        for k, kind in sorted(new_edges.items())
        if old_edges.get(k) != kind
    ]
    removed = [
        _label(k, kind, old_dir)
        for k, kind in sorted(old_edges.items())
        if new_edges.get(k) != kind
    ]
    return {"added_edges": added, "removed_edges": removed}


def _freeze_impact_facts(
    removed_ids: set[str],
    added_ids: set[str],
    *,
    has_frozen: bool = True,
    added_edges: list[str] | None = None,
    removed_edges: list[str] | None = None,
) -> list[str]:
    """影響プレビューに添える事実文（該当するものだけ・順序は決定論）。"""
    facts: list[str] = []
    if removed_ids:
        facts.append(FACT_REMOVED_NODES_HIDE_LEARNER_TRACES)
    elif has_frozen:
        facts.append(FACT_NO_REMOVED_NODES)
    edges_changed = bool(added_edges or removed_edges)
    if added_ids:
        facts.append(FACT_ADDED_NODES_NEED_REANALYSIS)
    elif has_frozen and not removed_ids:
        facts.append(FACT_SAME_NODES_EDGES_CHANGED if edges_changed else FACT_SAME_NODE_SET)
    if has_frozen and added_edges:
        facts.append(FACT_ADDED_EDGES_TEMPLATE.format(edges="、".join(added_edges)))
    if has_frozen and removed_edges:
        facts.append(FACT_REMOVED_EDGES_TEMPLATE.format(edges="、".join(removed_edges)))
    return facts


def freeze_readiness(
    pending_labels: list[str],
    pending_edges: list[str],
    pending_items: list[dict] | None = None,
) -> dict:
    """凍結の可否（IK-0885 の正本。freeze と freeze-impact が同じものを読む）。

    材料（採用済み未反映の候補ラベル・関係ラベル）の取得は route 側（DB 照会と
    fail-open の責務）で、ここは可否と事実文を決める純関数。件数は書かない。
    """
    gaps = [str(label) for label in pending_labels or () if str(label or "").strip()]
    edges = [str(label) for label in pending_edges or () if str(label or "").strip()]
    facts: list[str] = []
    if gaps:
        facts.append(FACT_BLOCKED_BY_GAPS_TEMPLATE.format(labels="、".join(gaps)))
    if edges:
        facts.append(FACT_BLOCKED_BY_EDGES_TEMPLATE.format(labels="、".join(edges)))
    ready = not (gaps or edges)
    if ready:
        facts.append(FACT_READY)
    out = {
        "ready": ready,
        "pending_labels": gaps,
        "pending_edges": edges,
        "facts": facts,
    }
    # 止めている採用ごとの帰属と根拠（IK-0939）。渡されたときだけ足す（判定は変えない）。
    if pending_items:
        out["pending_items"] = [dict(item) for item in pending_items]
    return out


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
            "added_edges": [],
            "removed_edges": [],
            "facts": [],
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
    changes = edge_changes(draft_skeleton, frozen_skeleton)

    return {
        "cartridge_id": domain_key,
        "frozen_version": frozen_version,
        "removed_node_ids": sorted(removed_ids),
        "added_node_ids": sorted(added_ids),
        "affected_courses": affected_courses,
        # 関係（辺）の増減（IK-0885。ラベルの列挙・件数なし）。
        "added_edges": changes["added_edges"],
        "removed_edges": changes["removed_edges"],
        # 差分の意味を教員向けの事実文で添える（数値・督促は書かない。§5.5）。
        "facts": _freeze_impact_facts(
            removed_ids,
            added_ids,
            has_frozen=frozen_skeleton is not None,
            added_edges=changes["added_edges"],
            removed_edges=changes["removed_edges"],
        ),
    }
