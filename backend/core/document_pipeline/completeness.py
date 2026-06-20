"""Deterministic document-completeness analysis (issues #366 / #371).

A truncated PDF ingest — where the document's central results never reach the
pipeline — is detectable without ground truth. This module performs that check
with no LLM so both the pipeline ExportValidationGate and the export route can
reuse it:

  * equation label continuity — internal gaps in the per-section (N.1)…(N.k)
    sequence, *and* labels referenced in prose (e.g. "Eq. (3.40)") that were
    never ingested as equations (catches a tail truncation at (3.36)),
  * terminal section presence — a Conclusion / Summary-like tail section,
  * ingest reachability (issue #371) — did the DocumentStructure ingest reach
    the *end* of the source document? The pass/fail signal is whether ingested
    content extends close enough to the final page (no large trailing
    un-ingested range), NOT what fraction of pages carry content. Blank /
    figure-only / references-only pages are legitimate, so a sparse page
    distribution alone does not mean the ingest was truncated.

The EvidenceRegistry page distribution is reported separately as an *audit-only*
signal (``evidence_page_distribution``). EvidenceRegistry indexes adopted spans /
equations / captions, not every page of the document, so a complete document can
legitimately have evidence clustered on a few pages. Its sparseness never sets
``complete=false`` (issue #371).

`core` must not import FastAPI / route modules; this module stays dependency-free
so the orchestrator can call it at the DocumentStructure / EvidenceRegistry exit.
"""
from __future__ import annotations

import re
from typing import Any

# Source offsets still use this threshold as a truncation signal. Page
# reachability itself requires explicit EOF/page-end evidence.
MIN_INGEST_REACH_RATIO = 0.8

# EvidenceRegistry pages below this fraction of the document are flagged as
# *sparse* — an audit signal only, never a completeness pass/fail (issue #371).
EVIDENCE_SPARSE_RATIO = 0.5

# Coverage / terminal-section checks are only meaningful once enough content was
# ingested; a small stub legitimately cannot cover many pages or carry a
# dedicated conclusion.
MIN_CONTENT_BLOCKS_FOR_CHECKS = 8

# A detected boundary spanning far fewer pages than the document total is a
# tail-truncation signal (issue #373), matching the export boundary heuristic.
MIN_BOUNDARY_SPAN_RATIO = 0.5

# Tail-truncation (issue #373): a *suspicion* — not a confirmed missing label —
# that the document was cut off before its end. Combine several deterministic
# signals; never conclude from a single weak one. ``suspected`` requires at
# least two signals and a combined confidence above the threshold.
_TAIL_TRUNCATION_SIGNAL_WEIGHTS = {
    "equation_environment_cut_at_boundary": 0.5,
    "referenced_equation_not_ingested": 0.4,
    "parser_ended_mid_equation": 0.35,
    "document_end_not_reached": 0.3,
    "source_offset_far_from_end": 0.3,
    "terminal_section_missing": 0.2,
    "boundary_span_short": 0.2,
}
_TAIL_TRUNCATION_MIN_SIGNALS = 2
_TAIL_TRUNCATION_CONFIDENCE_THRESHOLD = 0.5

# TeX math environments whose unclosed \begin (issue #373: an align block cut at
# the ingest boundary) is a strong, ground-truth-free truncation signal.
_MATH_ENV_BASE = {
    "align", "equation", "gather", "multline", "eqnarray", "flalign",
    "alignat", "math", "displaymath", "split",
}
_TEX_BEGIN_RE = re.compile(r"\\begin\s*\{([A-Za-z*]+)\}")
_TEX_END_RE = re.compile(r"\\end\s*\{([A-Za-z*]+)\}")

# A symbolic TeX equation label: ``\label{eq:foo}`` / ``\label{some_label}``.
# TeX equations are labelled symbolically (not by a numeric (N.k) sequence), so
# label coverage must be reported separately from numeric continuity (#416).
_TEX_LABEL_RE = re.compile(r"\\label\s*\{([^}]+)\}")
# Unnumbered display math delimiters: ``\[ ... \]`` and ``$$ ... $$``.
_TEX_BRACKET_DISPLAY_RE = re.compile(r"\\\[")
_TEX_DOLLAR_DISPLAY_RE = re.compile(r"(?<!\\)\$\$")

# A parsed equation label token: "(3.36)" / "3.36" / "(12)".
_EQ_LABEL_RE = re.compile(r"^\(?\s*(?:(\d+)\.)?(\d+)\s*\)?$")

