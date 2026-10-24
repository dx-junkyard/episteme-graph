"""コーススコープ component 文脈 API の core ロジック（非LLM・読み取り専用）。

設計書: ``docs/features/component_evidence_redesign.md``（Phase 2 + Phase 3
バックエンド）。

``build_component_context()`` は、学習者が受講しているコースの文脈で「1つの
component が論文の中でどういう役割を持ち、何を前提・入出力とし、どの claim/
equation に裏付けられ、共通部品（L層）や周辺構造（W層 context lens）とどう
つながっているか」を1つの DTO にまとめて返す。

- **fail-closed**: component の ``document_id`` がコースの document 集合
  （呼び出し側が渡す ``course_document_ids``、通常は
  ``routes/lecture.py::_course_document_ids`` の戻り値）に含まれない場合は
  SQL の時点で除外し、``None`` を返す（コース外文書の component は解決自体が
  失敗する — 図配信 API（``routes/learning.py::get_course_figure_image``）と
  同じ設計）。
- **component_id は二形式を受け付ける**: snapshot 由来の agent 側 ID
  （例 ``comp_001``。ComponentAssemblyAgent が振る ``ComponentRecord.component_id``）と
  ``theory_components.id``（DB UUID）の両方。DB 行の
  ``source_scope.legacy_ids``（persistence.py の ``persist_components`` が
  ``legacy_ids = [agent_component_id]`` を保存する契約）で解決する
  （``core/deliberation/decomposition.py::_agent_id_candidates_for_focus`` と同型の
  逆引き規約）。
- **数値 confidence を一切含めない**: 最終レスポンスを再帰走査して
  ``"confidence"`` キーを除去する（W8 相当。同一性リンクの ``evidence`` に
  confidence が混入していても落とす）。
- **共通プリミティブは ``core/learner_context_common.py`` が正本**: 数値除去
  （``strip_confidence``）・コース document スコープを SQL で強制する要素解決
  （``scoped_id_match_sql``）・W層 ITEM の学習者向け射影と遮断層
  （``project_item`` / ``visible_lane_items``）は claim / equation 文脈 API
  （``core/element_context.py``）と共有する。``graph.upper`` / ``graph.lower`` の
  キー集合は従来の6キーのまま（``legacy_keys_only=True``）で、内部 ID・生 TeX の
  ラベル遮断と ``navigable`` の fail-closed 再計算だけが共有によって効く。
- 本モジュールは FastAPI を import しない（開発ルール2 / core/ 共通ルール）。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session
from core import learner_context_common
from core.display_projection import contains_pipeline_policy_token
from core.focus_document import resolve_in_focus, unique_row_lookup
from core.learner_context_common import (  # noqa: F401  (旧名の再エクスポート)
    PROVENANCE_COURSE_FREEZE,
    is_symbol_like_concept as _is_symbol_like_concept,
    is_uuid as _is_uuid,
    json_dict as _json_dict,
    json_list as _json_list,
    normalized_document_ids as _normalized_document_ids,
    project_item,
    reconstructed_equation_note as _reconstructed_equation_note,
    scoped_id_match_sql,
    strip_confidence,
    visible_concept_names,
)
from core.deliberation import context_lens as context_lens_mod
from core.deliberation import identity_links as identity_links_mod
from core.deliberation.refs import document_run_artifacts, equation_records
from core.deliberation.schema import (
    ELEMENT_THEORY_COMPONENT,
    IDENTITY_LINK_STATUS_CONFIRMED,
    SCOPE_DOCUMENT,
    ElementRef,
)
from core.library import schema as library_schema
from core.library import store as library_store_mod

logger = logging.getLogger(__name__)

# ComponentFieldRef 系（preconditions/inputs/outputs/cautions）の表示上限
# （設計書: text 空は除外、最大8）。
_MAX_FIELD_REF_ITEMS = 8

# W層 context lens レーンの表示上限（正本は ``learner_context_common.LANE_MAX``。
# context_lens.py の _CONTEXT_LANE_MAX と同じ値で、ここでは candidate 除外後の件数に
# 対して適用する。旧名を再エクスポートして既存の参照面を維持する）。
_GRAPH_LANE_MAX = learner_context_common.LANE_MAX

# equation の役割語彙（*_equation_ids フィールド名 → role）。優先順はこの順序
# （同じ equation_id が複数フィールドに現れた場合は最初に一致した role を採用する）。
_EQUATION_ROLE_FIELDS = (
    ("definition_equation_ids", "definition"),
    ("input_equation_ids", "input"),
    ("intermediate_equation_ids", "intermediate"),
    ("output_equation_ids", "output"),
    ("constraint_equation_ids", "constraint"),
)


# 旧名（本モジュール内および既存テストからの参照）。公開名 ``strip_confidence``
# （正本は ``learner_context_common``）の別名。
_strip_confidence = strip_confidence


# ---------------------------------------------------------------------------
# component 解決（DB UUID / agent ID の両対応・コース document スコープ制約）
# ---------------------------------------------------------------------------


def _resolve_component_row(
    component_id: str,
    course_document_ids: set[str],
    preferred_document_ids: "tuple[str, ...] | list[str] | set[str] | None" = None,
) -> dict | None:
    """component_id を、コースの document 集合内に限定して1行解決する。

    ``document_id = ANY(course_document_ids)`` を SQL の WHERE 句に直接含めることで
    （後付けの Python フィルタではなく）、agent 側 ID（``comp_001`` 等、論文ごとに
    独立採番されるため文書間で衝突しうる）がコース外文書の同名 component に
    誤って一致する余地を断つ（fail-closed）。WHERE 断片の組み立ては
    ``learner_context_common.scoped_id_match_sql``（claim 側の
    ``element_context._resolve_claim`` と共有する正本）に委ねる。

    論文をまたぐ一致の扱いは焦点論文の規則（FD4 — ``core.focus_document.resolve_in_focus``）:
    ``preferred_document_ids``（焦点の論文・段順）で先に解決し、焦点の外ではコース内で
    **ちょうど1論文**に当たるときだけ解決する（以前はコース全体の ``LIMIT 1`` で
    別論文の同名 component を拾い得た — IK-0554 の残り）。DB UUID の完全一致は一意なので
    即決。同じ論文の中の複数一致は ``ORDER BY (id::text = :raw_id) DESC`` の先頭行。
    """
    document_ids = _normalized_document_ids(course_document_ids)
    if not document_ids:
        return None

    raw_id = str(component_id)

    def _fetch_one(doc_ids: list[str]):
        row = _query_component_row(component_id, doc_ids)
        return (str(row.get("document_id") or ""), row) if row else None

    allowed = set(document_ids)
    if isinstance(preferred_document_ids, (set, frozenset)):
        preferred_values = sorted(preferred_document_ids)
    else:
        preferred_values = list(preferred_document_ids or ())
    focus = [str(d) for d in preferred_values if str(d) in allowed]
    return resolve_in_focus(
        unique_row_lookup(_fetch_one, is_exact=lambda row: str(row.get("id")) == raw_id),
        focus,
        scope_document_ids=document_ids,
    )


def resolve_course_component_id(
    component_id: str,
    course_document_ids: set[str],
    preferred_document_ids: "tuple[str, ...] | list[str] | set[str] | None" = None,
) -> str | None:
    """component_id（DB UUID / agent ID）をコースの document 集合内で DB UUID に解決する。

    C層の学生向け説明一覧（IK-0861）が、agent ID を UUID に CAST せずに
    使えるようにする公開入口。解決規則は ``_resolve_component_row`` と同一。
    """
    row = _resolve_component_row(component_id, course_document_ids, preferred_document_ids)
    if not row or not row.get("id"):
        return None
    return str(row.get("id"))


def component_matches_multiple_documents(
    component_id: str, course_document_ids: set[str]
) -> bool:
    """component_id がコースの document 集合のうち 2 論文以上に当たるか（IK-0892）。

    解決できなかった理由を「不在」と「どの論文か特定できない」に分けるための読み取り。
    SQL の WHERE 断片は解決と同じ ``scoped_id_match_sql``。fail-soft（例外は False）。
    """
    document_ids = _normalized_document_ids(course_document_ids)
    if not document_ids or not str(component_id or "").strip():
        return False
    where_clause, params = scoped_id_match_sql(component_id, document_ids)
    try:
        session = get_session()
        try:
            row = session.execute(
                sa_text(
                    f"""
                    SELECT COUNT(DISTINCT document_id) FROM theory_components_live
                    WHERE document_id = ANY(CAST(:doc_ids AS uuid[])) AND ({where_clause})
                    """
                ),
                params,
            ).fetchone()
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — 理由の書き分けだけ。解決そのものは止めない
        logger.debug("component ambiguity check failed for %s", component_id, exc_info=True)
        return False
    return bool(row) and int(row[0] or 0) >= 2


def _query_component_row(component_id: str, document_ids: list[str]) -> dict | None:
    """``document_ids`` の中で component を1行引く（UUID 完全一致を優先・読み取り専用）。"""
    where_clause, params = scoped_id_match_sql(component_id, document_ids)

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                SELECT id::text AS id, document_id::text AS document_id, course_id, name,
                       component_type, component_type_text, summary, status, review_status,
                       source_scope, thesis_context, parent_agent_component_id
                FROM theory_components_live
                WHERE document_id = ANY(CAST(:doc_ids AS uuid[])) AND ({where_clause})
                ORDER BY (id::text = :raw_id) DESC
                LIMIT 1
                """
            ),
            params,
        ).mappings().fetchone()
    finally:
        session.close()
    return dict(row) if row else None


