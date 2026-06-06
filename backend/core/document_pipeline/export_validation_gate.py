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


@dataclass
class ValidationEntry:
    code: str
    message: str
    artifact: str
    path: str | None = None
    source_stage: str | None = None


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

# Stage names where validation_issues with severity=error are hard errors
_HARD_ERROR_STAGES = {
    "component_assembly",
    "course_mapping",
    "dsl_linking",
    "claim_object_builder",
    "evidence_registry",
}

# Claim types representing the paper's main result / central conclusion (#312).
# A non-atomic main-result claim is a paper-level summary, not a usable claim.
_MAIN_RESULT_CLAIM_TYPES = {"result", "conclusion", "main_result"}


def _ordered_unique(values) -> list:
    result = []
    seen = set()
    for value in values or []:
        text = str(value or "")
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


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
            self._check_dsl_edges(dsl, errors, warnings)

        # 5. Component graph export structure
        self._check_component_graph_artifact(artifacts, errors, warnings)

        # 6. Required artifact presence
        self._check_required_artifacts(artifacts, errors)

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
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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

                entry = ValidationEntry(
                    code=code,
                    message=message,
                    artifact=stage,
                    path=path,
                    source_stage=stage,
                )

                if code in _NEEDS_REVIEW_RULE_IDS:
                    review_items.append(entry)
                elif severity == _SEVERITY_ERROR and (
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
            for nid in dsl_refs.get("node_ids") or []:
                if known_dsl_node_ids and nid not in known_dsl_node_ids:
                    errors.append(ValidationEntry(
                        code="UNRESOLVED_DSL_NODE_ID",
                        message=f"component {comp_id!r} references missing DSL node {nid!r}",
                        artifact="component_assembly",
                        path=f"$.components[{comp_id}].evidence_refs.dsl_refs.node_ids",
                        source_stage="export_validation",
                    ))
            for eid in dsl_refs.get("edge_ids") or []:
                if known_dsl_edge_ids and eid not in known_dsl_edge_ids:
                    errors.append(ValidationEntry(
                        code="UNRESOLVED_DSL_EDGE_ID",
                        message=f"component {comp_id!r} references missing DSL edge {eid!r}",
                        artifact="component_assembly",
                        path=f"$.components[{comp_id}].evidence_refs.dsl_refs.edge_ids",
                        source_stage="export_validation",
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

    def _cross_validate_course_mapping(
        self,
        course_mapping,
        component_result,
        errors: list,
        warnings: list,
    ) -> None:
        """Verify CourseMapping topics reference real component IDs."""
        known_component_ids: set[str] = {
            c.component_id
            for c in (getattr(component_result, "components", []) or [])
        }
        topics = getattr(course_mapping, "topics", []) or []
        if isinstance(topics, list):
            for idx, topic in enumerate(topics):
                linked = []
                if isinstance(topic, dict):
                    linked = topic.get("linked_component_ids") or []
                else:
                    linked = getattr(topic, "linked_component_ids", []) or []
                for comp_id in linked:
                    if known_component_ids and comp_id not in known_component_ids:
                        errors.append(ValidationEntry(
                            code="UNRESOLVED_COMPONENT_ID",
                            message=f"course topic [{idx}] references missing component {comp_id!r}",
                            artifact="course_mapping",
                            path=f"$.topics[{idx}].linked_component_ids",
                            source_stage="export_validation",
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

    def _check_dsl_edges(
        self,
        dsl,
        errors: list,
        warnings: list,
    ) -> None:
        """Verify DSL graph edges have non-empty source and target."""
        node_ids: set[str] = {n.node_id for n in (dsl.nodes or [])}
        for edge in dsl.edges or []:
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

    def _check_component_graph_artifact(
        self,
        artifacts: dict,
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

        node_ids: set[str] = set()
        for idx, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            comp_id = str(node.get("component_id") or "")
            if comp_id:
                node_ids.add(comp_id)
            if not str(node.get("label") or "").strip():
                errors.append(ValidationEntry(
                    code="COMPONENT_GRAPH_NODE_MISSING_LABEL",
                    message=f"component graph node at index {idx} has empty label",
                    artifact="component_graph",
                    path=f"$.nodes[{idx}].label",
                    source_stage="export_validation",
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
