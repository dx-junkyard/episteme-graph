"""ComponentAssemblyAgent: assemble reusable components from claims, equations, thesis, and DSL."""
from __future__ import annotations

import logging

from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult
from episteme_graph.agents.dsl_linking.schema import DSLLinkingResult
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult
from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult
from episteme_graph.agents.claim_selection import selection_issue_payloads
from episteme_graph.agents.id_canonicalization import (
    canonicalize_claim_refs,
    claim_aliases_from_accepted_claims,
)

from .cartridge_loader import CartridgeLoader
from .component_refiner import ComponentRefiner
from .derivation_graph_aligner import DerivationGraphAligner
from .enrichment import enrich_component_assembly
from .granularity_analyzer import ComponentGranularityAnalyzer
from .input_builder import ComponentAssemblyInputBuilder
from .llm_client import ComponentAssemblyLLMClient
from .overlap_cleanup import ComponentOverlapCleanup
from .prompt import ComponentAssemblyPromptFactory
from .repair import ComponentAssemblyRepairer, _parse_raw, make_deterministic_fallback
from .schema import CartridgeContext, ComponentAssemblyLLMInput, ComponentAssemblyResult, ValidationIssue
from .theory_bundle import TheoryBundleStage
from .validator import ComponentAssemblyValidator

logger = logging.getLogger(__name__)