# An equation label referenced inside prose: a parenthesised "(major.minor)".
_EQ_REF_RE = re.compile(r"\((\d+)\.(\d+)\)")

# Terminal / wrap-up section headings whose absence means the document tail was
# not ingested (issue #366: the Conclusion section was missing entirely).
_TERMINAL_SECTION_RE = re.compile(
    r"\b(conclusion|conclusions|concluding|summary|discussion|outlook|"
    r"final remarks|closing remarks)\b|結論|まとめ|結言|総括|考察",
    re.IGNORECASE | re.UNICODE,
)

# Block types that carry ingested document content (page coverage is judged on
# these; section headings spread thinly and would mask a truncated body).
_CONTENT_BLOCK_TYPES = {
    "body_paragraph",
    "equation_block",
    "figure_caption",
    "table_caption",
}


def parse_equation_label(label: Any) -> tuple[int, int] | None:
    """Parse an equation label into a (major, minor) ordinal pair, or None."""
    if label is None:
        return None
    m = _EQ_LABEL_RE.match(str(label).strip())
    if not m:
        return None
    major = int(m.group(1)) if m.group(1) is not None else 0
    minor = int(m.group(2))
    return major, minor


def _label_text(major: int, minor: int) -> str:
    return f"({major}.{minor})" if major else f"({minor})"


def _compress_ranges(pages: list[int]) -> list[list[int]]:
    """Compress a sorted page list into [start, end] inclusive ranges."""
    ranges: list[list[int]] = []
    for p in sorted(pages):
        if ranges and p == ranges[-1][1] + 1:
            ranges[-1][1] = p
        else:
            ranges.append([p, p])
    return ranges


def _evidence_pages(evidence: dict | None) -> set[int]:
    pages: set[int] = set()
    if not isinstance(evidence, dict):
        return pages
    for rec in evidence.get("records") or []:
        if not isinstance(rec, dict):
            continue
        src = rec.get("source") if isinstance(rec.get("source"), dict) else {}
        page = src.get("page")
        if isinstance(page, int):
            pages.add(page)
    return pages


def _is_math_env(env: str) -> bool:
    return env.rstrip("*") in _MATH_ENV_BASE


def detect_unclosed_math_environments(tex_source: Any) -> list[str]:
    """Return math environments left unclosed in ``tex_source`` (issue #373).

    A ``\\begin{align}`` with no matching ``\\end{align}`` means the LaTeX source
    was cut inside an equation block (the #366 failure mode). Only math
    environments are considered, so unbalanced prose environments do not raise a
    false truncation signal. Labelled-vs-unlabelled equations are irrelevant
    here — this works purely on environment balance.
    """
    text = str(tex_source or "")
    if not text:
        return []
    text = re.sub(r"(?m)(?<!\\)%.*$", "", text)
    stack: list[str] = []
    unmatched: set[str] = set()
    tokens = sorted(
        [(m.start(), "begin", m.group(1)) for m in _TEX_BEGIN_RE.finditer(text)]
        + [(m.start(), "end", m.group(1)) for m in _TEX_END_RE.finditer(text)]
    )
    for _, kind, env in tokens:
        if not _is_math_env(env):
            continue
        if kind == "begin":
            stack.append(env)
        elif stack and stack[-1] == env:
            stack.pop()
        else:
            unmatched.add(env)
    unmatched.update(stack)
    return sorted(unmatched)


