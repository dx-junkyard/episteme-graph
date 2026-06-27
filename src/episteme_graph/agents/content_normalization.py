"""Deterministic content normalization + hashing (issue #362).

claim_id / equation_id are document-scoped, so the same definition or
standard assumption in two papers can never be matched automatically. As the
first step toward cross-paper reuse, every claim / equation gets a
``content_hash`` computed from a deterministically normalised form of its
text. Hashing is versioned: when the normalisation rules change,
``CONTENT_HASH_VERSION`` is bumped so stale hashes are recognisable and can
be recomputed instead of silently mismatching.

This module only normalises and hashes — similarity search / merge proposals
are a separate, teacher-reviewed concern.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

# Bump when any normalisation rule below changes.
# v1: full-text lowercasing. v2: prose-token casefolding only — math-like
# tokens (R_D, DHOST, single capitals) keep their case (#362 review fix), so
# v1 hashes are not comparable and must be recomputed.
CONTENT_HASH_VERSION = 2

_TRAILING_PUNCTUATION = ".,;:。、"

# A token is lowercased only when it looks like prose: an alphabetic word of
# length >= 2 whose only capital (if any) is the first letter, optionally with
# trailing punctuation. Math-like tokens (R_D, DHOST, \beta, b_1, single
# capitals) keep their case — b and B are different symbols (issue #362).
_PROSE_TOKEN = re.compile(r"([A-Za-z][a-z]+)([.,;:!?)\]\"']*)$")


def _casefold_prose_tokens(value: str) -> str:
    tokens = []
    for token in value.split(" "):
        match = _PROSE_TOKEN.fullmatch(token)
        tokens.append(match.group(1).lower() + match.group(2) if match else token)
    return " ".join(tokens)


def normalize_text_for_hash(text: str) -> str:
    """Canonical claim-text form: NFKC, collapsed whitespace, no trailing
    punctuation, prose words case-folded while math-like tokens keep case."""
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = re.sub(r"\s+", " ", value).strip()
    value = value.rstrip(_TRAILING_PUNCTUATION).strip()
    return _casefold_prose_tokens(value)


def normalize_equation_for_hash(latex: str | None, plain_text: str | None = None) -> str:
    """Canonical equation form: prefer LaTeX, strip math-mode wrappers and all
    whitespace (LaTeX ignores it), normalise brace spacing. Case is preserved —
    b and B are different symbols."""
    source = str(latex or "").strip() or str(plain_text or "").strip()
    value = unicodedata.normalize("NFKC", source)
    value = value.strip()
    for wrapper in ("$$", "$", r"\[", r"\]", r"\(", r"\)"):
        value = value.replace(wrapper, "")
    value = re.sub(r"\s+", "", value)
    return value


def content_hash(normalized: str) -> str:
    """Stable 16-hex-digit digest of a normalised content string ('' stays '')."""
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def claim_content_hash(text: str) -> str:
    return content_hash(normalize_text_for_hash(text))


def equation_content_hash(latex: str | None, plain_text: str | None = None) -> str:
    return content_hash(normalize_equation_for_hash(latex, plain_text))