class ComponentAssemblyAgent:
    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._input_builder = ComponentAssemblyInputBuilder()
        self._prompt_factory = ComponentAssemblyPromptFactory()
        self._llm_client = ComponentAssemblyLLMClient(model=llm_model)
        self._cleanup = ComponentOverlapCleanup()
        self._validator = ComponentAssemblyValidator()
        self._repairer = ComponentAssemblyRepairer(cleanup=self._cleanup)
        self._refiner = ComponentRefiner()
        self._granularity_analyzer = ComponentGranularityAnalyzer()
        self._derivation_graph_aligner = DerivationGraphAligner()
        self._theory_bundle_stage = TheoryBundleStage()

    def run(
        self,
        qualified_claims: ClaimQualificationResult,
        equations: EquationSemanticsResult | None = None,
        thesis: ThesisReconstructionResult | None = None,
        dsl: DSLLinkingResult | None = None,
        cartridge_id: str | None = None,
        config: dict | None = None,
        claim_objects=None,
        evidence_registry=None,
        derivations=None,
    ) -> ComponentAssemblyResult:
        cartridge = self._load_cartridge(cartridge_id)
        llm_input = self._input_builder.build(
            qualified_claims,
            equations=equations,
            thesis=thesis,
            dsl=dsl,
            cartridge=cartridge,
            config=config,
            claim_objects=claim_objects,
            evidence_registry=evidence_registry,
            derivations=derivations,
        )
        diagnostics: dict = {}
        # Defensively strip dangling equation references (#368 follow-up): a
        # claim / thesis / DSL ref to an equation absent from the final set must
        # not collapse the whole assembly into a deterministic fallback.
        equation_ref_cleanup = _strip_unavailable_equation_refs(llm_input)
        if equation_ref_cleanup:
            diagnostics["component_assembly_equation_ref_cleanup"] = equation_ref_cleanup
            logger.warning(
                "Stripped %d dangling equation reference(s) before component assembly: "
                "document=%s equation_ids=%s",
                len(equation_ref_cleanup),
                qualified_claims.document_id,
                sorted({r["equation_id"] for r in equation_ref_cleanup}),
            )
        diagnostics["component_assembly_input_validation"] = _preflight_check(llm_input)
        if diagnostics["component_assembly_input_validation"]["status"] == "failed":
            logger.warning(
                "Component assembly input preflight failed: document=%s issue_codes=%s",
                qualified_claims.document_id,
                [issue["code"] for issue in diagnostics["component_assembly_input_validation"]["issues"]],
            )
            result = make_deterministic_fallback(
                llm_input,
                "component_assembly_input_validation failed",
                [
                    ValidationIssue(
                        issue["code"],
                        "error",
                        issue["message"],
                        issue.get("field"),
                    )
                    for issue in diagnostics["component_assembly_input_validation"]["issues"]
                ],
            )
            diagnostics["fallback_reason"] = "component_assembly_input_validation failed"
            diagnostics["original_failure_codes"] = [
                issue["code"] for issue in diagnostics["component_assembly_input_validation"]["issues"]
            ]
            result.diagnostics = diagnostics
            self._record_claim_exclusions(result, llm_input)
            return result

        messages = self._prompt_factory.build_messages(llm_input)
        try:
            raw_output = self._llm_client.generate(messages)
        except Exception as exc:
            logger.error("Component assembly failed: %s", exc)
            result = make_deterministic_fallback(llm_input, str(exc))
            diagnostics["initial_llm_exception"] = str(exc)
            diagnostics["fallback_reason"] = str(exc)
            diagnostics["original_failure_codes"] = ["initial_llm_exception"]
            result.diagnostics = diagnostics
            self._record_claim_exclusions(result, llm_input)
            return result
        diagnostics["initial_llm_raw_output"] = _llm_capture(self._llm_client, raw_output)
        diagnostics["initial_llm_output_component_count"] = diagnostics["initial_llm_raw_output"]["component_count"]
        raw_output = canonicalize_claim_refs(
            raw_output,
            claim_objects,
            claim_aliases_from_accepted_claims(llm_input.accepted_claims),
        )
        parsed = _parse_raw(raw_output, qualified_claims.document_id, llm_input.cartridge_id)
        diagnostics["parsed_component_count"] = len(parsed.components)
        result = self._cleanup.cleanup(parsed)
        diagnostics["cleanup_component_count"] = len(result.components)
        result = enrich_component_assembly(result, llm_input)
        diagnostics["enriched_component_count"] = len(result.components)
        issues = self._validator.validate(result, cartridge, llm_input=llm_input)
        diagnostics["initial_validation_issues"] = _issue_dicts(issues, llm_input=llm_input)
        diagnostics["initial_error_codes"] = [
            issue.rule_id for issue in issues if issue.severity == "error"
        ]
        if [i for i in issues if i.severity == "error"]:
            result = self._repairer.repair(
                llm_input=llm_input,
                raw_output=raw_output,
                validation_issues=issues,
                cartridge=cartridge,
                llm_client=self._llm_client,
                prompt_factory=self._prompt_factory,
                validator=self._validator,
                diagnostics=diagnostics,
            )
            result = enrich_component_assembly(result, llm_input)
        else:
            result.validation_issues = issues

        # Step 1: detect component granularity issues before graph/mapping.
        # This pass annotates components only; it never changes component count.
        if (config or {}).get("enable_component_granularity_analyzer", True):
            result = self._granularity_analyzer.analyze(result, derivations=derivations)
            result.validation_issues = self._validator.validate(
                result, cartridge, llm_input=llm_input
            )

        # Step 3: actual refinement/splitting. Now conditionally DEFAULT-ON
        # (issue #385): when the granularity analyzer detects quality triggers
        # (split recommendations, mixed/coarse components, few components with
        # many artifacts, or unlinked derivation chains) the refiner runs
        # automatically. An explicit ``enable_component_refiner`` config value
        # (True/False) always overrides the automatic decision.
        refiner_cfg = (config or {}).get("enable_component_refiner")
        if refiner_cfg is None:
            run_refiner, refiner_reasons = _refinement_triggered(result, llm_input, derivations)
        else:
            run_refiner, refiner_reasons = bool(refiner_cfg), (["explicit_config"] if refiner_cfg else [])
        diagnostics["component_refiner_decision"] = {
            "ran": run_refiner,
            "auto": refiner_cfg is None,
            "triggers": refiner_reasons,
            "component_count_before": len(result.components),
        }
        if run_refiner:
            result = self._refiner.refine(result, llm_input, derivations)
            result.validation_issues = self._validator.validate(
                result, cartridge, llm_input=llm_input
            )
            diagnostics["component_refiner_decision"]["component_count_after"] = len(result.components)

        # Step 4: align refined components with derivation chains, equation
        # operations, the theory component graph, and the support map. Disabled
        # by default so the upstream contract stays stable until opted in.
        if (config or {}).get("enable_derivation_graph_aligner", False):
            alignment = self._derivation_graph_aligner.align(
                result, llm_input=llm_input, derivations=derivations
            )
            result.derivation_graph_alignment = alignment.to_dict()

        # Step 5: represent the whole paper as a TheoryBundle and map refined
        # components into a teaching output (course topics + minimal blueprint
        # reflection). Disabled by default; depends on Step 3/4 output being
        # present, so it stays additive until opted in.
        if (config or {}).get("enable_theory_bundle_builder", False):
            bundle = self._theory_bundle_stage.run(
                result, llm_input=llm_input, derivations=derivations
            )
            result.theory_bundle = bundle.to_dict()

        merged_diagnostics = dict(diagnostics)
        merged_diagnostics.update(result.diagnostics or {})
        result.diagnostics = merged_diagnostics
        self._record_claim_exclusions(result, llm_input)
        return result

    @staticmethod
    def _record_claim_exclusions(result, llm_input) -> None:
        """Persist limit-dropped claims and surface them as warnings (#356)."""
        excluded = list(getattr(llm_input, "excluded_from_pipeline_input", []) or [])
        if not excluded:
            return
        result.excluded_from_pipeline_input = excluded
        result.validation_issues = list(result.validation_issues or []) + [
            ValidationIssue(**payload)
            for payload in selection_issue_payloads(
                excluded, stage="component_assembly"
            )
        ]

    def _load_cartridge(self, cartridge_id: str | None) -> CartridgeContext | None:
        if not cartridge_id:
            return None
        try:
            return self._cartridge_loader.load(cartridge_id)
        except FileNotFoundError:
            logger.warning(
                "Cartridge '%s' not found; proceeding without cartridge", cartridge_id
            )
            return None


