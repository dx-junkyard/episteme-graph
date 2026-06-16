"""Helpers for surfacing agent pipeline artifacts in the export bundle (issue #242).

Source-of-truth: `document_analysis_runs.stage_outputs._artifacts` JSONB column,
populated by `core.document_pipeline.orchestrator.save_artifact`.

Each helper here is purely functional and operates on the artifact dicts so the
export route can compose them without coupling to SQLAlchemy or the orchestrator.

Issue #242 acceptance criteria:
- equations.json contains first-class Equation objects.
- equation_candidates.json keeps the audit trail (raw_text, source_location,
  detection_method, acceptance_status, accepted_equation_id, ...).
- derivation_chains.json exposes step-level operation / inputs / outputs.
- evidence_snippets.json keeps PDF-derived evidence_text separate from LLM
  analysis_note / review_note, with extraction_source / extraction_status /
  needs_review.
- document_boundary records the active article boundary inside multi-article
  PDFs (e.g. journal scans).
"""
from __future__ import annotations

import json
import re
from typing import Any

ARTIFACTS_KEY = "_artifacts"


# ---------------------------------------------------------------------------
# Low-level artifact access
# ---------------------------------------------------------------------------


def _coerce_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def get_artifacts(stage_outputs: Any) -> dict[str, Any]:
    """Return the `_artifacts` sub-dict from a stage_outputs JSONB payload.

    Tolerates missing / malformed payloads; returns {} so callers can chain
    `get_artifacts(...).get("equation_semantics")` without guarding every call.
    """
    payload = _coerce_dict(stage_outputs)
    artifacts = payload.get(ARTIFACTS_KEY)
    return artifacts if isinstance(artifacts, dict) else {}


# ---------------------------------------------------------------------------
# Equation registry (first-class equations.json)
# ---------------------------------------------------------------------------


def _section_id_to_block_lookup(structure: dict) -> dict[str, dict]:
    blocks = structure.get("blocks", []) if isinstance(structure, dict) else []
    out: dict[str, dict] = {}
    for b in blocks:
        if isinstance(b, dict) and b.get("block_id"):
            out[b["block_id"]] = b
    return out


def build_equations_export(
    equation_artifact: Any,
    *,
    document_id: str,
    structure_artifact: Any = None,
    evidence_index: dict[str, list[str]] | None = None,
    claim_index: dict[str, list[str]] | None = None,
) -> list[dict]:
    """Convert the equation_semantics artifact into equations/equations.json shape.

    Reads from the current nested EquationRecord schema (issue #258):
    source_extraction.*, reconstruction.*, semantics.*, confidence_policy.*

    Falls back to legacy flat format for compatibility.
    """
    from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult

    eq = _coerce_dict(equation_artifact)
    evidence_index = evidence_index or {}
    claim_index = claim_index or {}

    # Prefer structured deserialization via EquationSemanticsResult when the artifact
    # has the nested schema (equations[].source_extraction present).
    records_raw = eq.get("equations") if isinstance(eq.get("equations"), list) else []
    first = records_raw[0] if records_raw else {}
    has_nested = isinstance(first, dict) and "source_extraction" in first

    if has_nested:
        try:
            sem_result = EquationSemanticsResult.from_dict({
                "document_id": eq.get("document_id", document_id),
                "cartridge_id": eq.get("cartridge_id"),
                "equation_candidates": eq.get("equation_candidates", []),
                "equations": records_raw,
                "validation_issues": eq.get("validation_issues", []),
            })
            exported = sem_result.to_equations_export(
                evidence_index=evidence_index,
                claim_index=claim_index,
            )
            # Validate and annotate
            _validate_equations_export(exported)
            return exported
        except Exception:
            pass  # fall through to legacy path

    # Legacy flat format (older artifacts)
    structure = _coerce_dict(structure_artifact)
    block_lookup = _section_id_to_block_lookup(structure)

    out: list[dict] = []
    seen_ids: set[str] = set()

    for r in records_raw:
        if not isinstance(r, dict):
            continue
        equation_id = str(r.get("equation_id") or "").strip()
        if not equation_id:
            continue
        if equation_id in seen_ids:
            continue
        seen_ids.add(equation_id)

        block_id = str(r.get("block_id") or "")
        section_id = r.get("section_id")
        block = block_lookup.get(block_id, {})
        page = block.get("page") if isinstance(block, dict) else None
        bbox = block.get("bbox") if isinstance(block, dict) else None
        text = block.get("text") if isinstance(block, dict) else ""
        text = text or r.get("text") or ""

        eq_role = r.get("equation_role") or {}
        primary_role = (eq_role.get("primary") if isinstance(eq_role, dict) else None) or "unknown"

        defined_symbols_raw = r.get("defined_symbols") or []
        defined_symbols: list[str] = []
        used_symbols: list[str] = []
        symbol_definitions: dict[str, str] = {}
        for s in defined_symbols_raw if isinstance(defined_symbols_raw, list) else []:
            if not isinstance(s, dict):
                continue
            sym = str(s.get("symbol") or "").strip()
            if not sym:
                continue
            status = s.get("definition_status") or "unknown"
            if status in ("defined", "redefined"):
                defined_symbols.append(sym)
            elif status == "used":
                used_symbols.append(sym)
            ev_text = s.get("evidence_text") or ""
            if ev_text:
                symbol_definitions[sym] = ev_text

        derivation_links = r.get("derivation_links") or {}
        from_eqs = list(derivation_links.get("from_equations") or []) if isinstance(derivation_links, dict) else []
        to_eqs = list(derivation_links.get("to_equations") or []) if isinstance(derivation_links, dict) else []

        latex = r.get("latex")
        plain_text = r.get("plain_text") or text
        review_flags = list(r.get("review_flags") or [])
        needs_math_review = bool(review_flags) or not bool(latex) or "low_confidence" in review_flags

        if block.get("text"):
            extraction_source = "pdf_text_layer"
            extraction_status = "parsed" if latex else "partially_parsed"
        else:
            extraction_source = "llm_reconstruction"
            extraction_status = "reconstructed" if latex or plain_text else "unparsed"

        candidate_trace_ids = list(r.get("candidate_trace_ids") or [f"eqcand_{equation_id}"])

        out.append({
            "equation_id": equation_id,
            "document_id": document_id,
            "label": r.get("label"),
            "raw_text": r.get("raw_text") or text,
            "latex": latex,
            "plain_text": plain_text,
            "source_location": {
                "page": page,
                "section_id": section_id,
                "block_id": block_id,
                "span_start": 0,
                "span_end": len(text or ""),
                "bbox": list(bbox) if isinstance(bbox, (list, tuple)) else [],
            },
            "extraction_source": extraction_source,
            "extraction_status": extraction_status,
            "needs_math_review": needs_math_review,
            "review_reason": review_flags,
            "candidate_trace_ids": candidate_trace_ids,
            "reconstruction": {"status": "none"},
            "equation_role": {"primary": primary_role, "secondary": []},
            "semantic_kind": None,
            "equation_type": primary_role,
            "secondary_types": [],
            "semantic_status": "unknown",
            "defined_symbols": defined_symbols,
            "used_symbols": used_symbols,
            "introduced_symbols": defined_symbols,
            "symbol_definitions": symbol_definitions,
            "local_assumptions": [
                a.get("text", "") for a in (r.get("local_assumptions") or [])
                if isinstance(a, dict) and a.get("text")
            ],
            "derivation_links": {"from_equations": from_eqs, "to_equations": to_eqs},
            "input_equation_ids": from_eqs,
            "output_equation_ids": to_eqs,
            "source_evidence_ids": list(evidence_index.get(block_id, [])),
            "linked_claim_ids": list(claim_index.get(equation_id, [])),
            "confidence_policy": {},
            "confidence_gate": _confidence_gate_for_equation(
                equation_id,
                {},
                latex=latex,
                plain_text=plain_text,
                extraction_status=extraction_status,
            ),
            "equation_consistency": _legacy_equation_consistency(
                raw_text=r.get("raw_text") or text,
                latex=latex,
                label=r.get("label"),
                source_location={
                    "page": page,
                    "section_id": section_id,
                    "block_id": block_id,
                    "span_start": 0,
                    "span_end": len(text or ""),
                    "bbox": list(bbox) if isinstance(bbox, (list, tuple)) else [],
                },
                extraction_status=extraction_status,
                needs_math_review=needs_math_review,
            ),
        })
    return out


