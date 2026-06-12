"""DerivationChainAgent.

EquationSemanticsResult の derivation_links を統合し、終端式（leaf result equation）
から逆方向に traversal して導出 chain を構築する。

Issue #261: equation-only chain を Claim/Evidence/Assumption を含む推論チェーンへ拡張。
- claim_build_result を受け取り、equation リンクがない場合は claim order / claim_type から chain を生成する。

design:
- LLM-first ではなく、equation_semantics の局所リンクから決定論的に組み立てる
- LLM 補助は operation 名の正規化や teaching_takeaway 生成のために
  optional な enricher として差し込めるようにする（本実装では省略）
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from episteme_graph.agents.equation_semantics.schema import (
    EquationRecord,
    EquationSemanticsResult,
)

from .schema import (
    DEFAULT_OPERATION,
    OPERATION_ONTOLOGY,
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
    ValidationIssue,
)

# Mapping from claim_type → preferred operation (issue #261)
_CLAIM_TYPE_TO_OPERATION: dict[str, str] = {
    "assumption": "state_assumption",
    "definition": "apply_definition",
    "equation_definition": "apply_definition",
    "criterion": "apply_criterion",
    "observable_definition": "introduce_observable",
    "equation_relation": "apply_equation",
    "operator_relation": "apply_equation",
    "equation_transformation": "substitute",
    "measurement_or_update": "apply_measurement_or_update",
    "incompatibility_or_constraint": "apply_non_disturbance_or_independence",
    "comparison": "compare",
    "result": "infer_conclusion",
    "conclusion": "infer_conclusion",
    "causal_or_dependency_claim": "infer_intermediate_claim",
    "limitation": "flag_limitation",
}

# Generic operations that name no concrete theory operation (issue #361).
# Mirrors component_graph.schema.GENERIC_OPERATIONS so steps the graph layer
# would flag are reclassified here first.
_GENERIC_OPERATIONS = {"transform", "relate", "connect", "support", "associate", ""}

# Secondary equation roles → concrete OPERATION_ONTOLOGY verb (issue #361).
_SECONDARY_TYPE_TO_OPERATION = {
    "definition": "apply_definition",
    "result": "derive_result",
    "constraint": "apply_constraint",
    "approximation": "approximate",
    "transformation": "substitute",
}


class DerivationChainAgent:
    """EquationSemanticsResult (and optionally ClaimObjectBuildResult) から DerivationChain を組み立てる。"""

    def __init__(self) -> None:
        pass

    def run(
        self,
        equations: EquationSemanticsResult,
        *,
        cartridge_id: str | None = None,
        claim_link_index: dict[str, list[str]] | None = None,
        claim_build_result: object | None = None,
        evidence_registry: object | None = None,
    ) -> DerivationChainResult:
        records: list[EquationRecord] = list(equations.equations)
        all_records_by_id: dict[str, EquationRecord] = {r.equation_id: r for r in records}
        blocked_equation_ids = {
            r.equation_id
            for r in records
            if not getattr(r.confidence_policy, "can_be_used_in_derivation", False)
        }
        records = [r for r in records if r.equation_id not in blocked_equation_ids]

        # When no equations available, fall back to claim-based chain (issue #261)
        if not records:
            blocked_issues = [
                ValidationIssue(
                    rule_id="derivation_excludes_inconsistent_equation",
                    severity="warning",
                    message=f"equation {eq_id!r} cannot be used in derivations due to confidence/consistency policy",
                    field=eq_id,
                )
                for eq_id in sorted(blocked_equation_ids)
            ]
            claim_chains = self._build_claim_chains(
                equations.document_id,
                claim_build_result,
                evidence_registry,
                cartridge_id,
            )
            if claim_chains:
                return DerivationChainResult(
                    document_id=equations.document_id,
                    cartridge_id=cartridge_id,
                    chains=claim_chains,
                    validation_issues=blocked_issues + [ValidationIssue(
                        rule_id="derivation_equation_only_fallback",
                        severity="info",
                        message="No equations; derivation chains built from claim source order.",
                    )],
                )
            return DerivationChainResult(
                document_id=equations.document_id,
                cartridge_id=cartridge_id,
                chains=[],
                validation_issues=blocked_issues + [ValidationIssue(
                    rule_id="derivation_no_equations",
                    severity="warning",
                    message="No equations in input; nothing to chain.",
                )],
            )

        eq_by_id: dict[str, EquationRecord] = {r.equation_id: r for r in records}
        # Adjacency: from -> to (the local link "this eq is derived from those")
        from_map: dict[str, list[str]] = defaultdict(list)
        # Reverse: to -> from (which downstream equations cite this one as input)
        children: dict[str, list[str]] = defaultdict(list)
        for r in records:
            srcs = list(r.semantics.input_equation_ids or [])
            for s in srcs:
                if s in eq_by_id:
                    from_map[r.equation_id].append(s)
                    children[s].append(r.equation_id)
                elif s in blocked_equation_ids:
                    continue

        # Identify "leaf result" equations: have inbound links but no outbound usage,
        # or are tagged as equation_result.
        leaf_ids: list[str] = []
        for r in records:
            primary = r.semantics.equation_type
            has_inputs = bool(from_map.get(r.equation_id))
            has_consumers = bool(children.get(r.equation_id))
            if primary == "result" and has_inputs:
                leaf_ids.append(r.equation_id)
            elif has_inputs and not has_consumers:
                leaf_ids.append(r.equation_id)

        if not leaf_ids:
            # Fallback: any equation with inputs becomes a leaf.
            leaf_ids = [r.equation_id for r in records if from_map.get(r.equation_id)]

        chains: list[DerivationChainRecord] = []
        issues: list[ValidationIssue] = [
            ValidationIssue(
                rule_id="derivation_excludes_inconsistent_equation",
                severity="warning",
                message=f"equation {eq_id!r} cannot be used in derivations due to confidence/consistency policy",
                field=eq_id,
            )
            for eq_id in sorted(blocked_equation_ids)
        ]
        seen_pairs: set[tuple[str, str]] = set()
        chain_counter = 0

        for leaf_id in leaf_ids:
            chain_counter += 1
            steps, sections = self._walk_back(leaf_id, from_map, eq_by_id, claim_link_index or {})
            if not steps:
                continue
            # Deduplicate identical chains (same edge set).
            edge_signature = tuple(
                (tuple(s.input_equation_ids), tuple(s.output_equation_ids))
                for s in steps
            )
            if edge_signature in seen_pairs:
                continue
            seen_pairs.add(edge_signature)

            derivation_id = f"derivation_{leaf_id}"
            chains.append(DerivationChainRecord(
                derivation_id=derivation_id,
                document_id=equations.document_id,
                source_section_ids=sorted(sections),
                steps=steps,
                teaching_takeaway=self._generate_takeaway(steps, eq_by_id),
                blackbox_policy_suggestion={
                    "expand_steps": [s.step_id for s in steps[-2:]],
                    "blackbox_steps": [s.step_id for s in steps[:-2]] if len(steps) > 2 else [],
                },
                review_status=_chain_review_status(steps),
                review_reason=_chain_review_reason(steps),
                confidence_gate=_merge_step_confidence_gates(steps),
            ))

        # Quality checks (issue #237 / #261 acceptance criteria)
        for chain in chains:
            chain_has_any_assumption = any(
                s.assumption_refs or s.assumption_ids for s in chain.steps
            )
            chain_has_any_claim = any(
                s.required_claim_ids or s.input_claim_ids or s.output_claim_ids
                for s in chain.steps
            )
            for s in chain.steps:
                has_eq_input = bool(s.input_equation_ids)
                has_claim_input = bool(s.input_claim_ids or s.required_claim_ids)
                if not has_eq_input and not has_claim_input:
                    issues.append(ValidationIssue(
                        rule_id="derivation_step_missing_input",
                        severity="warning",
                        message=f"step {s.step_id} has no input_equation_ids or input_claim_ids",
                        field=chain.derivation_id,
                    ))
                has_eq_output = bool(s.output_equation_ids)
                has_claim_output = bool(s.output_claim_ids)
                if not has_eq_output and not has_claim_output:
                    issues.append(ValidationIssue(
                        rule_id="derivation_step_missing_output",
                        severity="warning",
                        message=f"step {s.step_id} has no output_equation_ids or output_claim_ids",
                        field=chain.derivation_id,
                    ))
                if not s.operation or s.operation in {DEFAULT_OPERATION, "relate"}:
                    issues.append(ValidationIssue(
                        rule_id="derivation_step_generic_operation",
                        severity="info",
                        message=f"step {s.step_id} uses generic operation {s.operation!r}",
                        field=chain.derivation_id,
                    ))
                # Unresolved step references (issue #261)
                for ref in (s.input_equation_ids + s.output_equation_ids):
                    if ref and ref not in eq_by_id:
                        if ref in all_records_by_id:
                            issues.append(ValidationIssue(
                                rule_id="derivation_step_references_blocked_eq",
                                severity="warning",
                                message=f"step {s.step_id} references blocked equation {ref!r}",
                                field=chain.derivation_id,
                            ))
                            continue
                        issues.append(ValidationIssue(
                            rule_id="derivation_step_unresolved_eq_ref",
                            severity="warning",
                            message=f"step {s.step_id} references unknown equation {ref!r}",
                            field=chain.derivation_id,
                        ))
            if not chain_has_any_assumption:
                issues.append(ValidationIssue(
                    rule_id="derivation_chain_missing_assumptions",
                    severity="warning",
                    message=f"chain {chain.derivation_id} has no assumption_refs on any step",
                    field=chain.derivation_id,
                ))
            if not chain_has_any_claim and (claim_link_index or claim_build_result):
                issues.append(ValidationIssue(
                    rule_id="derivation_chain_missing_claim_links",
                    severity="warning",
                    message=f"chain {chain.derivation_id} has no claim links on any step",
                    field=chain.derivation_id,
                ))
            if not chain.teaching_takeaway:
                issues.append(ValidationIssue(
                    rule_id="derivation_chain_missing_teaching_takeaway",
                    severity="warning",
                    message=f"chain {chain.derivation_id} has no teaching_takeaway",
                    field=chain.derivation_id,
                ))

        return DerivationChainResult(
            document_id=equations.document_id,
            cartridge_id=cartridge_id,
            chains=chains,
            validation_issues=issues,
        )

    # ------------------------------------------------------------------
    def _walk_back(
        self,
        leaf_id: str,
        from_map: dict[str, list[str]],
        eq_by_id: dict[str, EquationRecord],
        claim_link_index: dict[str, list[str]],
    ) -> tuple[list[DerivationStep], set[str]]:
        steps: list[DerivationStep] = []
        sections: set[str] = set()
        visited_edges: set[tuple[tuple[str, ...], str]] = set()
        # BFS by output equation
        frontier: list[str] = [leaf_id]
        step_counter = 0

        while frontier:
            current = frontier.pop(0)
            sources = from_map.get(current) or []
            if not sources:
                continue
            key = (tuple(sorted(sources)), current)
            if key in visited_edges:
                continue
            visited_edges.add(key)
            step_counter += 1

            current_record = eq_by_id.get(current)
            section_id = (
                current_record.source_extraction.source_location.get("section_id")
                if current_record else None
            )
            if section_id:
                sections.add(section_id)

            assumptions: list[str] = []
            if current_record:
                assumptions = list(current_record.semantics.assumptions)

            operation = self._infer_operation(current_record)
            operation = self._refine_generic_operation(operation, current_record)
            linked_claims = list(claim_link_index.get(current, []))
            sem_claims = list(current_record.semantics.linked_claim_ids) if current_record else []
            all_claim_ids = sorted(set(linked_claims + sem_claims))
            ev_ids = list(current_record.semantics.source_evidence_ids) if current_record else []
            step = DerivationStep(
                step_id=f"step_{step_counter:03d}",
                input_equation_ids=list(sources),
                operation=operation,
                output_equation_ids=[current],
                required_claim_ids=all_claim_ids,
                assumption_refs=assumptions,
                reason=current_record.semantics.summary if current_record else "",
                confidence=current_record.semantics.confidence if current_record else 0.0,
                input_claim_ids=[],
                output_claim_ids=[],
                assumption_ids=assumptions,
                source_evidence_ids=ev_ids,
                review_status="teacher_review_required",
                review_reason="",
                confidence_gate=_confidence_gate([]),
            )
            steps.append(step)
            for s in sources:
                if from_map.get(s):
                    frontier.append(s)

        # Reverse so steps go input → output chronologically
        steps.reverse()
        # Renumber for readability
        for idx, s in enumerate(steps, start=1):
            s.step_id = f"step_{idx:03d}"
        return steps, sections

    def _build_claim_chains(
        self,
        document_id: str,
        claim_build_result: object | None,
        evidence_registry: object | None,
        cartridge_id: str | None,
    ) -> list[DerivationChainRecord]:
        """Claim type/source order ベースで軽量な chain を生成する (issue #261)."""
        if not claim_build_result:
            return []
        claims = getattr(claim_build_result, "claims", []) or []
        if not claims:
            return []

        # Build evidence lookup: evidence_id → evidence record
        ev_by_block: dict[str, list[str]] = {}
        for ev_rec in getattr(evidence_registry, "records", []) or []:
            src = getattr(ev_rec, "source", None)
            bid = getattr(src, "block_id", None) if src else None
            ev_id = getattr(ev_rec, "evidence_id", None)
            if bid and ev_id:
                ev_by_block.setdefault(bid, []).append(ev_id)

        # Group claims by section, preserve order
        section_groups: dict[str, list] = {}
        no_section: list = []
        for c in claims:
            sid = getattr(c, "section_id", None) or ""
            if sid:
                section_groups.setdefault(sid, []).append(c)
            else:
                no_section.append(c)

        chains: list[DerivationChainRecord] = []
        chain_counter = 0

        for section_id, section_claims in section_groups.items():
            if len(section_claims) < 2:
                continue
            chain_counter += 1
            steps: list[DerivationStep] = []
            prev_claim_id: str | None = None

            for step_idx, claim in enumerate(section_claims, start=1):
                claim_id = getattr(claim, "claim_id", None) or f"claim_{step_idx}"
                claim_type = getattr(claim, "claim_type", "unknown") or "unknown"
                operation = _CLAIM_TYPE_TO_OPERATION.get(claim_type, "infer_intermediate_claim")

                # Evidence IDs for this claim
                source_ev_ids: list[str] = list(getattr(claim, "source_evidence_ids", []) or [])

                steps.append(DerivationStep(
                    step_id=f"step_{step_idx:03d}",
                    input_equation_ids=[],
                    operation=operation,
                    output_equation_ids=[],
                    required_claim_ids=[prev_claim_id] if prev_claim_id else [],
                    assumption_refs=list(claim_id for c2 in section_claims[:step_idx - 1]
                                        if getattr(c2, "claim_type", "") == "assumption"
                                        for claim_id in [getattr(c2, "claim_id", "")]),
                    reason=getattr(claim, "review_note", "") or "",
                    confidence=float(getattr(claim, "confidence", 0.5) or 0.5),
                    input_claim_ids=[prev_claim_id] if prev_claim_id else [],
                    output_claim_ids=[claim_id],
                    assumption_ids=[],
                    source_evidence_ids=source_ev_ids,
                    review_status="teacher_review_required",
                ))
                prev_claim_id = claim_id

            chains.append(DerivationChainRecord(
                derivation_id=f"derivation_claim_{chain_counter:04d}",
                document_id=document_id,
                source_section_ids=[section_id],
                steps=steps,
                teaching_takeaway=f"Logical progression through {len(steps)} claims in section {section_id}",
                blackbox_policy_suggestion={},
                source_scope={"section_id": section_id},
                linked_component_ids=[],
                chain_type="claim_chain",
                review_status=_chain_review_status(steps),
                review_reason=_chain_review_reason(steps),
                confidence_gate=_merge_step_confidence_gates(steps),
            ))

        return chains

    @staticmethod
    def _generate_takeaway(
        steps: list[DerivationStep],
        eq_by_id: dict[str, EquationRecord],
    ) -> str:
        if not steps:
            return ""
        first_inputs = steps[0].input_equation_ids
        last_output = steps[-1].output_equation_ids
        if not first_inputs or not last_output:
            return ""
        ops = [s.operation for s in steps if s.operation]
        op_summary = " -> ".join(ops) if ops else "transform"
        return (
            f"Derive {', '.join(last_output)} from {', '.join(first_inputs)} "
            f"via {op_summary}"
        )

    @staticmethod
    def _infer_operation(record: Optional[EquationRecord]) -> str:
        if record is None:
            return DEFAULT_OPERATION
        text = " ".join([
            str(getattr(record, "label", "") or ""),
            record.semantics.summary or "",
            record.semantics.reason or "",
            " ".join(record.semantics.used_symbols or []),
            " ".join(s.symbol for s in (record.semantics.defined_symbols or [])),
        ]).lower()
        if "skewness" in text and "linear" in text and "bias" in text:
            return "linearize_skewness_bias_dependence"
        if "kurtosis" in text and "linear" in text and "bias" in text:
            return "linearize_kurtosis_bias_dependence"
        if ("second" in text or "2nd" in text) and "bias" in text and ("solve" in text or "eliminat" in text):
            return "solve_second_order_bias"
        if ("third" in text or "3rd" in text) and "bias" in text and ("solve" in text or "eliminat" in text):
            return "solve_third_order_bias"
        if "substitut" in text and ("second" in text or "2nd" in text):
            return "substitute_second_order_bias"
        if "substitut" in text and ("third" in text or "3rd" in text):
            return "substitute_third_order_bias"
        if "skewness" in text and "consistency relation" in text:
            return "derive_skewness_consistency_relation"
        if "kurtosis" in text and "consistency relation" in text:
            return "derive_first_kurtosis_consistency_relation"
        primary = record.semantics.equation_type
        mapping = {
            "definition": "define",
            "relation": "relate",
            "transformation": "transform",
            "approximation": "approximate",
            "result": "derive_result",
            "constraint": "apply_constraint",
        }
        return mapping.get(primary, DEFAULT_OPERATION)

    @staticmethod
    def _refine_generic_operation(operation: str, record: Optional[EquationRecord]) -> str:
        """Single deterministic reclassification of a generic operation (#361).

        Before a step is emitted with a generic operation (which the graph
        layer would push toward the debug layer), try once to pick a concrete
        OPERATION_ONTOLOGY verb from the equation's own signals: secondary
        roles first, then defined symbols. When no signal supports a concrete
        verb the operation stays generic — review keeps it honest.
        """
        if record is None or str(operation or "").strip().lower() not in _GENERIC_OPERATIONS:
            return operation
        sem = record.semantics
        for secondary in sem.secondary_types or []:
            mapped = _SECONDARY_TYPE_TO_OPERATION.get(str(secondary).strip().lower())
            if mapped:
                return mapped
        has_definition = any(
            str(getattr(s, "definition_status", "") or "") in ("defined", "redefined")
            for s in (sem.defined_symbols or [])
        )
        if has_definition:
            return "apply_definition"
        return operation


def _confidence_gate(blocked_eqs: list[str]) -> dict:
    if not blocked_eqs:
        return {
            "blocked_by_equation_ids": [],
            "blocked_reason": "",
            "downstream_allowed_use": "display_with_warning",
        }
    return {
        "blocked_by_equation_ids": list(blocked_eqs),
        "blocked_reason": "linked equation cannot support claim or derivation",
        "downstream_allowed_use": "blocked",
    }


def _merge_step_confidence_gates(steps: list[DerivationStep]) -> dict:
    blocked: list[str] = []
    for step in steps:
        gate = step.confidence_gate or {}
        blocked.extend(str(v) for v in gate.get("blocked_by_equation_ids") or [] if v)
    seen: set[str] = set()
    uniq = [v for v in blocked if not (v in seen or seen.add(v))]
    return _confidence_gate(uniq)


def _chain_review_status(steps: list[DerivationStep]) -> str:
    if any((s.confidence_gate or {}).get("blocked_by_equation_ids") for s in steps):
        return "teacher_review_required"
    return "teacher_review_required"


def _chain_review_reason(steps: list[DerivationStep]) -> str:
    gate = _merge_step_confidence_gates(steps)
    return gate.get("blocked_reason") or ""
