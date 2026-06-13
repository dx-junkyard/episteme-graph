"""Deterministic document-completeness analysis (issue #366).

A truncated PDF ingest — where the document's central results never reach the
pipeline — is detectable without ground truth. This module performs that check
with no LLM so both the pipeline ExportValidationGate and the export route can
reuse it:

  * equation label continuity — internal gaps in the per-section (N.1)…(N.k)
    sequence, *and* labels referenced in prose (e.g. "Eq. (3.40)") that were
    never ingested as equations (catches a tail truncation at (3.36)),
  * terminal section presence — a Conclusion / Summary-like tail section,
  * page coverage — ingested content (evidence when available, else content
    blocks) must not cluster on a small fraction of the document's pages, and
    the un-ingested page ranges are reported.

`core` must not import FastAPI / route modules; this module stays dependency-free
so the orchestrator can call it at the DocumentStructure / EvidenceRegistry exit.
"""
from __future__ import annotations

import re
from typing import Any

# Ingested content clustered on a small fraction of the document's pages signals
# a truncated ingest (issue #366: evidence only on pages 1 and 3 of 20).
MIN_PAGE_COVERAGE_RATIO = 0.5

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
    """Return a JSON-serialisable document-completeness report (issue #366).

    ``structure`` is a DocumentStructureResult dict; ``evidence`` is an optional
    EvidenceRegistryResult dict used to judge page coverage from actually
    ingested evidence (falling back to content blocks). ``complete`` is False —
    and ``review_reasons`` non-empty — when any check fails.
    """
    structure = structure if isinstance(structure, dict) else {}
    evidence = evidence if isinstance(evidence, dict) else None
    blocks = structure.get("blocks") or []
    sections = structure.get("sections") or []
    metadata = structure.get("metadata") or {}
    pages_total = metadata.get("pages") if isinstance(metadata, dict) else None

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

    # --- page coverage ------------------------------------------------------
    source = "evidence" if evidence is not None else "content_blocks"
    ev_pages = _evidence_pages(evidence)
    if evidence is not None:
        ingested = ev_pages
    else:
        ingested = {
            int(b["page"]) for b in content_blocks if isinstance(b.get("page"), int)
        }
    ingested_pages = sorted(ingested)
    distinct_page_count = len(ingested_pages)
    coverage_ratio = None
    missing_pages: list[list[int]] = []
    coverage_sufficient = True
    if isinstance(pages_total, int) and pages_total > 0:
        coverage_ratio = round(distinct_page_count / pages_total, 4)
        missing_pages = _compress_ranges(
            [p for p in range(1, pages_total + 1) if p not in ingested]
        )
        # Coverage is only judged once enough content was ingested.
        if enough_content or (evidence is not None and distinct_page_count):
            coverage_sufficient = coverage_ratio >= MIN_PAGE_COVERAGE_RATIO

    review_reasons: list[str] = []
    if equation_has_gaps:
        review_reasons.append("equation_label_discontinuity")
    if terminal_missing:
        review_reasons.append("terminal_section_missing")
    if not coverage_sufficient:
        review_reasons.append("page_coverage_insufficient")

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
        "page_coverage": {
            "source": source,
            "ingested_pages": ingested_pages,
            "missing_pages": missing_pages,
            "distinct_page_count": distinct_page_count,
            "pages_total": pages_total,
            "coverage_ratio": coverage_ratio,
            "sufficient": coverage_sufficient,
        },
    }