def _confidence_gate_for_equation(
    equation_id: str,
    policy: dict,
    *,
    latex: str | None,
    plain_text: str | None,
    extraction_status: str,
) -> dict:
    blocked = (
        policy.get("can_support_claim") is False
        and policy.get("can_be_used_in_derivation") is False
    ) or (
        latex is None
        and plain_text is None
        and extraction_status in ("partial", "fragment_only", "label_only", "missing", "unparsed")
    )
    if blocked:
        return {
            "blocked_by_equation_ids": [equation_id],
            "blocked_reason": "linked equation cannot support claim or derivation",
            "downstream_allowed_use": "semantic_hint_only",
        }
    if policy.get("display_requires_note"):
        return {
            "blocked_by_equation_ids": [],
            "blocked_reason": "",
            "downstream_allowed_use": "display_with_warning",
        }
    return {
        "blocked_by_equation_ids": [],
        "blocked_reason": "",
        "downstream_allowed_use": "display_with_warning",
    }


def _validate_equations_export(records: list[dict]) -> None:
    """In-place annotation of validation issues on exported equation records.

    Checks issue #258 criteria:
    - duplicate equation_id → mark error
    - source-backed equation with empty block_id → mark error
    - empty candidate_trace_ids → mark warning
    - reconstruction-based equation not flagged as such → mark error
    """
    seen_ids: set[str] = set()
    for r in records:
        eq_id = r.get("equation_id", "")
        validation = r.setdefault("_export_validation", [])

        if not eq_id:
            validation.append({"severity": "error", "rule": "eq_id_empty"})
        elif eq_id in seen_ids:
            validation.append({"severity": "error", "rule": "eq_id_duplicate", "equation_id": eq_id})
        seen_ids.add(eq_id)

        src_loc = r.get("source_location") or {}
        block_id = src_loc.get("block_id", "")
        extraction_status = r.get("extraction_status") or r.get("source_extraction", {}).get("extraction_status", "")
        if extraction_status not in ("missing", "label_only", "fragment_only") and not block_id:
            validation.append({"severity": "error", "rule": "source_backed_missing_block_id"})

        candidate_trace_ids = r.get("candidate_trace_ids") or []
        if not candidate_trace_ids:
            validation.append({"severity": "warning", "rule": "candidate_trace_ids_empty"})

        cp = r.get("confidence_policy") or {}
        must_not = cp.get("must_not_treat_as_source_extracted", False)
        rec = r.get("reconstruction") or {}
        rec_status = rec.get("status", "none")
        if rec_status != "none" and not must_not:
            validation.append({
                "severity": "warning",
                "rule": "reconstruction_based_not_flagged",
                "reconstruction_status": rec_status,
            })
        consistency = r.get("equation_consistency") if isinstance(r.get("equation_consistency"), dict) else {}
        if (
            consistency.get("raw_text_latex_match") == "mismatch"
            or consistency.get("label_location_match") == "mismatch"
            or consistency.get("source_span_quality") == "corrupted"
        ):
            validation.append({
                "severity": "warning",
                "rule": "equation_consistency_mismatch",
                "equation_id": eq_id,
            })


def _legacy_equation_consistency(
    *,
    raw_text: str,
    latex: str | None,
    label: str | None,
    source_location: dict,
    extraction_status: str,
    needs_math_review: bool,
) -> dict:
    """Best-effort consistency metadata for legacy flat equation artifacts."""
    import re

    def symbols(text: str) -> set[str]:
        normalized = (text or "").replace("\\", " ")
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]*|[=+\-*/^<>≤≥≈≃∝∑∫]", normalized)
        stop = {"left", "right", "frac", "sqrt", "sum", "int", "mathrm", "text", "label", "equation"}
        return {t.lower() for t in tokens if t.lower() not in stop}

    raw_symbols = symbols(raw_text or "")
    latex_symbols = symbols(latex or "")
    if raw_symbols and latex_symbols:
        score = len(raw_symbols & latex_symbols) / max(len(raw_symbols | latex_symbols), 1)
        raw_match = "mismatch" if score < 0.2 else "match"
    else:
        score = 0.0
        raw_match = "uncertain"

    raw_numbers = set(re.findall(r"\((\d+[A-Za-z]?)\)", raw_text or ""))
    label_number = str(label or "").strip().strip("()")
    if raw_numbers and label_number:
        label_match = "match" if label_number in raw_numbers else "mismatch"
    elif raw_numbers or label_number:
        label_match = "uncertain"
    else:
        label_match = "uncertain"

    if not (raw_text or "").strip() or not (source_location or {}).get("block_id"):
        source_quality = "corrupted"
    elif extraction_status in ("partial", "fragment_only", "label_only", "unparsed", "missing") or needs_math_review:
        source_quality = "partial"
    else:
        source_quality = "clean"

    review_required = raw_match != "match" or label_match == "mismatch" or source_quality != "clean"
    return {
        "raw_text_latex_match": raw_match,
        "label_location_match": label_match,
        "symbol_overlap_score": round(score, 3),
        "source_span_quality": source_quality,
        "review_required": review_required,
    }


# ---------------------------------------------------------------------------
# Equation candidate trace (audit log)
# ---------------------------------------------------------------------------