def _agent_id_candidates(component_row: dict) -> set[str]:
    """component 行から突合キー候補（DB UUID ∪ legacy_ids）を返す。"""
    ids = {str(component_row.get("id") or "").strip()}
    scope = _json_dict(component_row.get("source_scope"))
    for legacy_id in _json_list(scope.get("legacy_ids")):
        key = str(legacy_id or "").strip()
        if key:
            ids.add(key)
    return {i for i in ids if i}


# ---------------------------------------------------------------------------
# artifact 併読（component_assembly / claim_object_builder / equation_semantics）
# ---------------------------------------------------------------------------


def _component_assembly_record(artifacts: dict, agent_ids: set[str]) -> dict:
    assembly = _json_dict(artifacts.get("component_assembly"))
    for comp in _json_list(assembly.get("components")):
        if isinstance(comp, dict) and str(comp.get("component_id") or "") in agent_ids:
            return comp
    return {}


def _components_by_agent_id(artifacts: dict) -> dict[str, dict]:
    """依存先ラベル解決用: agent 側 component_id → ComponentRecord dict。"""
    assembly = _json_dict(artifacts.get("component_assembly"))
    result: dict[str, dict] = {}
    for comp in _json_list(assembly.get("components")):
        if isinstance(comp, dict) and comp.get("component_id"):
            result[str(comp["component_id"])] = comp
    return result


def _claims_index(artifacts: dict) -> dict[str, dict]:
    claim_artifact = _json_dict(artifacts.get("claim_object_builder"))
    result: dict[str, dict] = {}
    for claim in _json_list(claim_artifact.get("claims")):
        if isinstance(claim, dict) and claim.get("claim_id"):
            result[str(claim["claim_id"])] = claim
    return result


def _equations_index(document_id: str) -> dict[str, dict]:
    return {
        str(r.get("equation_id")): r
        for r in equation_records(document_id)
        if isinstance(r, dict) and r.get("equation_id")
    }


def _equation_label(record: dict | None) -> str:
    """式の表示名 — 正本 ``graph_paper_layer.schema.equation_display_label`` に委ねる。

    以前は平文・LaTeX を 80 字で素朴に切っており、学習者に切れた生 TeX と TeX 経路の
    式 ID が出ていた（IK-0862）。印字番号があれば「式 (12)」、無ければ ``$…$`` の
    先頭か定義する記号。equation_id はラベルに使わない。
    """
    if not record:
        return ""
    from core.graph_paper_layer.schema import equation_display_label  # 遅延 import（推移的純粋性の保護）

    return equation_display_label(record)


