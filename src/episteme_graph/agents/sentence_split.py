"""Deterministic, abbreviation-aware sentence splitting — the single canonical
implementation for the agent pipeline (IK-0393).

Before this module the pipeline had three splitters: the evidence registry's
offset splitter (abbreviation-aware) and two naive regexes in
``claim_object_builder`` (``[.!?]+\\s+`` / ``(?<=[.!?])\\s+``). The naive ones cut
``The ACF analysis (Houde et al. 2009), extends ...`` into
``The ACF analysis (Houde et al.`` + ``2009), extends ...`` and flagged the claim
as multi-sentence.

Rules (general scholarly notation only — no field-specific vocabulary):

* a terminator must be followed by whitespace or the end of text
  (``3.14`` / ``v2.0`` / ``Fig.5`` are not boundaries);
* a period after a common abbreviation is not a boundary
  (:data:`NON_TERMINAL_ABBREVIATIONS`: ``et al.``, ``Fig.``, ``Eq.``, ``e.g.``, ...);
* a period after a bare number inside a still-open parenthesis is not a
  boundary (``(Davis 1951. ...`` / ``(see ref. 12. ...``);
* a terminator inside inline / display math (``$…$`` / ``$$…$$`` / ``\\(…\\)`` /
  ``\\[…\\]``) is not a boundary — a math segment is one token
  (2026-10-16, S2 seq 2750: ``$\\hat{N}_{ijk...} = …$`` was cut at ``ijk.``).

Pure functions, stdlib only (importable from ``backend`` and ``src`` alike).
"""

from __future__ import annotations

import re

__all__ = [
    "NON_TERMINAL_ABBREVIATIONS",
    "math_regions",
    "position_in_math",
    "split_sentence_spans",
    "split_sentences",
]

#: Abbreviations whose trailing period does not end a sentence. Compared against
#: the lower-cased last token with surrounding brackets/quotes and trailing
#: periods removed (``(e.g.`` → ``e.g``, ``Figs.`` → ``figs``).
NON_TERMINAL_ABBREVIATIONS = frozenset(
    {
        "eq", "eqs", "fig", "figs", "tab", "tabs", "ref", "refs", "sec", "secs",
        "e.g", "i.e", "eg", "ie", "cf", "vs", "et al", "al", "no", "nos",
        "dr", "prof", "etc", "jr", "sr", "approx", "resp", "ch", "chap", "vol",
        "pp", "app", "appx",
    }
)

DEFAULT_TERMINATORS = ".!?。！？"

_LEADING_PUNCT = "([{\"'“‘«"
_NUMBER_TOKEN_RE = re.compile(r"^[-+]?\d+(?:[.,]\d+)*[a-z]?$")


_MATH_OPEN_CLOSE = (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$"))


def math_regions(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of math segments in ``text`` (delimiters included).

    Recognizes ``$$…$$``, ``\\[…\\]``, ``\\(…\\)`` and ``$…$``; an escaped
    ``\\$`` is not a delimiter. An unclosed opener is ignored (no region), so a
    stray ``$`` never swallows the rest of the text.
    """
    text = str(text or "")
    regions: list[tuple[int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        matched = False
        for opener, closer in _MATH_OPEN_CLOSE:
            if not text.startswith(opener, i):
                continue
            if opener.startswith("$") and i > 0 and text[i - 1] == "\\":
                break
            j = i + len(opener)
            while True:
                k = text.find(closer, j)
                if k < 0:
                    break
                if closer.startswith("$") and text[k - 1] == "\\":
                    j = k + 1
                    continue
                break
            if k < 0:
                break
            regions.append((i, k + len(closer)))
            i = k + len(closer)
            matched = True
            break
        if not matched:
            i += 1
    return regions


def position_in_math(regions: list[tuple[int, int]], pos: int) -> tuple[int, int] | None:
    """The math region strictly containing ``pos`` (``start < pos < end``), or None."""
    for start, end in regions:
        if start < pos < end:
            return start, end
    return None


def _last_token(prefix: str) -> str:
    parts = prefix.split()
    if not parts:
        return ""
    return parts[-1].lstrip(_LEADING_PUNCT).lower().rstrip(".")


def _inside_open_paren(segment: str) -> bool:
    return segment.count("(") > segment.count(")") or segment.count("[") > segment.count("]")


def split_sentence_spans(
    text: str, *, terminators: str = DEFAULT_TERMINATORS
) -> list[tuple[int, int]]:
    """Return ``(start, end)`` character offsets of the sentences in ``text``.

    Offsets index into the original ``text`` (leading whitespace of each
    sentence is skipped; the terminator is included).
    """
    text = str(text or "")
    boundary = re.compile("[" + re.escape(terminators) + "]")
    regions = math_regions(text)
    spans: list[tuple[int, int]] = []
    start = 0
    for match in boundary.finditer(text):
        end = match.end()
        if position_in_math(regions, match.start()) is not None:
            continue  # a terminator inside $…$ / \\(…\\) / \\[…\\] is not a boundary
        if end < len(text) and not text[end].isspace():
            continue  # mid-token period like "3.14" or "v2.0"
        if match.group() == ".":
            prefix = text[start:match.start()].rstrip()
            token = _last_token(prefix)
            if token in NON_TERMINAL_ABBREVIATIONS:
                continue
            if _NUMBER_TOKEN_RE.match(token) and _inside_open_paren(prefix):
                continue  # "(Davis 1951. ..." — a number inside an unclosed parenthesis
        stripped_start = start
        while stripped_start < end and text[stripped_start].isspace():
            stripped_start += 1
        if end > stripped_start and text[stripped_start:end].strip():
            spans.append((stripped_start, end))
        start = end
    rest = text[start:].strip()
    if rest:
        stripped_start = start
        while stripped_start < len(text) and text[stripped_start].isspace():
            stripped_start += 1
        spans.append((stripped_start, len(text)))
    return spans


def split_sentences(text: str, *, terminators: str = DEFAULT_TERMINATORS) -> list[str]:
    """Sentence texts of ``text`` (stripped), in order."""
    text = str(text or "")
    return [
        text[start:end].strip()
        for start, end in split_sentence_spans(text, terminators=terminators)
        if text[start:end].strip()
    ]