def build_equation_candidates_export(
    equation_artifact: Any,
    *,
    document_id: str,
    structure_artifact: Any = None,
) -> list[dict]:
    """Build equations/equation_candidates.json.

    Prefers the EquationSemanticsResult.equation_candidates list (issue #258/#259).
    Falls back to reconstructing from equation_semantics records and
    document_structure equation_blocks.

    rejection_reason is always populated on rejected candidates.
    """
    eq = _coerce_dict(equation_artifact)
    structure = _coerce_dict(structure_artifact)
    block_lookup = _section_id_to_block_lookup(structure)

    # Prefer first-class candidate list from the agent (issue #258)
    agent_candidates = eq.get("equation_candidates")
    if isinstance(agent_candidates, list) and agent_candidates:
        out: list[dict] = []
        eq_consistency_by_id = {
            str(r.get("equation_id")): r.get("equation_consistency")
            for r in (eq.get("equations") or [])
            if isinstance(r, dict) and isinstance(r.get("equation_consistency"), dict)
        }
        for c in agent_candidates:
            if not isinstance(c, dict):
                continue
            candidate_id = str(c.get("candidate_id") or "").strip()
            if not candidate_id:
                continue
            rejection_reason = c.get("rejection_reason") or c.get("review_reason")
            if isinstance(rejection_reason, list):
                rejection_reason = "; ".join(rejection_reason) if rejection_reason else None
            acceptance = c.get("acceptance_status", "rejected")
            if acceptance in ("rejected", "context_only") and not rejection_reason:
                rejection_reason = "candidate_not_accepted"
            out.append({
                "candidate_id": candidate_id,
                "document_id": c.get("document_id") or document_id,
                "source_location": c.get("source_location") or {},
                "raw_text": c.get("raw_text") or "",
                "matched_label": c.get("matched_label"),
                "detection_method": list(c.get("detection_method") or []),
                "candidate_score": c.get("candidate_score"),
                "extraction_status": c.get("extraction_status", "unparsed"),
                "acceptance_status": acceptance,
                "accepted_equation_id": c.get("accepted_equation_id"),
                "merge_target_hint": c.get("merge_target_hint"),
                "rejection_reason": rejection_reason,
                "needs_math_review": bool(c.get("needs_math_review", True)),
                "review_reason": list(c.get("review_reason") or []),
                "equation_consistency": eq_consistency_by_id.get(str(c.get("accepted_equation_id") or "")),
            })
        return out

    # Fallback: reconstruct from equation records + document_structure blocks
    candidates: list[dict] = []
    accepted_block_ids: set[str] = set()

    for r in eq.get("equations") or []:
        if not isinstance(r, dict):
            continue
        equation_id = str(r.get("equation_id") or "")
        if not equation_id:
            continue

        # Support nested schema
        src = r.get("source_extraction") or {}
        src_loc = src.get("source_location") or {}
        block_id = str(src_loc.get("block_id") or r.get("block_id") or "")
        accepted_block_ids.add(block_id)

        raw_text = src.get("raw_text") or r.get("raw_text") or ""
        review_flags = list((src.get("review_reason") or r.get("review_flags") or []))
        needs_review = bool(src.get("needs_math_review") or review_flags or not src.get("latex"))

        block = block_lookup.get(block_id, {})
        candidates.append({
            "candidate_id": f"eqcand_{equation_id}",
            "document_id": document_id,
            "source_location": src_loc or {
                "page": block.get("page") if isinstance(block, dict) else None,
                "section_id": r.get("section_id"),
                "block_id": block_id,
            },
            "raw_text": raw_text or (block.get("text") if isinstance(block, dict) else "") or "",
            "detection_method": ["document_structure_equation_block", "llm_semantic_classification"],
            "matched_label": r.get("label"),
            "candidate_score": None,
            "extraction_status": src.get("extraction_status") or "complete",
            "acceptance_status": "accepted",
            "accepted_equation_id": equation_id,
            "merge_target_hint": None,
            "rejection_reason": None,
            "needs_math_review": needs_review,
            "review_reason": review_flags,
            "equation_consistency": (
                r.get("equation_consistency")
                if isinstance(r.get("equation_consistency"), dict)
                else _legacy_equation_consistency(
                    raw_text=raw_text or (block.get("text") if isinstance(block, dict) else "") or "",
                    latex=src.get("latex") or r.get("latex"),
                    label=r.get("label"),
                    source_location=src_loc,
                    extraction_status=src.get("extraction_status") or "complete",
                    needs_math_review=needs_review,
                )
            ),
        })

    for block in structure.get("blocks", []) or []:
        if not isinstance(block, dict):
            continue
        if block.get("block_type") != "equation_block":
            continue
        block_id = block.get("block_id") or ""
        if block_id in accepted_block_ids:
            continue
        candidates.append({
            "candidate_id": f"eqcand_{block_id}",
            "document_id": document_id,
            "source_location": {
                "page": block.get("page"),
                "section_id": block.get("section_id"),
                "block_id": block_id,
                "span_start": 0,
                "span_end": len(block.get("text") or ""),
                "bbox": list(block.get("bbox") or []) if isinstance(block.get("bbox"), (list, tuple)) else [],
            },
            "raw_text": block.get("text") or "",
            "detection_method": ["document_structure_equation_block"],
            "matched_label": block.get("equation_label"),
            "candidate_score": float(block.get("confidence") or 0.0),
            "extraction_status": "unparsed",
            "acceptance_status": "rejected",
            "accepted_equation_id": None,
            "merge_target_hint": None,
            "rejection_reason": "not_promoted_by_equation_semantics_agent",
            "needs_math_review": True,
            "review_reason": ["not_promoted"],
        })
    return candidates


# ---------------------------------------------------------------------------
# Evidence registry (source-backed)
# ---------------------------------------------------------------------------


def build_evidence_export(
    evidence_artifact: Any,
    *,
    document_id: str,
    fallback_claims: list[dict] | None = None,
) -> list[dict]:
    """Build evidence/evidence_snippets.json with source-backed fields.

    Prefers the EvidenceRegistry artifact (PDF-quoted text, separate review
    notes). Falls back to claim.evidence_text when no registry artifact is
    available, marking those entries with extraction_status=reconstructed and
    needs_review=true so consumers know they may include LLM commentary.
    """
    out: list[dict] = []
    ev = _coerce_dict(evidence_artifact)
    records = ev.get("records") if isinstance(ev.get("records"), list) else []
    if records:
        for r in records:
            if not isinstance(r, dict):
                continue
            src = r.get("source") or {}
            evidence_text = r.get("evidence_text") or ""
            review_note = r.get("review_note") or ""
            out.append({
                "evidence_id": r.get("evidence_id") or "",
                "document_id": r.get("document_id") or document_id,
                "page": src.get("page") if isinstance(src, dict) else None,
                "section_id": src.get("section_id") if isinstance(src, dict) else None,
                "block_id": src.get("block_id") if isinstance(src, dict) else "",
                "span_start": src.get("span_start", 0) if isinstance(src, dict) else 0,
                "span_end": src.get("span_end", 0) if isinstance(src, dict) else 0,
                "evidence_text": evidence_text,
                "evidence_role": r.get("evidence_role") or "source_quote",
                "analysis_note": "",
                "review_note": review_note,
                "extraction_source": "pdf_text_layer",
                "extraction_status": "extracted" if evidence_text else "unavailable",
                "public_export_policy": r.get("public_export_policy") or "location_only",
                "needs_review": bool(review_note),
                "review_reason": [review_note] if review_note else [],
            })
        return out

    # Fallback: synthesise evidence rows from claim.evidence_text.
    # Legacy DB rows may store LLM commentary (qualification reason) in evidence_text
    # rather than PDF-derived text (#257). To preserve the source-backed contract,
    # we move the text to analysis_note and leave evidence_text empty.
    # Consumers must check extraction_status=reconstructed and needs_review=true.
    for i, claim in enumerate(fallback_claims or []):
        text = claim.get("evidence_text") or ""
        if not text:
            continue
        scope = claim.get("source_scope") or {}
        # qualification_reason stored in source_scope by persist_theory_claims (#257)
        qualification_reason = scope.get("qualification_reason", "") if isinstance(scope, dict) else ""
        out.append({
            "evidence_id": f"evidence_{i+1:04d}",
            "document_id": claim.get("document_id") or document_id,
            "page": scope.get("page"),
            "section_id": scope.get("section_id", ""),
            "block_id": scope.get("chunk_id", ""),
            "span_start": 0,
            "span_end": 0,
            # evidence_text is intentionally empty: text from legacy claim.evidence_text
            # is not verified as PDF-derived and is surfaced in analysis_note instead.
            "evidence_text": "",
            "evidence_role": "section_summary",
            "analysis_note": text,
            "review_note": qualification_reason,
            "extraction_source": "reconstructed",
            "extraction_status": "reconstructed",
            "public_export_policy": "location_only",
            "needs_review": True,
            "review_reason": ["evidence_not_source_verified", "legacy_evidence_text_in_analysis_note"],
            "claim_id": claim.get("claim_id") or "",
        })
    return out


