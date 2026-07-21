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
- 本モジュールは FastAPI を import しない（開発ルール2 / core/ 共通ルール）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session
from core.deliberation import context_lens as context_lens_mod
from core.deliberation import identity_links as identity_links_mod
from core.deliberation.refs import document_run_artifacts, equation_records
from core.deliberation.schema import (
    CONTEXT_STATUS_CANDIDATE,
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

# W層 context lens レーンの表示上限（context_lens.py の _CONTEXT_LANE_MAX と同じ値。
# ここでは candidate 除外後の件数に対して適用する）。
_GRAPH_LANE_MAX = 20

# equation の役割語彙（*_equation_ids フィールド名 → role）。優先順はこの順序
# （同じ equation_id が複数フィールドに現れた場合は最初に一致した role を採用する）。
_EQUATION_ROLE_FIELDS = (
    ("definition_equation_ids", "definition"),
    ("input_equation_ids", "input"),
    ("intermediate_equation_ids", "intermediate"),
    ("output_equation_ids", "output"),
    ("constraint_equation_ids", "constraint"),
)


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _strip_confidence(value: Any) -> Any:
    """レスポンスを再帰走査して ``"confidence"`` キーを除去する（数値非公開・W8 相当）。"""
    if isinstance(value, dict):
        return {k: _strip_confidence(v) for k, v in value.items() if k != "confidence"}
    if isinstance(value, list):
        return [_strip_confidence(v) for v in value]
    return value


def _json_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _json_list(value: Any) -> list:
    return value if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# component 解決（DB UUID / agent ID の両対応・コース document スコープ制約）
# ---------------------------------------------------------------------------


def _resolve_component_row(component_id: str, course_document_ids: set[str]) -> dict | None:
    """component_id を、コースの document 集合内に限定して1行解決する。

    ``document_id = ANY(course_document_ids)`` を SQL の WHERE 句に直接含めることで
    （後付けの Python フィルタではなく）、agent 側 ID（``comp_001`` 等、論文ごとに
    独立採番されるため文書間で衝突しうる）がコース外文書の同名 component に
    誤って一致する余地を断つ（fail-closed）。
    """
    document_ids = sorted({str(d) for d in (course_document_ids or set()) if str(d or "").strip()})
    if not document_ids:
        return None

    conditions = ["source_scope->'legacy_ids' ? :raw_id"]
    params: dict[str, Any] = {"raw_id": str(component_id), "doc_ids": document_ids}
    if _is_uuid(component_id):
        conditions.append("id = CAST(:uuid_id AS uuid)")
        params["uuid_id"] = str(component_id)
    where_clause = " OR ".join(conditions)

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                SELECT id::text AS id, document_id, course_id, name, component_type,
                       component_type_text, summary, status, review_status,
                       source_scope, thesis_context
                FROM theory_components
                WHERE document_id = ANY(:doc_ids) AND ({where_clause})
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
    if not record:
        return ""
    src = _json_dict(record.get("source_extraction"))
    rec = _json_dict(record.get("reconstruction"))
    text = (
        rec.get("plain_text") or rec.get("latex")
        or src.get("plain_text") or src.get("latex")
        or record.get("label") or record.get("equation_id") or ""
    )
    return str(text)[:80]


def _load_graph_narrative(document_id: str) -> dict:
    """当該 document の最新 theory_component_graphs 行から narrative dict を返す。"""
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT graph_json FROM theory_component_graphs
                WHERE document_id = :doc ORDER BY updated_at DESC LIMIT 1
                """
            ),
            {"doc": document_id},
        ).fetchone()
    finally:
        session.close()
    graph = row[0] if row and isinstance(row[0], dict) else {}
    return _json_dict(graph.get("narrative")) if isinstance(graph, dict) else {}


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
        if not text:
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
        if not text:
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
        label = _equation_label(eq_record) if eq_record else eq_id
        items.append({"id": eq_id, "label": label, "role": role})
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


def _build_shared_part(component_db_id: str, document_id: str, agent_ids: set[str]) -> dict | None:
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
        source_document_ids = {
            str(d) for d in _json_list(entry.get("source_document_ids")) if str(d or "").strip()
        }
        other_documents_count = len(source_document_ids - {str(document_id)})
        decided_by = str(link.get("decided_by") or "").strip()
        return {
            "entry": {
                "name": str(entry.get("name") or ""),
                "aliases": list(entry.get("aliases") or []),
                "summary": str(entry.get("summary") or ""),
                "standardization_status": str(
                    entry.get("standardization_status") or library_schema.STANDARDIZATION_STATUS_UNKNOWN
                ),
                "other_documents_count": other_documents_count,
            },
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


def _project_context_item(item: dict) -> dict:
    return {
        "id": item.get("element_id"),
        "element_type": item.get("element_type"),
        "label": item.get("label"),
        "relation_label": item.get("relation_label"),
        "relation_status": item.get("relation_status"),
        "navigable": bool(item.get("navigable")),
    }


def _build_graph(component_db_id: str, document_id: str) -> dict | None:
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
    upper = [
        _project_context_item(item)
        for item in _json_list(result.get("upper"))
        if isinstance(item, dict) and item.get("relation_status") != CONTEXT_STATUS_CANDIDATE
    ]
    lower = [
        _project_context_item(item)
        for item in _json_list(result.get("lower"))
        if isinstance(item, dict) and item.get("relation_status") != CONTEXT_STATUS_CANDIDATE
    ]
    return {
        "focus": {"id": component_db_id, "label": str(focus.get("label") or "")},
        "upper": upper[:_GRAPH_LANE_MAX],
        "lower": lower[:_GRAPH_LANE_MAX],
    }


# ---------------------------------------------------------------------------
# 公開インターフェース
# ---------------------------------------------------------------------------


def build_component_context(
    component_id: str, course_id: str, course_document_ids: set[str]
) -> dict | None:
    """コーススコープ component 文脈 DTO を組み立てる。

    ``course_id`` は現状 DB クエリには使わない（コースの受講ゲート自体は
    呼び出し側 ``routes/learning.py`` が ``get_accessible_course_data`` で
    既に検証済み。ここでの ``course_document_ids`` フィルタが唯一の scope
    制約）が、将来のコース単位キャッシュ・監査用に引数として保持する。

    component が ``course_document_ids`` 内で解決できなければ ``None``
    （呼び出し側は 404 にマッピングする）。戻り値には ``instance.explanation``
    を含まない — C層の承認済み説明の取得・マージは呼び出し側
    （``routes/learning.py``）の責務とする（core は routes を import しない）。
    """
    component_row = _resolve_component_row(component_id, course_document_ids)
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
    graph_summary_excerpt = str(narrative.get("graph_summary") or "").strip()[:200]

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
        "provenance": "course_freeze",
    }

    result = {
        "component_id": component_db_id,
        "instance": instance,
        "shared_part": _build_shared_part(component_db_id, document_id, agent_ids),
        "graph": _build_graph(component_db_id, document_id),
    }
    return _strip_confidence(result)