def _load_graph_narrative(document_id: str) -> dict:
    """当該 document の最新 theory_component_graphs 行から narrative dict を返す。"""
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT graph_json FROM theory_component_graphs
                WHERE document_id = CAST(NULLIF(:doc, '') AS uuid) ORDER BY updated_at DESC LIMIT 1
                """
            ),
            {"doc": document_id},
        ).fetchone()
    finally:
        session.close()
    graph = row[0] if row and isinstance(row[0], dict) else {}
    return _json_dict(graph.get("narrative")) if isinstance(graph, dict) else {}


#: 要約の表示上限（IK-0860: 投影の後・語境界で切る）。
_GRAPH_SUMMARY_MAX_CHARS = 200


def _load_graph_node_labels(document_id: str) -> dict[str, str]:
    """当該 document の最新グラフのノード id → 表示名（main 層は日本語の段名）。"""
    from core.element_vocab import theory_stage_display_label

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT graph_json FROM theory_component_graphs
                WHERE document_id = CAST(NULLIF(:doc, '') AS uuid) ORDER BY updated_at DESC LIMIT 1
                """
            ),
            {"doc": document_id},
        ).fetchone()
    finally:
        session.close()
    graph = row[0] if row and isinstance(row[0], dict) else {}
    labels: dict[str, str] = {}
    for node in (graph.get("nodes") or []) if isinstance(graph, dict) else []:
        if not isinstance(node, dict):
            continue
        nid = str(node.get("component_id") or node.get("id") or "").strip()
        if not nid:
            continue
        raw = str(node.get("display_label") or node.get("label") or "").strip()
        if str(node.get("graph_layer") or "main") == "main":
            raw = theory_stage_display_label(node.get("label") or raw) or raw
        label = raw.split("\n", 1)[0].strip()
        if label and label != nid:
            labels[nid] = label
    return labels


def readable_graph_summary(summary: object, node_labels: dict[str, str]) -> str:
    """グラフ要約のノード id を表示名に写し、語境界で切る（IK-0860・純関数）。

    要約は「まず theory_op_0001 が…」とノード id で文を組む。表示投影は後段で id を
    置き換え語にするだけなので、先に**同じグラフの表示名**へ写す（置き換え語で文を埋めない）。
    切り詰めは写した後に ``text_excerpt.excerpt`` で行い、id の途中で切れた断片
    （``theory_op``）を作らない。写せない id は残す（後段の投影が遮断する）。
    """
    from core.text_excerpt import excerpt

    text = str(summary or "").strip()
    if not text:
        return ""
    for nid in sorted(node_labels, key=len, reverse=True):
        label = node_labels[nid]
        if nid and label:
            text = re.sub(
                r"(?<![A-Za-z0-9_])" + re.escape(nid) + r"(?![A-Za-z0-9_])",
                lambda _m, _label=label: f"「{_label}」",
                text,
            )
    return excerpt(text, _GRAPH_SUMMARY_MAX_CHARS) or ""


