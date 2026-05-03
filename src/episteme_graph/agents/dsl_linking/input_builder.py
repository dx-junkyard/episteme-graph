"""Build LLM input for DSLLinkingAgent."""
from __future__ import annotations

from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult
from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult

from .schema import DSLLLMInput

_MAX_CLAIMS = 48
_MAX_EQUATIONS = 24
_MAX_THESIS_NODES = 24


class DSLLinkingInputBuilder:
    def build(
        self,
        qualified_claims: ClaimQualificationResult,
        equations: EquationSemanticsResult | None = None,
        thesis: ThesisReconstructionResult | None = None,
        config: dict | None = None,
    ) -> DSLLLMInput:
        cfg = config or {}
        return DSLLLMInput(
            document_id=qualified_claims.document_id,
            accepted_claims=self._accepted_claims(
                qualified_claims, int(cfg.get("max_claims", _MAX_CLAIMS))
            ),
            equations=self._equations(
                equations, int(cfg.get("max_equations", _MAX_EQUATIONS))
            ),
            thesis_nodes=self._thesis_nodes(
                thesis, int(cfg.get("max_thesis_nodes", _MAX_THESIS_NODES))
            ),
            excluded_from_core=list(thesis.excluded_from_core if thesis else []),
        )

    @staticmethod
    def _accepted_claims(
        qualified_claims: ClaimQualificationResult,
        limit: int,
    ) -> list[dict]:
        result = []
        for record in qualified_claims.qualified_spans[:limit]:
            result.append({
                "claim_id": f"claim:{record.block_id}:{record.span_id}",
                "span_id": record.span_id,
                "block_id": record.block_id,
                "section_id": record.section_id,
                "text": record.text,
                "role_labels": record.role_labels,
                "claim_tier": record.qualification.get("claim_tier"),
                "claim_type_candidate": record.qualification.get("claim_type_candidate"),
                "reason": record.reason,
                "confidence": record.confidence,
            })
        return result

    @staticmethod
    def _equations(
        equations: EquationSemanticsResult | None,
        limit: int,
    ) -> list[dict]:
        if not equations:
            return []
        result = []
        for record in equations.equations[:limit]:
            result.append({
                "equation_id": record.equation_id,
                "block_id": record.block_id,
                "section_id": record.section_id,
                "label": record.label,
                "role": record.equation_role.primary,
                "secondary_roles": record.equation_role.secondary,
                "summary": record.summary,
                "defined_symbols": [
                    {
                        "symbol": s.symbol,
                        "definition_status": s.definition_status,
                    }
                    for s in record.defined_symbols
                ],
                "local_assumptions": [a.text for a in record.local_assumptions],
                "from_equations": record.derivation_links.from_equations,
                "confidence": record.equation_role.confidence,
            })
        return result

    @staticmethod
    def _thesis_nodes(
        thesis: ThesisReconstructionResult | None,
        limit: int,
    ) -> list[dict]:
        if not thesis:
            return []
        nodes = [{
            "thesis_ref": "central_thesis",
            "text": thesis.central_thesis.get("text", ""),
            "claim_ids": thesis.central_thesis.get("claim_ids", []),
            "equation_ids": thesis.central_thesis.get("equation_ids", []),
            "evidence_block_ids": thesis.central_thesis.get("evidence_block_ids", []),
            "kind": "central_thesis",
            "confidence": thesis.central_thesis.get("confidence", thesis.confidence),
        }]
        for section, entries in (thesis.support_structure or {}).items():
            if not isinstance(entries, list):
                continue
            for idx, entry in enumerate(entries):
                nodes.append({
                    "thesis_ref": f"support:{section}:{idx}",
                    "text": entry.get("text", ""),
                    "claim_ids": entry.get("claim_ids", []),
                    "equation_ids": entry.get("equation_ids", []),
                    "kind": section,
                    "support_type": entry.get("support_type"),
                    "confidence": entry.get("confidence", 0.0),
                })
                if len(nodes) >= limit:
                    return nodes
        return nodes[:limit]
