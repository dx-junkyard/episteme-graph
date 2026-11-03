"""Validation for EquationSemanticsAgent outputs.

Issue #245: EquationCandidate / EquationRecord の hard error / warning を実装。
"""
from __future__ import annotations

from ..internal_id_text import find_internal_ids, strip_internal_ids
from .schema import (
    ACCEPTANCE_STATUSES,
    CANDIDATE_REVIEW_REASONS,
    DEFINITION_STATUSES,
    EQUATION_TYPES,
    EXTRACTION_STATUSES,
    LINKED_TEXT_RELATIONS,
    RECONSTRUCTION_STATUSES,
    REVIEW_FLAGS,
    SEMANTIC_STATUSES,
    CartridgeContext,
    EquationCandidate,
    EquationRecord,
    EquationSemanticsResult,
    ValidationIssue,
)


class EquationSemanticsValidator:
    def validate(
        self,
        result: EquationSemanticsResult,
        cartridge: CartridgeContext | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        # --- candidates ---
        for candidate in result.equation_candidates:
            issues += self._check_candidate(candidate)

        # --- equations (accepted) ---
        if not result.equations and not result.equation_candidates:
            return [ValidationIssue(
                rule_id="no_equations",
                severity="error",
                message="No equation candidates or semantics records are present",
                field="equations",
            )]

        for record in result.equations:
            issues += self._check_record(record)
            issues += self._check_reconstruction(record)
            issues += self._check_equation_consistency(record)
            issues += self._check_cartridge_terms(record, cartridge)

        issues += self._check_duplicate_content_hashes(result.equations)
        issues += self._check_candidate_record_completeness(result)

        return issues

    # ------------------------------------------------------------------
    # Candidate → record completeness (issue #416)
    # ------------------------------------------------------------------

    def _check_candidate_record_completeness(
        self, result: EquationSemanticsResult
    ) -> list[ValidationIssue]:
        """Guarantee accepted/provisional candidates resolve to a final record.

        A candidate that was accepted/provisional but whose ``accepted_equation_id``
        is empty or points at no EquationRecord is a hard error
        (``accepted_candidate_missing_equation``): downstream claim / component /
        derivation could otherwise reference an equation that does not exist. A
        large silent shrink from candidate count to record count is also surfaced.

        This check only runs on the full result (candidates + equations both
        present); per-record partial validation passes an empty candidate list and
        is unaffected.
        """
        candidates = result.equation_candidates
        if not candidates:
            return []

        issues: list[ValidationIssue] = []
        record_ids = {r.equation_id for r in result.equations}
        accepted_like = [
            c for c in candidates
            if c.acceptance_status in ("accepted", "provisional")
        ]
        resolved = 0
        for c in accepted_like:
            accepted_id = c.accepted_equation_id
            if not accepted_id:
                issues.append(ValidationIssue(
                    rule_id="accepted_candidate_missing_equation",
                    severity="error",
                    message=(
                        f"{c.candidate_id} is {c.acceptance_status} but has no "
                        "accepted_equation_id (no EquationRecord produced)"
                    ),
                    field=f"{c.candidate_id}.accepted_equation_id",
                ))
                continue
            if accepted_id not in record_ids:
                issues.append(ValidationIssue(
                    rule_id="accepted_candidate_missing_equation",
                    severity="error",
                    message=(
                        f"{c.candidate_id} accepted_equation_id "
                        f"{accepted_id!r} does not resolve to any EquationRecord"
                    ),
                    field=f"{c.candidate_id}.accepted_equation_id",
                ))
                continue
            resolved += 1

        # Large silent shrink: many accepted/provisional candidates collapsed into
        # far fewer records without each being individually accounted for above.
        if accepted_like and resolved < len(accepted_like):
            issues.append(ValidationIssue(
                rule_id="equation_candidate_silent_shrink",
                severity="warning",
                message=(
                    f"{len(accepted_like)} accepted/provisional candidate(s) but "
                    f"only {resolved} resolved to an EquationRecord — "
                    "candidate→record shrink requires review"
                ),
                field="equation_candidates",
            ))
        return issues

    @staticmethod
    def _check_duplicate_content_hashes(records: list) -> list[ValidationIssue]:
        """Within-document hash collisions are reported, never auto-merged (#362)."""
        by_hash: dict[str, list[str]] = {}
        for record in records:
            hash_value = str(getattr(record, "content_hash", "") or "")
            if hash_value:
                by_hash.setdefault(hash_value, []).append(record.equation_id)
        issues: list[ValidationIssue] = []
        for hash_value, equation_ids in by_hash.items():
            if len(equation_ids) > 1:
                issues.append(ValidationIssue(
                    rule_id="duplicate_equation_content_hash",
                    severity="warning",
                    message=(
                        f"equations {', '.join(equation_ids)} share content_hash "
                        f"{hash_value}; review for duplication (not auto-merged)"
                    ),
                    field=equation_ids[0],
                ))
        return issues

    # ------------------------------------------------------------------
    # Candidate validation
    # ------------------------------------------------------------------

    def _check_candidate(self, c: EquationCandidate) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        cid = c.candidate_id

        if c.extraction_status not in EXTRACTION_STATUSES:
            issues.append(ValidationIssue(
                rule_id="invalid_extraction_status",
                severity="error",
                message=f"{cid} has invalid extraction_status {c.extraction_status!r}",
                field=f"{cid}.extraction_status",
            ))
        if c.acceptance_status not in ACCEPTANCE_STATUSES:
            issues.append(ValidationIssue(
                rule_id="invalid_acceptance_status",
                severity="error",
                message=f"{cid} has invalid acceptance_status {c.acceptance_status!r}",
                field=f"{cid}.acceptance_status",
            ))

        # Hard errors: fragment / label_only として accepted は禁止
        if c.extraction_status == "fragment_only" and c.acceptance_status == "accepted":
            issues.append(ValidationIssue(
                rule_id="fragment_only_accepted",
                severity="error",
                message=f"{cid} is fragment_only but accepted — must not be treated as equation body",
                field=f"{cid}.acceptance_status",
            ))
        if c.extraction_status == "label_only" and c.acceptance_status == "accepted":
            issues.append(ValidationIssue(
                rule_id="label_only_accepted",
                severity="error",
                message=f"{cid} is label_only but accepted — must not be treated as equation body",
                field=f"{cid}.acceptance_status",
            ))

        for r in c.review_reason:
            if r not in CANDIDATE_REVIEW_REASONS:
                issues.append(ValidationIssue(
                    rule_id="invalid_candidate_review_reason",
                    severity="warning",
                    message=f"{cid} has unknown review_reason {r!r}",
                    field=f"{cid}.review_reason",
                ))

        return issues

    # ------------------------------------------------------------------
    # EquationRecord validation
    # ------------------------------------------------------------------

    def _check_record(self, record: EquationRecord) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        eid = record.equation_id

        if not eid:
            issues.append(ValidationIssue(
                rule_id="missing_equation_id",
                severity="error",
                message="equation_id is empty",
                field="equation_id",
            ))

        # candidate_trace_ids must not be empty for accepted equations
        if not record.candidate_trace_ids:
            issues.append(ValidationIssue(
                rule_id="missing_candidate_trace",
                severity="error",
                message=f"{eid} accepted equation has no candidate_trace_ids",
                field=f"{eid}.candidate_trace_ids",
            ))

        # Issue #368: a rejected LLM label / equation_id is kept as audit
        # metadata; surface it as a warning so the override is traceable.
        src_loc = record.source_extraction.source_location or {}
        rejected_label = src_loc.get("llm_rejected_label")
        if rejected_label is not None:
            issues.append(ValidationIssue(
                rule_id="llm_label_overridden",
                severity="warning",
                message=(
                    f"{eid} LLM-proposed label {rejected_label!r} was rejected by the "
                    "source-aware validator and normalized away"
                ),
                field=f"{eid}.source_extraction.source_location.llm_rejected_label",
            ))
        rejected_eq_id = src_loc.get("llm_rejected_equation_id")
        if rejected_eq_id is not None:
            issues.append(ValidationIssue(
                rule_id="llm_equation_id_overridden",
                severity="warning",
                message=(
                    f"{eid} LLM-proposed equation_id {rejected_eq_id!r} was discarded; "
                    "the deterministic normalized id is used instead"
                ),
                field=f"{eid}.source_extraction.source_location.llm_rejected_equation_id",
            ))

        # source_extraction.block_id must exist
        block_id = record.source_extraction.source_location.get("block_id", "")
        if not block_id:
            issues.append(ValidationIssue(
                rule_id="missing_source_block_id",
                severity="error",
                message=f"{eid} source_extraction.source_location.block_id is empty",
                field=f"{eid}.source_extraction.source_location.block_id",
            ))

        # extraction_status validation
        ext_status = record.source_extraction.extraction_status
        if ext_status not in EXTRACTION_STATUSES:
            issues.append(ValidationIssue(
                rule_id="invalid_extraction_status",
                severity="error",
                message=f"{eid} has invalid source_extraction.extraction_status {ext_status!r}",
                field=f"{eid}.source_extraction.extraction_status",
            ))

        # semantics validation
        sem = record.semantics
        if sem.equation_type not in EQUATION_TYPES:
            issues.append(ValidationIssue(
                rule_id="invalid_equation_type",
                severity="error",
                message=f"{eid} has invalid equation_type {sem.equation_type!r}",
                field=f"{eid}.semantics.equation_type",
            ))
        for t in sem.secondary_types:
            if t not in EQUATION_TYPES:
                issues.append(ValidationIssue(
                    rule_id="invalid_equation_type",
                    severity="error",
                    message=f"{eid} has invalid secondary_type {t!r}",
                    field=f"{eid}.semantics.secondary_types",
                ))
        if sem.semantic_status not in SEMANTIC_STATUSES:
            issues.append(ValidationIssue(
                rule_id="invalid_semantic_status",
                severity="error",
                message=f"{eid} has invalid semantic_status {sem.semantic_status!r}",
                field=f"{eid}.semantics.semantic_status",
            ))
        if not (0.0 <= sem.confidence <= 1.0):
            issues.append(ValidationIssue(
                rule_id="confidence_out_of_range",
                severity="error",
                message=f"{eid} semantics.confidence is out of range",
                field=f"{eid}.semantics.confidence",
            ))
        for symbol in sem.defined_symbols:
            if symbol.definition_status not in DEFINITION_STATUSES:
                issues.append(ValidationIssue(
                    rule_id="invalid_definition_status",
                    severity="error",
                    message=f"{eid} has invalid definition_status {symbol.definition_status!r}",
                    field=f"{eid}.semantics.defined_symbols",
                ))
        for span in sem.linked_text_spans:
            relation = span.get("relation")
            if relation not in LINKED_TEXT_RELATIONS:
                issues.append(ValidationIssue(
                    rule_id="invalid_linked_text_relation",
                    severity="error",
                    message=f"{eid} has invalid linked text relation {relation!r}",
                    field=f"{eid}.semantics.linked_text_spans",
                ))
        for flag in sem.review_flags:
            if flag not in REVIEW_FLAGS:
                issues.append(ValidationIssue(
                    rule_id="invalid_review_flag",
                    severity="error",
                    message=f"{eid} has invalid review flag {flag!r}",
                    field=f"{eid}.semantics.review_flags",
                ))
        leaked_ids = find_internal_ids(sem.summary)
        if leaked_ids:
            # IK-0819: 通常は scrub_internal_ids_from_summary が先に外す。残っていれば知らせる。
            issues.append(ValidationIssue(
                rule_id="internal_id_in_summary",
                severity="warning",
                message=f"{eid} semantics.summary contains internal id(s): {', '.join(leaked_ids[:5])}",
                field=f"{eid}.semantics.summary",
            ))
        if not sem.summary.strip():
            issues.append(ValidationIssue(
                rule_id="empty_summary",
                severity="error",
                message=f"{eid} semantics.summary is empty",
                field=f"{eid}.semantics.summary",
            ))

        # Hard error: reconstruction_based が can_support_claim=True は禁止
        if record.confidence_policy.must_not_treat_as_source_extracted and record.confidence_policy.can_support_claim:
            issues.append(ValidationIssue(
                rule_id="reconstruction_only_supporting_claim",
                severity="error",
                message=(
                    f"{eid} must_not_treat_as_source_extracted is True "
                    "but can_support_claim is also True"
                ),
                field=f"{eid}.confidence_policy",
            ))

        if getattr(record, "equation_consistency", None) and record.equation_consistency.review_required:
            if record.confidence_policy.can_support_claim:
                issues.append(ValidationIssue(
                    rule_id="inconsistent_equation_supporting_claim",
                    severity="error",
                    message=f"{eid} has equation_consistency.review_required but can_support_claim is true",
                    field=f"{eid}.confidence_policy.can_support_claim",
                ))
            # review_required の理由が「PDF テキスト層は信用しない」という一律の前提
            # だけのときは、確度の高い復元を導出に使ってよい（``EquationConfidencePolicy
            # .derive`` の救済分岐と同じ線。指揮者判断 2026-09-19）。その式固有の疑い
            # （raw↔latex の mismatch・ラベルの食い違い・corrupted な出典スパン・
            # fidelity guard の理由コード）が1つでもあれば従来どおり hard error。
            if record.confidence_policy.can_be_used_in_derivation and not (
                record.equation_consistency.untrusted_pdf_only()
            ):
                issues.append(ValidationIssue(
                    rule_id="inconsistent_equation_used_in_derivation",
                    severity="error",
                    message=f"{eid} has equation_consistency.review_required but can_be_used_in_derivation is true",
                    field=f"{eid}.confidence_policy.can_be_used_in_derivation",
                ))

        # Consistency warnings
        issues += self._check_semantics_consistency(record)

        return issues

    def _check_equation_consistency(self, record: EquationRecord) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        eid = record.equation_id
        consistency = getattr(record, "equation_consistency", None)
        if consistency is None:
            return [ValidationIssue(
                rule_id="equation_consistency_missing",
                severity="warning",
                message=f"{eid} has no equation_consistency metadata",
                field=f"{eid}.equation_consistency",
            )]
        if consistency.raw_text_latex_match not in ("match", "mismatch", "uncertain"):
            issues.append(ValidationIssue(
                rule_id="invalid_equation_consistency_status",
                severity="error",
                message=f"{eid} has invalid raw_text_latex_match={consistency.raw_text_latex_match!r}",
                field=f"{eid}.equation_consistency.raw_text_latex_match",
            ))
        if consistency.label_location_match not in ("match", "mismatch", "uncertain"):
            issues.append(ValidationIssue(
                rule_id="invalid_equation_consistency_status",
                severity="error",
                message=f"{eid} has invalid label_location_match={consistency.label_location_match!r}",
                field=f"{eid}.equation_consistency.label_location_match",
            ))
        if consistency.source_span_quality not in ("clean", "partial", "corrupted"):
            issues.append(ValidationIssue(
                rule_id="invalid_source_span_quality",
                severity="error",
                message=f"{eid} has invalid source_span_quality={consistency.source_span_quality!r}",
                field=f"{eid}.equation_consistency.source_span_quality",
            ))
        if not (0.0 <= consistency.symbol_overlap_score <= 1.0):
            issues.append(ValidationIssue(
                rule_id="symbol_overlap_score_out_of_range",
                severity="error",
                message=f"{eid} symbol_overlap_score is out of range",
                field=f"{eid}.equation_consistency.symbol_overlap_score",
            ))
        if (
            consistency.raw_text_latex_match == "mismatch"
            or consistency.label_location_match == "mismatch"
            or consistency.source_span_quality == "corrupted"
        ):
            issues.append(ValidationIssue(
                rule_id="equation_consistency_mismatch",
                severity="warning",
                message=f"{eid} raw/source equation data is inconsistent and requires review",
                field=f"{eid}.equation_consistency",
            ))
        return issues

    def _check_reconstruction(self, record: EquationRecord) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        eid = record.equation_id
        rec = record.reconstruction

        if rec.status not in RECONSTRUCTION_STATUSES:
            issues.append(ValidationIssue(
                rule_id="invalid_reconstruction_status",
                severity="error",
                message=f"{eid} has invalid reconstruction.status {rec.status!r}",
                field=f"{eid}.reconstruction.status",
            ))

        if record.source_extraction.needs_math_review and rec.status == "none":
            issues.append(ValidationIssue(
                rule_id="missing_mandatory_reconstruction",
                severity="error",
                message=f"{eid} PDF-derived math must pass through reconstruction",
                field=f"{eid}.reconstruction.status",
            ))

        if rec.status != "none":
            # method / supporting_refs must not be empty when reconstructing
            if not rec.method:
                issues.append(ValidationIssue(
                    rule_id="reconstruction_missing_method",
                    severity="error",
                    message=f"{eid} reconstruction.status={rec.status!r} but method is empty",
                    field=f"{eid}.reconstruction.method",
                ))
            if not rec.supporting_refs:
                issues.append(ValidationIssue(
                    rule_id="reconstruction_missing_supporting_refs",
                    severity="error",
                    message=f"{eid} reconstruction.status={rec.status!r} but supporting_refs is empty",
                    field=f"{eid}.reconstruction.supporting_refs",
                ))
            if not (0.0 <= rec.confidence <= 1.0):
                issues.append(ValidationIssue(
                    rule_id="reconstruction_confidence_out_of_range",
                    severity="error",
                    message=f"{eid} reconstruction.confidence is out of range",
                    field=f"{eid}.reconstruction.confidence",
                ))
            # reconstructed content must not contaminate source_extraction fields
            src = record.source_extraction
            if rec.plain_text and src.plain_text and rec.plain_text == src.plain_text and src.extraction_status in ("missing", "label_only", "fragment_only"):
                issues.append(ValidationIssue(
                    rule_id="reconstruction_contaminating_source",
                    severity="error",
                    message=f"{eid} reconstructed plain_text appears in source_extraction field",
                    field=f"{eid}.source_extraction.plain_text",
                ))

        return issues

    def _check_semantics_consistency(self, record: EquationRecord) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        eid = record.equation_id
        sem = record.semantics

        if sem.equation_type == "definition" and not sem.defined_symbols:
            issues.append(ValidationIssue(
                rule_id="definition_without_symbols",
                severity="warning",
                message=f"{eid} is a definition but has no defined_symbols",
                field=f"{eid}.semantics.defined_symbols",
            ))
        if sem.equation_type == "transformation" and not sem.input_equation_ids:
            issues.append(ValidationIssue(
                rule_id="transformation_without_input_equations",
                severity="warning",
                message=f"{eid} is a transformation but has no input_equation_ids",
                field=f"{eid}.semantics.input_equation_ids",
            ))
        if not record.label and "missing_label" not in sem.review_flags:
            issues.append(ValidationIssue(
                rule_id="missing_label_without_review_flag",
                severity="warning",
                message=f"{eid} has no label and no missing_label review flag",
                field=f"{eid}.semantics.review_flags",
            ))
        if sem.confidence < 0.5 and "low_confidence" not in sem.review_flags:
            issues.append(ValidationIssue(
                rule_id="low_confidence_without_review_flag",
                severity="warning",
                message=f"{eid} has low confidence without review flag",
                field=f"{eid}.semantics.review_flags",
            ))

        # extraction_status が complete なのに source_backed でない場合は警告
        src_status = record.source_extraction.extraction_status
        if src_status == "complete" and sem.semantic_status not in ("source_backed", "context_inferred"):
            issues.append(ValidationIssue(
                rule_id="source_complete_but_not_source_backed",
                severity="warning",
                message=f"{eid} extraction is complete but semantic_status is {sem.semantic_status!r}",
                field=f"{eid}.semantics.semantic_status",
            ))
        # extraction が不十分なのに source_backed を主張している場合は警告
        if src_status in ("missing", "label_only", "fragment_only") and sem.semantic_status == "source_backed":
            issues.append(ValidationIssue(
                rule_id="insufficient_extraction_but_source_backed",
                severity="warning",
                message=(
                    f"{eid} extraction_status={src_status!r} "
                    "but semantic_status claims source_backed"
                ),
                field=f"{eid}.semantics.semantic_status",
            ))

        return issues

    def _check_cartridge_terms(
        self,
        record: EquationRecord,
        cartridge: CartridgeContext | None,
    ) -> list[ValidationIssue]:
        if not cartridge or not cartridge.aliases:
            return []
        src = record.source_extraction
        sem = record.semantics
        text = f"{src.raw_text} {src.plain_text or ''} {sem.summary}".lower()
        matched = [
            canonical
            for canonical, aliases in cartridge.aliases.items()
            if canonical.lower() in text
            or any(str(alias).lower() in text for alias in aliases)
        ]
        if matched and not sem.defined_symbols:
            return [ValidationIssue(
                rule_id="cartridge_notation_without_symbols",
                severity="warning",
                message=(
                    f"{record.equation_id} mentions cartridge term(s) {matched[:3]} "
                    "but has no defined_symbols"
                ),
                field=f"{record.equation_id}.semantics.defined_symbols",
            )]
        return []


def scrub_internal_ids_from_summary(record: EquationRecord) -> list[ValidationIssue]:
    """要約（``semantics.summary``）から内部 ID を外す（IK-0819）。

    材料は近傍ブロックを ``[tex_b80] …`` の形で示すため、LLM が要約に ``(tex_b80)`` を
    書き写すことがある。要約は二層説明の材料や表示に本文として渡るので、ここで外す。
    hard error にはせず（P4）、外した ID は warning の message に列挙し、
    ``internal_id_in_summary`` を review_flags に残す。外した結果が空になるときは元の
    要約を残す（空の要約を作らない — 表示側の遮断が残りを受け持つ）。
    """
    sem = record.semantics
    leaked = find_internal_ids(sem.summary)
    if not leaked:
        return []
    cleaned = strip_internal_ids(sem.summary)
    if cleaned.strip():
        sem.summary = cleaned
    if "internal_id_in_summary" not in sem.review_flags:
        sem.review_flags.append("internal_id_in_summary")
    eid = record.equation_id
    return [ValidationIssue(
        rule_id="internal_id_in_summary",
        severity="warning",
        message=f"{eid} had internal id(s) removed from semantics.summary: {', '.join(leaked[:5])}",
        field=f"{eid}.semantics.summary",
    )]


def restrict_io_links_to_neighbors(
    record: EquationRecord,
    neighbor_equations: list[dict] | None,
) -> list[ValidationIssue]:
    """input/output_equation_ids を LLM に渡した ``neighbor_equations`` の実在 ID に絞る。

    2026-10-16（S1 seq 2839/2845/2848）: 材料に無い ID（別 req から持ち込んだ ID）を
    導出リンクとして信用しない。hard error にはせず（P4）、外した ID は warning の
    message に列挙し、``io_id_not_in_neighbor_equations`` を review_flags に残す。
    ``neighbor_equations is None``（一覧を組まなかった旧経路）は制限しない。
    """
    if neighbor_equations is None:
        return []
    allowed = {
        str(entry.get("equation_id"))
        for entry in neighbor_equations
        if isinstance(entry, dict) and entry.get("equation_id")
    }
    sem = record.semantics
    removed: list[str] = []
    kept_in = [ref for ref in sem.input_equation_ids if ref in allowed]
    removed += [f"input:{ref}" for ref in sem.input_equation_ids if ref and ref not in allowed]
    kept_out = [ref for ref in sem.output_equation_ids if ref in allowed]
    removed += [f"output:{ref}" for ref in sem.output_equation_ids if ref and ref not in allowed]
    if not removed:
        return []
    sem.input_equation_ids = kept_in
    sem.output_equation_ids = kept_out
    if "io_id_not_in_neighbor_equations" not in sem.review_flags:
        sem.review_flags.append("io_id_not_in_neighbor_equations")
    eid = record.equation_id
    return [ValidationIssue(
        rule_id="io_id_not_in_neighbor_equations",
        severity="warning",
        message=(
            f"{eid} had derivation link(s) not listed in neighbor_equations removed: "
            f"{', '.join(removed)}"
        ),
        field=f"{eid}.semantics.input_equation_ids/output_equation_ids",
    )]


def restrict_linked_spans_to_candidates(
    record: EquationRecord,
    candidate_text_spans: list[dict] | None,
) -> list[ValidationIssue]:
    """linked_text_spans を LLM に渡した ``candidate_text_spans`` の実在 span に絞る（IK-0809）。

    ``restrict_io_links_to_neighbors`` と同じ規律: 材料に無い span を本文との結び付きとして
    信用しない。hard error にはせず（P4）、外した span は warning の message に列挙し、
    ``linked_span_not_in_candidates`` を review_flags に残す。span_id はブロックごとに
    振り直されるので (block_id, span_id) の組で照合し、block_id が無い項目は span_id が
    候補内で一意なときだけ受け入れて block_id を補う。``None``（旧経路）は制限しない。
    """
    if candidate_text_spans is None:
        return []
    pairs = {
        (str(c.get("block_id") or ""), str(c.get("span_id") or ""))
        for c in candidate_text_spans
        if isinstance(c, dict) and c.get("span_id")
    }
    blocks_by_span: dict[str, set[str]] = {}
    for block_id, span_id in pairs:
        blocks_by_span.setdefault(span_id, set()).add(block_id)
    sem = record.semantics
    kept: list[dict] = []
    removed: list[str] = []
    for link in sem.linked_text_spans or []:
        if not isinstance(link, dict):
            removed.append(str(link))
            continue
        span_id = str(link.get("span_id") or "")
        block_id = str(link.get("block_id") or "")
        if block_id and (block_id, span_id) in pairs:
            kept.append(link)
        elif not block_id and len(blocks_by_span.get(span_id, ())) == 1:
            kept.append({**link, "block_id": next(iter(blocks_by_span[span_id]))})
        else:
            removed.append(f"{block_id}:{span_id}" if block_id else span_id)
    if not removed:
        if kept != list(sem.linked_text_spans or []):
            sem.linked_text_spans = kept
        return []
    sem.linked_text_spans = kept
    if "linked_span_not_in_candidates" not in sem.review_flags:
        sem.review_flags.append("linked_span_not_in_candidates")
    eid = record.equation_id
    return [ValidationIssue(
        rule_id="linked_span_not_in_candidates",
        severity="warning",
        message=(
            f"{eid} had linked text span(s) not listed in candidate_text_spans removed: "
            f"{', '.join(removed)}"
        ),
        field=f"{eid}.semantics.linked_text_spans",
    )]