def _document_title(document_id: str) -> str:
    session = get_session()
    try:
        row = session.execute(
            sa_text("SELECT title, source_path FROM documents WHERE id = CAST(:id AS uuid) LIMIT 1"),
            {"id": document_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return ""
    return str(row[0] or row[1] or "")


def _user_display_name(user_id: str) -> str:
    if not str(user_id or "").strip():
        return ""
    session = get_session()
    try:
        row = session.execute(
            sa_text("SELECT display_name FROM users WHERE id = CAST(:id AS uuid) LIMIT 1"),
            {"id": user_id},
        ).fetchone()
    finally:
        session.close()
    return str(row[0] or "") if row else ""


# ---------------------------------------------------------------------------
# narrative_role（優先順: グラフ node_narratives → thesis_context.role_in_thesis →
# artifact teaching_takeaway → summary）
# ---------------------------------------------------------------------------


def _narrative_role(
    component_db_id: str, component_row: dict, narrative: dict, assembly_record: dict
) -> str:
    node_narratives = _json_dict(narrative.get("node_narratives"))
    node = node_narratives.get(component_db_id)
    if isinstance(node, dict):
        role = str(node.get("narrative_role") or "").strip()
        if role:
            return role
    thesis_context = _json_dict(component_row.get("thesis_context"))
    role_in_thesis = str(thesis_context.get("role_in_thesis") or "").strip()
    if role_in_thesis:
        return role_in_thesis
    teaching_takeaway = str((assembly_record or {}).get("teaching_takeaway") or "").strip()
    if teaching_takeaway:
        return teaching_takeaway
    return str(component_row.get("summary") or "").strip()


# ---------------------------------------------------------------------------
# supports（preconditions/inputs/outputs/cautions/equations/claims/dependencies）
# ---------------------------------------------------------------------------


def _build_field_refs(record: dict, key: str) -> list[dict]:
    items: list[dict] = []
    for ref in _json_list(record.get(key)):
        if not isinstance(ref, dict):
            continue
        text = str(ref.get("text") or "").strip()
        if not text or contains_pipeline_policy_token(text):
            continue
        claim_ids = [str(c) for c in _json_list(ref.get("claim_ids")) if str(c or "").strip()]
        equation_ids = [str(e) for e in _json_list(ref.get("equation_ids")) if str(e or "").strip()]
        items.append({"text": text, "refs": {"claim_ids": claim_ids, "equation_ids": equation_ids}})
        if len(items) >= _MAX_FIELD_REF_ITEMS:
            break
    return items


def _build_cautions(record: dict) -> list[dict]:
    items: list[dict] = []
    for ref in _json_list(record.get("cautions")):
        text = ""
        if isinstance(ref, dict):
            text = str(ref.get("text") or "").strip()
        elif isinstance(ref, str):
            text = ref.strip()
        if not text or contains_pipeline_policy_token(text):
            # 解析層の方針語（semantic_hint_only 等）を含む注意は学習者に意味をなさない（IK-0862）。
            continue
        items.append({"text": text})
        if len(items) >= _MAX_FIELD_REF_ITEMS:
            break
    return items


def _equation_role_map(record: dict) -> dict[str, str]:
    role_map: dict[str, str] = {}
    for field_name, role in _EQUATION_ROLE_FIELDS:
        for eq_id in _json_list(record.get(field_name)):
            key = str(eq_id or "").strip()
            if key and key not in role_map:
                role_map[key] = role
    for eq_id in _json_list(record.get("linked_equation_ids")):
        key = str(eq_id or "").strip()
        if key and key not in role_map:
            role_map[key] = "linked"
    return role_map


def _build_equations(record: dict, equations_by_id: dict[str, dict]) -> list[dict]:
    items: list[dict] = []
    for eq_id, role in _equation_role_map(record).items():
        eq_record = equations_by_id.get(eq_id)
        # 記録が引けない式は内部 ID を出さず一般ラベルにする（IK-0862）。
        label = _equation_label(eq_record) if eq_record else learner_context_common.generic_item_label("equation")
        item = {"id": eq_id, "label": label, "role": role}
        # IK-0894: 教材の式に解決できない式も数式として描けるよう、式本文を同梱する。
        if eq_record:
            from core.graph_paper_layer.schema import equation_display_latex  # 遅延 import

            latex = equation_display_latex(eq_record)
            if latex:
                item["latex"] = latex
        # 復元由来の式には事実文を1行添える（`_equation_label` は reconstruction を
        # 優先して本文にするため、ここを黙って抽出結果と同じ顔で出さない）。
        note = _reconstructed_equation_note(eq_record) if eq_record else None
        if note:
            item["label_note"] = note
        items.append(item)
    return items


def _build_claims(record: dict, claims_by_id: dict[str, dict]) -> list[dict]:
    items: list[dict] = []
    for claim_id in _json_list(record.get("linked_claim_ids")):
        key = str(claim_id or "").strip()
        if not key:
            continue
        claim = claims_by_id.get(key)
        if not claim:
            # excerpt が引けない裸 ID は表示しない(設計書: 「裸ID禁止」)。
            continue
        excerpt = str(claim.get("text") or claim.get("normalized_text") or "").strip()
        if not excerpt:
            continue
        items.append({"id": key, "excerpt": excerpt})
    return items


def _build_dependencies(record: dict, components_by_agent_id: dict[str, dict]) -> list[dict]:
    items: list[dict] = []
    for dep in _json_list(record.get("dependencies")):
        if not isinstance(dep, dict):
            continue
        dep_type = str(dep.get("dependency_type") or "").strip()
        reason = str(dep.get("reason") or "").strip()
        for ref in _json_list(dep.get("component_refs")):
            ref_key = str(ref or "").strip()
            if not ref_key:
                continue
            target = components_by_agent_id.get(ref_key)
            target_label = str((target or {}).get("label") or ref_key)
            items.append({"type": dep_type, "target_label": target_label, "reason": reason})
    return items


def _build_supports(
    assembly_record: dict,
    claims_by_id: dict[str, dict],
    equations_by_id: dict[str, dict],
    components_by_agent_id: dict[str, dict],
) -> dict:
    return {
        "preconditions": _build_field_refs(assembly_record, "preconditions"),
        "inputs": _build_field_refs(assembly_record, "inputs"),
        "outputs": _build_field_refs(assembly_record, "outputs"),
        "cautions": _build_cautions(assembly_record),
        "equations": _build_equations(assembly_record, equations_by_id),
        "claims": _build_claims(assembly_record, claims_by_id),
        "dependencies": _build_dependencies(assembly_record, components_by_agent_id),
    }


# ---------------------------------------------------------------------------
# shared_part（confirmed 同一性リンク → active な L層エントリ、揃わなければ None）
# ---------------------------------------------------------------------------


def _confirmed_identity_link(component_db_id: str, document_id: str, agent_ids: set[str]) -> dict | None:
    candidates = [component_db_id] + sorted(agent_ids - {component_db_id})
    for candidate_id in candidates:
        try:
            links = identity_links_mod.list_for_instance(ELEMENT_THEORY_COMPONENT, candidate_id, document_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "component_context: identity link lookup failed for %s", candidate_id, exc_info=True
            )
            continue
        for link in links:
            if str(link.get("status") or "") == IDENTITY_LINK_STATUS_CONFIRMED:
                return link
    return None


#: 「共通部品として」が出ないときの事実文（IK-0863。固定文の正本 — JS にミラーしない）。
SHARED_PART_ABSENT_NOTE = (
    "この部品は、ほかの論文の部品と同じものとして教員が確かめた記録が、このコーパスの中にはまだありません。"
)


#: 「共通部品として」面の見出し（IK-0917。同一性リンクの意味 = 教員が「同じもの」として
#: 確かめた、を学生に言う。固定文の正本 — JS にミラーしない）。
SHARED_PART_HEADING = "教員がこの部品と同じものとして確かめた共通部品"

#: 他の論文の題名の列挙の前置き（IK-0920。件数ではなく題名で言う — 数値非表示）。
SHARED_PART_OTHER_DOCUMENTS_LEAD = "あなたが読めるほかの論文で、教員がこの共通部品と同じものとして確かめた部品があるもの:"

#: 読める他の論文に確定リンクが無いときの事実文（IK-0920。閉世界・読めない論文の有無は言わない）。
SHARED_PART_NO_OTHER_DOCUMENTS_NOTE = (
    "あなたが読めるほかの論文には、この共通部品と同じものとして教員が確かめた部品の記録がまだありません。"
)

#: 題名を列挙する上限（それ以上は「ほか」）。
SHARED_PART_OTHER_DOCUMENTS_MAX = 6


def _document_titles(document_ids: list[str]) -> dict[str, str]:
    """``{document_id: 題名}``（1 クエリ。引けない id はキーを持たない）。"""
    keys = [str(d) for d in document_ids if str(d or "").strip()]
    if not keys:
        return {}
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                "SELECT id::text, title, source_path FROM documents "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": keys},
        ).fetchall()
    finally:
        session.close()
    return {str(r[0]): str(r[1] or r[2] or "") for r in rows if str(r[1] or r[2] or "").strip()}


def _other_confirmed_documents(
    shared_part_id: str, document_id: str, readable_document_ids: set[str] | None
) -> tuple[list[dict], bool]:
    """確定リンク（``shared_part_id`` 一致・confirmed）から、読める他の論文の題名を導く（IK-0920）。

    ``readable_document_ids`` は呼び出し側が渡す学生の読める論文の集合（コースの sources ∪
    本人の可視集合）。``None`` / 空は fail-closed で何も列挙しない（DB も引かない）。
    件数・読めない論文の有無は返さない（数値非表示・存在を明かさない）。
    """
    readable = {str(d) for d in (readable_document_ids or set()) if str(d or "").strip()}
    readable.discard(str(document_id))
    if not readable:
        return [], False
    links = identity_links_mod.list_for_shared_part(shared_part_id)
    ordered: list[str] = []
    for link in links:
        if str(link.get("status") or "") != IDENTITY_LINK_STATUS_CONFIRMED:
            continue
        doc = str(link.get("instance_document_id") or "")
        if doc and doc in readable and doc not in ordered:
            ordered.append(doc)
    if not ordered:
        return [], False
    titles = _document_titles(ordered)
    items = [{"title": titles[d]} for d in ordered if titles.get(d)]
    truncated = len(items) > SHARED_PART_OTHER_DOCUMENTS_MAX
    return items[:SHARED_PART_OTHER_DOCUMENTS_MAX], truncated


def _build_shared_part(
    component_db_id: str,
    document_id: str,
    agent_ids: set[str],
    *,
    readable_document_ids: set[str] | None = None,
) -> dict | None:
    try:
        link = _confirmed_identity_link(component_db_id, document_id, agent_ids)
        if not link:
            return None
        shared_part_id = str(link.get("shared_part_id") or "").strip()
        if not shared_part_id:
            return None
        entry = library_store_mod.get_entry(shared_part_id)
        if not entry or str(entry.get("status") or "") != library_schema.STATUS_ACTIVE:
            return None
        try:
            other_documents, other_truncated = _other_confirmed_documents(
                shared_part_id, document_id, readable_document_ids
            )
        except Exception:  # noqa: BLE001 - 列挙できないだけ（共通部品の面は出す）
            logger.warning(
                "component_context: other documents for shared part %s failed", shared_part_id, exc_info=True
            )
            other_documents, other_truncated = [], False
        decided_by = str(link.get("decided_by") or "").strip()
        entry_dto: dict = {
            "name": str(entry.get("name") or ""),
            "aliases": list(entry.get("aliases") or []),
            "summary": str(entry.get("summary") or ""),
            "standardization_status": str(
                entry.get("standardization_status") or library_schema.STANDARDIZATION_STATUS_UNKNOWN
            ),
            # IK-0920: 他の論文は確定リンクから導いた**題名**で言う（件数をやめる）。
            "other_documents": other_documents,
            "other_documents_truncated": other_truncated,
        }
        if other_documents:
            entry_dto["other_documents_lead"] = SHARED_PART_OTHER_DOCUMENTS_LEAD
        else:
            entry_dto["other_documents_note"] = SHARED_PART_NO_OTHER_DOCUMENTS_NOTE
        return {
            "heading": SHARED_PART_HEADING,
            "entry": entry_dto,
            "link": {
                "confirmed_by": _user_display_name(decided_by),
                "evidence": link.get("evidence") or [],
            },
            "provenance": "identity_link_confirmed + library_entry",
        }
    except Exception:  # noqa: BLE001
        logger.warning(
            "component_context: shared_part build failed for %s", component_db_id, exc_info=True
        )
        return None


# ---------------------------------------------------------------------------
# graph（W層 context lens の1-hop近傍、candidate 除外）
# ---------------------------------------------------------------------------


def _project_context_item(item: dict) -> dict | None:
    """W層 ITEM を graph レーン用に射影する（キー集合は従来の6キーのまま）。

    射影の本体は claim / equation 文脈 API と共有する
    ``learner_context_common.project_item`` で、``legacy_keys_only=True`` により
    ITEM v2 の追加キー（``sublabel`` / ``qualifier`` / ``group`` / ``unresolved``）は
    足さない — component の DTO に ``group`` を混ぜると統一パーツカード
    （``element-card.js`` の ``hasGroupedItems``）が4区画描画へ切り替わり、可視の
    UX 変更になってしまうため（キー集合の世代差は意図的に維持する）。

    共有によって従来この経路に無かった2つの遮断が効く:

    - 内部 ID / 生 TeX の label を element_type 別の一般ラベルへ置換する
      （W層は ``comp_003`` のような生 ID をラベルに入れ得る）。
    - ``navigable`` を学習者が実際に開ける型（claim / equation / component）だけに
      絞り直す（W層の ``navigable`` は教員向けの可否で、figure / evidence /
      derivation でも真になり得た）。

    加えて component レーンでは ``include_agent_id_tokens=True`` により
    ``comp_003`` / ``theory_op_0001`` 型の agent 側 ID ラベルも遮断する
    （claim / equation 経路は従来判定のまま — 拡張はオーナー判断待ち）。

    レーン単位の組み立て（candidate 除外・上限）は ``_visible_lane`` が行い、
    ``visible_lane_items(project=...)`` 経由で**本番経路がこの関数を通る**
    （seam をテスト専用にしない）。
    """
    return project_item(item, legacy_keys_only=True, include_agent_id_tokens=True)


def _visible_lane(items: Any) -> list[dict]:
    """1レーン分の ITEM を candidate 除外（LE2）+ 射影 + 上限（``_GRAPH_LANE_MAX``）で組む。

    実体は claim / equation 文脈 API と共有する
    ``learner_context_common.visible_lane_items``。1件の射影は
    ``_project_context_item`` を注入して行う（旧6キー + agent ID 遮断）。
    """
    return learner_context_common.visible_lane_items(items, project=_project_context_item)


#: 依存先 component の関係語（W層 ``context_lens`` の ``requires`` と同じ語）。
_DEPENDENCY_RELATION_LABEL = "を前提とする"
_DEPENDENCY_RELATION_STATUS = "source_backed"


def _dependency_agent_refs(assembly_record: dict) -> list[str]:
    refs: list[str] = []
    for dep in _json_list(assembly_record.get("dependencies")):
        if not isinstance(dep, dict):
            continue
        for ref in _json_list(dep.get("component_refs")):
            key = str(ref or "").strip()
            if key and key not in refs:
                refs.append(key)
    return refs


def _resolve_live_components(document_id: str, agent_refs: list[str]) -> dict[str, dict]:
    """agent 側 component ID → 同じ論文の live component 行（``{id, name}``）。

    ``document_id`` を WHERE に直接含める（agent ID は論文ごとに独立採番され衝突する）。
    1つの ref が複数行に当たるときは解決しない（どれを指すか決められない — fail-closed）。
    """
    if not document_id or not agent_refs:
        return {}
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT id::text AS id, name, source_scope
                FROM theory_components_live
                WHERE document_id = CAST(:document_id AS uuid)
                  AND source_scope->'legacy_ids' ?| CAST(:refs AS text[])
                """
            ),
            {"document_id": document_id, "refs": list(agent_refs)},
        ).mappings().fetchall()
    finally:
        session.close()
    hits: dict[str, list[dict]] = {}
    wanted = set(agent_refs)
    for row in rows or []:
        row = dict(row)
        for legacy_id in _json_list(_json_dict(row.get("source_scope")).get("legacy_ids")):
            key = str(legacy_id or "").strip()
            if key in wanted:
                hits.setdefault(key, []).append(row)
    return {k: v[0] for k, v in hits.items() if len({r["id"] for r in v}) == 1}


def _dependency_lane_items(
    component_db_id: str, document_id: str, assembly_record: dict
) -> tuple[list[dict], bool]:
    """第 15 周: 依存先 component を同じ論文の live 行へ解決し、辿れる項目にする。

    W層の依存項目はグラフのノードに無い component を ``id=None``（「関連する論理要素」）
    で出すため、学習者の 1 hop が成立しなかった。A層の ``dependencies`` の
    agent ID を当該論文の live component に解決できたものだけを navigable で足す。
    """
    refs = _dependency_agent_refs(assembly_record)
    if not refs:
        return [], False
    try:
        resolved = _resolve_live_components(document_id, refs)
    except Exception:  # noqa: BLE001 - fail-soft（W層の項目のまま）
        logger.warning("component_context: dependency resolve failed for %s", document_id, exc_info=True)
        return [], False
    items: list[dict] = []
    for ref in refs:
        row = resolved.get(ref)
        if not row or str(row.get("id")) == component_db_id:
            continue
        entry = _project_context_item(
            {
                "element_type": ELEMENT_THEORY_COMPONENT,
                "element_id": str(row["id"]),
                "label": str(row.get("name") or ""),
                "relation_label": _DEPENDENCY_RELATION_LABEL,
                "relation_status": _DEPENDENCY_RELATION_STATUS,
            }
        )
        if entry is not None:
            items.append(entry)
    return items, all(ref in resolved for ref in refs)


#: 分割の親子・依存の逆向きの関係語（W層 ``context_lens.RELATION_LABELS`` の既存語。
#: 主語は常に focus — 上位の親は「（focus は）親 の構成要素である」、下位の子は
#: 「子 を構成要素として含む」、上位の利用元は「利用元 に利用される」と読む）。
_PART_OF_RELATION_LABEL = context_lens_mod.RELATION_LABELS["member_of"]
_CONTAINS_RELATION_LABEL = context_lens_mod.RELATION_LABELS["contains"]
_USED_BY_RELATION_LABEL = context_lens_mod.RELATION_LABELS["used_by_component"]

#: 中心命題・支持構造（``element_type="thesis"``）は学習者の文脈 API では開けない。
#: 次の一歩は discuss の開幕画面なので、そのレーンに1回だけこの事実文を添える（IK-0570）。
THESIS_EXIT_FACT = "議論の開幕画面で中心命題と支持構造を読めます"
_ELEMENT_THESIS = "thesis"


def _split_parent_label(artifacts: dict, parent_ref: str) -> str:
    """分割の親（LLM 原案）の表示名（``refinement_report.split_actions.parent_label``）。"""
    assembly = _json_dict(artifacts.get("component_assembly"))
    report = _json_dict(assembly.get("refinement_report"))
    for action in _json_list(report.get("split_actions")):
        if isinstance(action, dict) and str(action.get("parent_component_id") or "") == parent_ref:
            return str(action.get("parent_label") or "").strip()
    return ""


def _reverse_dependency_refs(artifacts: dict, agent_ids: set[str]) -> list[str]:
    """focus を ``dependencies.component_refs`` に挙げる component の agent ID（論文順）。"""
    refs: list[str] = []
    assembly = _json_dict(artifacts.get("component_assembly"))
    for comp in _json_list(assembly.get("components")):
        if not isinstance(comp, dict):
            continue
        comp_id = str(comp.get("component_id") or "").strip()
        if not comp_id or comp_id in agent_ids or comp_id in refs:
            continue
        if set(_dependency_agent_refs(comp)) & agent_ids:
            refs.append(comp_id)
    return refs


def _resolve_child_components(document_id: str, agent_ids: set[str]) -> list[dict]:
    """``parent_agent_component_id`` が focus の agent ID である同じ論文の live 子 component。"""
    if not document_id or not agent_ids:
        return []
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT id::text AS id, name
                FROM theory_components_live
                WHERE document_id = CAST(:document_id AS uuid)
                  AND parent_agent_component_id = ANY(CAST(:refs AS text[]))
                ORDER BY name, id
                """
            ),
            {"document_id": document_id, "refs": sorted(agent_ids)},
        ).mappings().fetchall()
    finally:
        session.close()
    return [dict(r) for r in rows or []]


