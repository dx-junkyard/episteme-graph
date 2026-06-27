"""Build LLM input for DSLLinkingAgent."""
from __future__ import annotations

from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult
from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult
from episteme_graph.agents.claim_selection import (
    headline_text_for_selection,
    select_claim_rows,
    thesis_claim_id_set,
)
from episteme_graph.agents.id_canonicalization import (
    canonical_claim_id_for_span,
    canonicalize_claim_refs,
    legacy_claim_id_for_span,
)

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
        claim_objects=None,
    ) -> DSLLLMInput:
        cfg = config or {}
        claim_selection = self._accepted_claims(
            qualified_claims,
            int(cfg.get("max_claims", _MAX_CLAIMS)),
            claim_objects=claim_objects,
            thesis=thesis,
        )
        accepted_claims = claim_selection.selected
        aliases = {
            c["legacy_claim_id"]: c["claim_id"]
            for c in accepted_claims
            if c.get("legacy_claim_id") and c.get("claim_id") != c.get("legacy_claim_id")
        }
        return DSLLLMInput(
            document_id=qualified_claims.document_id,
            accepted_claims=accepted_claims,
            equations=self._equations(
                equations, int(cfg.get("max_equations", _MAX_EQUATIONS))
            ),
            thesis_nodes=self._thesis_nodes(
                thesis,
                int(cfg.get("max_thesis_nodes", _MAX_THESIS_NODES)),
                claim_objects=claim_objects,
                claim_aliases=aliases,
            ),
            excluded_from_core=canonicalize_claim_refs(
                list(thesis.excluded_from_core if thesis else []),
                claim_objects,
                aliases,
            ),
            excluded_from_pipeline_input=claim_selection.excluded,
        )

    @staticmethod
    def _accepted_claims(
        qualified_claims: ClaimQualificationResult,
        limit: int,
        claim_objects=None,
        thesis=None,
    ):
        result = []
        seen_claim_ids: set[str] = set()
        claim_index = _claim_object_index(claim_objects)
        has_claim_objects = bool(claim_index)
        for record in qualified_claims.qualified_spans:
            claim_id = canonical_claim_id_for_span(record, claim_objects)
            claim_obj = claim_index.get(claim_id)
            if has_claim_objects and claim_obj and _claim_is_non_atomic(claim_obj):
                continue
            row = {
                "claim_id": claim_id,
                "legacy_claim_id": legacy_claim_id_for_span(record),
                "span_id": record.span_id,
                "block_id": record.block_id,
                "section_id": record.section_id,
                "text": record.text,
                "role_labels": record.role_labels,
                "claim_tier": record.qualification.get("claim_tier"),
                "claim_type_candidate": record.qualification.get("claim_type_candidate"),
                "reason": record.reason,
                "confidence": record.confidence,
            }
            if claim_obj:
                _attach_claim_object_metadata(row, claim_obj)
            result.append(row)
            if claim_id:
                seen_claim_ids.add(claim_id)
        for claim_obj in list(getattr(claim_objects, "claims", []) or []):
            claim_id = str(getattr(claim_obj, "claim_id", "") or "")
            if not claim_id or claim_id in seen_claim_ids or _claim_is_non_atomic(claim_obj):
                continue
            result.append(_claim_object_row(claim_obj))
            seen_claim_ids.add(claim_id)
        return select_claim_rows(
            result,
            limit,
            stage="dsl_linking",
            thesis_claim_ids=thesis_claim_id_set(thesis),
            headline_text=headline_text_for_selection(thesis=thesis),
        )

    @staticmethod
    def _equations(
        equations: EquationSemanticsResult | None,
        limit: int,
    ) -> list[dict]:
        if not equations:
            return []
        result = []
        for record in equations.equations[:limit]:
            loc = record.source_extraction.source_location
            sem = record.semantics
            result.append({
                "equation_id": record.equation_id,
                "block_id": loc.get("block_id", ""),
                "section_id": loc.get("section_id", ""),
                "label": record.label,
                "role": sem.equation_type,
                "secondary_roles": sem.secondary_types,
                "summary": sem.summary,
                "defined_symbols": [
                    {
                        "symbol": s.symbol,
                        "definition_status": s.definition_status,
                    }
                    for s in sem.defined_symbols
                ],
                "local_assumptions": sem.assumptions,
                "from_equations": sem.input_equation_ids,
                "confidence": sem.confidence,
            })
        return result

    @staticmethod
    def _thesis_nodes(
        thesis: ThesisReconstructionResult | None,
        limit: int,
        claim_objects=None,
        claim_aliases: dict[str, str] | None = None,
    ) -> list[dict]:
        if not thesis:
            return []
        nodes = [{
            "thesis_ref": "central_thesis",
            "text": thesis.central_thesis.get("text", ""),
            "claim_ids": canonicalize_claim_refs(
                {"claim_ids": thesis.central_thesis.get("claim_ids", [])},
                claim_objects,
                claim_aliases,
            ).get("claim_ids", []),
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
                    "claim_ids": canonicalize_claim_refs(
                        {"claim_ids": entry.get("claim_ids", [])},
                        claim_objects,
                        claim_aliases,
                    ).get("claim_ids", []),
                    "equation_ids": entry.get("equation_ids", []),
                    "kind": section,
                    "support_type": entry.get("support_type"),
                    "confidence": entry.get("confidence", 0.0),
                })
                if len(nodes) >= limit:
                    return nodes
        return nodes[:limit]


