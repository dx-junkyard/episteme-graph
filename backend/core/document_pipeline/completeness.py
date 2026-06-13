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

# Ingest reachability threshold (issue #371): the document is judged to have
# reached its end when ingested content extends to at least this fraction of the
# total pages. A small trailing tail of blank / figure-only / references-only
# pages is tolerated; a large trailing un-ingested range is a truncated ingest.
MIN_INGEST_REACH_RATIO = 0.8

# EvidenceRegistry pages below this fraction of the document are flagged as
# *sparse* — an audit signal only, never a completeness pass/fail (issue #371).
EVIDENCE_SPARSE_RATIO = 0.5

# Coverage / terminal-section checks are only meaningful once enough content was
# ingested; a small stub legitimately cannot cover many pages or carry a
# dedicated conclusion.
MIN_CONTENT_BLOCKS_FOR_CHECKS = 8

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


def analyze_document_completeness(
    structure: Any,
    evidence: Any = None,
    *,
    document_id: str,
) -> dict:
    """Return a JSON-serialisable document-completeness report (issues #366 / #371).

    ``structure`` is a DocumentStructureResult dict; ``evidence`` is an optional
    EvidenceRegistryResult dict. The pass/fail signal for the document body is
    the *ingest reachability* of the DocumentStructure (did the parser reach the
    document end?), reported under ``ingest_coverage``. The EvidenceRegistry page
    distribution is reported separately under ``evidence_page_distribution`` as
    audit-only metadata and never sets ``complete=false`` (issue #371).
    ``complete`` is False — and ``review_reasons`` non-empty — when any check
    fails.
    """
    structure = structure if isinstance(structure, dict) else {}
    evidence = evidence if isinstance(evidence, dict) else None
    blocks = structure.get("blocks") or []
    sections = structure.get("sections") or []
    metadata = structure.get("metadata") or {}
    pages_total = metadata.get("pages") if isinstance(metadata, dict) else None
    parser_pages_processed = None
    if isinstance(metadata, dict):
        for key in ("parser_pages_processed", "pages_processed", "pages_parsed"):
            value = metadata.get(key)
            if isinstance(value, int) and value > 0:
                parser_pages_processed = value
                break

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

    missing_pairs: set[tuple[int, int]] = set(referenced_missing)
    for major, minors in defined.items():
        for minor in range(1, max(minors)):
            if minor not in minors:
                missing_pairs.add((major, minor))
    missing_labels = [_label_text(maj, mn) for maj, mn in sorted(missing_pairs)]
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
            (parser_pages_processed is not None and parser_pages_processed >= pages_total)
            or effective_last >= pages_total
            or effective_last / pages_total >= MIN_INGEST_REACH_RATIO
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

    review_reasons: list[str] = []
    if equation_has_gaps:
        review_reasons.append("equation_label_discontinuity")
    if terminal_missing:
        review_reasons.append("terminal_section_missing")
    if not ingest_sufficient:
        review_reasons.append("ingest_incomplete")

    return {
        "document_id": document_id,
        "complete": not review_reasons,
        "review_reasons": review_reasons,
        "equation_label_continuity": {
            "observed_labels": observed_labels,
            "missing_labels": missing_labels,
            "referenced_missing_labels": [
                _label_text(maj, mn) for maj, mn in sorted(referenced_missing)
            ],
            "has_gaps": equation_has_gaps,
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
    }