def _component_lane_entry(row_id: str, label: str, relation_label: str) -> dict | None:
    return _project_context_item(
        {
            "element_type": ELEMENT_THEORY_COMPONENT,
            "element_id": row_id,
            "label": label,
            "relation_label": relation_label,
            "relation_status": _DEPENDENCY_RELATION_STATUS,
        }
    )


def _hierarchy_lane_items(
    component_db_id: str,
    document_id: str,
    component_row: dict,
    agent_ids: set[str],
    artifacts: dict,
) -> tuple[list[dict], list[dict]]:
    """分割の親子と依存の逆向きを、同じ論文の live 行へ解決して辿れる項目にする（IK-0588）。

    下へ辿る参照（依存先・子）だけでは、移った先から元へ戻れない。上位には
    ①分割の親（``parent_agent_component_id`` → 同じ論文の live 行。親が行として残って
    いない分割では原案の表示名だけを id なしで出す — 捏造しない）②focus に依存する
    component（「に利用される」）を、下位には③分割の子（「を構成要素として含む」）を足す。
    どれも fail-soft（解決できなければ足さない）。
    """
    upper: list[dict] = []
    lower: list[dict] = []
    parent_ref = str(component_row.get("parent_agent_component_id") or "").strip()
    try:
        wanted = ([parent_ref] if parent_ref else []) + _reverse_dependency_refs(artifacts, agent_ids)
        resolved = _resolve_live_components(document_id, wanted) if wanted else {}
    except Exception:  # noqa: BLE001 - fail-soft
        logger.warning("component_context: hierarchy resolve failed for %s", document_id, exc_info=True)
        resolved = {}
    if parent_ref:
        row = resolved.get(parent_ref)
        if row and str(row.get("id")) != component_db_id:
            entry = _component_lane_entry(str(row["id"]), str(row.get("name") or ""), _PART_OF_RELATION_LABEL)
        else:
            parent_label = _split_parent_label(artifacts, parent_ref)
            entry = (
                _component_lane_entry("", parent_label, _PART_OF_RELATION_LABEL)
                if parent_label
                else None
            )
            if entry is not None:
                entry.pop("id", None)
        if entry is not None:
            upper.append(entry)
    for ref in _reverse_dependency_refs(artifacts, agent_ids):
        row = resolved.get(ref)
        if not row or str(row.get("id")) == component_db_id:
            continue
        entry = _component_lane_entry(str(row["id"]), str(row.get("name") or ""), _USED_BY_RELATION_LABEL)
        if entry is not None:
            upper.append(entry)
    try:
        children = _resolve_child_components(document_id, agent_ids)
    except Exception:  # noqa: BLE001 - fail-soft
        logger.warning("component_context: child resolve failed for %s", document_id, exc_info=True)
        children = []
    for child in children:
        if str(child.get("id")) == component_db_id:
            continue
        entry = _component_lane_entry(str(child["id"]), str(child.get("name") or ""), _CONTAINS_RELATION_LABEL)
        if entry is not None:
            lower.append(entry)
    return upper, lower