_REFINER_INTERNAL_FLOW_OPERATIONS = {
    "define", "transform", "solve", "substitute", "eliminate", "infer", "validate",
    "linearize", "approximate", "derive", "marginalize",
}


def _refinement_triggered(result, llm_input, derivations) -> tuple[bool, list[str]]:
    """Decide whether the ComponentRefiner should run by default (issue #385).

    Returns ``(should_run, reasons)``. Domain-independent: the triggers are read
    from the granularity analyzer's structural annotations and artifact counts,
    never from field-specific names.
    """
    reasons: list[str] = []
    components = list(result.components or [])

    for component in components:
        rec = component.split_recommendation or {}
        if rec.get("required"):
            reasons.append("component_split_recommended")
            break

    for component in components:
        status = (component.component_quality or {}).get("granularity_status")
        if status in ("too_coarse", "mixed_responsibility"):
            reasons.append("coarse_or_mixed_responsibility_component")
            break

    for component in components:
        quality = component.component_quality or {}
        if int(quality.get("responsibility_count") or 0) > 1:
            reasons.append("component_has_multiple_responsibility_types")
            break

    # A component whose internal flow mixes several major operations in one unit.
    for component in components:
        ops = {
            str(step.get("relation") or step.get("operation") or "").strip().lower().split("_")[0]
            for step in (component.internal_flow or [])
            if isinstance(step, dict)
        }
        if len(ops & _REFINER_INTERNAL_FLOW_OPERATIONS) > 1:
            reasons.append("internal_flow_mixes_operations")
            break

    # Few components while there is a lot of extracted equation/evidence material.
    eq_count = len(getattr(llm_input, "available_equations", []) or [])
    ev_count = len(getattr(llm_input, "available_evidence", []) or [])
    if components and len(components) <= 2 and (eq_count >= 6 or ev_count >= 12):
        reasons.append("few_components_with_many_artifacts")

    # Derivation chains exist but none are linked to a component.
    chains = list(getattr(derivations, "chains", []) or [])
    if chains and not any(getattr(ch, "linked_component_ids", None) for ch in chains):
        reasons.append("derivation_chains_unlinked_to_components")

    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique = [r for r in reasons if not (r in seen or seen.add(r))]
    return (bool(unique), unique)