def _claim_object_index(claim_objects) -> dict[str, object]:
    return {
        str(getattr(claim, "claim_id", "") or ""): claim
        for claim in list(getattr(claim_objects, "claims", []) or [])
        if getattr(claim, "claim_id", None)
    }


def _claim_is_non_atomic(claim: object) -> bool:
    atomicity = str(getattr(claim, "atomicity", "atomic") or "atomic").lower()
    is_atomic = bool(getattr(claim, "is_atomic", atomicity == "atomic"))
    return atomicity in {
        "composite",
        "split_required",
        "compound",
        "non_atomic",
        "split_pending",
    } or not is_atomic


def _attach_claim_object_metadata(row: dict, claim: object) -> None:
    row.update({
        "text": str(getattr(claim, "text", row.get("text", "")) or row.get("text", "")),
        "claim_type_candidate": str(
            getattr(claim, "claim_type", row.get("claim_type_candidate", "unknown"))
            or row.get("claim_type_candidate", "unknown")
        ),
        "atomicity": str(getattr(claim, "atomicity", "atomic") or "atomic"),
        "is_atomic": bool(getattr(claim, "is_atomic", True)),
        "split_suggestions": list(getattr(claim, "split_suggestions", []) or []),
        "source_evidence_ids": list(getattr(claim, "source_evidence_ids", []) or []),
        # Human-readable section title (issue #359) so downstream prompts can
        # show where the claim came from without the structure artifact.
        "section_title": getattr(claim, "section_title", None),
    })
    if getattr(claim, "qualification_reason", None):
        row["reason"] = str(getattr(claim, "qualification_reason"))
    if getattr(claim, "confidence", None) is not None:
        row["confidence"] = float(
            getattr(claim, "confidence", row.get("confidence", 0.0)) or 0.0
        )


def _claim_object_row(claim: object) -> dict:
    source_span_ids = list(getattr(claim, "source_span_ids", []) or [])
    row = {
        "claim_id": str(getattr(claim, "claim_id", "") or ""),
        "legacy_claim_id": "",
        "span_id": str(source_span_ids[0]) if source_span_ids else "",
        "block_id": "",
        "section_id": getattr(claim, "section_id", None),
        "text": str(getattr(claim, "text", "") or ""),
        "role_labels": [str(getattr(claim, "claim_type", "unknown") or "unknown")],
        "claim_tier": None,
        "claim_type_candidate": str(getattr(claim, "claim_type", "unknown") or "unknown"),
        "reason": str(getattr(claim, "qualification_reason", "") or ""),
        "confidence": float(getattr(claim, "confidence", 0.0) or 0.0),
    }
    _attach_claim_object_metadata(row, claim)
    return row