def tex_equation_inventory(tex_source: Any) -> dict:
    """Inventory TeX math environments and symbolic labels (#416).

    Returns ``{"display_math_blocks": int, "labels": [str], "label_count": int}``.
    ``display_math_blocks`` counts numbered/unnumbered display environments and
    ``\\[ \\]`` / ``$$`` delimiters; ``labels`` are the symbolic ``\\label{...}``
    targets that occur inside math environments (TeX labels are symbolic, not the
    numeric ``(N.k)`` scheme handled by the label-continuity check).
    """
    text = str(tex_source or "")
    if not text:
        return {"display_math_blocks": 0, "labels": [], "label_count": 0}
    text = re.sub(r"(?m)(?<!\\)%.*$", "", text)

    # Collect math-environment spans so labels can be attributed to math.
    begins = [(m.start(), m.end(), m.group(1)) for m in _TEX_BEGIN_RE.finditer(text)]
    ends = {m.group(1): [] for m in _TEX_END_RE.finditer(text)}
    for m in _TEX_END_RE.finditer(text):
        ends.setdefault(m.group(1), []).append(m.start())

    display_math_blocks = 0
    math_spans: list[tuple[int, int]] = []
    used_end: dict[str, set[int]] = {}
    for start, body_start, env in begins:
        if not _is_math_env(env):
            continue
        display_math_blocks += 1
        end_starts = ends.get(env) or []
        chosen = None
        for e in sorted(end_starts):
            if e > body_start and e not in used_end.get(env, set()):
                chosen = e
                break
        if chosen is not None:
            used_end.setdefault(env, set()).add(chosen)
            math_spans.append((body_start, chosen))
    display_math_blocks += len(_TEX_BRACKET_DISPLAY_RE.findall(text))
    # ``$$`` delimiters come in pairs; each pair is one display block.
    display_math_blocks += len(_TEX_DOLLAR_DISPLAY_RE.findall(text)) // 2

    labels: list[str] = []
    for m in _TEX_LABEL_RE.finditer(text):
        pos = m.start()
        in_math = any(s <= pos <= e for s, e in math_spans)
        label = m.group(1).strip()
        # Heuristic: a label is an equation label if it sits inside a math
        # environment or uses a conventional ``eq``-style prefix.
        if in_math or re.match(r"(?i)^(eq|eqn|equation)[:.]", label):
            labels.append(label)
    labels = sorted(dict.fromkeys(labels))
    return {
        "display_math_blocks": display_math_blocks,
        "labels": labels,
        "label_count": len(labels),
    }


def _equation_artifact_counts(equations: Any) -> dict:
    """Count candidates / records and accepted-candidate resolution (#416)."""
    eq = equations if isinstance(equations, dict) else {}
    records = [r for r in (eq.get("equations") or eq.get("records") or []) if isinstance(r, dict)]
    candidates = [c for c in (eq.get("equation_candidates") or []) if isinstance(c, dict)]
    record_ids = {str(r.get("equation_id") or r.get("id") or "") for r in records}
    accepted_like = [
        c for c in candidates
        if str(c.get("acceptance_status") or "") in ("accepted", "provisional")
    ]
    resolved = sum(
        1 for c in accepted_like
        if str(c.get("accepted_equation_id") or "") in record_ids
        and str(c.get("accepted_equation_id") or "")
    )
    return {
        "candidate_count": len(candidates),
        "accepted_candidate_count": len(accepted_like),
        "resolved_candidate_count": resolved,
        "record_count": len(records),
    }


def analyze_equation_artifact_coverage(
    structure: Any,
    equations: Any,
    *,
    tex_source: Any = None,
    pages_total: Any = None,
) -> dict:
    """Compare TeX math blocks / labels / candidates / records (#416).

    Produces a coverage report (counts + symbolic labels) and a list of
    ``review_reasons`` for artifact-level equation gaps that page-based ingest
    reachability cannot see — in particular TeX inputs where ``pages=0`` would
    otherwise let a missing equation registry pass silently.
    """
    structure = structure if isinstance(structure, dict) else {}
    tex = tex_equation_inventory(tex_source)
    counts = _equation_artifact_counts(equations)

    review_reasons: list[str] = []
    # A large silent shrink from accepted/provisional candidates to final records.
    accepted = counts["accepted_candidate_count"]
    resolved = counts["resolved_candidate_count"]
    if accepted and resolved < accepted:
        review_reasons.append("accepted_candidate_not_in_registry")

    # pages=0 (or unknown) TeX documents: page reachability is meaningless, so a
    # TeX file with display math but no final equation records is the #416 silent
    # pass. Use artifact coverage as the completeness signal instead.
    no_pages = not (isinstance(pages_total, int) and pages_total > 0)
    if no_pages and tex["display_math_blocks"] > 0 and counts["record_count"] == 0:
        review_reasons.append("tex_display_math_without_equation_records")

    return {
        "tex_display_math_blocks": tex["display_math_blocks"],
        "tex_equation_labels": tex["labels"],
        "tex_equation_label_count": tex["label_count"],
        "equation_candidate_count": counts["candidate_count"],
        "accepted_candidate_count": counts["accepted_candidate_count"],
        "resolved_candidate_count": counts["resolved_candidate_count"],
        "equation_record_count": counts["record_count"],
        "review_reasons": review_reasons,
        "complete": not review_reasons,
    }


