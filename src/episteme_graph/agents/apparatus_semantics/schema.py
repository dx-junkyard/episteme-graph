"""ApparatusSemanticsAgent data models.

Design doc: docs/features/image_pipeline_knowledge_library_design.md §5.

``ApparatusSemanticsAgent`` reads a figure image + caption + nearby text +
optional library retrieval candidates and produces a candidate-only apparatus
identification (never a confirmed one — design principle #2: "画像の意味解釈は
candidate 止まり、確定は人間"). All dataclasses here are JSON-serializable
(``ApparatusSemanticsResult.to_dict()`` / ``to_json()`` / ``from_dict()``);
``FigureImageInput.image_bytes`` is the one intentional exception (raw bytes,
an *input* to the agent — never part of the exported result).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from episteme_graph.agents.cartridge_context import CartridgeContext

# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

MATCH_STATUSES = ("matched", "novel", "unknown")

# Shared vocabulary with TheoryOperationGraph (CLAUDE.md §TheoryOperationGraph).
# NOTE: "source_backed" is included only because it is the shared vocabulary's
# full set — ApparatusRecord must never actually be assigned this value (design
# principle #2/#4: vision-derived apparatus identification is candidate-only).
# validator.py enforces this as a hard error; see test_source_backed_never_assigned.
SOURCE_BACKING_STATUSES = (
    "source_backed",
    "partially_source_backed",
    "inferred",
    "review_required",
)

# review_status is always a "review_required" family value (design principle
# #2: LLM output is never auto-confirmed by a teacher). A single-value
# vocabulary today, kept as a tuple so validator.py / future extensions treat
# it as a controlled vocabulary rather than a magic string.
REVIEW_STATUS_DEFAULT = "review_required"
REVIEW_STATUSES = (REVIEW_STATUS_DEFAULT,)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass
class FigureImageInput:
    """Per-figure input assembled upstream (figure_image_extraction stage + FigureRecord).

    ``image_bytes`` is ``None`` when the image could not be extracted/rendered
    (or extraction failed) — the agent must still hold a reviewable record for
    the figure rather than dropping it (P4, "情報を落とさない").
    """
    figure_id: str
    figure_key: str
    figure_label: str | None
    caption_text: str
    image_bytes: bytes | None
    nearby_text: list[str] = field(default_factory=list)
    figure_record: dict | None = None


@dataclass
class LibraryCandidate:
    """A frozen library entry (§6-2 ``library_entry_versions``) retrieved as a
    few-shot candidate for one figure (§5-3). Retrieval happens upstream
    (pgvector cosine over caption/nearby text); this agent only consumes the
    result — 0 candidates is a normal, supported input (design principle #5).
    """
    entry_id: str
    version_no: int
    name: str
    aliases: list[str] = field(default_factory=list)
    summary: str = ""
    body: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

@dataclass
class ApparatusPart:
    name: str
    role: str
    evidence_quote: str = ""
    reason: str = ""
    confidence: float = 0.0


@dataclass
class ApparatusConnection:
    from_part: str
    to_part: str
    relation: str
    reason: str = ""
    confidence: float = 0.0


@dataclass
class ApparatusRecord:
    figure_id: str
    figure_key: str
    apparatus_name_candidate: str
    matched_library_entry_id: str | None
    matched_library_version_no: int | None
    match_status: str
    parts: list[ApparatusPart] = field(default_factory=list)
    connections: list[ApparatusConnection] = field(default_factory=list)
    evidence_quote: str = ""
    reason: str = ""
    confidence: float = 0.0
    # Deterministically derived (never taken verbatim from the LLM — design
    # principle #2/#4). See repair.py::_parse_record.
    source_backing_status: str = "inferred"
    review_status: str = REVIEW_STATUS_DEFAULT
    # True only when the bounded LLM validation-repair loop was exhausted
    # (repair.py, max 2 attempts) without producing a valid output. The figure
    # is still kept as a reviewable record (P4) — never dropped.
    repair_failed: bool = False


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class ApparatusSemanticsResult:
    document_id: str
    cartridge_id: str | None
    apparatus_records: list[ApparatusRecord] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "ApparatusSemanticsResult":
        records = [_record_from_dict(r) for r in d.get("apparatus_records", [])]
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            cartridge_id=d.get("cartridge_id"),
            apparatus_records=records,
            validation_issues=issues,
        )


# ---------------------------------------------------------------------------
# Private deserialization helpers
# ---------------------------------------------------------------------------

def _record_from_dict(d: dict) -> ApparatusRecord:
    parts = [
        ApparatusPart(
            name=p.get("name", ""),
            role=p.get("role", ""),
            evidence_quote=p.get("evidence_quote", ""),
            reason=p.get("reason", ""),
            confidence=float(p.get("confidence", 0.0) or 0.0),
        )
        for p in d.get("parts", [])
    ]
    connections = [
        ApparatusConnection(
            from_part=c.get("from_part", ""),
            to_part=c.get("to_part", ""),
            relation=c.get("relation", ""),
            reason=c.get("reason", ""),
            confidence=float(c.get("confidence", 0.0) or 0.0),
        )
        for c in d.get("connections", [])
    ]
    return ApparatusRecord(
        figure_id=d["figure_id"],
        figure_key=d.get("figure_key", d["figure_id"]),
        apparatus_name_candidate=d.get("apparatus_name_candidate", ""),
        matched_library_entry_id=d.get("matched_library_entry_id"),
        matched_library_version_no=d.get("matched_library_version_no"),
        match_status=d.get("match_status", "unknown"),
        parts=parts,
        connections=connections,
        evidence_quote=d.get("evidence_quote", ""),
        reason=d.get("reason", ""),
        confidence=float(d.get("confidence", 0.0) or 0.0),
        source_backing_status=d.get("source_backing_status", "inferred"),
        review_status=d.get("review_status", REVIEW_STATUS_DEFAULT),
        repair_failed=bool(d.get("repair_failed", False)),
    )
