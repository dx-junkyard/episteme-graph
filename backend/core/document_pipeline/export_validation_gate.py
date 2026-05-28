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
class ExportValidationResult:
    status: str                          # one of EXPORT_STATUSES
    exportable: bool
    publish_ready: bool
    errors: list[ValidationEntry] = field(default_factory=list)
    warnings: list[ValidationEntry] = field(default_factory=list)
    review_items: list[ValidationEntry] = field(default_factory=list)
    summary: ValidationSummary = field(default_factory=ValidationSummary)

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

        # 1. Aggregate validation_issues from all stage artifacts
        self._aggregate_artifact_issues(
            artifacts, errors, warnings, review_items
        )

        # 2. Cross-artifact ID validation
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

        # 3. Course mapping → component ID resolution
        if course_mapping and component_result:
            self._cross_validate_course_mapping(
                course_mapping, component_result, errors, warnings,
            )

        # 4. DSL graph edge completeness
        if dsl:
            self._check_dsl_edges(dsl, errors, warnings)

        # 5. Required artifact presence
        self._check_required_artifacts(artifacts, errors)

        # 6. source-backed claim must reference EvidenceRegistry (#257)
        if claim_objects and evidence:
            self._check_source_backed_claims(claim_objects, evidence, warnings)

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
            summary=summary,
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

            for cid in refs.get("claim_ids") or []:
                if known_claim_ids and cid not in known_claim_ids:
                    errors.append(ValidationEntry(
                        code="UNRESOLVED_COMPONENT_CLAIM_ID",
                        message=f"component {comp_id!r} references missing claim {cid!r}",
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
                if claim_linked and policy.get("can_support_claim") is False:
                    errors.append(ValidationEntry(
                        code="NON_SUPPORTING_EQUATION_USED_FOR_CLAIM_SUPPORT",
                        message=(
                            f"component {comp_id!r} links claim evidence to equation {eq_id!r}, "
                            "but equation.confidence_policy.can_support_claim is false"
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
                    errors.append(ValidationEntry(
                        code="NON_SUPPORTING_EQUATION_USED_AS_COMPONENT_OUTPUT",
                        message=(
                            f"component {comp_id!r} uses equation {eq_id!r} as output, "
                            "but equation.confidence_policy.can_support_claim is false"
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
        return (
            bool(eq.get("needs_math_review"))
            or bool(eq.get("review_flags"))
            or eq.get("semantic_status") == "reconstruction_based"
            or reconstruction.get("status") not in (None, "", "none")
            or bool(policy.get("must_not_treat_as_source_extracted"))
            or policy.get("can_support_claim") is False
        )

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

    def _check_source_backed_claims(
        self,
        claim_objects,
        evidence,
        warnings: list,
    ) -> None:
        """Warn on source_backed claims that have no source_evidence_ids (#257).

        A source_backed claim without source_evidence_ids means the claim cannot
        be traced back to a PDF-derived Evidence record. This breaks the assumption
        that source_backed ↔ EvidenceRegistry linkage is verified.
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
                warnings.append(ValidationEntry(
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
                # Warn if any referenced evidence_id is not in the registry
                for eid in ev_ids:
                    if known_evidence_ids and eid not in known_evidence_ids:
                        warnings.append(ValidationEntry(
                            code="SOURCE_BACKED_CLAIM_UNRESOLVED_EVIDENCE_ID",
                            message=(
                                f"source_backed claim {getattr(claim, 'claim_id', '?')!r} "
                                f"references evidence {eid!r} not in EvidenceRegistry"
                            ),
                            artifact="claim_object_builder",
                            path=f"$.claims[{getattr(claim, 'claim_id', '?')}].source_evidence_ids",
                            source_stage="export_validation",
                        ))

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