def _strip_unavailable_equation_refs(llm_input: ComponentAssemblyLLMInput) -> list[dict]:
    """Drop dangling equation_id references not present in available_equations.

    A claim / thesis node / DSL node-or-edge may reference an equation_id that is
    absent from the final equation set — e.g. LLM drift, or an equation that was
    demoted / dropped upstream (table-derived candidate, prose reconstruction,
    rejected label). Such a dangling reference must not collapse the whole
    component assembly into a deterministic fallback (which then aborts the
    pipeline at the export-validation gate). It is filtered out here and reported
    in diagnostics, mirroring the equation_semantics I/O-link cleanup (#368).

    Mutates ``llm_input`` in place and returns the removed references.
    """
    available = _ids(llm_input.available_equations, "equation_id")
    if not available:
        # No equation set to validate against — the preflight already skips the
        # equation check in this case, so nothing is stripped.
        return []

    removed: list[dict] = []

    def _filter(refs, location: str, owner_id) -> list:
        kept = []
        for ref in refs or []:
            ref_str = str(ref)
            if not ref_str:
                continue
            if ref_str in available:
                kept.append(ref)
            else:
                removed.append({
                    "location": location,
                    "owner_id": owner_id,
                    "equation_id": ref_str,
                })
        return kept

    for claim in llm_input.accepted_claims or []:
        if isinstance(claim, dict) and "equation_ids" in claim:
            claim["equation_ids"] = _filter(
                claim.get("equation_ids"), "accepted_claims.equation_ids", claim.get("claim_id")
            )
    for claim in llm_input.available_claims or []:
        if isinstance(claim, dict) and "equation_ids" in claim:
            claim["equation_ids"] = _filter(
                claim.get("equation_ids"), "available_claims.equation_ids", claim.get("claim_id")
            )
    for node in llm_input.thesis_nodes or []:
        if isinstance(node, dict) and "equation_ids" in node:
            node["equation_ids"] = _filter(
                node.get("equation_ids"), "thesis_nodes.equation_ids",
                node.get("node_id") or node.get("id"),
            )
    for node in llm_input.dsl_nodes or []:
        refs = node.get("source_refs") if isinstance(node, dict) else None
        if isinstance(refs, dict) and "equation_ids" in refs:
            refs["equation_ids"] = _filter(
                refs.get("equation_ids"), "dsl_nodes.source_refs.equation_ids", node.get("node_id")
            )
    for edge in llm_input.dsl_edges or []:
        refs = edge.get("evidence_refs") if isinstance(edge, dict) else None
        if isinstance(refs, dict) and "equation_ids" in refs:
            refs["equation_ids"] = _filter(
                refs.get("equation_ids"), "dsl_edges.evidence_refs.equation_ids", edge.get("edge_id")
            )

    return removed


def _preflight_check(llm_input: ComponentAssemblyLLMInput) -> dict:
    issues: list[dict] = []
    accepted_claim_ids = _ids(llm_input.accepted_claims, "claim_id")
    available_claim_ids = _ids(llm_input.available_claims, "claim_id")
    if available_claim_ids:
        missing = sorted(accepted_claim_ids - available_claim_ids)
        if missing:
            issues.append(_preflight_issue(
                "accepted_claim_ids_not_available",
                "accepted_claims.claim_id",
                missing,
                available_claim_ids,
            ))

    available_evidence_ids = _ids(llm_input.available_evidence, "evidence_id")
    referenced_evidence_ids = set()
    for claim in llm_input.available_claims or []:
        referenced_evidence_ids.update(str(v) for v in claim.get("source_evidence_ids") or [] if str(v))
    if available_evidence_ids:
        missing = sorted(referenced_evidence_ids - available_evidence_ids)
        if missing:
            issues.append(_preflight_issue(
                "accepted_evidence_ids_not_available",
                "available_claims.source_evidence_ids",
                missing,
                available_evidence_ids,
            ))

    available_equation_ids = _ids(llm_input.available_equations, "equation_id")
    referenced_equation_ids = set()
    for claim in llm_input.available_claims or []:
        referenced_equation_ids.update(str(v) for v in claim.get("equation_ids") or [] if str(v))
    for node in llm_input.thesis_nodes or []:
        referenced_equation_ids.update(str(v) for v in node.get("equation_ids") or [] if str(v))
    for node in llm_input.dsl_nodes or []:
        refs = node.get("source_refs") or {}
        referenced_equation_ids.update(str(v) for v in refs.get("equation_ids") or [] if str(v))
    for edge in llm_input.dsl_edges or []:
        refs = edge.get("evidence_refs") or {}
        referenced_equation_ids.update(str(v) for v in refs.get("equation_ids") or [] if str(v))
    if available_equation_ids:
        missing = sorted(referenced_equation_ids - available_equation_ids)
        if missing:
            issues.append(_preflight_issue(
                "accepted_equation_ids_not_available",
                "input.equation_ids",
                missing,
                available_equation_ids,
            ))

    return {
        "status": "failed" if _has_fatal_preflight_issue(issues) else "passed",
        "accepted_claim_count": len(accepted_claim_ids),
        "available_claim_count": len(available_claim_ids),
        "available_evidence_count": len(available_evidence_ids),
        "available_equation_count": len(available_equation_ids),
        "available_derivation_count": len(llm_input.available_derivation_ids or []),
        "issues": issues,
    }


