"""ExportValidationGate — deterministic cross-artifact validation before persist.

Aggregates validation_issues from all stage artifacts, runs cross-artifact ID
checks, and decides whether the pipeline result is exportable.

Status values:
  passed              — no errors, no warnings
  passed_with_warnings — no errors, has warnings
  needs_review        — review items present but no hard errors
  failed_validation   — has hard errors → persist / completed are blocked
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

EXPORT_STATUSES = [
    "passed",
    "passed_with_warnings",
    "needs_review",
    "failed_validation",
]

# Issue #432: a result/relation equation with no input links is only acceptable
# when explicitly justified by one of these link_status values.
_ALLOWED_NO_INPUT_LINK_STATUSES = {"axiomatic", "external_reference"}


@dataclass
class ValidationEntry:
    code: str
    message: str
    artifact: str
    path: str | None = None
    source_stage: str | None = None
    # Issue #418: the concrete entity a finding is about, stored directly so the
    # revision inventory can resolve it without parsing the JSON ``path``.
    target_type: str | None = None
    target_id: str | None = None


@dataclass
class ValidationSummary:
    error_count: int = 0
    warning_count: int = 0
    review_required_count: int = 0


@dataclass
class ComponentQualityEntry:
    component_id: str
    granularity_status: str
    equation_count: int = 0
    claim_count: int = 0
    derivation_step_count: int = 0
    responsibility_count: int = 0
    source_scope_width: int = 0
    split_required: bool = False
    split_reasons: list[str] = field(default_factory=list)
    suggested_split: list[dict] = field(default_factory=list)


def _empty_concept_validation() -> dict:
    return {
        "missing_concepts": [],
        "empty_concepts_on_main_claims": [],
        "empty_concepts_on_main_components": [],
        "concepts_on_composite_claims": [],
        "concept_role_mismatch": [],
        "concepts_from_low_confidence_sources": [],
    }


def _empty_refinement_validation() -> dict:
    return {
        "split_components": [],
        "unchanged_components": [],
        "failed_refinements": [],
        "review_required_refinements": [],
        # Components whose split was required but were neither split, failed, nor
        # review_required — a silent contract violation that must hard-block
        # publish (#421).
        "unprocessed_split_required": [],
        "unassigned_links": [],
        "dangling_component_refs": [],
        "teaching_granularity_warnings": [],
    }


def _empty_derivation_graph_alignment() -> dict:
    return {
        "errors": [],
        "warnings": [],
        "review_items": [],
        "component_graph_clean": True,
        "operation_graph_clean": True,
        "support_map_renderable": True,
    }


def _empty_theory_bundle_validation() -> dict:
    return {
        "errors": [],
        "warnings": [],
        "review_items": [],
        "bundle_created": False,
        "headline_claim_linked": False,
        "component_refs_valid": True,
        "support_map_linked": False,
    }


def _empty_thesis_coverage() -> dict:
    return {
        "thesis_present": False,
        "coverage_by_section": {},
        "total_refs": 0,
        "reachable_refs": 0,
        "unreachable_refs": [],
    }


def _empty_teaching_output_validation() -> dict:
    return {
        "errors": [],
        "warnings": [],
        "review_items": [],
        "course_topics_link_components": True,
        "blueprint_refs_valid": True,
        "blackbox_policy_respects_confidence": True,
    }


def _empty_document_completeness() -> dict:
    # Document ingest completeness report (issue #366): aggregated per-document.
    return {
        "checked": False,
        "all_documents_complete": True,
        "documents": [],
    }


# Equation reconstruction fidelity reason codes (issue #368). Aggregated per
# type with target ids + artifact paths in equation_fidelity.
_EQUATION_FIDELITY_CODES = [
    "latex_is_prose",
    "invalid_equation_label",
    "table_derived_equation_candidate",
    "nonequation_id_in_links",
    "symbol_loss_unverifiable",
]


def _empty_equation_fidelity() -> dict:
    return {
        "checked": False,
        "total": 0,
        "issue_counts": {code: 0 for code in _EQUATION_FIDELITY_CODES},
        "issues": {code: [] for code in _EQUATION_FIDELITY_CODES},
    }


def _load_completeness_analyzer():
    """Resolve analyze_document_completeness robustly (issue #366).

    This module is imported both as part of the ``core.document_pipeline``
    package (production) and as a standalone file via spec_from_file_location
    (unit tests), where relative imports have no parent package. Try both, then
    fall back to loading the sibling file directly by path.
    """
    try:
        from .completeness import analyze_document_completeness
        return analyze_document_completeness
    except Exception:
        pass
    try:
        from core.document_pipeline.completeness import analyze_document_completeness
        return analyze_document_completeness
    except Exception:
        pass
    try:
        import importlib.util
        import os

        path = os.path.join(os.path.dirname(__file__), "completeness.py")
        spec = importlib.util.spec_from_file_location("_dp_completeness", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.analyze_document_completeness
    except Exception:
        return None


@dataclass
class ExportValidationResult:
    status: str                          # one of EXPORT_STATUSES
    exportable: bool
    publish_ready: bool
    errors: list[ValidationEntry] = field(default_factory=list)
    warnings: list[ValidationEntry] = field(default_factory=list)
    review_items: list[ValidationEntry] = field(default_factory=list)
    component_quality: list[ComponentQualityEntry] = field(default_factory=list)
    summary: ValidationSummary = field(default_factory=ValidationSummary)
    # Concept coverage report for claims / components (issue #8).
    concept_validation: dict = field(default_factory=_empty_concept_validation)
    # ComponentRefiner Step 3 reporting (issue #324).
    component_refinement_validation: dict = field(default_factory=_empty_refinement_validation)
    # DerivationGraphAligner Step 4 reporting (issue #325).
    derivation_graph_alignment: dict = field(default_factory=_empty_derivation_graph_alignment)
    # TheoryBundleBuilder Step 5 reporting (issue #326).
    theory_bundle_validation: dict = field(default_factory=_empty_theory_bundle_validation)
    # TeachingOutputMapper Step 5 reporting (issue #326).
    teaching_output_validation: dict = field(default_factory=_empty_teaching_output_validation)
    # Thesis coverage report (issue #354): is the central thesis (and each
    # support_structure section) actually backed by the exported main graph?
    thesis_coverage: dict = field(default_factory=_empty_thesis_coverage)
    # Document ingest completeness report (issue #366): did the source ingest
    # capture the whole document (equations / terminal section / page coverage)?
    document_completeness: dict = field(default_factory=_empty_document_completeness)
    # Equation reconstruction fidelity report (issue #368): per-type counts,
    # target equation/candidate ids and artifact paths for the fine-grained
    # PDF math quality problems.
    equation_fidelity: dict = field(default_factory=_empty_equation_fidelity)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

# Severity → bucket mapping
_SEVERITY_ERROR = "error"
_SEVERITY_WARNING = "warning"

# Rule IDs that represent hard errors from ComponentAssembly
_HARD_ERROR_RULE_IDS = {
    "unresolved_claim_id",
    "unresolved_evidence_id",
    "unresolved_equation_id",
    "unresolved_dsl_node_id",
    "unresolved_dsl_edge_id",
    "no_components",
    "component_assembly_failed",
    "invalid_component_type",
    "internal_flow_invalid_entry",
    "internal_flow_incomplete_step",
}

# Rule IDs that always need_review (but not necessarily hard error)
_NEEDS_REVIEW_RULE_IDS = {
    "summary_only_component",
    "boundary_needs_review",
    "review_required_claim_in_component",
}

# Rule IDs escalated to hard errors regardless of their reported severity.
# Deterministic-fallback components are conservative placeholders, not
# source-backed reusable components, and must never be silently persisted
# as regular theory_components (#347).
_FORCED_ERROR_RULE_IDS = {
    "component_assembly_deterministic_fallback",
}

# Stage names where validation_issues with severity=error are hard errors
_HARD_ERROR_STAGES = {
    "component_assembly",
    "course_mapping",
    "dsl_linking",
    "claim_object_builder",
    "evidence_registry",
    # ComponentGraphValidator errors (dependency cycles, equation-id labels,
    # fallback nodes marked source_backed, ...) are design-level hard errors
    # and must stop persist (#358 review fix).
    "component_graph",
}

# Rule IDs kept as warnings even when their stage is a hard-error stage.
# component_graph_failed marks the orchestrator's non-fatal stage fallback
# (the agent crashed and an empty graph was substituted); escalating it would
# turn a designed-to-be-non-fatal stage failure into a pipeline abort.
_SOFT_ERROR_RULE_IDS = {
    "component_graph_failed",
}

# Claim types representing the paper's main result / central conclusion (#312).
# A non-atomic main-result claim is a paper-level summary, not a usable claim.
_MAIN_RESULT_CLAIM_TYPES = {"result", "conclusion", "main_result"}

# Issue #433: controlled vocabulary a derivation step ``operation`` must belong
# to. Single source of truth is derivation_chain.schema.CONTROLLED_OPERATIONS;
# imported when available with a hardcoded mirror as a fallback so the gate never
# fails to load if the agents package is not importable.
try:  # pragma: no cover - import wiring
    from episteme_graph.agents.derivation_chain.schema import (
        CONTROLLED_OPERATIONS as _DERIVATION_CONTROLLED_OPERATIONS,
    )
    _DERIVATION_CONTROLLED_OPERATIONS = set(_DERIVATION_CONTROLLED_OPERATIONS)
except Exception:  # pragma: no cover - fallback mirror
    _DERIVATION_CONTROLLED_OPERATIONS = {
        "state_assumption", "apply_definition", "apply_criterion",
        "introduce_observable", "apply_equation", "substitute",
        "solve_linear_system", "eliminate_parameter", "normalize", "approximate",
        "compare", "branch_on_condition", "apply_measurement_or_update",
        "apply_non_disturbance_or_independence", "infer_intermediate_claim",
        "infer_conclusion", "flag_limitation", "define", "relate", "transform",
        "derive_result", "apply_constraint", "linearize", "eliminate", "solve",
        "constrain", "integrate", "eliminate_variable",
    }

# Step review states that count as "flagged for review" — an out-of-vocabulary
# operation is tolerated (review item, not hard error) when the step is flagged.
_DERIVATION_REVIEW_STATES = {"teacher_review_required", "needs_verification"}

# Issue #439: controlled vocabulary for an equation's role in the argument, and
# a free-symbol extractor, sourced from the agents package when importable with a
# hardcoded mirror so the gate never fails to load.
try:  # pragma: no cover - import wiring
    from episteme_graph.agents.equation_semantics.schema import (
        ROLE_IN_ARGUMENT_VOCAB as _EQUATION_ROLE_VOCAB,
        extract_free_symbols as _extract_free_symbols,
    )
    _EQUATION_ROLE_VOCAB = set(_EQUATION_ROLE_VOCAB)
except Exception:  # pragma: no cover - fallback mirror
    _EQUATION_ROLE_VOCAB = {"premise", "definition", "derived", "result", "constraint"}

    def _extract_free_symbols(latex):  # type: ignore[misc]
        if not latex:
            return set()
        text = re.sub(r"\\[A-Za-z]+", " ", str(latex))
        return {ch for token in re.findall(r"[A-Za-z]+", text) for ch in token}

# Issue #441: controlled vocabulary a DSL edge's edge_type must belong to.
try:  # pragma: no cover - import wiring
    from episteme_graph.agents.dsl_linking.schema import (
        EDGE_TYPE_VOCAB as _DSL_EDGE_TYPE_VOCAB,
    )
    _DSL_EDGE_TYPE_VOCAB = set(_DSL_EDGE_TYPE_VOCAB)
except Exception:  # pragma: no cover - fallback mirror
    _DSL_EDGE_TYPE_VOCAB = {
        "REQUIRES", "PRODUCES", "TRANSFORMS", "DEFINES", "MEASURES",
        "CONTAINS", "CORRELATES", "CAUSES", "INHIBITS", "EQUIVALENT",
    }


def _ordered_unique(values) -> list:
    result = []
    seen = set()
    for value in values or []:
        text = str(value or "")
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _append_fallback_diagnostics(message: str, artifact: dict) -> str:
    """Append the stage artifact's fallback root cause to an escalated error.

    The deterministic-fallback hard error (#347) is what operators see in the
    exported ERROR log, but the underlying reason (LLM exception, repair
    exhausted, ...) is only recorded in the artifact's diagnostics or on the
    fallback components themselves. Inline it so the error is self-contained.
    """
    diagnostics = artifact.get("diagnostics") or {}
    reason = str(diagnostics.get("fallback_reason") or "")
    failure_codes = list(diagnostics.get("original_failure_codes") or [])
    if not reason:
        for component in artifact.get("components") or []:
            if not isinstance(component, dict):
                continue
            reason = str(component.get("fallback_reason") or "")
            if reason:
                failure_codes = failure_codes or list(
                    component.get("original_failure_codes") or []
                )
                break
    if not reason and not failure_codes:
        return message
    suffix = f"; fallback_reason={reason!r}" if reason else ""
    if failure_codes:
        suffix += f"; original_failure_codes={failure_codes}"
    return f"{message}{suffix}"


def _component_low_confidence_equation_ids(component) -> list:
    """Low-confidence equation ids a component carries (Step 3 fields, #326).

    Used by the teaching-output check to verify a topic's blackbox_policy covers
    every low-confidence equation of its linked components.
    """
    ids = list(getattr(component, "review_required_equation_ids", []) or [])
    gate = getattr(component, "confidence_gate", {}) or {}
    ids.extend(gate.get("blocked_by_equation_ids") or [])
    return _ordered_unique(ids)


def _alignment_code(entry) -> str:
    code = str(entry.get("code") if isinstance(entry, dict) else entry or "issue")
    return code.upper()


def _alignment_message(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("message") or entry.get("code") or "")
    return str(entry or "")


def _claim_is_non_atomic(claim) -> bool:
    atomicity = str(getattr(claim, "atomicity", "atomic") or "atomic")
    is_atomic = bool(getattr(claim, "is_atomic", atomicity == "atomic"))
    return atomicity in {
        "composite",
        "split_required",
        "compound",
        "non_atomic",
        "split_pending",
    } or not is_atomic

# Domain-neutral operation / procedure words for concept role checks (issue #8).
_PROCEDURAL_CONCEPT_TERMS = {
    "derive", "derivation", "eliminate", "elimination", "solve", "substitute",
    "substitution", "linearize", "linearization", "transform", "transformation",
    "constrain", "constraint", "define", "definition", "normalize", "normalization",
    "approximate", "approximation", "expand", "expansion", "integrate", "integration",
    "differentiate", "reconstruct", "reconstruction",
}


def _looks_like_symbol(name: str) -> bool:
    token = str(name or "").strip()
    if not token or " " in token:
        return False
    if any(ch in token for ch in "_^{}\\=+/*()|<>"):
        return True
    return len(token) <= 3


def _has_math_or_procedural_concept(concepts) -> bool:
    for concept in concepts or []:
        text = str(concept or "")
        if text.lower() in _PROCEDURAL_CONCEPT_TERMS or _looks_like_symbol(text):
            return True
    return False


def _has_named_concept(concepts) -> bool:
    for concept in concepts or []:
        text = str(concept or "")
        if text.lower() in _PROCEDURAL_CONCEPT_TERMS or _looks_like_symbol(text):
            continue
        return True
    return False


def _component_concept_role_mismatch(component, concepts) -> str:
    """Return a reason string when concepts contradict the support_role (issue #8)."""
    role = str(getattr(component, "support_role", "") or "")
    operation = str(getattr(component, "operation", "") or "").lower()
    responsibility = str(getattr(component, "responsibility_type", "") or "")
    component_type = str(getattr(component, "component_type", "") or "")

    is_derivation = (
        role == "derivation_core"
        or responsibility == "derivation"
        or any(operation.startswith(p) for p in ("derive", "eliminate", "solve", "substitute", "linearize"))
    )
    if is_derivation and concepts and not _has_math_or_procedural_concept(concepts):
        return "derivation component lacks a mathematical or procedural concept"

    if (role == "observable_bridge" or component_type == "ObservableComponent") and concepts and not _has_named_concept(concepts):
        return "observable component lacks an observable-name concept"

    is_comparison = operation.startswith("compare") or component_type in {"ComparisonComponent", "TheoryComparisonComponent"}
    if is_comparison and concepts and not _has_named_concept(concepts):
        return "theory-comparison component lacks a theory-class concept"
    return ""


# Stage names where validation_issues are always warnings/review
_SOFT_STAGES = {
    "equation_semantics",
    "rhetorical_role",
    "figure_table_semantics",
    "derivation_chain",
    "blueprint",
}


class ExportValidationGate:
    """Deterministic gate run after Blueprint and before Persist."""

    def run(
        self,
        *,
        artifacts: dict,
        component_result=None,
        course_mapping=None,
        claim_objects=None,
        evidence=None,
        dsl=None,
    ) -> ExportValidationResult:
        errors: list[ValidationEntry] = []
        warnings: list[ValidationEntry] = []
        review_items: list[ValidationEntry] = []
        component_quality: list[ComponentQualityEntry] = []

        # 1. Aggregate validation_issues from all stage artifacts
        self._aggregate_artifact_issues(
            artifacts, errors, warnings, review_items
        )

        # 2. Equation consistency reporting
        self._check_equation_consistency_mismatches(artifacts, review_items, warnings)
        self._check_derivation_equation_confidence(artifacts, warnings, errors)
        # 2b. Equation reconstruction fidelity reporting (issue #368): per-type
        # counts + target ids + artifact paths for the fine-grained PDF math
        # quality problems (prose LaTeX, invalid label, table-derived candidate,
        # non-equation I/O link, unverifiable symbol loss).
        equation_fidelity = self._check_equation_fidelity(
            artifacts, review_items, warnings
        )
        # 2b-2. Equation self-describing report (issue #439): an equation with
        # free symbols in its (trusted) LaTeX must carry symbol descriptions, and
        # its role_in_argument must belong to the controlled vocabulary. Reported
        # as warnings — equation_semantics is a soft stage.
        self._check_equation_self_describing(artifacts, warnings)
        # 2c. Candidate→registry promotion invariant (issue #431): every accepted
        # equation candidate must be present in the equation registry. This is a
        # hard publish blocker enforced directly by the gate so it is independent
        # of the equation_semantics soft-stage error downgrade. Detection is a
        # pure ID-set difference — no branching on labels/formula text/domain.
        self._check_accepted_equations_registered(artifacts, errors)
        # 2d. Inter-equation link integrity (issue #432): links must resolve to
        # real registry equations and carry link_provenance; result/relation
        # equations must have at least one input link or an allowed link_status
        # (axiomatic / external_reference). Domain-agnostic — only ID resolution,
        # provenance presence, and link_status are checked.
        self._check_equation_link_integrity(artifacts, errors, review_items)
        # 2e. Derivation step structural constraints (issue #433): every step
        # needs non-empty endpoints, all referenced equations must be registered,
        # the operation must be in the controlled vocabulary (or the step flagged
        # for review), and at least one source_evidence_id must be present.
        self._check_derivation_step_constraints(artifacts, errors, review_items)

        # 3. Cross-artifact ID validation
        if component_result and claim_objects and evidence:
            self._cross_validate_component_ids(
                component_result, claim_objects, evidence, dsl,
                errors, warnings,
            )
            self._cross_validate_component_equation_confidence(
                component_result,
                artifacts,
                errors,
                warnings,
            )

        if component_result:
            self._check_deterministic_fallback_components(component_result, errors)
            self._check_component_internal_flow(component_result, errors)
            self._check_summary_only_derivation_components(component_result, errors)
            component_quality = self._check_component_quality(
                component_result,
                artifacts,
                warnings,
                review_items,
            )

        # 3. Course mapping → component ID resolution
        if course_mapping and component_result:
            self._cross_validate_course_mapping(
                course_mapping, component_result, errors, warnings,
            )

        # 4. DSL graph edge completeness
        if dsl:
            self._check_dsl_edges(
                dsl, artifacts, claim_objects, evidence, errors, warnings
            )
            # 4b. Thesis traversal anchor (issue #442): when there is a graph to
            # traverse, the thesis_reconstruction artifact must exist and its
            # anchor nodes must resolve to reachable graph nodes.
            self._check_thesis_artifact(artifacts, dsl, errors, review_items)

        # 5. Component graph export structure
        self._check_component_graph_artifact(
            artifacts, component_result, errors, warnings
        )

        # 6. Required artifact presence
        self._check_required_artifacts(artifacts, errors)

        # 6b. provisional claim ref leakage (#340): equation_semantics /
        # derivation_chain must not carry claim refs that are absent from the
        # final claims.json. Reported as warnings (review metadata), never a
        # hard block — the root-cause canonicalization happens upstream.
        if claim_objects is not None:
            self._check_unresolved_claim_refs(artifacts, claim_objects, warnings)

        # 7. source-backed claim must reference EvidenceRegistry (#257 / #312).
        # Per #312 these are hard errors regardless of the code path (freshly
        # built or reloaded artifact), so the gate enforces them itself.
        if claim_objects and evidence:
            self._check_source_backed_claims(claim_objects, evidence, errors)

        # 7b. claim atomicity reporting (#312): non-atomic claims cannot back
        # components / graph nodes; a non-atomic main result is a hard error.
        if claim_objects:
            self._check_claim_atomicity(claim_objects, errors, review_items)

        # 7c. concept coverage reporting (#8): claims / components used in the
        # graph or course mapping must carry non-empty, role-consistent concepts.
        concept_validation = self._check_concepts(
            claim_objects, component_result, warnings, review_items
        )

        # 7d. component refinement reporting (#324): ComponentRefiner Step 3
        # results — unassigned links, dangling refs, review-required refinements,
        # and teaching granularity warnings.
        component_refinement_validation = self._check_component_refinement(
            component_result, errors, warnings, review_items
        )

        # 7e. derivation graph alignment reporting (#325): aggregate the
        # DerivationGraphAligner Step 4 results (dangling refs / operation graph
        # equation refs / support map renderability) into the export buckets.
        # The gate aggregates results here; it does not re-run alignment.
        derivation_graph_alignment = self._check_derivation_graph_alignment(
            component_result, errors, warnings, review_items
        )

        # 7f. theory bundle + teaching output reporting (#326): Step 5 represents
        # the whole paper as a TheoryBundle and maps refined components into a
        # teaching output. The gate re-validates the bundle / topic / blueprint
        # references against the refined components so dangling refs surface here.
        theory_bundle_validation = self._check_theory_bundle(
            component_result, errors, warnings, review_items
        )
        teaching_output_validation = self._check_teaching_output(
            component_result, errors, warnings, review_items
        )

        # 7g-0. graph health checks (#358): claim↔equation link symmetry and
        # equation role conflicts across stages. Warnings only — the demotion
        # itself (primary link → inferred_*) happens upstream in the pipeline;
        # the gate keeps both demoted and still-asymmetric links visible.
        if claim_objects is not None:
            self._check_claim_equation_link_symmetry(
                claim_objects, artifacts, warnings
            )
        if component_result is not None:
            self._check_equation_role_conflicts(
                component_result, artifacts, warnings
            )

        # 7g. thesis coverage reporting (#354): verify each claim / equation the
        # reconstructed thesis references is still present in the final claim /
        # equation sets and is reachable from a main-layer graph node. Coverage
        # gaps are review items (never hard errors).
        thesis_coverage = self._check_thesis_coverage(
            artifacts, component_result, claim_objects, review_items
        )

        # 7h. document completeness gate (#366): a truncated ingest (missing
        # equations, no Conclusion, low page coverage) at the DocumentStructure /
        # EvidenceRegistry exit. Reported as warnings so the run stays exportable
        # for review but is never promoted to publish_ready.
        document_completeness = self._check_document_completeness(
            artifacts, warnings
        )

        # 6. Determine status
        summary = ValidationSummary(
            error_count=len(errors),
            warning_count=len(warnings),
            review_required_count=len(review_items),
        )

        if errors:
            status = "failed_validation"
            exportable = False
            publish_ready = False
        elif review_items:
            status = "needs_review"
            exportable = True
            publish_ready = False
        elif warnings:
            status = "passed_with_warnings"
            exportable = True
            publish_ready = False
        else:
            status = "passed"
            exportable = True
            publish_ready = True

        return ExportValidationResult(
            status=status,
            exportable=exportable,
            publish_ready=publish_ready,
            errors=errors,
            warnings=warnings,
            review_items=review_items,
            component_quality=component_quality,
            summary=summary,
            concept_validation=concept_validation,
            component_refinement_validation=component_refinement_validation,
            derivation_graph_alignment=derivation_graph_alignment,
            theory_bundle_validation=theory_bundle_validation,
            teaching_output_validation=teaching_output_validation,
            thesis_coverage=thesis_coverage,
            document_completeness=document_completeness,
            equation_fidelity=equation_fidelity,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_document_completeness(self, artifacts: dict, warnings: list) -> dict:
        """Deterministic ingest-completeness check at the structure exit (#366).

        Runs the shared completeness analysis on the document_structure (and
        evidence_registry) artifacts. Each incomplete document adds warnings so
        the run cannot be promoted to publish_ready while its central results may
        be missing. Never a hard error.
        """
        structure = artifacts.get("document_structure")
        if not isinstance(structure, dict) or not structure:
            return _empty_document_completeness()
        evidence = artifacts.get("evidence_registry")
        document_id = str(structure.get("document_id") or "")

        # Prefer the report the orchestrator already computed at the stage exit;
        # only recompute if it is absent (e.g. legacy / partial runs). The
        # orchestrator runs before equation_semantics, so equation-artifact
        # coverage (#416) is computed/refreshed here where all artifacts exist.
        equations = artifacts.get("equation_semantics")
        report = artifacts.get("document_completeness")
        if not isinstance(report, dict) or not report:
            analyze_document_completeness = _load_completeness_analyzer()
            if analyze_document_completeness is None:
                return _empty_document_completeness()
            report = analyze_document_completeness(
                structure,
                evidence if isinstance(evidence, dict) else None,
                document_id=document_id,
                equations=equations if isinstance(equations, dict) else None,
            )
        else:
            report = self._refresh_equation_artifact_coverage(
                report, structure, equations, document_id=document_id
            )

        result = _empty_document_completeness()
        result["checked"] = True
        result["all_documents_complete"] = bool(report.get("complete", True))
        result["documents"] = [report]

        if report.get("complete", True):
            return result

        eq = report.get("equation_label_continuity") or {}
        ingest = report.get("ingest_coverage") or {}
        if eq.get("missing_labels"):
            warnings.append(ValidationEntry(
                code="DOCUMENT_EQUATION_LABEL_DISCONTINUITY",
                message=(
                    f"document {document_id!r} is missing equation labels "
                    f"{eq.get('missing_labels')}"
                ),
                artifact="document_structure",
                path="$.completeness.equation_label_continuity",
                source_stage="export_validation",
            ))
        if report.get("terminal_section", {}).get("missing"):
            warnings.append(ValidationEntry(
                code="DOCUMENT_TERMINAL_SECTION_MISSING",
                message=(
                    f"document {document_id!r} has no terminal (Conclusion/Summary) "
                    "section; ingest may be truncated"
                ),
                artifact="document_structure",
                path="$.completeness.terminal_section",
                source_stage="export_validation",
            ))
        if not ingest.get("sufficient", True):
            warnings.append(ValidationEntry(
                code="DOCUMENT_INGEST_INCOMPLETE",
                message=(
                    f"document {document_id!r} ingest did not reach the document end: "
                    f"last ingested page {ingest.get('last_ingested_page')} of "
                    f"{ingest.get('pages_total')}; trailing un-ingested ranges "
                    f"{ingest.get('trailing_uningested_page_ranges')}"
                ),
                artifact="document_structure",
                path="$.completeness.ingest_coverage",
                source_stage="export_validation",
            ))
        tail = report.get("tail_truncation") or {}
        if tail.get("suspected"):
            warnings.append(ValidationEntry(
                code="DOCUMENT_TAIL_TRUNCATION_SUSPECTED",
                message=(
                    f"document {document_id!r} tail truncation suspected "
                    f"(confidence {tail.get('confidence')}); signals "
                    f"{tail.get('signals')}"
                ),
                artifact="document_structure",
                path="$.completeness.tail_truncation",
                source_stage="export_validation",
            ))
        coverage = report.get("equation_artifact_coverage") or {}
        if not coverage.get("complete", True):
            warnings.append(ValidationEntry(
                code="DOCUMENT_EQUATION_ARTIFACT_COVERAGE_INCOMPLETE",
                message=(
                    f"document {document_id!r} equation artifact coverage is "
                    f"incomplete: TeX display math blocks "
                    f"{coverage.get('tex_display_math_blocks')}, candidates "
                    f"{coverage.get('equation_candidate_count')}, records "
                    f"{coverage.get('equation_record_count')}; reasons "
                    f"{coverage.get('review_reasons')}"
                ),
                artifact="equation_semantics",
                path="$.completeness.equation_artifact_coverage",
                source_stage="export_validation",
            ))
        return result

    @staticmethod
    def _refresh_equation_artifact_coverage(
        report: dict,
        structure: dict,
        equations: Any,
        *,
        document_id: str,
    ) -> dict:
        """Recompute equation-artifact coverage on a precomputed report (#416).

        The orchestrator computes ``document_completeness`` before
        equation_semantics runs, so its coverage block is empty/stale. Here, where
        the equation artifacts exist, the coverage is recomputed and folded back
        into ``complete`` / ``review_reasons`` without disturbing the other checks.
        """
        if not isinstance(report, dict):
            return report
        try:
            from .completeness import analyze_equation_artifact_coverage
        except Exception:  # pragma: no cover - import resilience
            try:
                from core.document_pipeline.completeness import (
                    analyze_equation_artifact_coverage,
                )
            except Exception:
                return report
        metadata = structure.get("metadata") if isinstance(structure, dict) else {}
        pages_total = metadata.get("pages") if isinstance(metadata, dict) else None
        tex_source = None
        tex_inventory = None
        if isinstance(metadata, dict):
            tex_source = metadata.get("tex_source") or metadata.get("source_tex")
            # Issue #420: prefer the inventory persisted at ingest when the raw
            # source is not carried on the structure.
            tex_inventory = metadata.get("tex_equation_inventory")
        if tex_source is None and isinstance(structure, dict):
            tex_source = structure.get("tex_source") or structure.get("source_tex")
        coverage = analyze_equation_artifact_coverage(
            structure,
            equations if isinstance(equations, dict) else None,
            tex_source=tex_source,
            pages_total=pages_total,
            tex_inventory=tex_inventory,
        )
        report = dict(report)
        report["equation_artifact_coverage"] = coverage
        # Reconcile bidirectionally (#420): the orchestrator computes the report
        # before equation_semantics, so its coverage is stale (record_count=0 →
        # equation_artifact_coverage_incomplete). When the refreshed coverage is
        # now complete, the stale reason must be REMOVED and ``complete``
        # recomputed from the remaining reasons — otherwise a normal TeX document
        # stays permanently incomplete. Other failure reasons are preserved.
        reasons = [
            r for r in (report.get("review_reasons") or [])
            if r != "equation_artifact_coverage_incomplete"
        ]
        if not coverage.get("complete", True):
            reasons.append("equation_artifact_coverage_incomplete")
        report["review_reasons"] = reasons
        report["complete"] = not reasons
        return report

    def _aggregate_artifact_issues(
        self,
        artifacts: dict,
        errors: list,
        warnings: list,
        review_items: list,
    ) -> None:
        """Walk stage artifacts and collect their validation_issues."""
        for stage, artifact in artifacts.items():
            if stage.startswith("_") or not isinstance(artifact, dict):
                continue
            issues = artifact.get("validation_issues") or []
            for issue in issues:
                code = issue.get("rule_id") or issue.get("code") or "unknown"
                severity = issue.get("severity", "warning")
                message = issue.get("message", "")
                path = issue.get("field") or issue.get("path")
                if code in _FORCED_ERROR_RULE_IDS:
                    # Surface the root cause in the hard error itself: the
                    # exported ERROR log often only carries this message, while
                    # fallback_reason lives in the stage artifact diagnostics.
                    message = _append_fallback_diagnostics(message, artifact)

                entry = ValidationEntry(
                    code=code,
                    message=message,
                    artifact=stage,
                    path=path,
                    source_stage=stage,
                    target_type=issue.get("target_type"),
                    target_id=issue.get("target_id"),
                )

                if code in _NEEDS_REVIEW_RULE_IDS:
                    review_items.append(entry)
                elif code in _FORCED_ERROR_RULE_IDS:
                    errors.append(entry)
                elif severity == _SEVERITY_ERROR and code not in _SOFT_ERROR_RULE_IDS and (
                    stage in _HARD_ERROR_STAGES or code in _HARD_ERROR_RULE_IDS
                ):
                    errors.append(entry)
                elif severity == _SEVERITY_ERROR:
                    # errors from soft stages downgraded to warnings
                    warnings.append(entry)
                else:
                    warnings.append(entry)

    def _cross_validate_component_ids(
        self,
        component_result,
        claim_objects,
        evidence,
        dsl,
        errors: list,
        warnings: list,
    ) -> None:
        """Verify Component evidence_refs reference only real artifact IDs."""
        known_claim_ids: set[str] = {
            c.claim_id
            for c in (getattr(claim_objects, "claims", []) or [])
        }
        claim_by_id = {
            c.claim_id: c
            for c in (getattr(claim_objects, "claims", []) or [])
        }
        known_evidence_ids: set[str] = {
            r.evidence_id
            for r in (getattr(evidence, "records", []) or [])
        }
        known_eq_ids: set[str] = set()
        known_dsl_node_ids: set[str] = set()
        known_dsl_edge_ids: set[str] = set()
        if dsl:
            known_dsl_node_ids = {n.node_id for n in (dsl.nodes or [])}
            known_dsl_edge_ids = {e.edge_id for e in (dsl.edges or [])}

        for component in getattr(component_result, "components", []) or []:
            comp_id = component.component_id
            refs = component.evidence_refs or {}

            primary_claim_ids = _ordered_unique(
                list(refs.get("claim_ids") or [])
                + list(getattr(component, "linked_claim_ids", []) or [])
                + list(getattr(component, "supports_claim_ids", []) or [])
            )
            for cid in primary_claim_ids:
                if known_claim_ids and cid not in known_claim_ids:
                    errors.append(ValidationEntry(
                        code="UNRESOLVED_COMPONENT_CLAIM_ID",
                        message=f"component {comp_id!r} references missing claim {cid!r}",
                        artifact="component_assembly",
                        path=f"$.components[{comp_id}].evidence_refs.claim_ids",
                        source_stage="export_validation",
                    ))
                claim = claim_by_id.get(cid)
                if claim and _claim_is_non_atomic(claim):
                    errors.append(ValidationEntry(
                        code="NON_ATOMIC_CLAIM_USED_AS_COMPONENT_SUPPORT",
                        message=(
                            f"component {comp_id!r} uses non-atomic claim {cid!r} "
                            f"({getattr(claim, 'atomicity', '')}) as primary claim support"
                        ),
                        artifact="component_assembly",
                        path=f"$.components[{comp_id}].evidence_refs.claim_ids",
                        source_stage="export_validation",
                    ))

            for eid in refs.get("evidence_ids") or []:
                if known_evidence_ids and eid not in known_evidence_ids:
                    errors.append(ValidationEntry(
                        code="UNRESOLVED_COMPONENT_EVIDENCE_ID",
                        message=f"component {comp_id!r} references missing evidence {eid!r}",
                        artifact="component_assembly",
                        path=f"$.components[{comp_id}].evidence_refs.evidence_ids",
                        source_stage="export_validation",
                    ))

            dsl_refs = refs.get("dsl_refs") or {}
            dsl_node_refs = _ordered_unique(
                list(dsl_refs.get("node_ids") or [])
                + list(getattr(component, "linked_dsl_node_ids", []) or [])
            )
            for nid in dsl_node_refs:
                if nid not in known_dsl_node_ids:
                    errors.append(ValidationEntry(
                        code="UNRESOLVED_DSL_NODE_ID",
                        message=f"component {comp_id!r} references missing DSL node {nid!r}",
                        artifact="component_assembly",
                        path=f"$.components[{comp_id}].evidence_refs.dsl_refs.node_ids",
                        source_stage="export_validation",
                        target_type="dsl_node",
                        target_id=str(nid),
                    ))
            dsl_edge_refs = _ordered_unique(
                list(dsl_refs.get("edge_ids") or [])
                + list(getattr(component, "linked_dsl_edge_ids", []) or [])
            )
            for eid in dsl_edge_refs:
                if eid not in known_dsl_edge_ids:
                    errors.append(ValidationEntry(
                        code="UNRESOLVED_DSL_EDGE_ID",
                        message=f"component {comp_id!r} references missing DSL edge {eid!r}",
                        artifact="component_assembly",
                        path=f"$.components[{comp_id}].evidence_refs.dsl_refs.edge_ids",
                        source_stage="export_validation",
                        target_type="dsl_edge",
                        target_id=str(eid),
                    ))

            # Warn on summary-only (no claim or evidence)
            if (
                not refs.get("claim_ids")
                and not refs.get("evidence_ids")
            ):
                warnings.append(ValidationEntry(
                    code="SUMMARY_ONLY_COMPONENT",
                    message=f"component {comp_id!r} has no claim_ids or evidence_ids",
                    artifact="component_assembly",
                    path=f"$.components[{comp_id}].evidence_refs",
                    source_stage="export_validation",
                ))

    def _cross_validate_component_equation_confidence(
        self,
        component_result,
        artifacts: dict,
        errors: list,
        warnings: list,
    ) -> None:
        """Propagate equation confidence/review flags into Component validation."""
        equation_index = self._equation_index_from_artifacts(artifacts)
        if not equation_index:
            return

        for component in getattr(component_result, "components", []) or []:
            comp_id = getattr(component, "component_id", "?")
            refs = getattr(component, "evidence_refs", {}) or {}
            claim_linked = bool(refs.get("claim_ids") or getattr(component, "linked_claim_ids", []) or [])
            output_eqs = [str(e) for e in (getattr(component, "output_equation_ids", []) or []) if str(e)]
            review_required_eqs = {
                str(e) for e in (getattr(component, "review_required_equation_ids", []) or []) if str(e)
            }
            review_status = str(getattr(component, "review_status", "") or "")
            all_eqs = self._component_equation_refs(component, refs)

            for eq_id in all_eqs:
                eq = equation_index.get(eq_id)
                if not eq:
                    continue
                policy = eq.get("confidence_policy") if isinstance(eq.get("confidence_policy"), dict) else {}
                if self._equation_blocks_claim_and_derivation(eq):
                    warnings.append(ValidationEntry(
                        code="DOWNSTREAM_BLOCKED_EQUATION_IN_COMPONENT",
                        message=(
                            f"component {comp_id!r} references equation {eq_id!r}, "
                            "but the equation cannot support claims or derivations"
                        ),
                        artifact="component_assembly",
                        path=f"$.components[{comp_id}].confidence_gate",
                        source_stage="export_validation",
                    ))
                    if getattr(component, "review_status", "") in ("source_backed", "auto_accepted", "teacher_reviewed"):
                        errors.append(ValidationEntry(
                            code="SOURCE_BACKED_COMPONENT_USES_BLOCKED_EQUATION",
                            message=(
                                f"component {comp_id!r} cannot be source_backed/publish-ready "
                                f"because equation {eq_id!r} is blocked by confidence policy"
                            ),
                            artifact="component_assembly",
                            path=f"$.components[{comp_id}].review_status",
                            source_stage="export_validation",
                        ))
                if claim_linked and policy.get("can_support_claim") is False:
                    warnings.append(ValidationEntry(
                        code="NON_SUPPORTING_EQUATION_USED_FOR_CLAIM_SUPPORT",
                        message=(
                            f"component {comp_id!r} links claim evidence to equation {eq_id!r}, "
                            "but equation.confidence_policy.can_support_claim is false"
                        ),
                        artifact="component_assembly",
                        path=f"$.components[{comp_id}].linked_equation_ids",
                        source_stage="export_validation",
                    ))
                    if self._component_equation_violation_is_hard(review_status, eq_id, review_required_eqs):
                        errors.append(ValidationEntry(
                            code="NON_SUPPORTING_EQUATION_USED_FOR_CLAIM_SUPPORT",
                            message=(
                                f"component {comp_id!r} cannot use equation {eq_id!r} "
                                "as claim support because equation.confidence_policy.can_support_claim is false"
                            ),
                            artifact="component_assembly",
                            path=f"$.components[{comp_id}].linked_equation_ids",
                            source_stage="export_validation",
                        ))
                if claim_linked and self._equation_requires_review(eq):
                    warnings.append(ValidationEntry(
                        code="REVIEW_REQUIRED_EQUATION_USED_FOR_CLAIM_SUPPORT",
                        message=(
                            f"component {comp_id!r} links claim evidence to equation {eq_id!r}, "
                            "but equation requires review and cannot be accepted as claim support"
                        ),
                        artifact="component_assembly",
                        path=f"$.components[{comp_id}].linked_equation_ids",
                        source_stage="export_validation",
                    ))
                if self._equation_requires_review(eq) and eq_id not in review_required_eqs:
                    warnings.append(ValidationEntry(
                        code="REVIEW_REQUIRED_EQUATION_NOT_MARKED_ON_COMPONENT",
                        message=(
                            f"component {comp_id!r} references review-required equation {eq_id!r} "
                            "without listing it in review_required_equation_ids"
                        ),
                        artifact="component_assembly",
                        path=f"$.components[{comp_id}].review_required_equation_ids",
                        source_stage="export_validation",
                    ))

            for eq_id in output_eqs:
                eq = equation_index.get(eq_id)
                if not eq:
                    continue
                policy = eq.get("confidence_policy") if isinstance(eq.get("confidence_policy"), dict) else {}
                if policy.get("can_support_claim") is False:
                    warnings.append(ValidationEntry(
                        code="NON_SUPPORTING_EQUATION_USED_AS_COMPONENT_OUTPUT",
                        message=(
                            f"component {comp_id!r} uses equation {eq_id!r} as output, "
                            "but equation.confidence_policy.can_support_claim is false"
                        ),
                        artifact="component_assembly",
                        path=f"$.components[{comp_id}].output_equation_ids",
                        source_stage="export_validation",
                    ))
                    if self._component_equation_violation_is_hard(review_status, eq_id, review_required_eqs):
                        errors.append(ValidationEntry(
                            code="NON_SUPPORTING_EQUATION_USED_AS_COMPONENT_OUTPUT",
                            message=(
                                f"component {comp_id!r} cannot publish equation {eq_id!r} as an output "
                                "because equation.confidence_policy.can_support_claim is false"
                            ),
                            artifact="component_assembly",
                            path=f"$.components[{comp_id}].output_equation_ids",
                            source_stage="export_validation",
                        ))
                if self._equation_requires_review(eq) and getattr(component, "review_status", "") == "auto_accepted":
                    errors.append(ValidationEntry(
                        code="REVIEW_REQUIRED_OUTPUT_COMPONENT_AUTO_ACCEPTED",
                        message=(
                            f"component {comp_id!r} has review-required output equation {eq_id!r} "
                            "but component.review_status is auto_accepted"
                        ),
                        artifact="component_assembly",
                        path=f"$.components[{comp_id}].review_status",
                        source_stage="export_validation",
                    ))

    def _check_deterministic_fallback_components(
        self, component_result, errors: list
    ) -> None:
        """Hard-block deterministic-fallback components from persist (#347).

        Fallback components (maturity_source="deterministic_fallback") are
        conservative placeholders emitted when LLM component assembly failed.
        They may remain in the stage artifact for debugging, but must never be
        persisted as regular theory_components. Checking the components
        directly (not just the artifact validation_issues) also covers resumed
        runs that reloaded the component_assembly artifact.
        """
        fallback_components = [
            c for c in (getattr(component_result, "components", []) or [])
            if str(getattr(c, "maturity_source", "") or "") == "deterministic_fallback"
        ]
        if not fallback_components:
            return
        if any(e.code == "component_assembly_deterministic_fallback" for e in errors):
            return  # already escalated from the stage artifact's validation_issues
        comp_ids = [getattr(c, "component_id", "?") for c in fallback_components]
        reason = str(getattr(fallback_components[0], "fallback_reason", "") or "unknown")
        errors.append(ValidationEntry(
            code="component_assembly_deterministic_fallback",
            message=(
                f"{len(fallback_components)} deterministic-fallback component(s) "
                f"present ({', '.join(comp_ids[:5])}{'…' if len(comp_ids) > 5 else ''}); "
                f"fallback_reason={reason!r}; rerun component_assembly instead of persisting"
            ),
            artifact="component_assembly",
            path="$.components[*].maturity_source",
            source_stage="export_validation",
        ))

    def _check_component_internal_flow(self, component_result, errors: list) -> None:
        """Derivation-like components must expose their internal flow before export."""
        required_types = {
            "RelationComponent",
            "PaperRelationComponent",
            "CorrectionComponent",
            "DiagnosticComponent",
            "MethodComponent",
        }
        for component in getattr(component_result, "components", []) or []:
            comp_type = getattr(component, "component_type", "")
            if comp_type not in required_types:
                continue
            if getattr(component, "internal_flow", []) or []:
                continue
            comp_id = getattr(component, "component_id", "?")
            errors.append(ValidationEntry(
                code="COMPONENT_MISSING_INTERNAL_FLOW",
                message=(
                    f"component {comp_id!r} ({comp_type}) has no internal_flow; "
                    "publish-ready exports require explicit component wiring"
                ),
                artifact="component_assembly",
                path=f"$.components[{comp_id}].internal_flow",
                source_stage="export_validation",
            ))

    def _check_summary_only_derivation_components(
        self, component_result, errors: list
    ) -> None:
        """Block export when summary-only components remain on the main derivation path.

        Issue #300, acceptance criterion #10: a component on the main derivation
        path (Relation/PaperRelation/Method) that carries no equations, no
        internal_flow, and no declared operation is an explanation-level summary,
        not a reusable theory operation, and must not be exported.
        """
        derivation_path_types = {
            "RelationComponent",
            "PaperRelationComponent",
            "MethodComponent",
        }
        for component in getattr(component_result, "components", []) or []:
            comp_type = getattr(component, "component_type", "")
            if comp_type not in derivation_path_types:
                continue
            has_equations = bool(self._component_equation_refs(
                component, getattr(component, "evidence_refs", {}) or {}
            ))
            has_flow = bool(getattr(component, "internal_flow", []) or [])
            has_operation = bool(str(getattr(component, "operation", "") or "").strip())
            if not has_equations and not has_flow and not has_operation:
                comp_id = getattr(component, "component_id", "?")
                errors.append(ValidationEntry(
                    code="SUMMARY_ONLY_COMPONENT_IN_DERIVATION_PATH",
                    message=(
                        f"component {comp_id!r} ({comp_type}) is on the main derivation "
                        "path but is summary-only (no equations, internal_flow, or operation); "
                        "split it into theory-operation components before export"
                    ),
                    artifact="component_assembly",
                    path=f"$.components[{comp_id}]",
                    source_stage="export_validation",
                ))

    def _check_component_quality(
        self,
        component_result,
        artifacts: dict,
        warnings: list,
        review_items: list,
    ) -> list[ComponentQualityEntry]:
        quality: list[ComponentQualityEntry] = []
        for component in getattr(component_result, "components", []) or []:
            comp_id = getattr(component, "component_id", "?")
            raw = getattr(component, "component_quality", {}) or {}
            if not isinstance(raw, dict):
                raw = {}
            split = getattr(component, "split_recommendation", {}) or {}
            status = str(raw.get("granularity_status") or "good")
            split_reasons = list(raw.get("split_reasons") or split.get("reasons") or [])
            suggested = list(raw.get("suggested_split") or split.get("suggested_components") or [])
            entry = ComponentQualityEntry(
                component_id=str(comp_id),
                granularity_status=status,
                equation_count=int(raw.get("equation_count") or 0),
                claim_count=int(raw.get("claim_count") or 0),
                derivation_step_count=int(raw.get("derivation_step_count") or 0),
                responsibility_count=int(raw.get("responsibility_count") or 0),
                source_scope_width=int(raw.get("source_scope_width") or 0),
                split_required=bool(raw.get("split_required") or split.get("required")),
                split_reasons=split_reasons,
                suggested_split=suggested,
            )
            quality.append(entry)

            if status in {"too_coarse", "mixed_responsibility"}:
                warnings.append(ValidationEntry(
                    code="COMPONENT_GRANULARITY_ISSUE",
                    message=(
                        f"component {comp_id!r} is {status}; ID links may be valid "
                        "but the component is structurally too broad"
                    ),
                    artifact="component_assembly",
                    path=f"$.components[{comp_id}].component_quality",
                    source_stage="export_validation",
                ))
            elif status == "too_fine":
                warnings.append(ValidationEntry(
                    code="COMPONENT_TOO_FINE",
                    message=f"component {comp_id!r} has too little evidence or structure to stand alone",
                    artifact="component_assembly",
                    path=f"$.components[{comp_id}].component_quality",
                    source_stage="export_validation",
                ))
            elif status == "review_required":
                review_items.append(ValidationEntry(
                    code="COMPONENT_QUALITY_REVIEW_REQUIRED",
                    message=f"component {comp_id!r} requires review before publish-ready export",
                    artifact="component_assembly",
                    path=f"$.components[{comp_id}].component_quality",
                    source_stage="export_validation",
                ))
        return quality

    def _check_component_refinement(
        self,
        component_result,
        errors: list,
        warnings: list,
        review_items: list,
    ) -> dict:
        """Report ComponentRefiner Step 3 results (issue #324).

        Reads the ``component_refinement`` contract emitted by ComponentRefiner
        and surfaces unassigned links / teaching warnings as warnings,
        review-required refinements as review items, and dangling component refs
        / failed refinements as hard errors (the graph must not silently
        reference removed component IDs).
        """
        refinement = getattr(component_result, "component_refinement", {}) or {}
        if not isinstance(refinement, dict):
            return _empty_refinement_validation()
        validation = refinement.get("refinement_validation")
        if not isinstance(validation, dict):
            return _empty_refinement_validation()

        result = _empty_refinement_validation()
        result.update({k: validation.get(k, v) for k, v in result.items()})

        for original_id in result["failed_refinements"]:
            errors.append(ValidationEntry(
                code="COMPONENT_REFINEMENT_FAILED",
                message=f"component {original_id!r} refinement failed; no child components produced",
                artifact="component_assembly",
                path=f"$.component_refinement[{original_id}]",
                source_stage="export_validation",
                target_type="component",
                target_id=str(original_id),
            ))

        # A component whose split was required but was left unprocessed (neither
        # split, failed, nor review_required) is a silent contract violation
        # (#421): it must be a hard error so it can never reach publish-ready.
        for original_id in result["unprocessed_split_required"]:
            errors.append(ValidationEntry(
                code="COMPONENT_REFINEMENT_REQUIRED_BUT_UNPROCESSED",
                message=(
                    f"component {original_id!r} requires a split but was left "
                    "unprocessed (not split, failed, or review_required); it "
                    "cannot be published in this state"
                ),
                artifact="component_assembly",
                path="$.component_refinement.refinement_validation.unprocessed_split_required",
                source_stage="export_validation",
                target_type="component",
                target_id=str(original_id),
            ))

        for entry in result["dangling_component_refs"]:
            ref = entry.get("missing_ref") if isinstance(entry, dict) else entry
            owner = entry.get("component_id") if isinstance(entry, dict) else "?"
            errors.append(ValidationEntry(
                code="COMPONENT_REFINEMENT_DANGLING_REF",
                message=(
                    f"refined component {owner!r} references missing component {ref!r} "
                    "after refinement"
                ),
                artifact="component_assembly",
                path=f"$.component_refinement.component_graph_updates.unresolved_edges",
                source_stage="export_validation",
                target_type="component",
                target_id=str(owner),
            ))

        for entry in result["unassigned_links"]:
            link_id = entry.get("link_id") if isinstance(entry, dict) else entry
            link_type = entry.get("link_type") if isinstance(entry, dict) else "link"
            warnings.append(ValidationEntry(
                code="COMPONENT_REFINEMENT_UNASSIGNED_LINK",
                message=f"refinement could not assign {link_type} {link_id!r} to a child component",
                artifact="component_assembly",
                path="$.component_refinement.refinement_validation.unassigned_links",
                source_stage="export_validation",
            ))

        for entry in result["teaching_granularity_warnings"]:
            comp_id = entry.get("component_id") if isinstance(entry, dict) else entry
            warnings.append(ValidationEntry(
                code="COMPONENT_REFINEMENT_TEACHING_GRANULARITY",
                message=f"refined component {comp_id!r} is still too dense to teach as a single unit",
                artifact="component_assembly",
                path="$.component_refinement.refinement_validation.teaching_granularity_warnings",
                source_stage="export_validation",
            ))

        for original_id in result["review_required_refinements"]:
            review_items.append(ValidationEntry(
                code="COMPONENT_REFINEMENT_REVIEW_REQUIRED",
                message=f"component {original_id!r} refinement requires review before publish-ready export",
                artifact="component_assembly",
                path=f"$.component_refinement[{original_id}]",
                source_stage="export_validation",
                target_type="component",
                target_id=str(original_id),
            ))

        return result

    def _check_derivation_graph_alignment(
        self,
        component_result,
        errors: list,
        warnings: list,
        review_items: list,
    ) -> dict:
        """Aggregate DerivationGraphAligner Step 4 results (issue #325).

        Reads the ``derivation_graph_alignment`` contract emitted by
        DerivationGraphAligner and folds its pre-computed errors / warnings /
        review items into the export buckets. The alignment logic lives in the
        aligner; this method only aggregates and re-codes the entries.
        """
        alignment = getattr(component_result, "derivation_graph_alignment", {}) or {}
        if not isinstance(alignment, dict):
            return _empty_derivation_graph_alignment()
        block = alignment.get("export_validation")
        if not isinstance(block, dict):
            return _empty_derivation_graph_alignment()

        result = _empty_derivation_graph_alignment()
        result.update({k: block.get(k, v) for k, v in result.items()})

        for entry in result["errors"]:
            errors.append(ValidationEntry(
                code=f"DERIVATION_GRAPH_ALIGNMENT_{_alignment_code(entry)}",
                message=_alignment_message(entry),
                artifact="component_assembly",
                path="$.derivation_graph_alignment",
                source_stage="export_validation",
            ))
        for entry in result["warnings"]:
            warnings.append(ValidationEntry(
                code=f"DERIVATION_GRAPH_ALIGNMENT_{_alignment_code(entry)}",
                message=_alignment_message(entry),
                artifact="component_assembly",
                path="$.derivation_graph_alignment",
                source_stage="export_validation",
            ))
        for entry in result["review_items"]:
            review_items.append(ValidationEntry(
                code=f"DERIVATION_GRAPH_ALIGNMENT_{_alignment_code(entry)}",
                message=_alignment_message(entry),
                artifact="component_assembly",
                path="$.derivation_graph_alignment",
                source_stage="export_validation",
            ))

        return result

    def _check_theory_bundle(
        self,
        component_result,
        errors: list,
        warnings: list,
        review_items: list,
    ) -> dict:
        """Validate the TheoryBundle Step 5 container (issue #326).

        Reads ``component_result.theory_bundle.theory_bundle`` and re-validates
        its component references against the refined components so dangling refs
        surface here regardless of what the stage pre-computed. Aggregates the
        stage's own errors/warnings/review items as well.
        """
        bundle_block = getattr(component_result, "theory_bundle", {}) or {}
        if not isinstance(bundle_block, dict) or not bundle_block:
            return _empty_theory_bundle_validation()
        bundle = bundle_block.get("theory_bundle")
        if not isinstance(bundle, dict) or not bundle:
            return _empty_theory_bundle_validation()

        known_ids = {
            c.component_id for c in (getattr(component_result, "components", []) or [])
        }

        result = _empty_theory_bundle_validation()
        stage_block = bundle_block.get("theory_bundle_validation")
        if isinstance(stage_block, dict):
            for key in ("bundle_created", "headline_claim_linked", "support_map_linked"):
                if key in stage_block:
                    result[key] = stage_block[key]

        # Authoritative ref re-validation (independent of stage booleans).
        dangling = [
            cid for cid in bundle.get("component_ids", []) or [] if cid not in known_ids
        ]
        result["component_refs_valid"] = not dangling
        for cid in dangling:
            errors.append(ValidationEntry(
                code="THEORY_BUNDLE_DANGLING_COMPONENT_REF",
                message=f"theory bundle references missing component {cid!r}",
                artifact="component_assembly",
                path="$.theory_bundle.theory_bundle.component_ids",
                source_stage="export_validation",
            ))

        if not result["headline_claim_linked"]:
            warnings.append(ValidationEntry(
                code="THEORY_BUNDLE_HEADLINE_CLAIM_MISSING",
                message="theory bundle is not linked to a headline claim",
                artifact="component_assembly",
                path="$.theory_bundle.theory_bundle.headline_claim_id",
                source_stage="export_validation",
            ))
        if not result["support_map_linked"]:
            warnings.append(ValidationEntry(
                code="THEORY_BUNDLE_SUPPORT_MAP_MISSING",
                message="theory bundle is not linked to a renderable support map",
                artifact="component_assembly",
                path="$.theory_bundle.theory_bundle.support_map_id",
                source_stage="export_validation",
            ))
        if str(bundle.get("review_status")) == "review_required":
            review_items.append(ValidationEntry(
                code="THEORY_BUNDLE_REVIEW_REQUIRED",
                message="theory bundle requires review before publish-ready export",
                artifact="component_assembly",
                path="$.theory_bundle.theory_bundle.review_status",
                source_stage="export_validation",
            ))
        return result

    def _check_teaching_output(
        self,
        component_result,
        errors: list,
        warnings: list,
        review_items: list,
    ) -> dict:
        """Validate the TeachingOutput Step 5 mapping (issue #326).

        Re-validates course topic / blueprint component references against the
        refined components and verifies each topic's ``blackbox_policy`` covers
        the low-confidence equations of its linked components (equation
        confidence must be respected before teaching).
        """
        bundle_block = getattr(component_result, "theory_bundle", {}) or {}
        if not isinstance(bundle_block, dict) or not bundle_block:
            return _empty_teaching_output_validation()
        teaching = bundle_block.get("course_mapping")
        blueprint = bundle_block.get("blueprint_updates") or {}
        if not isinstance(teaching, dict) or not teaching:
            return _empty_teaching_output_validation()

        components = getattr(component_result, "components", []) or []
        known_ids = {c.component_id for c in components}
        component_by_id = {c.component_id: c for c in components}

        result = _empty_teaching_output_validation()
        topics_link = True
        blackbox_ok = True

        for topic in teaching.get("topics", []) or []:
            if not isinstance(topic, dict):
                continue
            topic_id = topic.get("topic_id")
            linked = topic.get("linked_component_ids") or []
            resolved = [cid for cid in linked if cid in known_ids]
            if not resolved:
                topics_link = False
                errors.append(ValidationEntry(
                    code="TEACHING_OUTPUT_TOPIC_LINKS_NO_COMPONENT",
                    message=f"course topic {topic_id!r} links no refined component",
                    artifact="course_mapping",
                    path="$.theory_bundle.course_mapping.topics",
                    source_stage="export_validation",
                ))
            for cid in linked:
                if cid not in known_ids:
                    topics_link = False
                    errors.append(ValidationEntry(
                        code="TEACHING_OUTPUT_DANGLING_COMPONENT_REF",
                        message=(
                            f"course topic {topic_id!r} references missing component {cid!r}"
                        ),
                        artifact="course_mapping",
                        path="$.theory_bundle.course_mapping.topics",
                        source_stage="export_validation",
                    ))

            policy_eq_ids = {
                str(p.get("equation_id"))
                for p in topic.get("blackbox_policy") or []
                if isinstance(p, dict) and p.get("equation_id")
            }
            for cid in resolved:
                for eq_id in _component_low_confidence_equation_ids(component_by_id[cid]):
                    if eq_id not in policy_eq_ids:
                        blackbox_ok = False
                        errors.append(ValidationEntry(
                            code="TEACHING_OUTPUT_BLACKBOX_POLICY_IGNORES_CONFIDENCE",
                            message=(
                                f"course topic {topic_id!r} does not blackbox "
                                f"low-confidence equation {eq_id!r}"
                            ),
                            artifact="course_mapping",
                            path="$.theory_bundle.course_mapping.topics",
                            source_stage="export_validation",
                        ))

            if str(topic.get("review_status")) == "review_required":
                review_items.append(ValidationEntry(
                    code="TEACHING_OUTPUT_TOPIC_REVIEW_REQUIRED",
                    message=f"course topic {topic_id!r} contains a review_required component",
                    artifact="course_mapping",
                    path="$.theory_bundle.course_mapping.topics",
                    source_stage="export_validation",
                ))

        blueprint_refs_valid = True
        for cid in blueprint.get("linked_component_ids", []) or []:
            if cid not in known_ids:
                blueprint_refs_valid = False
                errors.append(ValidationEntry(
                    code="TEACHING_OUTPUT_BLUEPRINT_DANGLING_REF",
                    message=f"blueprint references missing component {cid!r}",
                    artifact="blueprint",
                    path="$.theory_bundle.blueprint_updates.linked_component_ids",
                    source_stage="export_validation",
                ))

        # Combine the gate's own (component-field) checks with the stage's
        # equation-policy-aware booleans: a property holds only if both agree.
        stage_block = bundle_block.get("teaching_output_validation") or {}
        result["course_topics_link_components"] = topics_link and bool(
            stage_block.get("course_topics_link_components", True)
        )
        result["blackbox_policy_respects_confidence"] = blackbox_ok and bool(
            stage_block.get("blackbox_policy_respects_confidence", True)
        )
        result["blueprint_refs_valid"] = blueprint_refs_valid and bool(
            stage_block.get("blueprint_refs_valid", True)
        )
        return result

    def _cross_validate_course_mapping(
        self,
        course_mapping,
        component_result,
        errors: list,
        warnings: list,
    ) -> None:
        """Verify CourseMapping topics reference real component IDs (#418)."""
        components = list(getattr(component_result, "components", []) or [])
        known_component_ids: set[str] = {c.component_id for c in components}
        # Per-component derivation links, used to verify topic→derivation relevance.
        component_derivations: dict[str, set[str]] = {
            c.component_id: {str(d) for d in (getattr(c, "linked_derivation_ids", []) or [])}
            for c in components
        }

        def _topic_attr(topic, name):
            if isinstance(topic, dict):
                return topic.get(name) or []
            return getattr(topic, name, []) or []

        topics = getattr(course_mapping, "topics", []) or []
        if isinstance(topics, list):
            for idx, topic in enumerate(topics):
                linked = _topic_attr(topic, "linked_component_ids")
                for comp_id in linked:
                    if known_component_ids and comp_id not in known_component_ids:
                        errors.append(ValidationEntry(
                            code="UNRESOLVED_COMPONENT_ID",
                            message=f"course topic [{idx}] references missing component {comp_id!r}",
                            artifact="course_mapping",
                            path=f"$.topics[{idx}].linked_component_ids",
                            source_stage="export_validation",
                            target_type="component",
                            target_id=str(comp_id),
                        ))
                # Issue #418: a topic may only link derivations that belong to one
                # of its linked components; an unrelated derivation link is flagged.
                relevant_derivations: set[str] = set()
                for comp_id in linked:
                    relevant_derivations |= component_derivations.get(comp_id, set())
                for der_id in _topic_attr(topic, "linked_derivation_ids"):
                    if str(der_id) not in relevant_derivations:
                        warnings.append(ValidationEntry(
                            code="COURSE_TOPIC_UNRELATED_DERIVATION",
                            message=(
                                f"course topic [{idx}] links derivation {der_id!r} that is "
                                "not connected to any of the topic's components"
                            ),
                            artifact="course_mapping",
                            path=f"$.topics[{idx}].linked_derivation_ids",
                            source_stage="export_validation",
                            target_type="derivation",
                            target_id=str(der_id),
                        ))

    @staticmethod
    def _component_equation_refs(component, refs: dict) -> list[str]:
        values: list[str] = []
        values.extend(refs.get("equation_ids") or [])
        for field_name in (
            "linked_equation_ids",
            "input_equation_ids",
            "intermediate_equation_ids",
            "output_equation_ids",
            "constraint_equation_ids",
            "definition_equation_ids",
            "review_required_equation_ids",
        ):
            values.extend(getattr(component, field_name, []) or [])
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            eq_id = str(value)
            if eq_id and eq_id not in seen:
                seen.add(eq_id)
                result.append(eq_id)
        return result

    @staticmethod
    def _equation_index_from_artifacts(artifacts: dict) -> dict[str, dict]:
        raw = artifacts.get("equation_semantics") or {}
        if not isinstance(raw, dict):
            return {}
        records = raw.get("equations") or []
        if not isinstance(records, list):
            return {}
        index: dict[str, dict] = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            eq_id = str(record.get("equation_id") or "")
            if eq_id:
                index[eq_id] = record
        return index

    @staticmethod
    def _equation_requires_review(eq: dict) -> bool:
        policy = eq.get("confidence_policy") if isinstance(eq.get("confidence_policy"), dict) else {}
        reconstruction = eq.get("reconstruction") if isinstance(eq.get("reconstruction"), dict) else {}
        consistency = eq.get("equation_consistency") if isinstance(eq.get("equation_consistency"), dict) else {}
        return (
            bool(eq.get("needs_math_review"))
            or bool(eq.get("review_flags"))
            or bool(consistency.get("review_required"))
            or consistency.get("raw_text_latex_match") == "mismatch"
            or consistency.get("label_location_match") == "mismatch"
            or consistency.get("source_span_quality") == "corrupted"
            or eq.get("semantic_status") == "reconstruction_based"
            or reconstruction.get("status") not in (None, "", "none")
            or bool(policy.get("must_not_treat_as_source_extracted"))
            or policy.get("can_support_claim") is False
        )

    @staticmethod
    def _component_equation_violation_is_hard(
        review_status: str,
        eq_id: str,
        review_required_eqs: set[str],
    ) -> bool:
        """Only review-marked components may carry non-supporting equations past the gate."""
        if review_status in {"source_backed", "auto_accepted", "teacher_reviewed"}:
            return True
        return eq_id not in review_required_eqs

    @staticmethod
    def _equation_blocks_claim_and_derivation(eq: dict) -> bool:
        policy = eq.get("confidence_policy") if isinstance(eq.get("confidence_policy"), dict) else {}
        if policy.get("can_support_claim") is False and policy.get("can_be_used_in_derivation") is False:
            return True
        src = eq.get("source_extraction") if isinstance(eq.get("source_extraction"), dict) else {}
        rec = eq.get("reconstruction") if isinstance(eq.get("reconstruction"), dict) else {}
        latex = eq.get("latex")
        plain_text = eq.get("plain_text")
        if latex is None:
            latex = rec.get("latex") if rec.get("status") not in (None, "", "none") else src.get("latex")
        if plain_text is None:
            plain_text = rec.get("plain_text") if rec.get("status") not in (None, "", "none") else src.get("plain_text")
        extraction_status = eq.get("extraction_status") or src.get("extraction_status")
        return (
            latex is None
            and plain_text is None
            and extraction_status in ("partial", "fragment_only", "label_only", "missing", "unparsed")
        )

    # Provisional / pre-canonical claim ID patterns (issue #340). Mirrors the
    # export-side _LEGACY_REF_PATTERNS so the pipeline catches the same leaks.
    _PROVISIONAL_CLAIM_REF_PATTERNS = (
        re.compile(r"^claim_span_"),
        re.compile(r"^claim:[^:]+:[^:]+$"),
        re.compile(r"^claim::"),
    )

    @classmethod
    def _looks_provisional_claim_ref(cls, ref: str) -> bool:
        return any(p.match(ref) for p in cls._PROVISIONAL_CLAIM_REF_PATTERNS)

    def _check_unresolved_claim_refs(
        self,
        artifacts: dict,
        claim_objects,
        warnings: list,
    ) -> None:
        """Report claim refs in equations / derivations absent from claims.json (#340).

        The claim_object_builder stage is the source of truth for claim IDs. Any
        claim ref carried by equation_semantics or derivation_chain that is not in
        the final claim set (or that still looks provisional) indicates a broken
        artifact ID contract. These are surfaced as warnings so they show up in
        export_validation review metadata without blocking persist.
        """
        final_claim_ids = {
            str(getattr(c, "claim_id", "") or "")
            for c in (getattr(claim_objects, "claims", []) or [])
            if getattr(c, "claim_id", None)
        }

        def report(refs, artifact: str, path: str) -> None:
            for ref in refs or []:
                ref_id = str(ref)
                if not ref_id:
                    continue
                if ref_id in final_claim_ids:
                    continue
                provisional = self._looks_provisional_claim_ref(ref_id)
                warnings.append(ValidationEntry(
                    code=(
                        "PROVISIONAL_CLAIM_REF_IN_ARTIFACT"
                        if provisional
                        else "UNRESOLVED_CLAIM_REF_IN_ARTIFACT"
                    ),
                    message=(
                        f"{path} references claim {ref_id!r} which is not in the final "
                        "claims.json claim set"
                    ),
                    artifact=artifact,
                    path=path,
                    source_stage="export_validation",
                ))

        equations = artifacts.get("equation_semantics") or {}
        if isinstance(equations, dict):
            for idx, eq in enumerate(equations.get("equations") or []):
                if not isinstance(eq, dict):
                    continue
                report(
                    eq.get("linked_claim_ids"),
                    "equation_semantics",
                    f"$.equations[{idx}].linked_claim_ids",
                )

        derivations = artifacts.get("derivation_chain") or {}
        if isinstance(derivations, dict):
            for chain_idx, chain in enumerate(derivations.get("chains") or []):
                if not isinstance(chain, dict):
                    continue
                for step_idx, step in enumerate(chain.get("steps") or []):
                    if not isinstance(step, dict):
                        continue
                    for key in (
                        "required_claim_ids",
                        "input_claim_ids",
                        "output_claim_ids",
                        "claim_ids",
                    ):
                        report(
                            step.get(key),
                            "derivation_chain",
                            f"$.chains[{chain_idx}].steps[{step_idx}].{key}",
                        )

    def _check_derivation_equation_confidence(
        self,
        artifacts: dict,
        warnings: list,
        errors: list,
    ) -> None:
        equation_index = self._equation_index_from_artifacts(artifacts)
        derivations = artifacts.get("derivation_chain") or {}
        chains = derivations.get("chains") if isinstance(derivations, dict) else []
        if not isinstance(chains, list):
            return
        for chain_idx, chain in enumerate(chains):
            if not isinstance(chain, dict):
                continue
            for step_idx, step in enumerate(chain.get("steps") or []):
                if not isinstance(step, dict):
                    continue
                refs = list(step.get("input_equation_ids") or []) + list(step.get("output_equation_ids") or [])
                blocked = [
                    str(eq_id) for eq_id in refs
                    if self._equation_blocks_claim_and_derivation(equation_index.get(str(eq_id), {}))
                ]
                if not blocked:
                    continue
                path = f"$.chains[{chain_idx}].steps[{step_idx}].confidence_gate"
                warnings.append(ValidationEntry(
                    code="DOWNSTREAM_BLOCKED_EQUATION_IN_DERIVATION",
                    message=(
                        f"derivation step {step.get('step_id') or step_idx!r} references "
                        f"blocked equations {blocked}"
                    ),
                    artifact="derivation_chain",
                    path=path,
                    source_stage="export_validation",
                ))
                if step.get("review_status") in ("auto_accepted", "source_backed"):
                    errors.append(ValidationEntry(
                        code="PUBLISH_READY_DERIVATION_STEP_USES_BLOCKED_EQUATION",
                        message="derivation step cannot be publish-ready with blocked equation inputs/outputs",
                        artifact="derivation_chain",
                        path=f"$.chains[{chain_idx}].steps[{step_idx}].review_status",
                        source_stage="export_validation",
                    ))

    def _check_derivation_step_constraints(
        self,
        artifacts: dict,
        errors: list,
        review_items: list,
    ) -> None:
        """Enforce generic structural constraints on derivation steps (issue #433).

        Domain-agnostic invariants, asserting nothing about paper-specific chain
        structure:

          - ``DERIVATION_STEP_MISSING_ENDPOINT`` (hard error): a step with no
            input endpoint (equation or claim) or no output endpoint.
          - ``DERIVATION_STEP_DANGLING_EQUATION_REF`` (hard error): an
            input/output equation id absent from the equation registry.
          - ``DERIVATION_STEP_UNCONTROLLED_OPERATION``: an ``operation`` outside
            the controlled vocabulary. A hard error when the step is not flagged
            for review; a review item when it is (the domain-specific name should
            live in ``operation_subtype``).
          - ``DERIVATION_STEP_MISSING_EVIDENCE`` (review item): no
            ``source_evidence_id`` on the step.
        """
        raw = artifacts.get("derivation_chain")
        if not isinstance(raw, dict):
            return
        chains = raw.get("chains")
        if not isinstance(chains, list):
            return
        registry_ids = set(self._equation_index_from_artifacts(artifacts).keys())

        for chain in chains:
            if not isinstance(chain, dict):
                continue
            deriv_id = str(chain.get("derivation_id") or "")
            for step in chain.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                step_id = str(step.get("step_id") or "")
                target = f"{deriv_id}:{step_id}" if deriv_id else step_id
                base_path = f"$.chains[{deriv_id}].steps[{step_id}]"

                in_eq = [str(x) for x in (step.get("input_equation_ids") or []) if x]
                out_eq = [str(x) for x in (step.get("output_equation_ids") or []) if x]
                in_claim = [
                    str(x) for x in (
                        list(step.get("input_claim_ids") or [])
                        + list(step.get("required_claim_ids") or [])
                    ) if x
                ]
                out_claim = [str(x) for x in (step.get("output_claim_ids") or []) if x]

                # (a) endpoints — every step needs an input and an output side.
                if not (in_eq or in_claim) or not (out_eq or out_claim):
                    errors.append(ValidationEntry(
                        code="DERIVATION_STEP_MISSING_ENDPOINT",
                        message=(
                            f"derivation step {target!r} is missing an input or "
                            f"output endpoint (equation or claim)"
                        ),
                        artifact="derivation_chain",
                        path=base_path,
                        source_stage="export_validation",
                        target_type="derivation_step",
                        target_id=target,
                    ))

                # (b) dangling equation references.
                if registry_ids:
                    for ref in in_eq + out_eq:
                        if ref not in registry_ids:
                            errors.append(ValidationEntry(
                                code="DERIVATION_STEP_DANGLING_EQUATION_REF",
                                message=(
                                    f"derivation step {target!r} references "
                                    f"non-existent equation {ref!r}"
                                ),
                                artifact="derivation_chain",
                                path=base_path,
                                source_stage="export_validation",
                                target_type="derivation_step",
                                target_id=target,
                            ))

                # (c) controlled operation vocabulary.
                operation = str(step.get("operation") or "")
                if operation not in _DERIVATION_CONTROLLED_OPERATIONS:
                    flagged = (
                        str(step.get("review_status") or "") in _DERIVATION_REVIEW_STATES
                        or bool(step.get("review_reason"))
                    )
                    entry = ValidationEntry(
                        code="DERIVATION_STEP_UNCONTROLLED_OPERATION",
                        message=(
                            f"derivation step {target!r} operation "
                            f"{operation or 'unset'!r} is not in the controlled "
                            f"vocabulary; domain-specific names belong in "
                            f"operation_subtype"
                        ),
                        artifact="derivation_chain",
                        path=f"{base_path}.operation",
                        source_stage="export_validation",
                        target_type="derivation_step",
                        target_id=target,
                    )
                    if flagged:
                        review_items.append(entry)
                    else:
                        errors.append(entry)

                # (d) evidence presence.
                if not (step.get("source_evidence_ids") or []):
                    review_items.append(ValidationEntry(
                        code="DERIVATION_STEP_MISSING_EVIDENCE",
                        message=(
                            f"derivation step {target!r} has no source_evidence_id"
                        ),
                        artifact="derivation_chain",
                        path=f"{base_path}.source_evidence_ids",
                        source_stage="export_validation",
                        target_type="derivation_step",
                        target_id=target,
                    ))

    def _check_equation_consistency_mismatches(
        self,
        artifacts: dict,
        review_items: list,
        warnings: list,
    ) -> None:
        """List equation raw/latex/source consistency candidates in export_validation."""
        equation_index = self._equation_index_from_artifacts(artifacts)
        for eq_id, eq in sorted(equation_index.items()):
            consistency = eq.get("equation_consistency") if isinstance(eq.get("equation_consistency"), dict) else {}
            if not consistency:
                continue
            is_mismatch = (
                consistency.get("raw_text_latex_match") == "mismatch"
                or consistency.get("label_location_match") == "mismatch"
                or consistency.get("source_span_quality") == "corrupted"
            )
            is_review = bool(consistency.get("review_required"))
            if not is_mismatch and not is_review:
                continue
            entry = ValidationEntry(
                code="EQUATION_CONSISTENCY_MISMATCH" if is_mismatch else "EQUATION_CONSISTENCY_REVIEW_REQUIRED",
                message=(
                    f"equation {eq_id!r} has raw_text/latex/source consistency status "
                    f"{consistency.get('raw_text_latex_match')!r}, "
                    f"label_location={consistency.get('label_location_match')!r}, "
                    f"source_span={consistency.get('source_span_quality')!r}"
                ),
                artifact="equation_semantics",
                path=f"$.equations[{eq_id}].equation_consistency",
                source_stage="export_validation",
            )
            if is_mismatch:
                review_items.append(entry)
            else:
                warnings.append(entry)

    def _check_equation_fidelity(
        self,
        artifacts: dict,
        review_items: list,
        warnings: list,
    ) -> dict:
        """Aggregate equation reconstruction fidelity problems (issue #368).

        Scans the equation_semantics artifact (accepted equations + candidates)
        for the deterministic fidelity reason codes, producing per-type counts,
        the target equation/candidate ids and the artifact path for each. The
        underlying blocking (confidence_policy / equation_consistency) is decided
        upstream; this is the cross-artifact observability report.

        Codes that block confirmed downstream use are surfaced as review_items;
        normalized / removed values (invalid_equation_label,
        nonequation_id_in_links) are surfaced as warnings.
        """
        report = _empty_equation_fidelity()
        raw = artifacts.get("equation_semantics")
        if not isinstance(raw, dict):
            return report
        report["checked"] = True

        # code -> bucket ("review_items" | "warnings")
        bucket_for = {
            "latex_is_prose": "review_items",
            "table_derived_equation_candidate": "review_items",
            "symbol_loss_unverifiable": "review_items",
            "invalid_equation_label": "warnings",
            "nonequation_id_in_links": "warnings",
        }

        def _record(code: str, target_kind: str, target_id: str, path: str) -> None:
            if code not in report["issues"]:
                return
            report["issues"][code].append({
                target_kind: target_id,
                "artifact": "equation_semantics",
                "path": path,
            })
            report["issue_counts"][code] += 1
            report["total"] += 1
            entry = ValidationEntry(
                code=f"EQUATION_FIDELITY_{code.upper()}",
                message=f"equation_semantics {target_kind} {target_id!r}: {code}",
                artifact="equation_semantics",
                path=path,
                source_stage="export_validation",
            )
            if bucket_for.get(code) == "review_items":
                review_items.append(entry)
            else:
                warnings.append(entry)

        # --- accepted equations ---
        equations = raw.get("equations") or []
        if isinstance(equations, list):
            for eq in equations:
                if not isinstance(eq, dict):
                    continue
                eq_id = str(eq.get("equation_id") or "")
                if not eq_id:
                    continue
                rec = eq.get("reconstruction") if isinstance(eq.get("reconstruction"), dict) else {}
                consistency = (
                    eq.get("equation_consistency")
                    if isinstance(eq.get("equation_consistency"), dict)
                    else {}
                )
                for code in self._codes_in(eq.get("review_reason")):
                    if code in report["issues"]:
                        _record(code, "equation_id", eq_id,
                                f"$.equations[{eq_id}].review_reason")
                for code in self._codes_in(rec.get("review_reason")):
                    if code in report["issues"]:
                        _record(code, "equation_id", eq_id,
                                f"$.equations[{eq_id}].reconstruction.review_reason")
                for code in self._codes_in(consistency.get("review_reason")):
                    if code in report["issues"]:
                        _record(code, "equation_id", eq_id,
                                f"$.equations[{eq_id}].equation_consistency.review_reason")
                for code in self._codes_in(eq.get("review_flags")):
                    if code in report["issues"]:
                        _record(code, "equation_id", eq_id,
                                f"$.equations[{eq_id}].review_flags")

        # --- candidates ---
        candidates = raw.get("equation_candidates") or []
        if isinstance(candidates, list):
            for cand in candidates:
                if not isinstance(cand, dict):
                    continue
                cand_id = str(cand.get("candidate_id") or "")
                if not cand_id:
                    continue
                for code in self._codes_in(cand.get("review_reason")):
                    if code in report["issues"]:
                        _record(code, "candidate_id", cand_id,
                                f"$.equation_candidates[{cand_id}].review_reason")

        return report

    @staticmethod
    def _codes_in(values) -> list:
        if not isinstance(values, list):
            return []
        return [str(v) for v in values if str(v) in _EQUATION_FIDELITY_CODES]

    def _check_accepted_equations_registered(self, artifacts: dict, errors: list) -> None:
        """Enforce the candidate→registry promotion invariant (issue #431).

        Invariant (domain-agnostic): for any document, the set of equation
        candidates with ``acceptance_status == "accepted"`` must be a subset of
        the equation registry. Registry membership is established purely by ID
        links and never by formula names, labels, or domain vocabulary:

          - a registry record traces the candidate via ``candidate_trace_ids``, or
          - the candidate's own ``accepted_equation_id`` resolves to a registry
            ``equation_id``.

        The check is a pure set difference ``accepted_ids - registered_ids``; a
        non-empty difference yields one hard ``EQ_ACCEPTED_NOT_REGISTERED`` error
        per orphaned candidate, which blocks publish. ``provisional`` and other
        statuses are intentionally out of scope — the invariant is about
        ``accepted`` candidates only.
        """
        raw = artifacts.get("equation_semantics")
        if not isinstance(raw, dict):
            return
        candidates = raw.get("equation_candidates")
        equations = raw.get("equations")
        if not isinstance(candidates, list) or not isinstance(equations, list):
            return

        # Registry side: equation ids present + candidate ids any record traces.
        registry_equation_ids: set[str] = set()
        traced_candidate_ids: set[str] = set()
        for rec in equations:
            if not isinstance(rec, dict):
                continue
            eq_id = str(rec.get("equation_id") or "")
            if eq_id:
                registry_equation_ids.add(eq_id)
            for cid in rec.get("candidate_trace_ids") or []:
                cid = str(cid)
                if cid:
                    traced_candidate_ids.add(cid)

        # Candidate side: accepted candidates + their accepted_equation_id pointer.
        accepted_candidate_ids: set[str] = set()
        accepted_equation_id_by_candidate: dict[str, str] = {}
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            if cand.get("acceptance_status") != "accepted":
                continue
            cid = str(cand.get("candidate_id") or "")
            if not cid:
                continue
            accepted_candidate_ids.add(cid)
            acc_eq = str(cand.get("accepted_equation_id") or "")
            if acc_eq:
                accepted_equation_id_by_candidate[cid] = acc_eq

        # registered = traced by a record OR own pointer resolves to the registry.
        registered_candidate_ids = set(traced_candidate_ids)
        for cid, acc_eq in accepted_equation_id_by_candidate.items():
            if acc_eq in registry_equation_ids:
                registered_candidate_ids.add(cid)

        for cid in sorted(accepted_candidate_ids - registered_candidate_ids):
            errors.append(ValidationEntry(
                code="EQ_ACCEPTED_NOT_REGISTERED",
                message=(
                    f"accepted equation candidate {cid!r} is absent from the "
                    f"equation registry: no registry record traces it and its "
                    f"accepted_equation_id does not resolve to a registry equation"
                ),
                artifact="equation_semantics",
                path=f"$.equation_candidates[{cid}]",
                source_stage="export_validation",
                target_type="equation_candidate",
                target_id=cid,
            ))

    def _check_equation_link_integrity(
        self,
        artifacts: dict,
        errors: list,
        review_items: list,
    ) -> None:
        """Enforce structural inter-equation link integrity (issue #432).

        Three domain-agnostic checks over the equation registry:

          - ``EQ_DANGLING_EQUATION_LINK`` (hard error): an input/output link id
            that does not resolve to a registry equation.
          - ``EQ_LINK_MISSING_PROVENANCE`` (hard error): a resolved input link
            with no ``link_provenance`` entry — links must be traceable to the
            structural cue (shared_symbol / textual_reference / derivation_step)
            that produced them.
          - ``EQ_RESULT_RELATION_WITHOUT_INPUT``: a ``result``/``relation``
            equation with no input links and no allowed ``link_status``
            justification. A derivation-usable equation in this state is a hard
            error (a result that came from nowhere); a non-usable / review-gated
            one is reported as a review item instead.

        Reads both the nested (asdict) and flattened (export) equation shapes.
        """
        raw = artifacts.get("equation_semantics")
        if not isinstance(raw, dict):
            return
        records = raw.get("equations")
        if not isinstance(records, list):
            return
        registry_ids = {
            str(r.get("equation_id") or "")
            for r in records
            if isinstance(r, dict)
        }
        registry_ids.discard("")

        def _field(eq: dict, name: str):
            if name in eq:
                return eq.get(name)
            sem = eq.get("semantics")
            return sem.get(name) if isinstance(sem, dict) else None

        for eq in records:
            if not isinstance(eq, dict):
                continue
            eq_id = str(eq.get("equation_id") or "")
            if not eq_id:
                continue
            inputs = [str(x) for x in (_field(eq, "input_equation_ids") or []) if x]
            outputs = [str(x) for x in (_field(eq, "output_equation_ids") or []) if x]
            provenance = _field(eq, "link_provenance")
            provenance = provenance if isinstance(provenance, dict) else {}
            link_status = str(_field(eq, "link_status") or "")
            eq_type = str(_field(eq, "equation_type") or eq.get("role") or "")

            # (a) dangling links
            for ref in inputs + outputs:
                if ref not in registry_ids:
                    errors.append(ValidationEntry(
                        code="EQ_DANGLING_EQUATION_LINK",
                        message=(
                            f"equation {eq_id!r} links to non-existent equation "
                            f"{ref!r}"
                        ),
                        artifact="equation_semantics",
                        path=f"$.equations[{eq_id}]",
                        source_stage="export_validation",
                        target_type="equation",
                        target_id=eq_id,
                    ))

            # (b) every input link must carry provenance
            for ref in inputs:
                if not provenance.get(ref):
                    errors.append(ValidationEntry(
                        code="EQ_LINK_MISSING_PROVENANCE",
                        message=(
                            f"equation {eq_id!r} input link {ref!r} has no "
                            f"link_provenance"
                        ),
                        artifact="equation_semantics",
                        path=f"$.equations[{eq_id}].link_provenance",
                        source_stage="export_validation",
                        target_type="equation",
                        target_id=eq_id,
                    ))

            # (c) result / relation equations need inputs or an allowed status
            if eq_type in ("result", "relation") and not inputs:
                if link_status in _ALLOWED_NO_INPUT_LINK_STATUSES:
                    continue
                policy = eq.get("confidence_policy")
                policy = policy if isinstance(policy, dict) else {}
                usable = bool(policy.get("can_be_used_in_derivation"))
                entry = ValidationEntry(
                    code="EQ_RESULT_RELATION_WITHOUT_INPUT",
                    message=(
                        f"{eq_type} equation {eq_id!r} has no input_equation_ids "
                        f"and link_status {link_status or 'unset'!r} is not an "
                        f"allowed no-input justification "
                        f"({sorted(_ALLOWED_NO_INPUT_LINK_STATUSES)})"
                    ),
                    artifact="equation_semantics",
                    path=f"$.equations[{eq_id}].input_equation_ids",
                    source_stage="export_validation",
                    target_type="equation",
                    target_id=eq_id,
                )
                if usable:
                    errors.append(entry)
                else:
                    review_items.append(entry)

    def _check_equation_self_describing(self, artifacts: dict, warnings: list) -> None:
        """Equation self-describing report (issue #439).

        An equation whose trusted LaTeX contains free symbols must describe them
        (``defined_symbols`` / ``used_symbols`` non-empty); a missing description
        is reported as ``EQ_SYMBOLS_EMPTY``. An equation whose ``role_in_argument``
        is absent or outside the controlled vocabulary is reported as
        ``EQ_ROLE_INVALID``. Both are warnings (equation_semantics is a soft stage)
        and are domain-agnostic — no symbol/equation names are asserted.
        """
        equations = artifacts.get("equation_semantics")
        if not isinstance(equations, dict):
            return
        records = equations.get("equations")
        if not isinstance(records, list):
            return
        for idx, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            eq_id = str(record.get("equation_id") or f"index_{idx}")
            sem = record.get("semantics") if isinstance(record.get("semantics"), dict) else {}
            # Only fully-analyzed equations (with a semantics block) are subject to
            # the self-describing contract; minimal/legacy stub records are skipped.
            if not sem:
                continue
            src = record.get("source_extraction") if isinstance(record.get("source_extraction"), dict) else {}
            rec = record.get("reconstruction") if isinstance(record.get("reconstruction"), dict) else {}

            # Trusted display math only: needs_math_review LaTeX is audit text.
            latex = None
            if not src.get("needs_math_review"):
                latex = src.get("latex")
            if str(rec.get("status") or "none") != "none":
                latex = rec.get("latex")
            free_symbols = _extract_free_symbols(latex)

            described = bool(sem.get("defined_symbols")) or bool(sem.get("used_symbols"))
            if free_symbols and not described:
                warnings.append(ValidationEntry(
                    code="EQ_SYMBOLS_EMPTY",
                    message=(
                        f"equation {eq_id!r} has free symbols {sorted(free_symbols)} in its "
                        "LaTeX but no symbol descriptions (defined_symbols / used_symbols empty)"
                    ),
                    artifact="equation_semantics",
                    path=f"$.equations[{idx}].semantics.defined_symbols",
                    source_stage="export_validation",
                    target_type="equation",
                    target_id=eq_id,
                ))

            role = str(sem.get("role_in_argument") or "")
            if role not in _EQUATION_ROLE_VOCAB:
                warnings.append(ValidationEntry(
                    code="EQ_ROLE_INVALID",
                    message=(
                        f"equation {eq_id!r} role_in_argument {role!r} is not in the "
                        f"controlled vocabulary {sorted(_EQUATION_ROLE_VOCAB)}"
                    ),
                    artifact="equation_semantics",
                    path=f"$.equations[{idx}].semantics.role_in_argument",
                    source_stage="export_validation",
                    target_type="equation",
                    target_id=eq_id,
                ))

    def _check_dsl_edges(
        self,
        dsl,
        artifacts: dict,
        claim_objects,
        evidence,
        errors: list,
        warnings: list,
    ) -> None:
        """Verify DSL graph edges are typed, evidence-backed, and resolvable.

        In addition to non-empty endpoints, issue #441 requires that every edge
        carry a non-null ``edge_type`` from the controlled vocabulary
        (``DSL_EDGE_UNTYPED``) and a non-empty ``evidence_refs`` set
        (``DSL_EDGE_NO_EVIDENCE``) whose claim/equation references resolve against
        the final artifacts (``DSL_EDGE_DANGLING_EVIDENCE``). Domain-agnostic — no
        specific verbs/ids are asserted.
        """
        node_ids: set[str] = {n.node_id for n in (dsl.nodes or [])}

        # Known id universe for dangling-ref detection.
        known_claim_ids = {
            str(getattr(c, "claim_id", "") or "")
            for c in (getattr(claim_objects, "claims", []) or [])
            if getattr(c, "claim_id", None)
        }
        known_equation_ids = set(self._equation_index_from_artifacts(artifacts))
        known_evidence_ids = {
            str(getattr(r, "evidence_id", "") or "")
            for r in (getattr(evidence, "records", []) or [])
            if getattr(r, "evidence_id", None)
        }

        for edge in dsl.edges or []:
            edge_id = getattr(edge, "edge_id", "?")
            # Issue #441: edge_type must be present and in the controlled vocab.
            edge_type = str(getattr(edge, "edge_type", "") or "")
            if not edge_type or edge_type not in _DSL_EDGE_TYPE_VOCAB:
                errors.append(ValidationEntry(
                    code="DSL_EDGE_UNTYPED",
                    message=(
                        f"DSL edge {edge_id!r} has no controlled edge_type "
                        f"(edge_type={edge_type!r}); traversal cannot branch on relation kind"
                    ),
                    artifact="dsl_linking",
                    path=f"$.edges[{edge_id}].edge_type",
                    source_stage="export_validation",
                    target_type="dsl_edge",
                    target_id=str(edge_id),
                ))

            # Issue #441: evidence_refs must be non-empty and resolve.
            refs = getattr(edge, "evidence_refs", {}) or {}
            claim_refs = [str(v) for v in (refs.get("claim_ids") or []) if v]
            equation_refs = [str(v) for v in (refs.get("equation_ids") or []) if v]
            thesis_refs = [str(v) for v in (refs.get("thesis_refs") or []) if v]
            if not (claim_refs or equation_refs or thesis_refs):
                errors.append(ValidationEntry(
                    code="DSL_EDGE_NO_EVIDENCE",
                    message=(
                        f"DSL edge {edge_id!r} has empty evidence_refs; the relation "
                        "cannot be traced back to a source"
                    ),
                    artifact="dsl_linking",
                    path=f"$.edges[{edge_id}].evidence_refs",
                    source_stage="export_validation",
                    target_type="dsl_edge",
                    target_id=str(edge_id),
                ))
            else:
                dangling: list[str] = []
                if known_claim_ids:
                    dangling += [r for r in claim_refs if r not in known_claim_ids]
                if known_equation_ids:
                    dangling += [r for r in equation_refs if r not in known_equation_ids]
                if dangling:
                    errors.append(ValidationEntry(
                        code="DSL_EDGE_DANGLING_EVIDENCE",
                        message=(
                            f"DSL edge {edge_id!r} evidence_refs reference unknown "
                            f"claim/equation ids {sorted(set(dangling))}"
                        ),
                        artifact="dsl_linking",
                        path=f"$.edges[{edge_id}].evidence_refs",
                        source_stage="export_validation",
                        target_type="dsl_edge",
                        target_id=str(edge_id),
                    ))

            if not getattr(edge, "from_node_id", None):
                errors.append(ValidationEntry(
                    code="EMPTY_GRAPH_EDGE_SOURCE",
                    message=f"DSL edge {getattr(edge, 'edge_id', '?')} has empty from_node_id",
                    artifact="dsl_linking",
                    path="$.edges",
                    source_stage="export_validation",
                ))
            elif node_ids and edge.from_node_id not in node_ids:
                errors.append(ValidationEntry(
                    code="UNRESOLVED_GRAPH_EDGE_SOURCE",
                    message=f"DSL edge {edge.edge_id!r} from_node_id {edge.from_node_id!r} not in nodes",
                    artifact="dsl_linking",
                    path=f"$.edges[{edge.edge_id}].from_node_id",
                    source_stage="export_validation",
                ))
            if not getattr(edge, "to_node_id", None):
                errors.append(ValidationEntry(
                    code="EMPTY_GRAPH_EDGE_TARGET",
                    message=f"DSL edge {getattr(edge, 'edge_id', '?')} has empty to_node_id",
                    artifact="dsl_linking",
                    path="$.edges",
                    source_stage="export_validation",
                ))
            elif node_ids and edge.to_node_id not in node_ids:
                errors.append(ValidationEntry(
                    code="UNRESOLVED_GRAPH_EDGE_TARGET",
                    message=f"DSL edge {edge.edge_id!r} to_node_id {edge.to_node_id!r} not in nodes",
                    artifact="dsl_linking",
                    path=f"$.edges[{edge.edge_id}].to_node_id",
                    source_stage="export_validation",
                ))

    def _check_thesis_artifact(
        self,
        artifacts: dict,
        dsl,
        errors: list,
        review_items: list,
    ) -> None:
        """Thesis traversal-anchor check (issue #442).

        When there is a DSL graph to traverse, the thesis_reconstruction artifact
        must exist with a central thesis and a headline claim
        (``THESIS_ARTIFACT_MISSING``). Its ``anchor_node_ids`` must reference real
        DSL nodes (``THESIS_ANCHOR_UNRESOLVED``); at least one anchor must be
        present (``THESIS_ANCHOR_MISSING``) and reachable from a graph root
        (``THESIS_ANCHOR_UNREACHABLE``). Domain-agnostic — no specific claim text
        or node id is asserted.
        """
        thesis = artifacts.get("thesis_reconstruction")
        central = thesis.get("central_thesis") if isinstance(thesis, dict) else None
        if not isinstance(thesis, dict) or not isinstance(central, dict) or not central:
            errors.append(ValidationEntry(
                code="THESIS_ARTIFACT_MISSING",
                message=(
                    "thesis_reconstruction artifact is missing or has no central_thesis; "
                    "graph traversal has no defined goal"
                ),
                artifact="thesis_reconstruction",
                path="$.central_thesis",
                source_stage="export_validation",
            ))
            return

        headline = str(thesis.get("headline_claim") or central.get("text") or "")
        central_question = str(thesis.get("central_question") or "")
        if not headline:
            errors.append(ValidationEntry(
                code="THESIS_ARTIFACT_MISSING",
                message="thesis_reconstruction artifact has no headline_claim",
                artifact="thesis_reconstruction",
                path="$.headline_claim",
                source_stage="export_validation",
            ))
        if not central_question:
            review_items.append(ValidationEntry(
                code="THESIS_CENTRAL_QUESTION_MISSING",
                message="thesis_reconstruction artifact has no central_question",
                artifact="thesis_reconstruction",
                path="$.central_question",
                source_stage="export_validation",
            ))

        node_ids = {str(getattr(n, "node_id", "") or "") for n in (getattr(dsl, "nodes", []) or [])}
        anchor_ids = [str(a) for a in (thesis.get("anchor_node_ids") or []) if a]
        if not anchor_ids:
            review_items.append(ValidationEntry(
                code="THESIS_ANCHOR_MISSING",
                message=(
                    "thesis_reconstruction has no anchor_node_ids; the traversal "
                    "entry/exit nodes are undefined"
                ),
                artifact="thesis_reconstruction",
                path="$.anchor_node_ids",
                source_stage="export_validation",
            ))
            return

        unresolved = [a for a in anchor_ids if node_ids and a not in node_ids]
        if unresolved:
            review_items.append(ValidationEntry(
                code="THESIS_ANCHOR_UNRESOLVED",
                message=(
                    f"thesis anchor_node_ids {sorted(set(unresolved))} do not resolve "
                    "to DSL graph nodes"
                ),
                artifact="thesis_reconstruction",
                path="$.anchor_node_ids",
                source_stage="export_validation",
            ))

        resolved_anchors = [a for a in anchor_ids if a in node_ids]
        if resolved_anchors and not self._anchor_reachable_from_root(dsl, set(resolved_anchors)):
            review_items.append(ValidationEntry(
                code="THESIS_ANCHOR_UNREACHABLE",
                message=(
                    "no path from a graph root reaches any thesis anchor node "
                    f"{sorted(set(resolved_anchors))}"
                ),
                artifact="thesis_reconstruction",
                path="$.anchor_node_ids",
                source_stage="export_validation",
            ))

    @staticmethod
    def _anchor_reachable_from_root(dsl, anchors: set) -> bool:
        """Return True when some graph root can reach a thesis anchor (issue #442).

        Roots are nodes with in-degree 0. With no edges, every node is its own
        root, so an anchor that exists is reachable. With a fully cyclic graph
        (no in-degree-0 node) reachability is treated as satisfied — there is no
        well-defined root to fail against. Domain-agnostic graph traversal.
        """
        nodes = [str(getattr(n, "node_id", "") or "") for n in (getattr(dsl, "nodes", []) or [])]
        node_set = {n for n in nodes if n}
        if anchors & node_set and not (getattr(dsl, "edges", []) or []):
            return True
        adjacency: dict[str, list[str]] = {n: [] for n in node_set}
        indegree: dict[str, int] = {n: 0 for n in node_set}
        for edge in getattr(dsl, "edges", []) or []:
            src = str(getattr(edge, "from_node_id", "") or "")
            dst = str(getattr(edge, "to_node_id", "") or "")
            if src in node_set and dst in node_set:
                adjacency[src].append(dst)
                indegree[dst] += 1
        roots = [n for n in node_set if indegree.get(n, 0) == 0]
        if not roots:
            # Fully cyclic / every node has an incoming edge: no root to fail on.
            return True
        seen: set[str] = set()
        stack = list(roots)
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            if current in anchors:
                return True
            stack.extend(adjacency.get(current, []))
        return False

    def _check_component_graph_artifact(
        self,
        artifacts: dict,
        component_result,
        errors: list,
        warnings: list,
    ) -> None:
        graph = artifacts.get("component_graph") or {}
        if not isinstance(graph, dict):
            return
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        if not isinstance(nodes, list) or not isinstance(edges, list):
            return

        canonical_component_ids = {
            str(getattr(component, "component_id", "") or "")
            for component in (getattr(component_result, "components", []) or [])
            if getattr(component, "component_id", None)
        }
        component_aliases = {component_id: component_id for component_id in canonical_component_ids}
        for component in (getattr(component_result, "components", []) or []):
            canonical = str(getattr(component, "component_id", "") or "")
            for alias in (
                list(getattr(component, "legacy_ids", []) or [])
                + [
                    getattr(component, "agent_component_id", None),
                    getattr(component, "legacy_component_id", None),
                ]
            ):
                alias = str(alias or "")
                if alias and canonical:
                    component_aliases[alias] = canonical
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = str(
                node.get("component_id") or node.get("node_id") or node.get("id") or ""
            )
            provenance_candidates = _ordered_unique(
                [
                    value
                    for value in (
                        [node.get("representative_component_id")]
                        + list(node.get("linked_component_ids") or [])
                    )
                    if value and str(value) in canonical_component_ids
                ]
            )
            if node_id and len(provenance_candidates) == 1:
                component_aliases[node_id] = str(provenance_candidates[0])
        canonical_registry_available = component_result is not None

        node_ids: set[str] = set()
        for node in nodes:
            if isinstance(node, dict):
                nid = str(node.get("component_id") or node.get("node_id") or node.get("id") or "")
                if nid:
                    node_ids.add(nid)
        for idx, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            comp_id = str(node.get("component_id") or node.get("node_id") or node.get("id") or "")
            if not str(node.get("label") or "").strip():
                errors.append(ValidationEntry(
                    code="COMPONENT_GRAPH_NODE_MISSING_LABEL",
                    message=f"component graph node at index {idx} has empty label",
                    artifact="component_graph",
                    path=f"$.nodes[{idx}].label",
                    source_stage="export_validation",
                    target_type="graph_node",
                    target_id=comp_id or None,
                ))
            # Issue #422: graph membership alone is insufficient. An operation
            # parent must resolve to a canonical component artifact (aliases are
            # accepted and normalized by export), never an aggregate theory node.
            # When this validator is invoked without a component_result (legacy
            # standalone graph checks), retain the older graph-membership check.
            parent_id = str(node.get("parent_component_id") or "")
            parent_known = (
                parent_id in component_aliases
                if canonical_registry_available
                else parent_id in node_ids
            )
            if parent_id and not parent_known:
                errors.append(ValidationEntry(
                    code="COMPONENT_GRAPH_PARENT_COMPONENT_INVALID",
                    message=(
                        f"component graph node {comp_id or idx!r} parent_component_id "
                        f"{parent_id!r} does not resolve to "
                        f"{'a canonical component' if canonical_registry_available else 'a graph node'}"
                    ),
                    artifact="component_graph",
                    path=f"$.nodes[{idx}].parent_component_id",
                    source_stage="export_validation",
                    target_type=(
                        "component" if canonical_registry_available else "graph_node"
                    ),
                    target_id=(
                        parent_id if canonical_registry_available else comp_id
                    ) or None,
                ))
            # Issue #418: a main node aggregates other nodes via member_component_ids;
            # each member must resolve to an existing graph node.
            for member_id in node.get("member_component_ids") or []:
                if str(member_id) and str(member_id) not in node_ids:
                    errors.append(ValidationEntry(
                        code="COMPONENT_GRAPH_MEMBER_COMPONENT_INVALID",
                        message=(
                            f"component graph node {comp_id or idx!r} member_component_id "
                            f"{member_id!r} does not resolve to a graph node"
                        ),
                        artifact="component_graph",
                        path=f"$.nodes[{idx}].member_component_ids",
                        source_stage="export_validation",
                        target_type="graph_node",
                        target_id=comp_id or None,
                    ))
            if not str(node.get("component_type") or "").strip():
                warnings.append(ValidationEntry(
                    code="COMPONENT_GRAPH_NODE_MISSING_COMPONENT_TYPE",
                    message=f"component graph node {comp_id or idx!r} has empty component_type",
                    artifact="component_graph",
                    path=f"$.nodes[{idx}].component_type",
                    source_stage="export_validation",
                ))

        seen_pairs: set[tuple[str, str]] = set()
        for idx, edge in enumerate(edges):
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("source") or edge.get("source_component_id") or "")
            target = str(edge.get("target") or edge.get("target_component_id") or "")
            edge_type = str(edge.get("edge_type") or edge.get("relation") or "")
            if not source or source not in node_ids:
                errors.append(ValidationEntry(
                    code="COMPONENT_GRAPH_EDGE_SOURCE_INVALID",
                    message=f"component graph edge at index {idx} has missing source {source!r}",
                    artifact="component_graph",
                    path=f"$.edges[{idx}].source",
                    source_stage="export_validation",
                ))
            if not target or target not in node_ids:
                errors.append(ValidationEntry(
                    code="COMPONENT_GRAPH_EDGE_TARGET_INVALID",
                    message=f"component graph edge at index {idx} has missing target {target!r}",
                    artifact="component_graph",
                    path=f"$.edges[{idx}].target",
                    source_stage="export_validation",
                ))
            evidence = edge.get("evidence") if isinstance(edge.get("evidence"), dict) else {}
            evidence_claims = edge.get("evidence_claims") or evidence.get("evidence_claims") or []
            evidence_equations = edge.get("evidence_equation_ids") or evidence.get("evidence_equation_ids") or []
            if not evidence_claims and not evidence_equations:
                warnings.append(ValidationEntry(
                    code="COMPONENT_GRAPH_EDGE_NO_EVIDENCE",
                    message=f"component graph edge {edge.get('edge_id') or idx!r} has no claim/equation evidence",
                    artifact="component_graph",
                    path=f"$.edges[{idx}].evidence",
                    source_stage="export_validation",
                ))
            if edge_type == "RELATED_TO":
                warnings.append(ValidationEntry(
                    code="COMPONENT_GRAPH_RELATED_TO_EDGE",
                    message=f"component graph edge {edge.get('edge_id') or idx!r} uses generic RELATED_TO",
                    artifact="component_graph",
                    path=f"$.edges[{idx}].edge_type",
                    source_stage="export_validation",
                ))
            if source and target:
                if (target, source) in seen_pairs:
                    warnings.append(ValidationEntry(
                        code="COMPONENT_GRAPH_BIDIRECTIONAL_EDGE_PAIR",
                        message=f"component graph has bidirectional edge pair {source!r} <-> {target!r}",
                        artifact="component_graph",
                        path=f"$.edges[{idx}]",
                        source_stage="export_validation",
                    ))
                seen_pairs.add((source, target))

    def _check_source_backed_claims(
        self,
        claim_objects,
        evidence,
        errors: list,
    ) -> None:
        """Hard-fail source_backed claims with broken evidence links (#257 / #312).

        A source_backed claim without source_evidence_ids — or referencing an
        evidence_id absent from the EvidenceRegistry — cannot be traced back to a
        PDF-derived Evidence record. Issue #312 requires these to be hard errors so
        the guarantee holds on every code path (freshly built or reloaded claims),
        not only when the builder happens to re-run its own validation.
        """
        known_evidence_ids: set[str] = {
            r.evidence_id
            for r in (getattr(evidence, "records", []) or [])
        }
        for claim in getattr(claim_objects, "claims", []) or []:
            support = getattr(claim, "support_status", "") or ""
            if support != "source_backed":
                continue
            ev_ids = list(getattr(claim, "source_evidence_ids", []) or [])
            if not ev_ids:
                errors.append(ValidationEntry(
                    code="SOURCE_BACKED_CLAIM_NO_EVIDENCE_IDS",
                    message=(
                        f"source_backed claim {getattr(claim, 'claim_id', '?')!r} "
                        "has no source_evidence_ids; PDF evidence cannot be verified"
                    ),
                    artifact="claim_object_builder",
                    path=f"$.claims[{getattr(claim, 'claim_id', '?')}].source_evidence_ids",
                    source_stage="export_validation",
                ))
            else:
                # Error if any referenced evidence_id is not in the registry
                for eid in ev_ids:
                    if known_evidence_ids and eid not in known_evidence_ids:
                        errors.append(ValidationEntry(
                            code="SOURCE_BACKED_CLAIM_UNRESOLVED_EVIDENCE_ID",
                            message=(
                                f"source_backed claim {getattr(claim, 'claim_id', '?')!r} "
                                f"references evidence {eid!r} not in EvidenceRegistry"
                            ),
                            artifact="claim_object_builder",
                            path=f"$.claims[{getattr(claim, 'claim_id', '?')}].source_evidence_ids",
                            source_stage="export_validation",
                        ))

    def _check_claim_atomicity(
        self,
        claim_objects,
        errors: list,
        review_items: list,
    ) -> None:
        """Report non-atomic claims clearly (#312, criteria #6 / #8).

        A non-atomic claim mixes multiple propositions and must not be treated as
        confirmed atomic backing. A non-atomic *main-result* claim is a hard error
        (it is a paper-level summary); other non-atomic claims are flagged for
        review so they are split before being used by components / graph nodes.
        """
        for claim in getattr(claim_objects, "claims", []) or []:
            atomicity = str(getattr(claim, "atomicity", "atomic") or "atomic")
            claim_id = getattr(claim, "claim_id", "?")
            # Deterministic-split suggestions are never confirmed atomic
            # backing; surface them so a teacher confirms the split before reuse.
            if atomicity in {"split_pending", "split_required"}:
                review_items.append(ValidationEntry(
                    code="SPLIT_PENDING_CLAIM_NEEDS_CONFIRMATION",
                    message=(
                        f"claim {claim_id!r} is a split suggestion "
                        f"({atomicity}); confirm via ClaimQualificationAgent atomic "
                        "rewrite before using it to back a component or graph node"
                    ),
                    artifact="claim_object_builder",
                    path=f"$.claims[{claim_id}].atomicity",
                    source_stage="export_validation",
                ))
                continue
            if atomicity in {"composite", "compound"}:
                continue
            if atomicity != "non_atomic":
                continue
            claim_type = str(getattr(claim, "claim_type", "") or "")
            if claim_type in _MAIN_RESULT_CLAIM_TYPES:
                errors.append(ValidationEntry(
                    code="NON_ATOMIC_MAIN_RESULT_CLAIM",
                    message=(
                        f"main-result claim {claim_id!r} ({claim_type}) is non_atomic; "
                        "a main result must be a single minimal proposition"
                    ),
                    artifact="claim_object_builder",
                    path=f"$.claims[{claim_id}].atomicity",
                    source_stage="export_validation",
                ))
            else:
                review_items.append(ValidationEntry(
                    code="NON_ATOMIC_CLAIM_NEEDS_SPLIT",
                    message=(
                        f"claim {claim_id!r} is non_atomic; split into atomic claims "
                        "before using it to back a component or graph node"
                    ),
                    artifact="claim_object_builder",
                    path=f"$.claims[{claim_id}].atomicity",
                    source_stage="export_validation",
                ))

    def _check_claim_equation_link_symmetry(
        self,
        claim_objects,
        artifacts: dict,
        warnings: list,
    ) -> None:
        """Report one-way claim↔equation links (issue #358).

        ``claim.equation_ids`` and ``equation.linked_claim_ids`` are maintained
        by different stages and can drift apart. The pipeline demotes one-way
        links into ``inferred_equation_ids`` / ``inferred_claim_ids`` (see
        ``annotate_claim_equation_link_asymmetries``); this check reports both
        already-demoted links and any asymmetry still present in the primary
        fields (artifacts that never went through the annotation step).
        """
        equation_index = self._equation_index_from_artifacts(artifacts)
        if not equation_index:
            return
        claims = list(getattr(claim_objects, "claims", []) or [])
        claim_to_eq: dict[str, set[str]] = {}
        for claim in claims:
            claim_id = str(getattr(claim, "claim_id", "") or "")
            if claim_id:
                claim_to_eq[claim_id] = {
                    str(v) for v in (getattr(claim, "equation_ids", []) or []) if v
                }
        eq_to_claim: dict[str, set[str]] = {}
        for eq_id, eq in equation_index.items():
            eq_to_claim[eq_id] = {
                str(v) for v in (eq.get("linked_claim_ids") or []) if v
            }

        for claim_id, eq_ids in claim_to_eq.items():
            for eq_id in eq_ids:
                if eq_id in eq_to_claim and claim_id not in eq_to_claim[eq_id]:
                    warnings.append(ValidationEntry(
                        code="CLAIM_EQUATION_LINK_ASYMMETRY",
                        message=(
                            f"claim {claim_id!r} links equation {eq_id!r} but the "
                            "equation does not link the claim back; treat the link "
                            "as inferred until reviewed"
                        ),
                        artifact="claim_object_builder",
                        path=f"$.claims[{claim_id}].equation_ids",
                        source_stage="export_validation",
                    ))
        for eq_id, claim_ids in eq_to_claim.items():
            for claim_id in claim_ids:
                if claim_id in claim_to_eq and eq_id not in claim_to_eq[claim_id]:
                    warnings.append(ValidationEntry(
                        code="CLAIM_EQUATION_LINK_ASYMMETRY",
                        message=(
                            f"equation {eq_id!r} links claim {claim_id!r} but the "
                            "claim does not link the equation back; treat the link "
                            "as inferred until reviewed"
                        ),
                        artifact="equation_semantics",
                        path=f"$.equations[{eq_id}].linked_claim_ids",
                        source_stage="export_validation",
                    ))

        # Links already demoted by the annotation step stay visible in the
        # gate report (#358 review fix).
        for claim in claims:
            claim_id = str(getattr(claim, "claim_id", "") or "")
            for eq_id in getattr(claim, "inferred_equation_ids", []) or []:
                warnings.append(ValidationEntry(
                    code="CLAIM_EQUATION_LINK_ASYMMETRY",
                    message=(
                        f"claim {claim_id!r} → equation {eq_id!r} link was "
                        "demoted to inferred (one-way link); review before "
                        "treating it as a confirmed link"
                    ),
                    artifact="claim_object_builder",
                    path=f"$.claims[{claim_id}].inferred_equation_ids",
                    source_stage="export_validation",
                ))
        for eq_id, eq in equation_index.items():
            sem = eq.get("semantics") if isinstance(eq.get("semantics"), dict) else {}
            for claim_id in (
                eq.get("inferred_claim_ids") or sem.get("inferred_claim_ids") or []
            ):
                warnings.append(ValidationEntry(
                    code="CLAIM_EQUATION_LINK_ASYMMETRY",
                    message=(
                        f"equation {eq_id!r} → claim {claim_id!r} link was "
                        "demoted to inferred (one-way link); review before "
                        "treating it as a confirmed link"
                    ),
                    artifact="equation_semantics",
                    path=f"$.equations[{eq_id}].inferred_claim_ids",
                    source_stage="export_validation",
                ))

    # Equation-semantics types whose equations must not appear in conflicting
    # component role lists (issue #358). equation_semantics is the source of
    # truth for an equation's type.
    _EQUATION_ROLE_CONFLICTS = {
        "definition": ("output_equation_ids",),
        "result": ("definition_equation_ids",),
    }

    def _check_equation_role_conflicts(
        self,
        component_result,
        artifacts: dict,
        warnings: list,
    ) -> None:
        """Report stage-to-stage equation role conflicts (issue #358)."""
        equation_index = self._equation_index_from_artifacts(artifacts)
        if not equation_index:
            return
        eq_type_by_id = {}
        for eq_id, eq in equation_index.items():
            semantics = eq.get("semantics") if isinstance(eq.get("semantics"), dict) else {}
            eq_type = str(eq.get("role") or semantics.get("equation_type") or "")
            if eq_type:
                eq_type_by_id[eq_id] = eq_type
        for component in getattr(component_result, "components", []) or []:
            comp_id = getattr(component, "component_id", "?")
            for eq_type, conflicting_fields in self._EQUATION_ROLE_CONFLICTS.items():
                for field_name in conflicting_fields:
                    for eq_id in getattr(component, field_name, []) or []:
                        if eq_type_by_id.get(str(eq_id)) != eq_type:
                            continue
                        warnings.append(ValidationEntry(
                            code="EQUATION_ROLE_CONFLICT",
                            message=(
                                f"component {comp_id!r} classifies equation "
                                f"{eq_id!r} as {field_name} but "
                                f"equation_semantics types it as {eq_type!r}"
                            ),
                            artifact="component_assembly",
                            path=f"$.components[{comp_id}].{field_name}",
                            source_stage="export_validation",
                        ))

    def _check_thesis_coverage(
        self,
        artifacts: dict,
        component_result,
        claim_objects,
        review_items: list,
    ) -> dict:
        """Thesis coverage report (issue #354).

        For the central thesis and each support_structure section, verify every
        referenced claim_id / equation_id (a) still exists in the final claim /
        equation sets and (b) is reachable from a main-layer graph node (or an
        assembled component when the component_graph artifact is absent).
        Unreachable refs are recorded with review_reasons=["thesis_support_unreachable"]
        and surfaced as review items — never hard errors.
        """
        result = _empty_thesis_coverage()
        thesis = artifacts.get("thesis_reconstruction")
        if not isinstance(thesis, dict):
            return result
        central = thesis.get("central_thesis")
        if not isinstance(central, dict):
            return result
        result["thesis_present"] = True

        final_claim_ids = {
            str(getattr(c, "claim_id", "") or "")
            for c in (getattr(claim_objects, "claims", []) or [])
            if getattr(c, "claim_id", None)
        }
        known_equation_ids = set(self._equation_index_from_artifacts(artifacts))
        backing = self._main_graph_backing_ids(artifacts, component_result)
        main_claim_ids = backing["claims"]
        main_equation_ids = backing["equations"]
        weak_claim_ids = backing["weak_claims"]
        weak_equation_ids = backing["weak_equations"]

        sections: list[tuple[str, list[dict]]] = [("central_thesis", [central])]
        support = thesis.get("support_structure")
        if isinstance(support, dict):
            for name, entries in support.items():
                if isinstance(entries, list):
                    sections.append(
                        (str(name), [e for e in entries if isinstance(e, dict)])
                    )

        for section, entries in sections:
            section_total = 0
            section_reachable = 0
            for entry in entries:
                ref_groups = (
                    ("claim", entry.get("claim_ids") or [],
                     final_claim_ids, main_claim_ids, weak_claim_ids),
                    ("equation", entry.get("equation_ids") or [],
                     known_equation_ids, main_equation_ids, weak_equation_ids),
                )
                for ref_type, refs, known_ids, covered_ids, weak_ids in ref_groups:
                    for ref in refs:
                        ref_id = str(ref or "")
                        if not ref_id:
                            continue
                        section_total += 1
                        if known_ids and ref_id not in known_ids:
                            reason = f"missing_{ref_type}"
                        elif ref_id in covered_ids:
                            section_reachable += 1
                            continue
                        elif ref_id in weak_ids:
                            # Reachable only through inferred / review_required
                            # main nodes — that is not support (#354 review fix).
                            reason = "main_node_backing_insufficient"
                        else:
                            reason = "not_linked_to_main_graph"
                        result["unreachable_refs"].append({
                            "ref_id": ref_id,
                            "ref_type": ref_type,
                            "section": section,
                            "reason": reason,
                            "review_reasons": ["thesis_support_unreachable"],
                        })
                        review_items.append(ValidationEntry(
                            code="THESIS_SUPPORT_UNREACHABLE",
                            message=(
                                f"thesis section {section!r} references {ref_type} "
                                f"{ref_id!r} which is not reachable from the exported "
                                f"main graph ({reason})"
                            ),
                            artifact="thesis_reconstruction",
                            path=f"$.support_structure[{section}]"
                            if section != "central_thesis"
                            else "$.central_thesis",
                            source_stage="export_validation",
                        ))
            if section_total:
                result["coverage_by_section"][section] = round(
                    section_reachable / section_total, 4
                )
                result["total_refs"] += section_total
                result["reachable_refs"] += section_reachable
        return result

    # Main-node backing statuses that count as thesis support (#354): only a
    # confirmed source_backed node. partially_source_backed / inferred /
    # review_required / unknown backing is itself unconfirmed structure, so
    # coverage through such nodes is reported as insufficient backing.
    _STRONG_MAIN_NODE_BACKING = {"source_backed"}

    @staticmethod
    def _main_graph_backing_ids(artifacts: dict, component_result) -> dict:
        """Claim / equation ids backed by main-layer graph nodes (issue #354).

        A main node covers its own linked ids plus those of the equation_detail
        members it aggregates. Only ``source_backed`` nodes count as strong
        support; any other ``source_backing_status`` (partially_source_backed,
        inferred, review_required, unknown) collects into the ``weak_*`` sets —
        coverage through them is reported as insufficient backing, not support.
        When the component_graph artifact is absent the assembled components
        stand in as reachability targets so the report degrades instead of
        marking everything unreachable.
        """
        result = {
            "claims": set(), "equations": set(),
            "weak_claims": set(), "weak_equations": set(),
        }
        node_claim_keys = (
            "linked_claim_ids", "supports_claim_ids", "input_claim_ids",
            "output_claim_ids", "required_claim_ids",
        )
        node_equation_keys = (
            "linked_equation_ids", "input_equation_ids",
            "intermediate_equation_ids", "output_equation_ids",
            "definition_equation_ids", "constraint_equation_ids",
        )

        graph = artifacts.get("component_graph")
        nodes = graph.get("nodes") if isinstance(graph, dict) else None
        if isinstance(nodes, list) and nodes:
            node_by_id = {
                str(n.get("component_id") or ""): n
                for n in nodes if isinstance(n, dict)
            }
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if str(node.get("graph_layer") or "main") != "main":
                    continue
                backing = str(node.get("source_backing_status") or "")
                weak = backing not in ExportValidationGate._STRONG_MAIN_NODE_BACKING
                claim_key = "weak_claims" if weak else "claims"
                equation_key = "weak_equations" if weak else "equations"
                members = [node] + [
                    node_by_id.get(str(m))
                    for m in node.get("member_component_ids") or []
                ]
                for member in members:
                    if not isinstance(member, dict):
                        continue
                    for key in node_claim_keys:
                        result[claim_key].update(
                            str(v) for v in member.get(key) or [] if v
                        )
                    for key in node_equation_keys:
                        result[equation_key].update(
                            str(v) for v in member.get(key) or [] if v
                        )
            # An id covered by both a strong and a weak node counts as strong.
            result["weak_claims"] -= result["claims"]
            result["weak_equations"] -= result["equations"]
            return result

        for component in getattr(component_result, "components", []) or []:
            refs = getattr(component, "evidence_refs", {}) or {}
            result["claims"].update(
                str(v) for v in (refs.get("claim_ids") or []) if v
            )
            for key in node_claim_keys:
                result["claims"].update(
                    str(v) for v in (getattr(component, key, []) or []) if v
                )
            result["equations"].update(
                ExportValidationGate._component_equation_refs(component, refs)
            )
        return result

    def _check_concepts(
        self,
        claim_objects,
        component_result,
        warnings: list,
        review_items: list,
    ) -> dict:
        """Report concept coverage for claims / components (issue #8).

        Builds the structured ``concept_validation`` block and surfaces the
        actionable gaps: main claims / components without enough concepts
        (warnings), confirmed concepts on composite claims, and concepts drawn
        from low-confidence sources (review). Artifacts that predate concept
        support (no ``concepts`` attribute) are skipped.
        """
        block = _empty_concept_validation()

        for claim in getattr(claim_objects, "claims", []) or []:
            if not hasattr(claim, "concepts") and not hasattr(claim, "concept_assignment_status"):
                continue
            claim_id = getattr(claim, "claim_id", "?")
            concepts = list(getattr(claim, "concepts", []) or [])
            claim_type = str(getattr(claim, "claim_type", "") or "")
            status = str(getattr(claim, "concept_assignment_status", "") or "")
            if not concepts:
                block["missing_concepts"].append(claim_id)
            if claim_type in _MAIN_RESULT_CLAIM_TYPES and len(concepts) < 2:
                block["empty_concepts_on_main_claims"].append(claim_id)
                warnings.append(ValidationEntry(
                    code="MAIN_CLAIM_INSUFFICIENT_CONCEPTS",
                    message=(
                        f"main claim {claim_id!r} has {len(concepts)} concept(s); "
                        "main claims need at least 2 for graph / course mapping"
                    ),
                    artifact="claim_object_builder",
                    path=f"$.claims[{claim_id}].concepts",
                    source_stage="export_validation",
                ))
            if _claim_is_non_atomic(claim) and status == "source_backed":
                block["concepts_on_composite_claims"].append(claim_id)
                review_items.append(ValidationEntry(
                    code="CONCEPTS_ON_COMPOSITE_CLAIM",
                    message=(
                        f"composite claim {claim_id!r} has source_backed concepts; "
                        "non-atomic claims must keep concepts tentative"
                    ),
                    artifact="claim_object_builder",
                    path=f"$.claims[{claim_id}].concept_assignment_status",
                    source_stage="export_validation",
                ))
            if concepts and status == "review_required":
                block["concepts_from_low_confidence_sources"].append(claim_id)
                review_items.append(ValidationEntry(
                    code="CONCEPTS_FROM_LOW_CONFIDENCE_SOURCE",
                    message=(
                        f"claim {claim_id!r} draws concepts from a low-confidence "
                        "source; teacher review required before downstream use"
                    ),
                    artifact="claim_object_builder",
                    path=f"$.claims[{claim_id}].concept_assignment_status",
                    source_stage="export_validation",
                ))

        for component in getattr(component_result, "components", []) or []:
            if not hasattr(component, "concepts"):
                continue
            comp_id = getattr(component, "component_id", "?")
            concepts = list(getattr(component, "concepts", []) or [])
            if not concepts:
                block["missing_concepts"].append(comp_id)
            if len(concepts) < 2:
                block["empty_concepts_on_main_components"].append(comp_id)
                warnings.append(ValidationEntry(
                    code="MAIN_COMPONENT_INSUFFICIENT_CONCEPTS",
                    message=(
                        f"component {comp_id!r} has {len(concepts)} concept(s); "
                        "components need at least 2 for graph / course mapping"
                    ),
                    artifact="component_assembly",
                    path=f"$.components[{comp_id}].concepts",
                    source_stage="export_validation",
                ))
            mismatch = _component_concept_role_mismatch(component, concepts)
            if mismatch:
                block["concept_role_mismatch"].append({
                    "component_id": comp_id,
                    "reason": mismatch,
                })
                warnings.append(ValidationEntry(
                    code="COMPONENT_CONCEPT_ROLE_MISMATCH",
                    message=f"component {comp_id!r} concept role mismatch: {mismatch}",
                    artifact="component_assembly",
                    path=f"$.components[{comp_id}].concepts",
                    source_stage="export_validation",
                ))

        return block

    def _check_required_artifacts(
        self,
        artifacts: dict,
        errors: list,
    ) -> None:
        """Ensure required pipeline stages produced non-empty artifacts."""
        required_stages = [
            "document_structure",
            "source_chunking",
            "claim_qualification",
            "component_assembly",
        ]
        for stage in required_stages:
            artifact = artifacts.get(stage)
            if artifact is None:
                errors.append(ValidationEntry(
                    code="MISSING_REQUIRED_ARTIFACT",
                    message=f"Required artifact for stage {stage!r} is missing",
                    artifact=stage,
                    path=f"$.stage_outputs._artifacts.{stage}",
                    source_stage="export_validation",
                ))
