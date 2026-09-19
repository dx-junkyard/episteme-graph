"""ComponentAssemblyAgent data models."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from episteme_graph.agents.validation import ValidationIssue as SharedValidationIssue

COMPONENTS_VERSION = "v1"

CORE_COMPONENT_TYPES = [
    "RelationComponent",
    "AssumptionComponent",
    "CorrectionComponent",
    "UncertaintyComponent",
    "DiagnosticComponent",
    "MethodComponent",
    "TheoryComponent",
    "ClaimBundleComponent",
    # Image pipeline (design doc §5-5, migration 041): apparatus/instrument
    # identification candidates built deterministically by
    # apparatus_components.py from ApparatusSemanticsAgent's vision-LLM
    # output. Kept in the built-in default vocabulary (not cartridge-only)
    # so these components remain valid even without a cartridge loaded
    # (design principle #5: cartridge absence must not break the agent).
    # Matches the theory_components.component_type DB CHECK vocabulary
    # added by migration 041 (backend/db/041_image_pipeline.sql).
    "apparatus",
    "instrument",
    "part",
]

CORE_DEPENDENCY_TYPES = [
    "requires",
    "depends_on",
    "refines",
    "qualifies",
    "supports",
    "diagnoses",
    "propagates_uncertainty_to",
]

DEPENDENCY_TYPE_ALIASES = {
    # LLMs often emit the past-participle wording when describing a relation
    # that qualifies another component. The schema uses the verb form.
    "qualified": "qualifies",
}

ASSEMBLY_HINT_TYPES = [
    "candidate_bridge_component",
    "candidate_uncertainty_hub",
    "candidate_core_component",
    "candidate_correction_cluster",
    "candidate_diagnostic_cluster",
]

# Issue #440: generic responsibility/operation labels that, on their own, fail to
# identify a component. A concrete name pairs a subject with a responsibility;
# a name equal to one of these bare labels is flagged COMPONENT_NAME_GENERIC.
# Domain-agnostic — these are structural responsibility/operation words, not
# field-specific terms.
GENERIC_COMPONENT_LABELS = {
    "",
    "component",
    "theory",
    "theory component",
    "relation",
    "relation component",
    "method",
    "method component",
    "model",
    "definition",
    "derivation",
    "constraint",
    "application",
    "limitation",
    "observation model",
    "observation_model",
    "observable basis",
    "observable_basis",
    "equation system",
    "equation_system",
    "correction",
    "uncertainty",
    "diagnostic",
    "result",
    "assumption",
    # bare generic operations (mirror component_refiner._GENERIC_OPERATIONS)
    "transform",
    "relate",
    "connect",
    "support",
    "associate",
}


def is_generic_component_name(name: object) -> bool:
    """Return True when ``name`` is a generic responsibility/operation label (#440)."""
    normalized = " ".join(str(name or "").strip().lower().split())
    return normalized in GENERIC_COMPONENT_LABELS


def normalize_dependency_type(value: object) -> str:
    raw = str(value or "").strip().lower()
    return DEPENDENCY_TYPE_ALIASES.get(raw, raw)


def normalize_dependencies(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    dependencies: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        dep = dict(item)
        dep["dependency_type"] = normalize_dependency_type(dep.get("dependency_type"))
        dep["component_refs"] = list(dep.get("component_refs") or [])
        dep["reason"] = str(dep.get("reason", ""))
        dependencies.append(dep)
    return dependencies


# ---------------------------------------------------------------------------
# concepts の型契約（knowledge_structure_review_2026-09-12 §4 Phase 0 の P0-3）
# ---------------------------------------------------------------------------
# 概念名の最小長（**ASCII 英数字だけで書かれた名前**に適用）。``R`` / ``e`` /
# ``b1`` のような短い ASCII トークンは概念ではなく記号とみなし concepts に
# 載せない（F-6 / K-2）。記号は symbol_registry artifact が正本なので、ここで
# 落としても情報は失われない。
MIN_CONCEPT_NAME_LENGTH = 3

# 非 ASCII の文字（ギリシャ文字・CJK など）を含む名前の最小長。「重力」のような
# 2 文字の概念名を巻き込まないため ASCII より緩く、記号 1 文字（``λ`` / ``φ``）
# だけを落とす。分野語をハードコードせずに記号を排除するための規則。
MIN_UNICODE_CONCEPT_NAME_LENGTH = 2

# LaTeX 制御記法を含む名前は長さによらず記号層（``\lambda`` は ASCII 7 文字なので
# 長さ規則を通ってしまう）。先頭の ``\`` と ``{ } $`` の混入は常に記号とみなす。
_LATEX_MARKUP_CHARS = frozenset("{}$")

# 添字記法（``b_1`` / ``R_D`` / ``x^2``）。``_`` ``^`` の混入だけで切ると
# ``zero_recoil_limit`` のような snake_case の概念名まで落ちるため、区切られた
# 各部分が短い（2 文字以下）か数字だけのときに限って記号とみなす。
_SUBSCRIPT_SEPARATORS = ("_", "^")
_MAX_SUBSCRIPT_SEGMENT_LENGTH = 2

# claim 側 concept の型のうち、概念層に載せないもの。equation_claim_synthesis が
# 生成する claim concept は全件 ``concept_type="symbol"`` で、これがそのまま
# component.concepts / prerequisite_concepts に流れていた（K-2）。
SYMBOL_CONCEPT_TYPES = {"symbol"}


def is_symbol_like_concept_name(name: str) -> bool:
    """概念名が「記号層」に属するか（P0-3）。

    判定は分野語をハードコードせず、書式だけで行う:

    - LaTeX 制御記法を含む（先頭が ``\\`` / ``{ } $`` を含む）→ 記号
      （``\\lambda`` は ASCII 7 文字なので長さ規則では落ちない）。
    - 添字記法（``b_1`` / ``R_D`` / ``x^2``）→ 記号。``_`` ``^`` の混入だけで
      切ると ``zero_recoil_limit`` のような snake_case の概念名まで落ちるため、
      区切られた各部分が :data:`_MAX_SUBSCRIPT_SEGMENT_LENGTH` 以下か数字だけの
      ときに限る。
    - 非 ASCII の文字を含む名前は :data:`MIN_UNICODE_CONCEPT_NAME_LENGTH` 未満 →
      記号（``λ`` は落ち、``重力`` は残る）。
    - それ以外（ASCII のみ）は :data:`MIN_CONCEPT_NAME_LENGTH` 未満 → 記号
      （``R`` / ``e`` / ``b1`` は落ちる）。

    P0-3 は記号を消す規律ではなく symbol_registry に閉じる規律なので、概念層に
    載らないことと情報が失われることは別（``R_D`` のような観測量の記号名は
    symbol_registry から辿れる。概念レジストリの新設は Phase 3）。
    """
    token = str(name or "").strip()
    if not token:
        return True
    if token.startswith("\\") or any(ch in _LATEX_MARKUP_CHARS for ch in token):
        return True
    if any(sep in token for sep in _SUBSCRIPT_SEPARATORS) and _is_subscripted_symbol(token):
        return True
    if any(ord(ch) > 127 for ch in token):
        return len(token) < MIN_UNICODE_CONCEPT_NAME_LENGTH
    return len(token) < MIN_CONCEPT_NAME_LENGTH


def _is_subscripted_symbol(token: str) -> bool:
    """``b_1`` / ``R_D`` / ``x^2`` のような添字付き記号か（snake_case は除く）。"""
    segments = [token]
    for sep in _SUBSCRIPT_SEPARATORS:
        segments = [part for seg in segments for part in seg.split(sep)]
    for segment in segments:
        if not segment:
            return True
        if segment.isdigit() or len(segment) <= _MAX_SUBSCRIPT_SEGMENT_LENGTH:
            return True
    return False


def _concept_name_and_type(item: object) -> tuple[str, str]:
    """concepts の 1 要素から（概念名, concept_type）を取り出す。

    受け付ける形は str / dict / ClaimConcept 等のオブジェクト。名前は
    ``normalized`` → ``name`` → ``text`` の順で解決する（型が不明な形では
    concept_type は空文字＝判定不能）。
    """
    if isinstance(item, bytes):
        return (item.decode("utf-8", "replace").strip(), "")
    if isinstance(item, str):
        return (item.strip(), "")
    if isinstance(item, dict):
        name = str(
            item.get("normalized")
            or item.get("name")
            or item.get("text")
            or ""
        ).strip()
        return (name, str(item.get("concept_type") or "").strip())
    name = str(
        getattr(item, "normalized", "")
        or getattr(item, "name", "")
        or getattr(item, "text", "")
        or ""
    ).strip()
    if not name:
        name = str(item or "").strip()
    return (name, str(getattr(item, "concept_type", "") or "").strip())


def concept_name_list(value: object) -> list[str]:
    """``concepts`` 値を「概念名の list[str]」へ正規化する単一の入口（P0-3）。

    str / list[str] / list[dict] / list[ClaimConcept] のどれで来ても同じ結果に
    なるよう揃える。**str は 1 要素の list として扱う**（``list("raptis")`` の
    ように 1 文字ずつ回り ``['r','a','p','i','s','t']`` が前提知識として
    学習者まで貫通した F0-6 の再発防止）。

    併せて概念層と記号層を分離する（P0-3）:

    - ``concept_type`` が ``symbol``（型が読める形のときのみ判定できる）の要素は
      concept にしない。記号は symbol_registry artifact が正本。
    - 書式から記号と分かる名前（:func:`is_symbol_like_concept_name`）は concept に
      しない。型が読めない str 形でも記号を落とせる唯一の手掛かりが書式のため。
    """
    if value is None:
        return []
    if isinstance(value, (str, bytes, dict)):
        items: list[object] = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        try:
            items = list(value)  # type: ignore[arg-type]
        except TypeError:
            items = [value]

    names: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item is None:
            continue
        name, concept_type = _concept_name_and_type(item)
        if not name:
            continue
        if concept_type.lower() in SYMBOL_CONCEPT_TYPES:
            continue
        if is_symbol_like_concept_name(name):
            continue
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


@dataclass
class CartridgeContext:
    cartridge_id: str
    ontology: dict
    component_types: dict
    relation_types: dict
    validation_rules: dict
    aliases: dict | None = None
    notation_patterns: list | None = None
    normalization_rules: list | None = None


@dataclass
class ComponentAssemblyLLMInput:
    document_id: str
    cartridge_id: str | None
    accepted_claims: list[dict]
    equations: list[dict]
    thesis_nodes: list[dict]
    dsl_nodes: list[dict]
    dsl_edges: list[dict]
    allowed_component_types: list[str]
    allowed_dependency_types: list[str]
    normalized_terms: list[dict] | None = None
    # Deterministic artifact ID lists for cross-reference validation
    available_claims: list[dict] = field(default_factory=list)
    available_evidence: list[dict] = field(default_factory=list)
    available_equations: list[dict] = field(default_factory=list)
    available_dsl_nodes: list[dict] = field(default_factory=list)
    available_dsl_edges: list[dict] = field(default_factory=list)
    available_derivation_ids: list[str] = field(default_factory=list)
    claim_centered_plan: dict = field(default_factory=dict)
    # Claims dropped by the shared input limit (issue #356).
    excluded_from_pipeline_input: list[dict] = field(default_factory=list)


@dataclass
class ComponentFieldRef:
    text: str
    node_refs: list[str] | None = None
    claim_ids: list[str] | None = None
    equation_ids: list[str] | None = None


@dataclass
class ComponentDependency:
    dependency_type: str
    component_refs: list[str]
    reason: str


INTERNAL_FLOW_REQUIRED_TYPES = {
    "RelationComponent",
    "PaperRelationComponent",
    "CorrectionComponent",
    "DiagnosticComponent",
    "MethodComponent",
}


@dataclass
class ComponentRecord:
    component_id: str
    component_type: str
    label: str
    summary: str
    inputs: list[dict]
    outputs: list[dict]
    preconditions: list[dict]
    cautions: list[dict]
    dependencies: list[dict]
    evidence_refs: dict
    reason: str
    confidence: float
    review_notes: list[str]
    internal_flow: list[dict] = field(default_factory=list)
    # Typed linked IDs (issue #262)
    linked_claim_ids: list[str] = field(default_factory=list)
    linked_equation_ids: list[str] = field(default_factory=list)
    linked_evidence_ids: list[str] = field(default_factory=list)
    linked_derivation_ids: list[str] = field(default_factory=list)
    linked_dsl_node_ids: list[str] = field(default_factory=list)
    linked_dsl_edge_ids: list[str] = field(default_factory=list)
    input_equation_ids: list[str] = field(default_factory=list)
    intermediate_equation_ids: list[str] = field(default_factory=list)
    output_equation_ids: list[str] = field(default_factory=list)
    constraint_equation_ids: list[str] = field(default_factory=list)
    definition_equation_ids: list[str] = field(default_factory=list)
    review_required_equation_ids: list[str] = field(default_factory=list)
    eliminated_symbols: list[str] = field(default_factory=list)
    retained_symbols: list[str] = field(default_factory=list)
    equation_confidence_summary: dict = field(default_factory=dict)
    confidence_gate: dict = field(default_factory=dict)
    review_status: str = "teacher_review_required"
    teaching_takeaway: str = ""
    source_scope: dict = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    approximations: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    invalid_conditions: list[str] = field(default_factory=list)
    # Reusable theory responsibility type used by analyzer/refiner stages.
    # Kept separate from cartridge-backed component_type for backward
    # compatibility with persistence, graph, and course-mapping code.
    responsibility_type: str = ""
    primary_operation: str = ""
    secondary_operations: list[str] = field(default_factory=list)
    split_recommendation: dict = field(default_factory=dict)
    component_quality: dict = field(default_factory=dict)
    support_role: str = ""
    supports_claim_ids: list[str] = field(default_factory=list)
    support_distance_to_headline_claim: int = 0
    support_kind: str = ""
    # Thesis nodes (central_thesis / support:<section>:<idx>) this component
    # backs, derived deterministically from claim/equation overlap (issue #354).
    supports_thesis_node_ids: list[str] = field(default_factory=list)
    # Issue #440: a self-describing statement of the component's role in the
    # thesis (e.g. "States the central result", "Provides the theoretical
    # basis"). Derived deterministically from support_role / support_kind /
    # thesis backing when the LLM leaves it empty. Domain-agnostic.
    role_in_thesis: str = ""
    # Concept tags required for component graph / course mapping (issue #8).
    # Derived deterministically from linked atomic-claim concepts, equation
    # symbols, and cartridge normalized terms.
    concepts: list[str] = field(default_factory=list)
    prerequisite_concepts: list[str] = field(default_factory=list)
    introduced_concepts: list[str] = field(default_factory=list)
    reused_concepts: list[str] = field(default_factory=list)
    # Single main theoretical operation for this component (issue #300).
    # One of the theory-operation families
    # (define, linearize, eliminate, substitute, solve, derive, constrain, ...).
    operation: str = ""
    # Teaching granularity metadata recomputed by ComponentRefiner Step 3 (#324):
    # {estimated_slide_count, teachable_as_single_unit, split_if_too_dense,
    #  teaching_takeaway_quality}.
    teaching_granularity: dict = field(default_factory=dict)
    maturity_source: str = "llm_proposed"
    publish_ready: bool = False
    fallback_reason: str = ""
    original_failure_codes: list[str] = field(default_factory=list)


@dataclass
class ValidationIssue(SharedValidationIssue):
    """Shared issue + the component/claim the issue is attached to.

    ``target_type`` / ``target_id`` let ``repair._issue_dict`` tell the model
    exactly which component to fix; no other agent needs them.
    """
    target_type: str | None = None
    target_id: str | None = None


@dataclass
class ComponentAssemblyResult:
    document_id: str
    components_version: str
    cartridge_id: str | None
    components: list[ComponentRecord]
    assembly_hints: list[dict]
    review_notes: list[str]
    confidence: float
    validation_issues: list[ValidationIssue] = field(default_factory=list)
    # ComponentRefiner output: record of summary→theory-operation splits (issue #300).
    refinement_report: dict = field(default_factory=dict)
    # ComponentRefiner Step 3 formal output contract (issue #324):
    # {refined_components, component_refinement_records, component_id_mapping,
    #  component_graph_updates, refinement_validation}.
    component_refinement: dict = field(default_factory=dict)
    # DerivationGraphAligner Step 4 output contract (issue #325):
    # {theory_component_graph, equation_operation_graph, component_operation_links,
    #  aligned_derivation_chains, support_map, graph_alignment_validation,
    #  export_validation}.
    derivation_graph_alignment: dict = field(default_factory=dict)
    # TheoryBundleBuilder + TeachingOutputMapper Step 5 output contract (issue #326):
    # {theory_bundle, course_mapping, blueprint_updates, theory_bundle_validation,
    #  teaching_output_validation}.
    theory_bundle: dict = field(default_factory=dict)
    # Claims dropped from the LLM input by the shared selection policy (issue #356).
    excluded_from_pipeline_input: list[dict] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        for component in data.get("components", []) or []:
            responsibility = component.get("responsibility_type") or ""
            if responsibility:
                component.setdefault("type", responsibility)
            # Issue #440: ``name`` mirrors the component label so consumers do not
            # have to know the internal field name. The classification ``type``
            # the issue mandates is carried by ``component_type`` (validated
            # against the COMPONENT_TYPE vocabulary by the agent validator).
            if "name" not in component:
                component["name"] = component.get("label") or ""
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "ComponentAssemblyResult":
        components = []
        for c in d.get("components", []):
            components.append(ComponentRecord(
                component_id=c.get("component_id", ""),
                component_type=c.get("component_type", ""),
                label=c.get("label", ""),
                summary=c.get("summary", ""),
                inputs=list(c.get("inputs") or []),
                outputs=list(c.get("outputs") or []),
                preconditions=list(c.get("preconditions") or []),
                cautions=list(c.get("cautions") or []),
                dependencies=normalize_dependencies(c.get("dependencies")),
                evidence_refs=c.get("evidence_refs") or {},
                reason=c.get("reason", ""),
                confidence=float(c.get("confidence", 0.0)),
                review_notes=list(c.get("review_notes") or []),
                internal_flow=list(c.get("internal_flow") or []),
                linked_claim_ids=list(c.get("linked_claim_ids") or []),
                linked_equation_ids=list(c.get("linked_equation_ids") or []),
                linked_evidence_ids=list(c.get("linked_evidence_ids") or []),
                linked_derivation_ids=list(c.get("linked_derivation_ids") or []),
                linked_dsl_node_ids=list(c.get("linked_dsl_node_ids") or []),
                linked_dsl_edge_ids=list(c.get("linked_dsl_edge_ids") or []),
                input_equation_ids=list(c.get("input_equation_ids") or []),
                intermediate_equation_ids=list(c.get("intermediate_equation_ids") or []),
                output_equation_ids=list(c.get("output_equation_ids") or []),
                constraint_equation_ids=list(c.get("constraint_equation_ids") or []),
                definition_equation_ids=list(c.get("definition_equation_ids") or []),
                review_required_equation_ids=list(c.get("review_required_equation_ids") or []),
                eliminated_symbols=list(c.get("eliminated_symbols") or []),
                retained_symbols=list(c.get("retained_symbols") or []),
                equation_confidence_summary=c.get("equation_confidence_summary") or {},
                confidence_gate=c.get("confidence_gate") or {},
                review_status=c.get("review_status", "teacher_review_required"),
                teaching_takeaway=c.get("teaching_takeaway", ""),
                source_scope=c.get("source_scope") or {},
                assumptions=list(c.get("assumptions") or []),
                approximations=list(c.get("approximations") or []),
                constraints=list(c.get("constraints") or []),
                invalid_conditions=list(c.get("invalid_conditions") or []),
                responsibility_type=c.get("responsibility_type", c.get("type", "")),
                primary_operation=c.get("primary_operation", c.get("operation", "")),
                secondary_operations=list(c.get("secondary_operations") or []),
                split_recommendation=c.get("split_recommendation") or {},
                component_quality=c.get("component_quality") or {},
                support_role=c.get("support_role", ""),
                supports_claim_ids=list(c.get("supports_claim_ids") or []),
                support_distance_to_headline_claim=int(c.get("support_distance_to_headline_claim", 0) or 0),
                support_kind=c.get("support_kind", ""),
                supports_thesis_node_ids=list(c.get("supports_thesis_node_ids") or []),
                role_in_thesis=c.get("role_in_thesis", ""),
                # P0-3: dict から復元するときも concepts の型契約を通す。
                # 素の list() は str を 1 文字ずつに割るため使わない（F0-6）。
                concepts=concept_name_list(c.get("concepts")),
                prerequisite_concepts=concept_name_list(c.get("prerequisite_concepts")),
                introduced_concepts=concept_name_list(c.get("introduced_concepts")),
                reused_concepts=concept_name_list(c.get("reused_concepts")),
                operation=c.get("operation", ""),
                teaching_granularity=c.get("teaching_granularity") or {},
                maturity_source=c.get("maturity_source", "llm_proposed"),
                publish_ready=bool(c.get("publish_ready", False)),
                fallback_reason=c.get("fallback_reason", ""),
                original_failure_codes=list(c.get("original_failure_codes") or []),
            ))
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            components_version=d.get("components_version", COMPONENTS_VERSION),
            cartridge_id=d.get("cartridge_id"),
            components=components,
            assembly_hints=d.get("assembly_hints", []),
            review_notes=d.get("review_notes", []),
            confidence=float(d.get("confidence", 0.0)),
            validation_issues=issues,
            refinement_report=d.get("refinement_report") or {},
            component_refinement=d.get("component_refinement") or {},
            derivation_graph_alignment=d.get("derivation_graph_alignment") or {},
            theory_bundle=d.get("theory_bundle") or {},
            excluded_from_pipeline_input=d.get("excluded_from_pipeline_input") or [],
            diagnostics=d.get("diagnostics") or {},
        )

    @classmethod
    def make_fallback(
        cls,
        document_id: str,
        cartridge_id: str | None,
        reason: str,
    ) -> "ComponentAssemblyResult":
        return cls(
            document_id=document_id,
            components_version=COMPONENTS_VERSION,
            cartridge_id=cartridge_id,
            components=[],
            assembly_hints=[],
            review_notes=[f"Component assembly failed: {reason}"],
            confidence=0.0,
            validation_issues=[ValidationIssue(
                rule_id="component_assembly_failed",
                severity="error",
                message=reason,
                field="components",
            )],
        )