# ---------------------------------------------------------------------------
# Derivation chains
# ---------------------------------------------------------------------------


def build_derivation_chains_export(
    derivation_artifact: Any,
    *,
    document_id: str,
    equation_artifact: Any = None,
    evidence_artifact: Any = None,
) -> list[dict]:
    """Build derivations/derivation_chains.json.

    Prefers the dedicated DerivationChainAgent artifact when present.
    Otherwise reconstructs minimal chains from the equation_semantics
    derivation_links so component.internal_flow can still be cross-checked.
    """
    out: list[dict] = []
    equation_gate_index = _equation_gate_index(equation_artifact)
    ev_by_block: dict[str, list[str]] = {}
    evidence_records = (_coerce_dict(evidence_artifact).get("records") or [])
    for ev in evidence_records if isinstance(evidence_records, list) else []:
        if not isinstance(ev, dict):
            continue
        source = ev.get("source") or {}
        block_id = source.get("block_id") if isinstance(source, dict) else None
        evidence_id = ev.get("evidence_id")
        if block_id and evidence_id:
            ev_by_block.setdefault(str(block_id), []).append(str(evidence_id))

    dc = _coerce_dict(derivation_artifact)
    chains = dc.get("chains") if isinstance(dc.get("chains"), list) else []
    if chains:
        for c in chains:
            if not isinstance(c, dict):
                continue
            steps = []
            for s in c.get("steps") or []:
                if not isinstance(s, dict):
                    continue
                claim_ids = []
                for key in ("claim_ids", "required_claim_ids", "input_claim_ids", "output_claim_ids"):
                    claim_ids.extend(str(v) for v in (s.get(key) or []) if v)
                step_eq_ids = list(s.get("input_equation_ids") or []) + list(s.get("output_equation_ids") or [])
                gate = _gate_for_step(step_eq_ids, equation_gate_index)
                steps.append({
                    "step_id": s.get("step_id") or "",
                    "operation": s.get("operation") or "transform",
                    "input_equation_ids": list(s.get("input_equation_ids") or []),
                    "output_equation_ids": list(s.get("output_equation_ids") or []),
                    "claim_ids": sorted(set(claim_ids)),
                    "required_claim_ids": list(s.get("required_claim_ids") or []),
                    "source_evidence_ids": list(s.get("source_evidence_ids") or []),
                    "assumption_refs": list(s.get("assumption_refs") or []),
                    "eliminated_symbols": list(s.get("eliminated_symbols") or []),
                    "retained_symbols": list(s.get("retained_symbols") or []),
                    "review_status": s.get("review_status") or "teacher_review_required",
                    "review_reason": s.get("review_reason") or _gate_review_reason(gate),
                    "confidence_gate": gate,
                    "reason": s.get("reason") or "",
                    "confidence": float(s.get("confidence") or 0.0),
                })
                if gate.get("blocked_by_equation_ids"):
                    steps[-1]["review_status"] = "teacher_review_required"
            chain_out = {
                "derivation_id": c.get("derivation_id") or "",
                "document_id": c.get("document_id") or document_id,
                "source_section_ids": list(c.get("source_section_ids") or []),
                "steps": steps,
                "teaching_takeaway": c.get("teaching_takeaway") or "",
                "blackbox_policy_suggestion": c.get("blackbox_policy_suggestion") or {},
                "chain_type": c.get("chain_type") or "equation_chain",
                "review_status": c.get("review_status") or _chain_review_status_from_steps(steps),
                "review_reason": c.get("review_reason") or _chain_review_reason_from_steps(steps),
                "confidence_gate": _chain_confidence_gate_from_steps(steps),
            }
            # System-level derivation fields (issue #386): expose the group-level
            # operation, symbol bookkeeping, and review reasons on the chain.
            if c.get("chain_type") == "system_level":
                chain_out.update({
                    "operation": c.get("operation") or "",
                    "input_equation_ids": list(c.get("input_equation_ids") or []),
                    "output_equation_ids": list(c.get("output_equation_ids") or []),
                    "intermediate_equation_ids": list(c.get("intermediate_equation_ids") or []),
                    "input_claim_ids": list(c.get("input_claim_ids") or []),
                    "output_claim_ids": list(c.get("output_claim_ids") or []),
                    "assumption_ids": list(c.get("assumption_ids") or []),
                    "source_evidence_ids": list(c.get("source_evidence_ids") or []),
                    "transformed_symbols": list(c.get("transformed_symbols") or []),
                    "eliminated_symbols": list(c.get("eliminated_symbols") or []),
                    "retained_symbols": list(c.get("retained_symbols") or []),
                    "conditions": list(c.get("conditions") or []),
                    "review_required": bool(c.get("review_required", True)),
                    "review_reasons": list(c.get("review_reasons") or []),
                })
            out.append(chain_out)
        return out

    eq = _coerce_dict(equation_artifact)
    eq_records = eq.get("equations") or []
    normalized_records: list[dict] = [r for r in eq_records if isinstance(r, dict)] if isinstance(eq_records, list) else []
    chain_id = 1
    for r in normalized_records:
        derivation_links = r.get("derivation_links") or {}
        from_eqs = list(derivation_links.get("from_equations") or []) if isinstance(derivation_links, dict) else []
        if not from_eqs:
            continue
        eq_id = r.get("equation_id") or ""
        block_id = str(r.get("block_id") or (r.get("source_location") or {}).get("block_id") or "")
        source_evidence_ids = list(r.get("source_evidence_ids") or ev_by_block.get(block_id, []))
        claim_ids = list(r.get("linked_claim_ids") or [])
        step_gate = _gate_for_step(from_eqs + ([eq_id] if eq_id else []), equation_gate_index)
        out.append({
            "derivation_id": f"deriv_{chain_id:04d}",
            "document_id": document_id,
            "source_section_ids": [r.get("section_id")] if r.get("section_id") else [],
            "steps": [{
                "step_id": f"deriv_{chain_id:04d}_step_1",
                "operation": "derive_from",
                "input_equation_ids": from_eqs,
                "output_equation_ids": [eq_id] if eq_id else [],
                "claim_ids": claim_ids,
                "required_claim_ids": [],
                "source_evidence_ids": source_evidence_ids,
                "assumption_refs": [
                    a.get("text", "") for a in (r.get("local_assumptions") or [])
                    if isinstance(a, dict) and a.get("text")
                ],
                "reason": "Reconstructed from equation_semantics.derivation_links",
                "confidence": 0.5,
                "review_status": _step_review_status_from_gate(step_gate),
                "review_reason": _gate_review_reason(step_gate),
                "confidence_gate": step_gate,
            }],
            "teaching_takeaway": "",
            "blackbox_policy_suggestion": {},
            "review_status": _step_review_status_from_gate(step_gate),
            "review_reason": _gate_review_reason(step_gate),
            "confidence_gate": step_gate,
        })
        chain_id += 1
    if out or not normalized_records:
        return out

    ordered = sorted(
        normalized_records,
        key=lambda r: (
            ((r.get("source_location") or {}).get("page") if isinstance(r.get("source_location"), dict) else None) or 10**9,
            str((r.get("source_location") or {}).get("section_id") if isinstance(r.get("source_location"), dict) else r.get("section_id") or ""),
            str(r.get("equation_id") or ""),
        ),
    )
    steps = []
    section_ids: list[str] = []
    for idx, r in enumerate(ordered, start=1):
        eq_id = str(r.get("equation_id") or "")
        if not eq_id:
            continue
        source_location = r.get("source_location") or {}
        block_id = str(r.get("block_id") or (source_location.get("block_id") if isinstance(source_location, dict) else "") or "")
        section_id = r.get("section_id") or (source_location.get("section_id") if isinstance(source_location, dict) else "")
        if section_id and section_id not in section_ids:
            section_ids.append(section_id)
        eq_role = r.get("equation_role") or {}
        primary_role = (eq_role.get("primary") if isinstance(eq_role, dict) else None) or r.get("equation_type") or "apply_equation"
        operation = {
            "equation_definition": "state_assumption",
            "definition": "state_assumption",
            "equation_relation": "apply_equation",
            "relation": "apply_equation",
            "result": "infer_conclusion",
        }.get(str(primary_role), "apply_equation")
        step_gate = _gate_for_step([eq_id], equation_gate_index)
        steps.append({
            "step_id": f"deriv_{chain_id:04d}_step_{idx}",
            "operation": operation,
            "input_equation_ids": [],
            "output_equation_ids": [eq_id],
            "claim_ids": list(r.get("linked_claim_ids") or []),
            "required_claim_ids": list(r.get("linked_claim_ids") or []),
            "source_evidence_ids": list(r.get("source_evidence_ids") or ev_by_block.get(block_id, [])),
            "assumption_refs": list(r.get("local_assumptions") or []),
            "reason": "Reconstructed as a source-order derivation step from equation registry",
            "confidence": 0.4,
            "review_status": _step_review_status_from_gate(step_gate),
            "review_reason": _gate_review_reason(step_gate),
            "confidence_gate": step_gate,
        })
    if steps:
        out.append({
            "derivation_id": f"deriv_{chain_id:04d}",
            "document_id": document_id,
            "source_section_ids": section_ids,
            "steps": steps,
            "teaching_takeaway": "Source-order equation chain reconstructed from first-class equation registry.",
            "blackbox_policy_suggestion": {},
            "review_status": _chain_review_status_from_steps(steps),
            "review_reason": _chain_review_reason_from_steps(steps),
            "confidence_gate": _chain_confidence_gate_from_steps(steps),
        })
    return out


