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
CONTENT_HASH_VERSION = 1

_TRAILING_PUNCTUATION = ".,;:。、"


def normalize_text_for_hash(text: str) -> str:
    """Canonical claim-text form: NFKC, collapsed whitespace, no trailing
    punctuation, case-folded prose (math symbols keep their case via NFKC)."""
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = re.sub(r"\s+", " ", value).strip()
    value = value.rstrip(_TRAILING_PUNCTUATION).strip()
    return value.lower()


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