# Dangling equation references are a referential-cleanup concern (handled by
# _strip_unavailable_equation_refs), not broken assembly input. They must never
# collapse the whole assembly into a deterministic fallback that aborts the
# pipeline at export validation (#368 follow-up). Only genuinely missing claim /
# evidence inputs are fatal.
_NON_FATAL_PREFLIGHT_CODES = {"accepted_equation_ids_not_available"}


def _has_fatal_preflight_issue(issues: list[dict]) -> bool:
    return any(issue.get("code") not in _NON_FATAL_PREFLIGHT_CODES for issue in issues)


def _preflight_issue(code: str, field: str, invalid_values: list[str], allowed_values: set[str]) -> dict:
    return {
        "code": code,
        "field": field,
        "invalid_values": invalid_values,
        "allowed_values": sorted(allowed_values),
        "message": f"{field} contains values not present in available IDs: {invalid_values}",
    }


def _ids(items: list[dict], key: str) -> set[str]:
    return {str(item.get(key)) for item in items or [] if item.get(key)}


def _llm_capture(llm_client: ComponentAssemblyLLMClient, parsed: dict) -> dict:
    return {
        "parsed": parsed,
        "component_count": len(parsed.get("components", [])) if isinstance(parsed.get("components"), list) else 0,
        "raw_text": getattr(llm_client, "last_raw_text", None),
        "parse_error": getattr(llm_client, "last_parse_error", None),
    }


def _issue_dicts(
    issues: list[ValidationIssue],
    *,
    llm_input: ComponentAssemblyLLMInput,
) -> list[dict]:
    return [_issue_dict(issue, llm_input=llm_input) for issue in issues]


def _issue_dict(issue: ValidationIssue, *, llm_input: ComponentAssemblyLLMInput) -> dict:
    data = {
        "code": issue.rule_id,
        "severity": issue.severity,
        "message": issue.message,
        "field": issue.field,
    }
    allowed = _allowed_values_for_issue(issue.rule_id, llm_input)
    invalid = _invalid_value_from_message(issue.message)
    if invalid:
        data["invalid_value"] = invalid
    if allowed:
        data["allowed_values"] = sorted(allowed)
    return data


def _allowed_values_for_issue(rule_id: str, llm_input: ComponentAssemblyLLMInput) -> set[str]:
    if rule_id == "unresolved_claim_id":
        return _ids(llm_input.available_claims, "claim_id")
    if rule_id == "unresolved_evidence_id":
        return _ids(llm_input.available_evidence, "evidence_id")
    if rule_id == "unresolved_equation_id":
        return _ids(llm_input.available_equations, "equation_id")
    if rule_id == "unresolved_derivation_id":
        return {str(v) for v in llm_input.available_derivation_ids or [] if str(v)}
    if rule_id == "unresolved_dsl_node_id":
        return _ids(llm_input.available_dsl_nodes, "node_id")
    if rule_id == "unresolved_dsl_edge_id":
        return _ids(llm_input.available_dsl_edges, "edge_id")
    return set()


def _invalid_value_from_message(message: str) -> str | None:
    if "'" not in message:
        return None
    parts = message.split("'")
    return parts[1] if len(parts) >= 3 else None