def _equation_gate_index(equation_artifact: Any) -> dict[str, dict]:
    eq = _coerce_dict(equation_artifact)
    records = eq.get("equations") if isinstance(eq.get("equations"), list) else []
    index: dict[str, dict] = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        eq_id = str(r.get("equation_id") or "")
        if not eq_id:
            continue
        src = r.get("source_extraction") if isinstance(r.get("source_extraction"), dict) else {}
        rec = r.get("reconstruction") if isinstance(r.get("reconstruction"), dict) else {}
        policy = r.get("confidence_policy") if isinstance(r.get("confidence_policy"), dict) else {}
        latex = r.get("latex")
        plain_text = r.get("plain_text")
        if latex is None:
            latex = rec.get("latex") if rec.get("status") not in (None, "", "none") else src.get("latex")
        if plain_text is None:
            plain_text = rec.get("plain_text") if rec.get("status") not in (None, "", "none") else src.get("plain_text")
        extraction_status = r.get("extraction_status") or src.get("extraction_status") or "unparsed"
        index[eq_id] = _confidence_gate_for_equation(
            eq_id,
            policy,
            latex=latex,
            plain_text=plain_text,
            extraction_status=extraction_status,
        )
    return index


def _gate_for_step(equation_ids: list[str], equation_gate_index: dict[str, dict]) -> dict:
    blocked: list[str] = []
    for eq_id in equation_ids:
        gate = equation_gate_index.get(str(eq_id)) or {}
        blocked.extend(str(v) for v in gate.get("blocked_by_equation_ids") or [] if v)
    seen: set[str] = set()
    unique = [v for v in blocked if not (v in seen or seen.add(v))]
    if not unique:
        return {
            "blocked_by_equation_ids": [],
            "blocked_reason": "",
            "downstream_allowed_use": "display_with_warning",
        }
    return {
        "blocked_by_equation_ids": unique,
        "blocked_reason": "linked equation cannot support claim or derivation",
        "downstream_allowed_use": "blocked",
    }


def _gate_review_reason(gate: dict) -> str:
    return str((gate or {}).get("blocked_reason") or "")


def _step_review_status_from_gate(gate: dict) -> str:
    return "teacher_review_required" if (gate or {}).get("blocked_by_equation_ids") else "teacher_review_required"


def _chain_confidence_gate_from_steps(steps: list[dict]) -> dict:
    blocked: list[str] = []
    for step in steps:
        gate = step.get("confidence_gate") if isinstance(step.get("confidence_gate"), dict) else {}
        blocked.extend(str(v) for v in gate.get("blocked_by_equation_ids") or [] if v)
    return _gate_for_step(blocked, {eq_id: {
        "blocked_by_equation_ids": [eq_id],
        "blocked_reason": "linked equation cannot support claim or derivation",
        "downstream_allowed_use": "blocked",
    } for eq_id in blocked})


def _chain_review_status_from_steps(steps: list[dict]) -> str:
    return "teacher_review_required"


def _chain_review_reason_from_steps(steps: list[dict]) -> str:
    gate = _chain_confidence_gate_from_steps(steps)
    return _gate_review_reason(gate)


# ---------------------------------------------------------------------------
# Course / topic metadata
# ---------------------------------------------------------------------------


def enrich_course_topics(
    course: dict,
    *,
    course_mapping_artifact: Any = None,
    blueprint_artifact: Any = None,
) -> dict:
    """Add learning_objectives, prerequisite_concepts, blackbox_policy,
    expected_misconceptions, assessment_prompts, visualization_plan to topics.

    Pulls from CourseMappingAgent (for objectives / prerequisites / blackbox)
    and BlueprintAgent (for visualization_plan derived from narrative_arc).
    """
    if not isinstance(course, dict):
        return course

    cm = _coerce_dict(course_mapping_artifact)
    mapping_topics = cm.get("topics") or []
    mapping_by_title: dict[str, dict] = {}
    for t in mapping_topics if isinstance(mapping_topics, list) else []:
        if isinstance(t, dict) and t.get("title"):
            mapping_by_title[str(t["title"]).strip()] = t

    bp = _coerce_dict(blueprint_artifact)
    narrative_arc = bp.get("narrative_arc") if isinstance(bp.get("narrative_arc"), list) else []
    visualization_plan = []
    for step in narrative_arc:
        if not isinstance(step, dict):
            continue
        visualization_plan.append({
            "step": step.get("step"),
            "role": step.get("role"),
            "visual_strategy": step.get("visual_strategy"),
            "rationale": step.get("rationale"),
            "linked_component_ids": list(step.get("linked_component_ids") or []),
        })

    enriched_topics: list[dict] = []
    for t in course.get("topics") or []:
        if not isinstance(t, dict):
            continue
        title = (t.get("title") or "").strip()
        mapping = mapping_by_title.get(title, {})
        topic = dict(t)
        topic.setdefault("learning_objectives", list(mapping.get("learning_objectives") or []))
        topic.setdefault("prerequisite_concepts", list(mapping.get("prerequisite_concepts") or t.get("prerequisites") or []))
        topic.setdefault("blackbox_policy", mapping.get("blackbox_policy") or {})
        topic.setdefault("assessment_prompts", list(mapping.get("assessment_prompts") or []))
        topic.setdefault("expected_misconceptions", list(mapping.get("expected_misconceptions") or []))
        topic.setdefault("visualization_plan", visualization_plan)
        enriched_topics.append(topic)

    enriched = dict(course)
    enriched["topics"] = enriched_topics
    return enriched


