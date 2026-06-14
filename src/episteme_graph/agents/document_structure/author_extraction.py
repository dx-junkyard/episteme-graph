"""Parser-driven, front-matter-limited author extraction (issue #372).

#366 added an *export-layer* filter that compared author-name tokens against
reference vs. non-reference blocks and deleted names it guessed were citation
authors. That heuristic both deleted legitimate authors (self-citations, bylines
the parser never isolated) and failed to remove contaminants whose surname also
appears in the body (e.g. "Horndeski gravity").

The robust fix is to extract authors only from *structured front-matter*
metadata at parse time and never from the references / bibliography:

  * TeX ``\\author{...}`` macros,
  * GROBID TEI ``<teiHeader>`` author metadata (handled in ``grobid_parser``),
  * the PDF front-matter byline (before Abstract / Introduction).

This module is dependency-free (no bs4 / LLM) so it can run inside the
parser-driven DocumentStructureAgent and be unit-tested without network access.
The GROBID TEI path lives in ``grobid_parser`` because it already owns the
BeautifulSoup tree; it records the same ``author_extraction`` provenance shape.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# An author list far larger than any plausible byline indicates reference /
# bibliography authors leaked into the document authors (issue #372). Shared with
# the export layer's deterministic audit.
MAX_PLAUSIBLE_AUTHORS = 25

# Per-source confidence for resolved author provenance.
_SOURCE_CONFIDENCE = {
    "tex_author": 0.95,
    "grobid_tei": 0.95,
    "pdf_front_matter": 0.6,
    "deterministic_fallback": 0.3,
    "none": 0.0,
    "unknown": 0.0,
}

# Headings that end the front matter — author candidates must come before these.
_FRONT_MATTER_STOP_RE = re.compile(
    r"\b(abstract|introduction|keywords?|references?|bibliography|"
    r"acknowledg\w*)\b|要旨|概要|序論|はじめに|参考文献|謝辞",
    re.IGNORECASE | re.UNICODE,
)

# Block types that never carry a byline (references / headings / equations).
_NON_BYLINE_BLOCK_TYPES = {
    "reference_entry",
    "section_heading",
    "subsection_heading",
    "equation_block",
    "figure_caption",
    "table_caption",
}

# A single name token: a Latin word (optionally accented / hyphenated /
# apostrophised) starting with an uppercase letter, or an initial ("A."), or a
# run of CJK characters.
_LATIN_TOKEN = r"(?:[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.\-]*|[A-ZÀ-ÖØ-Þ]\.?)"
_CJK = r"[぀-ヿ一-鿿]"
_NAME_RE = re.compile(
    rf"^(?:{_CJK}+(?:\s+{_CJK}+)*|{_LATIN_TOKEN}(?:[\s\-]{_LATIN_TOKEN})*)$"
)

# Separators between names in a byline / TeX author group.
_NAME_SEPARATOR_RE = re.compile(r"\s*(?:,|;|&|\band\b|\\and)\s*", re.IGNORECASE)


@dataclass
class AuthorExtractionResult:
    """Resolved author list plus provenance (issue #372).

    ``source`` records which structured signal produced the names so the export
    layer can surface it without re-guessing. ``needs_review`` /
    ``review_reasons`` are advisory — the export layer never deletes authors.
    """

    authors: list[str] = field(default_factory=list)
    source: str = "none"
    confidence: float = 0.0
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)
    candidate_sources: dict[str, list[str]] = field(default_factory=dict)

    def to_metadata(self) -> dict:
        return {
            "source": self.source,
            "confidence": self.confidence,
            "needs_review": self.needs_review,
            "review_reasons": list(self.review_reasons),
            "candidate_sources": {k: list(v) for k, v in self.candidate_sources.items()},
        }


# ---------------------------------------------------------------------------
# Name validation / splitting
# ---------------------------------------------------------------------------


def normalize_author_name(name: Any) -> str:
    """Collapse whitespace and trim trailing separators from a raw name."""
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    return text.strip(",;&").strip()


def looks_like_person_name(name: Any) -> bool:
    """True when ``name`` is plausibly a single human name (issue #372).

    Preserves Japanese names, accented Latin names, hyphenated and compound
    surnames; rejects sentences, numbers, URLs, "et al." and affiliation lines.
    """
    s = normalize_author_name(name)
    if not s or len(s) > 60:
        return False
    low = s.lower()
    if any(ch.isdigit() for ch in s) or "@" in s or "http" in low or "et al" in low:
        return False
    tokens = s.split()
    if len(tokens) > 5:
        return False
    return bool(_NAME_RE.match(s))


def split_author_list(text: Any) -> tuple[list[str], int]:
    """Split a byline / author group into individual names.

    Returns ``(names, parts_total)`` where ``names`` keeps only the parts that
    look like person names and ``parts_total`` is how many separator-delimited
    parts the string had (used to decide whether a block is "mostly names").
    """
    raw = normalize_author_name(text)
    if not raw:
        return [], 0
    parts = [p.strip() for p in _NAME_SEPARATOR_RE.split(raw) if p.strip()]
    if not parts:
        return [], 0
    names: list[str] = []
    seen: set[str] = set()
    for part in parts:
        name = normalize_author_name(part)
        if name and looks_like_person_name(name) and name not in seen:
            seen.add(name)
            names.append(name)
    return names, len(parts)


def _dedupe(names: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for n in names:
        norm = normalize_author_name(n)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


# ---------------------------------------------------------------------------
# TeX \author{...}
# ---------------------------------------------------------------------------

_TEX_AUTHOR_RE = re.compile(r"\\author\s*(?:\[[^\]]*\])?\s*\{", re.IGNORECASE)
# Inline LaTeX commands carrying affiliations / footnotes that must be dropped
# before reading the names.
_TEX_DROP_GROUP_CMDS = re.compile(
    r"\\(?:thanks|footnote|inst|affil|affiliation|address|email|orcid|"
    r"textsuperscript|fnref|corref|institute|samethanks)\s*(?:\[[^\]]*\])?\s*\{",
    re.IGNORECASE,
)


def _extract_brace_group(text: str, open_index: int) -> tuple[str, int]:
    """Return the balanced ``{...}`` content starting at ``open_index`` and the
    index just past the closing brace. ``text[open_index]`` must be ``{``."""
    depth = 0
    for i in range(open_index, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_index + 1:i], i + 1
    return text[open_index + 1:], len(text)


def _strip_tex_noise(chunk: str) -> str:
    """Remove affiliation/footnote groups and residual commands from a name."""
    # Drop \thanks{...} / \footnote{...} / affiliation groups (with their bodies).
    while True:
        m = _TEX_DROP_GROUP_CMDS.search(chunk)
        if not m:
            break
        _, end = _extract_brace_group(chunk, m.end() - 1)
        chunk = chunk[:m.start()] + " " + chunk[end:]
    # Drop math, superscripts and any remaining backslash commands / braces.
    chunk = re.sub(r"\$[^$]*\$", " ", chunk)
    chunk = re.sub(r"\^\{[^}]*\}|\^\S", " ", chunk)
    chunk = re.sub(r"\\[A-Za-z]+\s*", " ", chunk)
    chunk = chunk.replace("{", " ").replace("}", " ").replace("~", " ")
    return normalize_author_name(chunk)


def extract_authors_from_tex(tex_source: Any) -> AuthorExtractionResult:
    """Extract authors from TeX ``\\author{...}`` macros (issue #372).

    Splits multiple authors on ``\\and`` (and commas when no ``\\and`` is
    present), strips ``\\thanks`` / affiliation / superscript noise, and keeps
    only name-like results. Reference authors are never read because only the
    ``\\author`` front-matter macro is consulted.
    """
    text = str(tex_source or "")
    if not text:
        return AuthorExtractionResult()

    collected: list[str] = []
    for m in _TEX_AUTHOR_RE.finditer(text):
        body, _ = _extract_brace_group(text, m.end() - 1)
        # Authors are separated by \and; fall back to commas otherwise.
        if re.search(r"\\and\b", body):
            chunks = re.split(r"\\and\b", body)
        else:
            chunks = [body]
        for chunk in chunks:
            cleaned = _strip_tex_noise(chunk)
            if not cleaned:
                continue
            names, parts = split_author_list(cleaned)
            if names:
                collected.extend(names)
            elif looks_like_person_name(cleaned):
                collected.append(cleaned)

    authors = _dedupe(collected)
    if not authors:
        return AuthorExtractionResult()
    return AuthorExtractionResult(
        authors=authors,
        source="tex_author",
        confidence=_SOURCE_CONFIDENCE["tex_author"],
        needs_review=False,
        candidate_sources={"tex_author": authors},
    )


# ---------------------------------------------------------------------------
# PDF front-matter byline
# ---------------------------------------------------------------------------


def _block_get(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def extract_authors_from_front_matter(
    blocks: Any,
    *,
    title: str | None = None,
) -> AuthorExtractionResult:
    """Extract authors from the PDF front-matter byline (issue #372).

    Considers only blocks on the first page, in reading order, up to the first
    Abstract / Introduction / References heading, and never reference entries.
    Picks the first block that is "mostly names" as the byline. The document
    body and references are never consulted, so a body term such as
    "Horndeski gravity" cannot inject a citation author.
    """
    block_list = [b for b in (blocks or []) if b is not None]
    pages = [
        int(_block_get(b, "page"))
        for b in block_list
        if isinstance(_block_get(b, "page"), int)
    ]
    if not pages:
        return AuthorExtractionResult()
    first_page = min(pages)

    title_norm = normalize_author_name(title) if title else ""
    ordered = sorted(
        [b for b in block_list if _block_get(b, "page") == first_page],
        key=lambda b: _block_get(b, "order", 0) or 0,
    )

    # When no explicit title is supplied, treat the first eligible text block as
    # the (presumed) title and skip it, so a Title-Case heading like
    # "On Modified Gravity" is not mistaken for a byline.
    seen_title = bool(title_norm)

    for block in ordered:
        block_type = str(_block_get(block, "block_type", "") or "")
        text = str(_block_get(block, "text", "") or "").strip()
        if not text:
            continue
        # Stop at the first front-matter boundary heading.
        if _FRONT_MATTER_STOP_RE.search(text) and block_type in {
            "section_heading", "subsection_heading",
        }:
            break
        if block_type in _NON_BYLINE_BLOCK_TYPES:
            continue
        if title_norm and normalize_author_name(text) == title_norm:
            continue
        if len(text) > 200:
            continue
        if not seen_title:
            seen_title = True
            continue
        names, parts = split_author_list(text)
        # "Mostly names": every separated part is a person name (allowing one
        # stray affiliation token), and at least one name was found.
        if names and parts and len(names) / parts >= 0.7:
            return AuthorExtractionResult(
                authors=_dedupe(names),
                source="pdf_front_matter",
                confidence=_SOURCE_CONFIDENCE["pdf_front_matter"],
                needs_review=False,
                candidate_sources={"pdf_front_matter": _dedupe(names)},
            )
    return AuthorExtractionResult()


# ---------------------------------------------------------------------------
# Provenance resolution
# ---------------------------------------------------------------------------


def _author_key_set(names: list[str]) -> set[str]:
    return {normalize_author_name(n).lower() for n in names if normalize_author_name(n)}


def resolve_author_extraction(
    candidates: list[AuthorExtractionResult],
) -> AuthorExtractionResult:
    """Pick the highest-priority non-empty author source and record provenance.

    ``candidates`` is priority-ordered (e.g. TeX, then GROBID/TEI, then PDF
    front matter). The first non-empty source wins; every non-empty source is
    recorded under ``candidate_sources``. When two sources disagree
    substantially the result is flagged ``needs_review`` with
    ``author_source_mismatch`` — but the chosen authors are never deleted.
    """
    candidate_sources: dict[str, list[str]] = {}
    for cand in candidates:
        if cand and cand.authors:
            candidate_sources.setdefault(cand.source, _dedupe(cand.authors))

    chosen: AuthorExtractionResult | None = None
    for cand in candidates:
        if cand and cand.authors:
            chosen = cand
            break

    if chosen is None:
        return AuthorExtractionResult(
            source="none",
            confidence=0.0,
            needs_review=False,
            candidate_sources=candidate_sources,
        )

    review_reasons = list(chosen.review_reasons)
    needs_review = chosen.needs_review

    # Detect a large disagreement between independent structured sources.
    key_sets = [set(_author_key_set(v)) for v in candidate_sources.values()]
    if len(key_sets) >= 2:
        union = set().union(*key_sets)
        intersection = set(key_sets[0]).intersection(*key_sets[1:])
        if union and len(intersection) / len(union) < 0.5:
            needs_review = True
            if "author_source_mismatch" not in review_reasons:
                review_reasons.append("author_source_mismatch")

    return AuthorExtractionResult(
        authors=_dedupe(chosen.authors),
        source=chosen.source,
        confidence=_SOURCE_CONFIDENCE.get(chosen.source, chosen.confidence),
        needs_review=needs_review,
        review_reasons=review_reasons,
        candidate_sources=candidate_sources,
    )
