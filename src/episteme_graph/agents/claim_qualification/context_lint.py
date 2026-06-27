"""Deterministic context-dependency lint for atomic claims (issue #357).

The atomic-rewrite prompt instructs the LLM to resolve pronouns and
"this result"-style anaphora, but nothing verified the model actually did.
This lint enforces it deterministically:

- an *accepted atomic* claim whose text still leads with an unresolved
  anaphoric subject is demoted to ``status="review_required"`` with
  ``unresolved_reference`` recorded in its ``qualification_reason``
  (information is kept, never dropped);
- intra-document references ("Section 3", "Fig. 2", "Eq. (2.7)") are NOT
  treated as unresolved — they are extracted into the structured
  ``context_refs`` list (equation labels normalised to ``eq_*`` ids) so the
  claim stays reusable with its context made explicit.

Self-references to the paper itself ("This paper", "This work", …) are not
flagged: they are reviewable paper-level subjects, and paper-level claims are
already handled by the atomicity machinery.
"""
from __future__ import annotations

import re

UNRESOLVED_REFERENCE_MARKER = "unresolved_reference"

# Nouns that, after a leading demonstrative, still point back at unstated
# context ("This result shows…"). Domain-neutral discourse nouns only.
_ANAPHORIC_NOUNS = (
    "result|results|finding|findings|observation|observations|value|values|"
    "quantity|quantities|expression|expressions|relation|relations|term|terms|"
    "effect|effects|case|cases|procedure|behavior|behaviour|approach|approaches|"
    "argument|claim|statement|fact|property|properties|feature|features|"
    "consequence|consequences|condition|conditions|situation|scenario"
)

# Verbs that expose a bare demonstrative subject ("This shows…", "These are…").
_LEADING_VERBS = (
    "is|are|was|were|shows|show|showed|implies|imply|means|mean|leads|lead|"
    "gives|give|yields|yield|holds|hold|suggests|suggest|demonstrates|"
    "demonstrate|indicates|indicate|confirms|confirm|corresponds|correspond|"
    "provides|provide|results|result|follows|follow|allows|allow|requires|"
    "require|becomes|become|reduces|reduce|depends|depend|can|cannot|may|"
    "might|will|would|should|must|does|do|did|has|have|had"
)

# Paper self-reference subjects that are allowed (not anaphora to be resolved).
_ALLOWED_SELF_REFERENCE = (
    "paper|work|study|article|letter|manuscript|analysis|section"
)

_UNRESOLVED_LEAD_PATTERNS = [
    # Bare demonstrative subject: "This shows…", "Those were…"
    re.compile(
        r"^(this|that|these|those)\s+(?:also\s+)?(?:" + _LEADING_VERBS + r")\b",
        re.IGNORECASE,
    ),
    # Demonstrative + generic discourse noun: "This result…", "These findings…"
    re.compile(
        r"^(this|that|these|those)\s+(?:" + _ANAPHORIC_NOUNS + r")\b",
        re.IGNORECASE,
    ),
    # Bare pronoun subject: "It implies…", "They are…"
    re.compile(r"^(it|they)\s+(?:also\s+)?(?:" + _LEADING_VERBS + r")\b", re.IGNORECASE),
    # "The former/latter/above/aforementioned …"
    re.compile(r"^the\s+(former|latter|above|aforementioned)\b", re.IGNORECASE),
    # "As shown/mentioned/discussed above/earlier/before, …"
    re.compile(r"^as\s+(shown|mentioned|discussed|described|noted)\s+(above|earlier|before)\b", re.IGNORECASE),
    # Japanese leading anaphora: この/その/これら/それら/上記/前述/同様の
    re.compile(r"^(この|その|これら|それら|上記の?|前述の?|同様の)"),
]

_SELF_REFERENCE_PATTERN = re.compile(
    r"^(this|the present)\s+(?:" + _ALLOWED_SELF_REFERENCE + r")\b", re.IGNORECASE
)

# Intra-document references extracted into context_refs (not flagged).
_CONTEXT_REF_PATTERNS = [
    ("equation", re.compile(
        r"\b(?:Eq|Eqs|Equation|Equations)\.?\s*\(?([A-Za-z0-9][A-Za-z0-9.\-]*)\)?",
    )),
    ("section", re.compile(r"\b(Section|Sec\.|Appendix)\s+([A-Za-z0-9][A-Za-z0-9.\-]*)")),
    ("figure", re.compile(r"\b(Figure|Fig\.)\s*([A-Za-z0-9][A-Za-z0-9.\-]*)")),
    ("table", re.compile(r"\b(Table|Tab\.)\s*([A-Za-z0-9][A-Za-z0-9.\-]*)")),
    ("reference", re.compile(r"\bRefs?\.\s*\[([0-9,\s\-]+)\]")),
]


def find_unresolved_reference(text: str) -> str | None:
    """Return the unresolved leading fragment, or None when self-contained."""
    candidate = str(text or "").strip()
    if not candidate:
        return None
    if _SELF_REFERENCE_PATTERN.match(candidate):
        return None
    for pattern in _UNRESOLVED_LEAD_PATTERNS:
        match = pattern.match(candidate)
        if match:
            return match.group(0)
    return None


def extract_context_refs(text: str) -> list[str]:
    """Structured intra-document references mentioned by the claim text.

    Equation labels are normalised to registry-style ids ("Eq. (2.7)" →
    "eq_2_7"); other refs keep a readable "<Kind> <label>" form.
    """
    refs: list[str] = []
    candidate = str(text or "")
    for kind, pattern in _CONTEXT_REF_PATTERNS:
        for match in pattern.finditer(candidate):
            if kind == "equation":
                label = match.group(1).rstrip(".-")
                normalized = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")
                ref = f"eq_{normalized}" if normalized else ""
            elif kind == "reference":
                ref = f"Ref. [{match.group(1).strip()}]"
            else:
                ref = f"{match.group(1).rstrip('.')} {match.group(2).rstrip('.-')}"
            if ref and ref not in refs:
                refs.append(ref)
    return refs


def apply_context_lint(atomic_claims: list[dict]) -> list[str]:
    """Lint atomic claim entries in place (issue #357).

    Fills ``context_refs`` on every entry and demotes accepted atomic claims
    with an unresolved leading reference. Returns the unresolved fragments so
    the agent can run a single targeted repair retry.
    """
    fragments: list[str] = []
    for claim in atomic_claims or []:
        if not isinstance(claim, dict):
            continue
        text = str(claim.get("normalized_text") or claim.get("text") or "")
        claim["context_refs"] = extract_context_refs(text)
        if claim.get("atomicity") != "atomic" or claim.get("status") != "accepted":
            continue
        fragment = find_unresolved_reference(text)
        if not fragment:
            continue
        claim["status"] = "review_required"
        reason = str(claim.get("qualification_reason") or "")
        note = f"{UNRESOLVED_REFERENCE_MARKER}: leading {fragment!r} is not resolved"
        claim["qualification_reason"] = f"{reason}; {note}" if reason else note
        fragments.append(fragment)
    return fragments


def unresolved_fragments(atomic_claims: list[dict]) -> list[str]:
    """Fragments of claims demoted by this lint (marker-based, idempotent)."""
    fragments: list[str] = []
    for claim in atomic_claims or []:
        if not isinstance(claim, dict):
            continue
        if claim.get("status") == "review_required" and UNRESOLVED_REFERENCE_MARKER in str(
            claim.get("qualification_reason") or ""
        ):
            fragments.append(str(claim.get("text") or "")[:80])
    return fragments