# ---------------------------------------------------------------------------
# Document boundary + completeness (issue #366)
# ---------------------------------------------------------------------------

def _load_completeness_analyzer():
    """Resolve ``analyze_document_completeness`` without forcing the heavy import.

    Importing ``core.document_pipeline.completeness`` triggers the package
    ``__init__`` (orchestrator → agents), which is not always importable from the
    export route's environment. Try the package import first, then fall back to
    loading the dependency-free leaf module directly by path.
    """
    try:
        from core.document_pipeline.completeness import analyze_document_completeness
        return analyze_document_completeness
    except Exception:
        import importlib.util
        import os

        path = os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "..",
            "core", "document_pipeline", "completeness.py",
        ))
        spec = importlib.util.spec_from_file_location("_dp_completeness", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.analyze_document_completeness


# A detected boundary spanning far fewer pages than the document total is a
# collapsed boundary (issue #366: a 20-page paper reported as page 1..1). The
# boundary must not pass at confidence 1.0 / needs_review=false in that case.
_MIN_BOUNDARY_PAGE_SPAN_RATIO = 0.5

# An author list far larger than any plausible byline indicates reference /
# bibliography authors leaked into the document authors (issue #366 / #372).
_MAX_PLAUSIBLE_AUTHORS = 25


def _audit_authors(authors: list, author_extraction: Any) -> list[str]:
    """Deterministic author-list audit for the export layer (issue #372).

    The export layer NEVER deletes authors — the contamination is prevented at
    parse time by extracting authors only from front-matter / structured
    metadata. Here we only emit deterministic review reasons (returned as a
    list, never mutating ``authors``):

      * ``author_count_exceeds_limit`` — more names than any plausible byline,
      * ``author_provenance_missing``  — a non-empty author list with no
        structured ``author_extraction`` provenance,
      * ``author_extraction_needs_review`` — the parser flagged the extraction
        (e.g. ``author_source_mismatch``) for review.

    Data is preserved on anomaly; callers set ``needs_review=true`` instead of
    correcting with data loss.
    """
    raw = [str(a).strip() for a in (authors or []) if str(a).strip()]
    reasons: list[str] = []
    if len(raw) > _MAX_PLAUSIBLE_AUTHORS:
        reasons.append("author_count_exceeds_limit")

    provenance = author_extraction if isinstance(author_extraction, dict) else {}
    source = str(provenance.get("source") or "")
    has_provenance = bool(provenance) and source not in ("", "none", "unknown")
    if raw and not has_provenance:
        reasons.append("author_provenance_missing")
    elif provenance.get("needs_review"):
        reasons.append("author_extraction_needs_review")
    return reasons


def build_document_completeness(
    structure_artifact: Any,
    *,
    document_id: str,
    evidence_artifact: Any = None,
) -> dict:
    """Deterministic document-completeness report (issue #366).

    Thin wrapper over ``core.document_pipeline.completeness`` so the export route
    and the pipeline gate share one implementation. Imported lazily so importing
    this module never hard-depends on the (sometimes stubbed) ``core`` package.
    """
    analyze_document_completeness = _load_completeness_analyzer()

    return analyze_document_completeness(
        _coerce_dict(structure_artifact),
        _coerce_dict(evidence_artifact) if evidence_artifact is not None else None,
        document_id=document_id,
    )


def build_document_boundary(
    structure_artifact: Any,
    *,
    document_id: str,
    evidence_artifact: Any = None,
) -> dict:
    """Surface the active article boundary inside a (potentially multi-article) PDF.

    The DocumentStructureAgent records page ranges per section; we expose the
    first section's page_start as the article start and the last section's
    page_end as the article end, plus the title block when present, so
    consumers can flag spans that fall outside the boundary.

    Issue #366: a collapsed boundary (span ≪ pages_total) drops confidence below
    1.0 and raises needs_review. Every completeness failure (equation
    discontinuity, missing terminal section, low page coverage) also propagates
    to needs_review.

    Issue #372: the export layer never deletes authors. Authors are extracted at
    parse time from front-matter / structured metadata only; here we surface the
    ``author_extraction`` provenance and emit deterministic review reasons
    (count over limit, missing provenance, extraction flagged) without mutating
    the author list.
    """
    structure = _coerce_dict(structure_artifact)
    sections = structure.get("sections") or []
    metadata = structure.get("metadata") or {}
    pages_total = metadata.get("pages") if isinstance(metadata, dict) else None

    boundary_page_start = None
    boundary_page_end = None
    section_ids: list[str] = []
    for s in sections if isinstance(sections, list) else []:
        if not isinstance(s, dict):
            continue
        if boundary_page_start is None and s.get("page_start") is not None:
            boundary_page_start = s.get("page_start")
        if s.get("page_end") is not None:
            boundary_page_end = s.get("page_end")
        if s.get("section_id"):
            section_ids.append(s["section_id"])

    # Authors are preserved verbatim (issue #372): no token-matching deletion.
    authors = list(metadata.get("authors") or []) if isinstance(metadata, dict) else []
    author_extraction = (
        metadata.get("author_extraction") if isinstance(metadata, dict) else None
    )
    author_review_reasons = _audit_authors(authors, author_extraction)

    completeness = build_document_completeness(
        structure, document_id=document_id, evidence_artifact=evidence_artifact
    )

    review_reasons: list[str] = []
    have_pages = boundary_page_start is not None and boundary_page_end is not None
    if not have_pages:
        review_reasons.append("incomplete_section_page_range")

    # Collapsed boundary: detected span far smaller than the document total.
    boundary_span_collapsed = False
    if have_pages and isinstance(pages_total, int) and pages_total > 0:
        span_pages = int(boundary_page_end) - int(boundary_page_start) + 1
        if span_pages < pages_total * _MIN_BOUNDARY_PAGE_SPAN_RATIO:
            boundary_span_collapsed = True
            review_reasons.append("boundary_page_span_too_small")

    for reason in author_review_reasons:
        if reason not in review_reasons:
            review_reasons.append(reason)

    # Every completeness failure propagates to the boundary (#366 review).
    for reason in completeness["review_reasons"]:
        if reason not in review_reasons:
            review_reasons.append(reason)

    if not have_pages:
        confidence = 0.5
    elif boundary_span_collapsed:
        # A collapsed boundary must never pass at full confidence (issue #366).
        confidence = 0.4
    elif review_reasons:
        confidence = 0.7
    else:
        confidence = 1.0

    needs_review = bool(review_reasons) or confidence < 0.8

    return {
        "document_id": document_id,
        "title": metadata.get("title") if isinstance(metadata, dict) else None,
        "authors": authors,
        "author_extraction": author_extraction if isinstance(author_extraction, dict) else {
            "source": "none",
            "confidence": 0.0,
            "needs_review": bool(author_review_reasons),
            "review_reasons": author_review_reasons,
        },
        "boundary_page_start": boundary_page_start,
        "boundary_page_end": boundary_page_end,
        "pages_total": pages_total,
        "active_section_ids": section_ids,
        "confidence": confidence,
        "needs_review": needs_review,
        "review_reason": review_reasons,
        "completeness": completeness,
    }


# ---------------------------------------------------------------------------
# Artifact-first claims / components / graph (issue #383)
#
# The export bundle prefers the latest analysis-run artifacts over DB-persisted
# objects so that diagnostic dumps reflect the *current* pipeline stage output
# rather than legacy persisted side-products. Each builder converts a stage
# artifact into the same export shape the DB loaders produce, so the downstream
# normalize / validate / confidence-gate passes keep working unchanged. The DB
# loaders remain as the fallback when an artifact is absent.
# ---------------------------------------------------------------------------


def _concept_to_export(concept: Any) -> dict:
    if not isinstance(concept, dict):
        return {"name": str(concept), "normalized": str(concept)}
    return {
        "name": concept.get("name", ""),
        "normalized": concept.get("normalized", "") or concept.get("name", ""),
        "concept_type": concept.get("concept_type", "unknown"),
        "role": concept.get("role", "unknown"),
    }


def build_claims_export(
    claim_artifact: Any,
    *,
    document_id: str,
) -> list[dict]:
    """Convert a ``claim_object_builder`` artifact into claims/claims.json shape.

    Preserves the claim_object_builder fields (issue #383 acceptance: claims.json
    keeps claim_object_builder fields) — atomicity / is_atomic / equation_ids /
    source_evidence_ids / linked_component_ids / qualification_reason / concepts —
    while also emitting the legacy keys (``equation`` / ``source_scope`` /
    ``evidence_text``) the downstream normalize / validation passes expect.
    """
    cb = _coerce_dict(claim_artifact)
    records = cb.get("claims") if isinstance(cb.get("claims"), list) else []
    out: list[dict] = []
    seen: set[str] = set()
    for r in records:
        if not isinstance(r, dict):
            continue
        claim_id = str(r.get("claim_id") or "").strip()
        if not claim_id or claim_id in seen:
            continue
        seen.add(claim_id)
        equation_ids = [str(v) for v in (r.get("equation_ids") or []) if v]
        source_evidence_ids = [str(v) for v in (r.get("source_evidence_ids") or []) if v]
        source_span_ids = [str(v) for v in (r.get("source_span_ids") or []) if v]
        source_scope = {
            "document_id": r.get("document_id") or document_id,
            "section_id": r.get("section_id") or "",
            "section_title": r.get("section_title") or "",
            "span_id": source_span_ids[0] if source_span_ids else "",
        }
        out.append({
            "claim_id": claim_id,
            "document_id": r.get("document_id") or document_id,
            "source_scope": source_scope,
            "claim_type": r.get("claim_type") or "unknown",
            "text": r.get("text") or "",
            "normalized_text": r.get("normalized_text") or r.get("text") or "",
            "concepts": [_concept_to_export(c) for c in (r.get("concepts") or [])],
            "equation": {"equation_ids": equation_ids} if equation_ids else {},
            "support_status": r.get("support_status") or "source_backed",
            "evidence_text": "",
            "review_status": r.get("review_status") or "teacher_review_required",
            # Preserved claim_object_builder fields (issue #383).
            "equation_ids": equation_ids,
            "inferred_equation_ids": [str(v) for v in (r.get("inferred_equation_ids") or []) if v],
            "source_evidence_ids": source_evidence_ids,
            "source_span_ids": source_span_ids,
            "linked_component_ids": [str(v) for v in (r.get("linked_component_ids") or []) if v],
            "atomicity": r.get("atomicity") or "atomic",
            "is_atomic": bool(r.get("is_atomic", r.get("atomicity", "atomic") == "atomic")),
            "qualification_reason": r.get("qualification_reason"),
            "concept_assignment_status": r.get("concept_assignment_status") or "review_required",
            "confidence": float(r.get("confidence") or 0.0),
            # Equation/derivation-synthesised claim fields (issue #388).
            "derivation_ids": [str(v) for v in (r.get("derivation_ids") or []) if v],
            "synthesis_method": r.get("synthesis_method") or "",
            "review_reasons": list(r.get("review_reasons") or []),
        })
    return out


def build_components_export(
    component_artifact: Any,
    *,
    document_id: str,
) -> list[dict]:
    """Convert a ``component_assembly`` artifact into components/components.json shape.

    Preserves the component_assembly fields (issue #383 acceptance: components.json
    keeps component_assembly fields) — responsibility_type / linked_*_ids /
    input/output equation ids / internal_flow / split_recommendation / publish_ready —
    alongside the legacy keys the DB loader produced (``name`` / ``component_type`` /
    ``evidence_claims`` / ``source_scope`` …).
    """
    ca = _coerce_dict(component_artifact)
    records = ca.get("components") if isinstance(ca.get("components"), list) else []
    out: list[dict] = []
    seen: set[str] = set()
    for r in records:
        if not isinstance(r, dict):
            continue
        component_id = str(r.get("component_id") or "").strip()
        if not component_id or component_id in seen:
            continue
        seen.add(component_id)
        evidence_refs = r.get("evidence_refs") if isinstance(r.get("evidence_refs"), dict) else {}
        linked_claim_ids = [str(v) for v in (r.get("linked_claim_ids") or []) if v]
        evidence_claims = list(dict.fromkeys(
            linked_claim_ids + [str(v) for v in (evidence_refs.get("claim_ids") or []) if v]
        ))
        source_scope = dict(r.get("source_scope") or {})
        source_scope.setdefault("document_id", r.get("document_id") or document_id)
        component_type = r.get("component_type") or r.get("responsibility_type") or "theory"
        out.append({
            "component_id": component_id,
            "course_id": "",
            "document_id": r.get("document_id") or document_id,
            "name": r.get("label") or r.get("name") or "",
            "component_type": component_type,
            "origin": "paper",
            "source_scope": source_scope,
            "evidence_claims": evidence_claims,
            "maturity_level": r.get("maturity_level") or "paper_claim",
            "maturity_source": r.get("maturity_source") or "llm_proposed",
            "review_status": r.get("review_status") or "teacher_review_required",
            "summary": r.get("summary") or "",
            "inputs": list(r.get("inputs") or []),
            "outputs": list(r.get("outputs") or []),
            "preconditions": list(r.get("preconditions") or []),
            "cautions": list(r.get("cautions") or []),
            "constraints": list(r.get("constraints") or []),
            "invalid_conditions": list(r.get("invalid_conditions") or []),
            "dependencies": list(r.get("dependencies") or []),
            "connectors": r.get("connectors") or {},
            "internal_flow": list(r.get("internal_flow") or []),
            "teacher_notes": r.get("teaching_takeaway") or "",
            "smiles_dsl": "",
            # Preserved component_assembly fields (issue #383 / #384 / #386).
            "responsibility_type": r.get("responsibility_type") or "",
            "primary_operation": r.get("primary_operation") or r.get("operation") or "",
            "secondary_operations": list(r.get("secondary_operations") or []),
            "split_recommendation": r.get("split_recommendation") or {},
            "component_quality": r.get("component_quality") or {},
            "publish_ready": bool(r.get("publish_ready", False)),
            "assumptions": list(r.get("assumptions") or []),
            "approximations": list(r.get("approximations") or []),
            "evidence_refs": evidence_refs,
            "linked_claim_ids": linked_claim_ids,
            "linked_equation_ids": [str(v) for v in (r.get("linked_equation_ids") or []) if v],
            "linked_evidence_ids": [str(v) for v in (r.get("linked_evidence_ids") or []) if v],
            "linked_derivation_ids": [str(v) for v in (r.get("linked_derivation_ids") or []) if v],
            "input_equation_ids": [str(v) for v in (r.get("input_equation_ids") or []) if v],
            "intermediate_equation_ids": [str(v) for v in (r.get("intermediate_equation_ids") or []) if v],
            "output_equation_ids": [str(v) for v in (r.get("output_equation_ids") or []) if v],
            "constraint_equation_ids": [str(v) for v in (r.get("constraint_equation_ids") or []) if v],
            "definition_equation_ids": [str(v) for v in (r.get("definition_equation_ids") or []) if v],
            "review_required_equation_ids": [str(v) for v in (r.get("review_required_equation_ids") or []) if v],
        })
    return out


# A component-graph node is a *reusable component* only when it lives in the main
# layer as a TheoryOperationNode / component. equation_detail / debug nodes
# (EquationOperationNode, fallback, inferred) are operation-level and must not
# leak into component_graph.json (issue #387).
_COMPONENT_GRAPH_LAYERS = {"main", ""}
_OPERATION_NODE_TYPES = {"EquationOperationNode"}


def _graph_node_is_component(node: dict, known_component_ids: set[str]) -> bool:
    if not isinstance(node, dict):
        return False
    layer = str(node.get("graph_layer") or "main")
    comp_type = str(node.get("component_type") or "")
    node_id = str(node.get("component_id") or node.get("node_id") or node.get("id") or "")
    if known_component_ids and node_id in known_component_ids:
        return True
    if comp_type in _OPERATION_NODE_TYPES:
        return False
    return layer in _COMPONENT_GRAPH_LAYERS


def build_component_graph_export(
    graph_artifact: Any,
    *,
    document_id: str,
    known_component_ids: set[str] | None = None,
) -> dict:
    """Split a ``component_graph`` artifact into separate component / operation graphs.

    Issue #387: component_graph.json nodes are component IDs only; operation-level
    nodes (equation_detail / debug layers, EquationOperationNode) move to
    operation_graph.json, with a component_operation_links mapping. Edges whose
    endpoints are not both components move to the operation graph as well, so
    component_graph validation never fails on operation IDs.

    Returns ``{"component_graph": {...}, "operation_graph": {...},
    "component_operation_links": [...]}``.
    """
    cg = _coerce_dict(graph_artifact)
    raw_nodes = cg.get("nodes") if isinstance(cg.get("nodes"), list) else []
    raw_edges = cg.get("edges") if isinstance(cg.get("edges"), list) else []
    known_component_ids = known_component_ids or set()

    component_node_ids: set[str] = set()
    operation_node_ids: set[str] = set()
    component_nodes: list[dict] = []
    operation_nodes: list[dict] = []
    links: list[dict] = []

    def _node_id(n: dict) -> str:
        return str(n.get("component_id") or n.get("node_id") or n.get("id") or "")

    for n in raw_nodes:
        if not isinstance(n, dict):
            continue
        node_id = _node_id(n)
        if not node_id:
            continue
        if _graph_node_is_component(n, known_component_ids):
            if node_id in component_node_ids:
                continue
            component_node_ids.add(node_id)
            legacy_ids = []
            for key in ("agent_component_id", "legacy_component_id"):
                value = n.get(key)
                if value and str(value) != node_id:
                    legacy_ids.append(str(value))
            component_nodes.append({
                "node_id": node_id,
                "node_type": "component",
                "label": n.get("label") or n.get("name") or "",
                "component_type": n.get("component_type") or "",
                "review_status": n.get("review_status") or "teacher_review_required",
                "source_backing_status": n.get("source_backing_status") or "",
                "graph_layer": n.get("graph_layer") or "main",
                "legacy_ids": legacy_ids,
                "member_component_ids": list(n.get("member_component_ids") or []),
                "detail_node_ids": list(n.get("detail_node_ids") or []),
            })
            # Component → operation links (issue #387): a main node aggregates
            # equation_detail nodes via member_component_ids / detail_node_ids.
            for op_id in list(n.get("member_component_ids") or []) + list(n.get("detail_node_ids") or []):
                if op_id:
                    links.append({"component_id": node_id, "operation_id": str(op_id)})
        else:
            if node_id in operation_node_ids:
                continue
            operation_node_ids.add(node_id)
            operation_nodes.append({
                "operation_id": node_id,
                "node_type": "operation",
                "label": n.get("label") or n.get("visual_label") or "",
                "operation": n.get("operation") or "",
                "graph_layer": n.get("graph_layer") or "equation_detail",
                "source_backing_status": n.get("source_backing_status") or "",
                "review_status": n.get("review_status") or "teacher_review_required",
                "parent_component_id": str(n.get("parent_component_id") or ""),
                "linked_equation_ids": list(n.get("linked_equation_ids") or []),
                "linked_derivation_ids": list(n.get("linked_derivation_ids") or []),
                "review_reasons": list(n.get("review_reasons") or []),
            })
            parent = str(n.get("parent_component_id") or "")
            if parent:
                links.append({"component_id": parent, "operation_id": node_id})

    component_edges: list[dict] = []
    operation_edges: list[dict] = []
    for i, e in enumerate(raw_edges):
        if not isinstance(e, dict):
            continue
        evidence = e.get("evidence") if isinstance(e.get("evidence"), dict) else {}
        source = str(e.get("source_component_id") or e.get("source") or e.get("from") or "")
        target = str(e.get("target_component_id") or e.get("target") or e.get("to") or "")
        edge = {
            "edge_id": e.get("edge_id") or f"component_edge_{i+1:04d}",
            "source": source,
            "target": target,
            "edge_type": e.get("relation") or e.get("edge_type") or e.get("type") or "RELATED_TO",
            "support_status": e.get("support_status") or "source_inferred",
            "evidence_claims": list(evidence.get("evidence_claims") or e.get("evidence_claims") or []),
            "review_status": e.get("review_status") or "teacher_review_required",
        }
        if source in component_node_ids and target in component_node_ids:
            component_edges.append(edge)
        else:
            operation_edges.append({**edge, "edge_id": edge["edge_id"]})

    # De-dup links.
    seen_links: set[tuple[str, str]] = set()
    unique_links: list[dict] = []
    for link in links:
        key = (link["component_id"], link["operation_id"])
        if key in seen_links:
            continue
        seen_links.add(key)
        unique_links.append(link)

    return {
        "component_graph": {
            "graph_schema_version": cg.get("graph_schema_version", "0.1.0"),
            "nodes": component_nodes,
            "edges": component_edges,
        },
        "operation_graph": {
            "graph_schema_version": "0.1.0",
            "nodes": operation_nodes,
            "edges": operation_edges,
        },
        "component_operation_links": unique_links,
    }
