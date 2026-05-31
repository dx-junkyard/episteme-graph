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
        })
    return out


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
                    "reason": s.get("reason") or "",
                    "confidence": float(s.get("confidence") or 0.0),
                })
            out.append({
                "derivation_id": c.get("derivation_id") or "",
                "document_id": c.get("document_id") or document_id,
                "source_section_ids": list(c.get("source_section_ids") or []),
                "steps": steps,
                "teaching_takeaway": c.get("teaching_takeaway") or "",
                "blackbox_policy_suggestion": c.get("blackbox_policy_suggestion") or {},
            })
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
            }],
            "teaching_takeaway": "",
            "blackbox_policy_suggestion": {},
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
        })
    if steps:
        out.append({
            "derivation_id": f"deriv_{chain_id:04d}",
            "document_id": document_id,
            "source_section_ids": section_ids,
            "steps": steps,
            "teaching_takeaway": "Source-order equation chain reconstructed from first-class equation registry.",
            "blackbox_policy_suggestion": {},
        })
    return out


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
# Document boundary
# ---------------------------------------------------------------------------


def build_document_boundary(
    structure_artifact: Any,
    *,
    document_id: str,
) -> dict:
    """Surface the active article boundary inside a (potentially multi-article) PDF.

    The DocumentStructureAgent records page ranges per section; we expose the
    first section's page_start as the article start and the last section's
    page_end as the article end, plus the title block when present, so
    consumers can flag spans that fall outside the boundary.
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

    confidence = 1.0 if (boundary_page_start is not None and boundary_page_end is not None) else 0.5
    needs_review = confidence < 0.8

    return {
        "document_id": document_id,
        "title": metadata.get("title") if isinstance(metadata, dict) else None,
        "authors": list(metadata.get("authors") or []) if isinstance(metadata, dict) else [],
        "boundary_page_start": boundary_page_start,
        "boundary_page_end": boundary_page_end,
        "pages_total": pages_total,
        "active_section_ids": section_ids,
        "confidence": confidence,
        "needs_review": needs_review,
        "review_reason": ([] if not needs_review else ["incomplete_section_page_range"]),
    }
