"""DerivationChainAgent.

EquationSemanticsResult の derivation_links を統合し、終端式（leaf result equation）
から逆方向に traversal して導出 chain を構築する。

design:
- LLM-first ではなく、equation_semantics の局所リンクから決定論的に組み立てる
- LLM 補助は operation 名の正規化や teaching_takeaway 生成のために
  optional な enricher として差し込めるようにする（本実装では省略）
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from episteme_graph.agents.equation_semantics.schema import (
    EquationSemanticsRecord,
    EquationSemanticsResult,
)

from .schema import (
    DEFAULT_OPERATION,
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
    ValidationIssue,
)


class DerivationChainAgent:
    """EquationSemanticsResult から DerivationChain を組み立てる。"""

    def __init__(self) -> None:
        pass

    def run(
        self,
        equations: EquationSemanticsResult,
        *,
        cartridge_id: str | None = None,
        claim_link_index: dict[str, list[str]] | None = None,
    ) -> DerivationChainResult:
        records: list[EquationSemanticsRecord] = list(equations.equations)
        if not records:
            return DerivationChainResult(
                document_id=equations.document_id,
                cartridge_id=cartridge_id,
                chains=[],
                validation_issues=[ValidationIssue(
                    rule_id="derivation_no_equations",
                    severity="warning",
                    message="No equations in input; nothing to chain.",
                )],
            )

        eq_by_id: dict[str, EquationSemanticsRecord] = {r.equation_id: r for r in records}
        # Adjacency: from -> to (the local link "this eq is derived from those")
        from_map: dict[str, list[str]] = defaultdict(list)
        # Reverse: to -> from (which downstream equations cite this one as input)
        children: dict[str, list[str]] = defaultdict(list)
        for r in records:
            srcs = list(r.derivation_links.from_equations or [])
            for s in srcs:
                if s in eq_by_id:
                    from_map[r.equation_id].append(s)
                    children[s].append(r.equation_id)

        # Identify "leaf result" equations: have inbound links but no outbound usage,
        # or are tagged as equation_result.
        leaf_ids: list[str] = []
        for r in records:
            primary = r.equation_role.primary if r.equation_role else None
            has_inputs = bool(from_map.get(r.equation_id))
            has_consumers = bool(children.get(r.equation_id))
            if primary == "equation_result" and has_inputs:
                leaf_ids.append(r.equation_id)
            elif has_inputs and not has_consumers:
                leaf_ids.append(r.equation_id)

        if not leaf_ids:
            # Fallback: any equation with inputs becomes a leaf.
            leaf_ids = [r.equation_id for r in records if from_map.get(r.equation_id)]

        chains: list[DerivationChainRecord] = []
        issues: list[ValidationIssue] = []
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
                teaching_takeaway="",
                blackbox_policy_suggestion={
                    "expand_steps": [s.step_id for s in steps[-2:]],
                    "blackbox_steps": [s.step_id for s in steps[:-2]] if len(steps) > 2 else [],
                },
            ))

        # Quality checks
        for chain in chains:
            for s in chain.steps:
                if not s.input_equation_ids:
                    issues.append(ValidationIssue(
                        rule_id="derivation_step_missing_input",
                        severity="warning",
                        message=f"step {s.step_id} has no input_equation_ids",
                        field=chain.derivation_id,
                    ))
                if not s.output_equation_ids:
                    issues.append(ValidationIssue(
                        rule_id="derivation_step_missing_output",
                        severity="warning",
                        message=f"step {s.step_id} has no output_equation_ids",
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
        eq_by_id: dict[str, EquationSemanticsRecord],
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
            if current_record and current_record.section_id:
                sections.add(current_record.section_id)

            assumptions: list[str] = []
            if current_record:
                assumptions = [a.text for a in current_record.local_assumptions]

            operation = self._infer_operation(current_record)
            step = DerivationStep(
                step_id=f"step_{step_counter:03d}",
                input_equation_ids=list(sources),
                operation=operation,
                output_equation_ids=[current],
                required_claim_ids=list(claim_link_index.get(current, [])),
                assumption_refs=assumptions,
                reason=current_record.summary if current_record else "",
                confidence=current_record.equation_role.confidence if current_record and current_record.equation_role else 0.0,
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

    @staticmethod
    def _infer_operation(record: Optional[EquationSemanticsRecord]) -> str:
        if record is None or record.equation_role is None:
            return DEFAULT_OPERATION
        primary = record.equation_role.primary
        mapping = {
            "equation_definition": "define",
            "equation_relation": "relate",
            "equation_transformation": "transform",
            "equation_approximation": "approximate",
            "equation_result": "derive_result",
            "equation_constraint": "apply_constraint",
        }
        return mapping.get(primary, DEFAULT_OPERATION)