def _finalize_lane(lane: list[dict]) -> tuple[list[dict], bool]:
    """中心命題・支持構造の項目から宛先の無い ``id`` を外す（``id=None`` を描かせない）。"""
    out: list[dict] = []
    has_thesis = False
    for entry in lane:
        if entry.get("element_type") == _ELEMENT_THESIS:
            has_thesis = True
            entry = dict(entry)
            if not entry.get("id"):
                entry.pop("id", None)
            entry["navigable"] = False
        out.append(entry)
    return out, has_thesis


def _merge_lane(
    lane: list[dict], extra: list[dict], *, drop_unresolved_components: bool = False
) -> list[dict]:
    """辿れる依存項目を足し、重複（同じ種別・ID・ラベル・関係語）を畳む。

    ``drop_unresolved_components``: 依存先をすべて live 行へ解決できたときだけ、
    W層が出した id なしの component 項目（「関連する論理要素」= 同じ依存の未解決版）を
    外す。一部しか解けないときは残す（情報を落とさない）。
    """
    resolved_ids = {str(e.get("id")) for e in extra if e.get("id")}
    merged: list[dict] = []
    seen: set[tuple] = set()
    for entry in list(extra) + list(lane):
        is_component = entry.get("element_type") == ELEMENT_THEORY_COMPONENT
        if (
            is_component
            and drop_unresolved_components
            and not entry.get("navigable")
            and not entry.get("id")
        ):
            # 解決済みの依存項目で置き換えられた「関連する論理要素」（id なし）。
            continue
        if entry.get("id") and str(entry.get("id")) in resolved_ids and entry not in extra:
            continue
        key = (
            entry.get("element_type"),
            str(entry.get("id") or ""),
            str(entry.get("label") or ""),
            str(entry.get("relation_label") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(entry)
        if len(merged) >= learner_context_common.LANE_MAX:
            break
    return merged


def _build_graph(
    component_db_id: str,
    document_id: str,
    assembly_record: dict | None = None,
    *,
    component_row: dict | None = None,
    agent_ids: set[str] | None = None,
    artifacts: dict | None = None,
) -> dict | None:
    try:
        ref = ElementRef(
            scope=SCOPE_DOCUMENT,
            element_type=ELEMENT_THEORY_COMPONENT,
            element_id=component_db_id,
            document_id=document_id,
        )
        result = context_lens_mod.build(ref)
    except Exception:  # noqa: BLE001
        logger.warning(
            "component_context: context_lens build failed for %s", component_db_id, exc_info=True
        )
        return None
    if not result:
        return None
    focus = _json_dict(result.get("focus"))
    extra, all_resolved = _dependency_lane_items(
        component_db_id, document_id, assembly_record or {}
    )
    upper_extra: list[dict] = []
    lower_extra: list[dict] = []
    if component_row is not None:
        upper_extra, lower_extra = _hierarchy_lane_items(
            component_db_id, document_id, component_row, set(agent_ids or ()), artifacts or {}
        )
    upper, upper_thesis = _finalize_lane(
        _merge_lane(_visible_lane(result.get("upper")), upper_extra)
    )
    lower, lower_thesis = _finalize_lane(
        _merge_lane(
            _visible_lane(result.get("lower")),
            lower_extra + extra,
            drop_unresolved_components=all_resolved,
        )
    )
    graph = {
        "focus": {"id": component_db_id, "label": str(focus.get("label") or "")},
        "upper": upper,
        "lower": lower,
    }
    exits = {
        lane: THESIS_EXIT_FACT
        for lane, has_thesis in (("upper", upper_thesis), ("lower", lower_thesis))
        if has_thesis
    }
    if exits:
        graph["exits"] = exits
    return graph


# ---------------------------------------------------------------------------
# 公開インターフェース
# ---------------------------------------------------------------------------


def build_component_context(
    component_id: str,
    course_id: str,
    course_document_ids: set[str],
    *,
    preferred_document_ids: "tuple[str, ...] | list[str] | set[str] | None" = None,
    readable_document_ids: "set[str] | None" = None,
) -> dict | None:
    """コーススコープ component 文脈 DTO を組み立てる。

    ``readable_document_ids`` は学生が読める論文の集合（コースの sources ∪ 本人の可視集合。
    呼び出し側が組む）。「共通部品として」の他の論文の題名はこの集合の中からだけ列挙する
    （IK-0920。``None`` は ``course_document_ids`` に縮退 = fail-closed）。

    ``course_id`` は現状 DB クエリには使わない（コースの受講ゲート自体は
    呼び出し側 ``routes/learning.py`` が ``get_accessible_course_data`` で
    既に検証済み。ここでの ``course_document_ids`` フィルタが唯一の scope
    制約）が、将来のコース単位キャッシュ・監査用に引数として保持する。

    component が ``course_document_ids`` 内で解決できなければ ``None``
    （呼び出し側は 404 にマッピングする）。戻り値には ``instance.explanation``
    を含まない — C層の承認済み説明の取得・マージは呼び出し側
    （``routes/learning.py``）の責務とする（core は routes を import しない）。
    """
    component_row = _resolve_component_row(
        component_id, course_document_ids, preferred_document_ids
    )
    if component_row is None:
        return None

    component_db_id = str(component_row.get("id"))
    document_id = str(component_row.get("document_id") or "")
    agent_ids = _agent_id_candidates(component_row)

    try:
        artifacts = document_run_artifacts(document_id)
    except Exception:  # noqa: BLE001
        logger.warning("component_context: artifact read failed for document %s", document_id, exc_info=True)
        artifacts = {}

    assembly_record = _component_assembly_record(artifacts, agent_ids)
    claims_by_id = _claims_index(artifacts)
    components_by_agent_id = _components_by_agent_id(artifacts)
    try:
        equations_by_id = _equations_index(document_id)
    except Exception:  # noqa: BLE001
        logger.warning("component_context: equation index failed for document %s", document_id, exc_info=True)
        equations_by_id = {}

    try:
        narrative = _load_graph_narrative(document_id)
    except Exception:  # noqa: BLE001
        logger.warning("component_context: narrative load failed for document %s", document_id, exc_info=True)
        narrative = {}
    narrative_role = _narrative_role(component_db_id, component_row, narrative, assembly_record)
    # IK-0860: 切り詰めは投影（id → 表示名）の後・語境界で（id の途中で切らない）。
    try:
        node_labels = _load_graph_node_labels(document_id) if narrative.get("graph_summary") else {}
    except Exception:  # noqa: BLE001
        logger.debug("component_context: node labels unavailable for %s", document_id, exc_info=True)
        node_labels = {}
    graph_summary_excerpt = readable_graph_summary(narrative.get("graph_summary"), node_labels)

    try:
        doc_title = _document_title(document_id)
    except Exception:  # noqa: BLE001
        logger.warning("component_context: document title load failed for %s", document_id, exc_info=True)
        doc_title = ""

    scope = _json_dict(component_row.get("source_scope"))
    section = str(scope.get("section") or scope.get("section_title") or "")

    label = str(component_row.get("name") or assembly_record.get("label") or "component")
    summary = str(component_row.get("summary") or assembly_record.get("summary") or "")
    component_type = str(
        component_row.get("component_type_text") or component_row.get("component_type") or ""
    )
    teaching_takeaway = str(assembly_record.get("teaching_takeaway") or "")

    instance = {
        "component": {
            "label": label,
            "summary": summary,
            "component_type": component_type,
            "teaching_takeaway": teaching_takeaway,
        },
        "in_paper": {
            "document": {"id": document_id, "title": doc_title, "section": section},
            "narrative_role": narrative_role,
            "graph_summary_excerpt": graph_summary_excerpt,
        },
        "supports": _build_supports(assembly_record, claims_by_id, equations_by_id, components_by_agent_id),
        # 出所ラベルの位置は DTO 契約（component は instance の中、claim / equation は
        # トップレベル）。値だけを共有定数に寄せ、位置は動かさない。
        "provenance": PROVENANCE_COURSE_FREEZE,
    }

    shared_part = _build_shared_part(
        component_db_id,
        document_id,
        agent_ids,
        readable_document_ids=(
            set(readable_document_ids) if readable_document_ids is not None else set(course_document_ids)
        ),
    )
    result = {
        "component_id": component_db_id,
        "instance": instance,
        "shared_part": shared_part,
        "graph": _build_graph(
            component_db_id,
            document_id,
            assembly_record,
            component_row=component_row,
            agent_ids=agent_ids,
            artifacts=artifacts,
        ),
    }
    if shared_part is None:
        # 「共通部品として」が出ない理由を閉世界の一文で（IK-0863）。候補の有無・却下の
        # 区別は学生に出さない（候補は教員の判断前なので）。
        result["shared_part_note"] = SHARED_PART_ABSENT_NOTE
    return _strip_confidence(result)