def analyze_document_completeness(
    structure: Any,
    evidence: Any = None,
    *,
    document_id: str,
    tex_source: Any = None,
    equations: Any = None,
) -> dict:
    """Return a JSON-serialisable document-completeness report (issues #366 / #371 / #373).

    ``structure`` is a DocumentStructureResult dict; ``evidence`` is an optional
    EvidenceRegistryResult dict. The pass/fail signal for the document body is
    the *ingest reachability* of the DocumentStructure (did the parser reach the
    document end?), reported under ``ingest_coverage``. The EvidenceRegistry page
    distribution is reported separately under ``evidence_page_distribution`` as
    audit-only metadata and never sets ``complete=false`` (issue #371).

    Issue #373 separates three equation-continuity concepts — internal gaps,
    prose-referenced confirmed-missing labels, and a *suspected* tail truncation
    (a combination of deterministic signals, never a guessed missing label). An
    optional ``tex_source`` (LaTeX) lets an unclosed ``align``/``equation``
    environment raise the strongest truncation signal. ``complete`` is False —
    and ``review_reasons`` non-empty — when any check fails.
    """
    structure = structure if isinstance(structure, dict) else {}
    evidence = evidence if isinstance(evidence, dict) else None
    blocks = structure.get("blocks") or []
    sections = structure.get("sections") or []
    metadata = structure.get("metadata") or {}
    pages_total = metadata.get("pages") if isinstance(metadata, dict) else None
    # TeX source may be passed explicitly or carried on the structure metadata.
    if tex_source is None and isinstance(metadata, dict):
        tex_source = metadata.get("tex_source") or metadata.get("source_tex")
    if tex_source is None:
        tex_source = structure.get("tex_source") or structure.get("source_tex")
    parser_pages_processed = None
    parser_reached_eof = None
    if isinstance(metadata, dict):
        for key in ("parser_pages_processed", "pages_processed", "pages_parsed"):
            value = metadata.get(key)
            if isinstance(value, int) and value > 0:
                parser_pages_processed = value
                break
        if isinstance(metadata.get("parser_reached_eof"), bool):
            parser_reached_eof = metadata["parser_reached_eof"]

    block_list = [b for b in blocks if isinstance(b, dict)]
    content_blocks = [b for b in block_list if b.get("block_type") in _CONTENT_BLOCK_TYPES]
    enough_content = len(content_blocks) >= MIN_CONTENT_BLOCKS_FOR_CHECKS

    # --- equation label continuity ------------------------------------------
    defined: dict[int, set[int]] = {}
    observed_labels: list[str] = []
    for b in block_list:
        if b.get("block_type") != "equation_block":
            continue
        parsed = parse_equation_label(b.get("equation_label"))
        if parsed is None:
            continue
        major, minor = parsed
        defined.setdefault(major, set()).add(minor)
        observed_labels.append(str(b.get("equation_label")).strip())

    defined_pairs = {(maj, mn) for maj, mns in defined.items() for mn in mns}

    # Labels referenced in prose but never ingested as equations. Restrict to
    # major sections that DO have defined equations so figure / citation numbers
    # from unrelated schemes are not mistaken for missing equations.
    referenced_missing: set[tuple[int, int]] = set()
    for b in block_list:
        if b.get("block_type") == "equation_block":
            continue
        for m in _EQ_REF_RE.finditer(str(b.get("text") or "")):
            pair = (int(m.group(1)), int(m.group(2)))
            if pair[0] in defined and pair not in defined_pairs:
                referenced_missing.add(pair)

    # Internal gaps: minors missing *inside* an observed (N.1)…(N.k) run. These
    # are confirmed missing because a later label in the same run was ingested.
    # An unreferenced tail beyond the last observed label (e.g. (3.37) after a
    # run ending at (3.36)) is NOT added here — that is only a truncation
    # suspicion, never a guessed missing label (issue #373).
    internal_gap_pairs: set[tuple[int, int]] = set()
    for major, minors in defined.items():
        for minor in range(1, max(minors)):
            if minor not in minors:
                internal_gap_pairs.add((major, minor))

    # Confirmed missing = internal gaps ∪ prose-referenced-but-not-ingested.
    missing_pairs: set[tuple[int, int]] = set(referenced_missing) | internal_gap_pairs
    missing_labels = [_label_text(maj, mn) for maj, mn in sorted(missing_pairs)]
    internal_gap_labels = [_label_text(maj, mn) for maj, mn in sorted(internal_gap_pairs)]
    equation_has_gaps = bool(missing_labels)

    # --- terminal section presence ------------------------------------------
    terminal_titles: list[str] = []
    for s in sections if isinstance(sections, list) else []:
        if isinstance(s, dict) and _TERMINAL_SECTION_RE.search(str(s.get("title") or "")):
            terminal_titles.append(str(s.get("title")))
    for b in block_list:
        if b.get("block_type") in ("section_heading", "subsection_heading") and (
            _TERMINAL_SECTION_RE.search(str(b.get("text") or ""))
        ):
            terminal_titles.append(str(b.get("text")).strip())
    has_sections = bool(sections) or any(
        b.get("block_type") in ("section_heading", "subsection_heading")
        for b in block_list
    )
    terminal_present = bool(terminal_titles)
    # Only expect a terminal section once the document is substantial enough.
    terminal_missing = enough_content and has_sections and not terminal_present

    # --- ingest reachability (issue #371) -----------------------------------
    # The pass/fail signal is whether the DocumentStructure ingest reached the
    # document end, not what fraction of pages carry content. ``content_pages``
    # are pages with body / equation / caption blocks; ``all_ingested_pages``
    # also counts headings / references so a References- or figure-only final
    # page still proves the parser reached the document end.
    content_pages = sorted(
        {int(b["page"]) for b in content_blocks if isinstance(b.get("page"), int)}
    )
    all_ingested_pages = sorted(
        {int(b["page"]) for b in block_list if isinstance(b.get("page"), int)}
    )
    first_content_page = content_pages[0] if content_pages else None
    last_content_page = content_pages[-1] if content_pages else None
    last_ingested_page = all_ingested_pages[-1] if all_ingested_pages else None

    structure_page_coverage_ratio = None
    trailing_uningested_page_ranges: list[list[int]] = []
    reached_document_end = True
    ingest_sufficient = True
    if isinstance(pages_total, int) and pages_total > 0:
        structure_page_coverage_ratio = round(len(content_pages) / pages_total, 4)
        # The furthest page the parser is known to have reached: ingested blocks
        # or, when available, the parser's own processed-page count.
        effective_last = max(last_ingested_page or 0, parser_pages_processed or 0)
        if 0 < effective_last < pages_total:
            trailing_uningested_page_ranges = _compress_ranges(
                list(range(effective_last + 1, pages_total + 1))
            )
        elif effective_last == 0:
            trailing_uningested_page_ranges = _compress_ranges(
                list(range(1, pages_total + 1))
            )
        reached_document_end = bool(
            parser_reached_eof is True
            or (
                parser_pages_processed is not None
                and parser_pages_processed >= pages_total
            )
            or last_ingested_page == pages_total
        )
        # Reachability is only enforced once enough content was ingested; a small
        # stub legitimately cannot reach a 20-page tail.
        if enough_content:
            ingest_sufficient = reached_document_end

    # --- evidence page distribution (audit-only, issue #371) ----------------
    # EvidenceRegistry indexes adopted spans / equations / captions, not whole
    # pages, so its sparseness is recorded for audit but never blocks publish.
    evidence_pages = sorted(_evidence_pages(evidence))
    evidence_distribution_ratio = None
    evidence_sparse = False
    if evidence is not None and isinstance(pages_total, int) and pages_total > 0:
        evidence_distribution_ratio = round(len(evidence_pages) / pages_total, 4)
        evidence_sparse = evidence_distribution_ratio < EVIDENCE_SPARSE_RATIO

    # --- tail-truncation suspicion (issue #373) -----------------------------
    # Combine several deterministic signals; never conclude from one alone, and
    # never invent a missing label. The signals have distinct provenance from
    # confirmed missing labels, so they are reported as a *suspicion*.
    precomputed_unclosed = (
        metadata.get("unclosed_math_environments")
        if isinstance(metadata, dict)
        else None
    )
    if tex_source is not None:
        unclosed_envs = detect_unclosed_math_environments(tex_source)
    elif isinstance(precomputed_unclosed, list):
        unclosed_envs = sorted(
            {str(env) for env in precomputed_unclosed if _is_math_env(str(env))}
        )
    else:
        unclosed_envs = detect_unclosed_math_environments(tex_source)

    # parser ended inside an equation: the last block in reading order is an
    # equation block while the ingest did not reach the document end.
    parser_ended_mid_equation = False
    if block_list and enough_content and not reached_document_end:
        last_block = max(
            block_list,
            key=lambda b: (b.get("page") or 0, b.get("order") or 0),
        )
        if last_block.get("block_type") == "equation_block":
            parser_ended_mid_equation = True

    # boundary span far shorter than the document (collapsed boundary).
    boundary_span_short = False
    sec_starts = [
        int(s["page_start"]) for s in sections
        if isinstance(s, dict) and isinstance(s.get("page_start"), int)
    ]
    sec_ends = [
        int(s["page_end"]) for s in sections
        if isinstance(s, dict) and isinstance(s.get("page_end"), int)
    ]
    if sec_starts and sec_ends and isinstance(pages_total, int) and pages_total > 0:
        span = max(sec_ends) - min(sec_starts) + 1
        if span < pages_total * MIN_BOUNDARY_SPAN_RATIO:
            boundary_span_short = True

    # source byte offset far short of the input end (when the parser records it).
    source_offset_far_from_end = False
    if isinstance(metadata, dict):
        total_bytes = metadata.get("source_bytes_total")
        done_bytes = metadata.get("source_bytes_processed")
        if done_bytes is None:
            done_bytes = metadata.get("last_source_offset")
        if (
            isinstance(total_bytes, int) and total_bytes > 0
            and isinstance(done_bytes, int)
            and done_bytes / total_bytes < MIN_INGEST_REACH_RATIO
        ):
            source_offset_far_from_end = True

    tail_signals: list[str] = []
    if unclosed_envs:
        tail_signals.append("equation_environment_cut_at_boundary")
    if referenced_missing:
        tail_signals.append("referenced_equation_not_ingested")
    if parser_ended_mid_equation:
        tail_signals.append("parser_ended_mid_equation")
    if enough_content and not reached_document_end:
        tail_signals.append("document_end_not_reached")
    if source_offset_far_from_end:
        tail_signals.append("source_offset_far_from_end")
    if terminal_missing:
        tail_signals.append("terminal_section_missing")
    if boundary_span_short:
        tail_signals.append("boundary_span_short")

    tail_confidence = round(
        min(0.99, sum(_TAIL_TRUNCATION_SIGNAL_WEIGHTS.get(s, 0.0) for s in tail_signals)),
        2,
    )
    tail_truncation_suspected = bool(
        len(tail_signals) >= _TAIL_TRUNCATION_MIN_SIGNALS
        and tail_confidence >= _TAIL_TRUNCATION_CONFIDENCE_THRESHOLD
    )

    # --- equation artifact coverage (issue #416) ----------------------------
    # TeX math-block / label / candidate / record coverage. This is the
    # completeness signal that page reachability cannot provide for TeX inputs
    # where ``pages=0`` and the parser reports EOF.
    equation_coverage = analyze_equation_artifact_coverage(
        structure, equations, tex_source=tex_source, pages_total=pages_total
    )

    review_reasons: list[str] = []
    if equation_has_gaps:
        review_reasons.append("equation_label_discontinuity")
    if terminal_missing:
        review_reasons.append("terminal_section_missing")
    if not ingest_sufficient:
        review_reasons.append("ingest_incomplete")
    if tail_truncation_suspected:
        review_reasons.append("tail_truncation_suspected")
    if not equation_coverage.get("complete", True):
        review_reasons.append("equation_artifact_coverage_incomplete")

    return {
        "document_id": document_id,
        "complete": not review_reasons,
        "review_reasons": review_reasons,
        "equation_label_continuity": {
            "observed_labels": observed_labels,
            "missing_labels": missing_labels,
            "internal_gaps": internal_gap_labels,
            "referenced_missing_labels": [
                _label_text(maj, mn) for maj, mn in sorted(referenced_missing)
            ],
            "has_gaps": equation_has_gaps,
        },
        "tail_truncation": {
            "suspected": tail_truncation_suspected,
            "confidence": tail_confidence if tail_signals else 0.0,
            "signals": tail_signals,
            "unclosed_math_environments": unclosed_envs,
        },
        "terminal_section": {
            "present": terminal_present,
            "missing": terminal_missing,
            "matched_titles": terminal_titles,
        },
        "ingest_coverage": {
            "pages_total": pages_total,
            "first_content_page": first_content_page,
            "last_content_page": last_content_page,
            "last_ingested_page": last_ingested_page,
            "parser_pages_processed": parser_pages_processed,
            "parser_reached_eof": parser_reached_eof,
            "reached_document_end": reached_document_end,
            "trailing_uningested_page_ranges": trailing_uningested_page_ranges,
            "structure_page_coverage_ratio": structure_page_coverage_ratio,
            "sufficient": ingest_sufficient,
        },
        "evidence_page_distribution": {
            "pages": evidence_pages,
            "distribution_ratio": evidence_distribution_ratio,
            "sparse": evidence_sparse,
        },
        "equation_artifact_coverage": equation_coverage,
    }
