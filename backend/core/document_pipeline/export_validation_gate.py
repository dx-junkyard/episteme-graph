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
            self._check_dsl_edges(dsl, errors, warnings)

        # 5. Component graph export structure
        self._check_component_graph_artifact(artifacts, errors, warnings)

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
        # equation role conflicts across stages. Warnings only — the links are
        # kept, never dropped.
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
                )

                if code in _NEEDS_REVIEW_RULE_IDS:
                    review_items.append(entry)
                elif code in _FORCED_ERROR_RULE_IDS:
                    errors.append(entry)
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

    def _check_claim_equation_link_symmetry(
        self,
        claim_objects,
        artifacts: dict,
        warnings: list,
    ) -> None:
        """Report one-way claim↔equation links (issue #358).

        ``claim.equation_ids`` and ``equation.linked_claim_ids`` are maintained
        by different stages and can drift apart. A one-way link is downgraded
        to review metadata (warning) — the link itself is kept, never dropped.
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
        main_claim_ids, main_equation_ids = self._main_graph_backing_ids(
            artifacts, component_result
        )

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
                     final_claim_ids, main_claim_ids),
                    ("equation", entry.get("equation_ids") or [],
                     known_equation_ids, main_equation_ids),
                )
                for ref_type, refs, known_ids, covered_ids in ref_groups:
                    for ref in refs:
                        ref_id = str(ref or "")
                        if not ref_id:
                            continue
                        section_total += 1
                        if known_ids and ref_id not in known_ids:
                            reason = f"missing_{ref_type}"
                        elif ref_id not in covered_ids:
                            reason = "not_linked_to_main_graph"
                        else:
                            section_reachable += 1
                            continue
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

    @staticmethod
    def _main_graph_backing_ids(
        artifacts: dict, component_result
    ) -> tuple[set[str], set[str]]:
        """Claim / equation ids backed by main-layer graph nodes (issue #354).

        A main node covers its own linked ids plus those of the equation_detail
        members it aggregates. When the component_graph artifact is absent the
        assembled components stand in as reachability targets so the report
        degrades instead of marking everything unreachable.
        """
        claim_ids: set[str] = set()
        equation_ids: set[str] = set()
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
                members = [node] + [
                    node_by_id.get(str(m))
                    for m in node.get("member_component_ids") or []
                ]
                for member in members:
                    if not isinstance(member, dict):
                        continue
                    for key in node_claim_keys:
                        claim_ids.update(
                            str(v) for v in member.get(key) or [] if v
                        )
                    for key in node_equation_keys:
                        equation_ids.update(
                            str(v) for v in member.get(key) or [] if v
                        )
            return claim_ids, equation_ids

        for component in getattr(component_result, "components", []) or []:
            refs = getattr(component, "evidence_refs", {}) or {}
            claim_ids.update(
                str(v) for v in (refs.get("claim_ids") or []) if v
            )
            for key in node_claim_keys:
                claim_ids.update(
                    str(v) for v in (getattr(component, key, []) or []) if v
                )
            equation_ids.update(
                ExportValidationGate._component_equation_refs(component, refs)
            )
        return claim_ids, equation_ids

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
