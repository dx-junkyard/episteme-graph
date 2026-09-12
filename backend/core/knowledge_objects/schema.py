"""知識オブジェクト層の語彙・表名の正本（knowledge_objects_design.md §4 / §7）。

型語彙そのもの（CLAIM_TYPES / COMPONENT_TYPES / CLAIM_TIERS / CLAIM_ORIGINS /
KNOWLEDGE_OBJECT_KINDS）は ``core/schema.py`` が正本で、ここは再エクスポートと
表名・ビュー名の定数だけを持つ（二重定義しない）。
"""

from __future__ import annotations

from core.schema import (  # noqa: F401  (re-export)
    CLAIM_ORIGIN_ATOMIC_REWRITE,
    CLAIM_ORIGIN_CLAIM_OBJECT,
    CLAIM_ORIGIN_EQUATION_SYNTHESIS,
    CLAIM_ORIGIN_SPAN,
    CLAIM_ORIGINS,
    CLAIM_TIERS,
    CLAIM_TYPES,
    COMPONENT_TYPES,
    KNOWLEDGE_OBJECT_KINDS,
    LEARNING_UNIT_KINDS,
    LEARNING_UNIT_KINDS_FOR_COURSE,
    LEARNING_UNIT_REVIEW_STATUSES,
)

#: stable_key のバージョン接頭辞。材料や正規化を変えるときは k2 にして旧キーと区別する。
STABLE_KEY_VERSION_PREFIX = "k1:"

#: 基表（書き手だけが触る。KO5）
TABLE_CLAIMS = "theory_claims"
TABLE_COMPONENTS = "theory_components"
TABLE_COMPONENT_LINKS = "theory_component_links"
TABLE_COMPONENT_GRAPHS = "theory_component_graphs"
TABLE_EQUATIONS = "knowledge_equations"
TABLE_EVIDENCE = "knowledge_evidence"
TABLE_DERIVATION_STEPS = "knowledge_derivation_steps"
TABLE_SYMBOLS = "knowledge_symbols"
TABLE_REMAP = "element_id_remap"
TABLE_ARTIFACTS = "document_analysis_artifacts"
TABLE_CLAIM_TYPES = "knowledge_claim_types"
TABLE_COMPONENT_TYPES = "knowledge_component_types"
#: 学ぶ単位（Phase 2 / migration 081。learning_units_design.md §4.1）
TABLE_LEARNING_UNITS = "learning_units"
TABLE_UNIT_KINDS = "knowledge_unit_kinds"

#: live ビュー（読み手はこちらを読む。KO5）
VIEW_CLAIMS_LIVE = "theory_claims_live"
VIEW_COMPONENTS_LIVE = "theory_components_live"
VIEW_LEARNING_UNITS_LIVE = "learning_units_live"

#: learning_units.review_status の既定値（人間が触っていない = 候補）。
DEFAULT_UNIT_REVIEW_STATUS = "candidate"

#: 種別 → 知識表（新 4 表）。claim / component は既存 2 表。
KIND_TABLES: dict[str, str] = {
    "claim": TABLE_CLAIMS,
    "component": TABLE_COMPONENTS,
    "equation": TABLE_EQUATIONS,
    "evidence": TABLE_EVIDENCE,
    "derivation_step": TABLE_DERIVATION_STEPS,
    "symbol": TABLE_SYMBOLS,
}

#: 種別 → agent 側 ID を保存する列名。
KIND_AGENT_ID_COLUMNS: dict[str, str] = {
    "claim": "agent_claim_id",
    "component": "agent_component_id",
    "equation": "agent_equation_id",
    "evidence": "agent_evidence_id",
    "derivation_step": "agent_step_id",
    "symbol": "agent_symbol_id",
}

#: review_status の既定値（この値の行は「人間が触っていない」とみなす。§5.3）
DEFAULT_REVIEW_STATUS = "teacher_review_required"

#: 語彙外の型を丸める先（§7）。
CLAIM_TYPE_FALLBACK = "unknown"
COMPONENT_TYPE_FALLBACK = "theory"


def normalize_claim_type(raw: str | None) -> str:
    """語彙にあればその値、無ければ ``unknown``（自称は claim_type_text に残す）。"""
    value = str(raw or "").strip()
    return value if value in CLAIM_TYPES else CLAIM_TYPE_FALLBACK


def normalize_component_type(raw: str | None) -> str:
    """語彙にあればその値、無ければ従来どおり ``theory``（自称は component_type_text に残す）。"""
    value = str(raw or "").strip()
    return value if value in COMPONENT_TYPES else COMPONENT_TYPE_FALLBACK
