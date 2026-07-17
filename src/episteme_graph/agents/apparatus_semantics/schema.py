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
    # In-figure text spans extracted deterministically from the PDF text layer
    # (figure_image_extraction stage, page coordinate system):
    # [{"text": str, "bbox": [x0, y0, x1, y1]}, ...].
    inner_labels: list[dict] = field(default_factory=list)
    # Abbreviation → expansion dictionary mined from the paper body
    # (figure_context stage), e.g. {"ECDL": "external cavity diode laser"}.
    abbreviations: dict[str, str] = field(default_factory=dict)
    # --- Guided re-analysis (docs/features/guided_figure_reanalysis_design.md).
    # Populated only for a teacher-directed deliberation re-analyze call; the
    # batch orchestrator pipeline never sets these (GF7 — all default to a
    # falsy value so existing callers/artifacts are unaffected).
    guidance_text: str = ""  # normalized teacher hint (<=2000 chars)
    focus_bbox_rel: list | None = None  # [x0, y0, x1, y1] image-relative 0..1
    focus_image_bytes: bytes | None = None  # magnified crop, produced upstream by core
    focus_label_texts: list[str] = field(default_factory=list)  # inner_labels inside focus_bbox_rel


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
    # Reference to one of ``FigureImageInput.inner_labels[].text`` (LLM output,
    # verbatim). validator.py checks this actually exists among inner_labels.
    label_ref: str | None = None
    # Abbreviation expansion of label_ref/name. Deterministically derived from
    # ``FigureImageInput.abbreviations`` by agent.py::_attach_label_grounding —
    # never taken from the LLM (design principle #2/#4).
    expanded_name: str = ""
    # [x0, y0, x1, y1] in the same page coordinate system as
    # ``document_figures.bbox``. Deterministically derived by matching
    # label_ref against inner_labels — never taken verbatim from the LLM.
    bbox: list | None = None
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
    # Issue #496: generic presentation classification produced by the same
    # vision call.  It remains candidate-only until a teacher writes
    # document_figures.reviewed_mode.
    suggested_mode: str = "unknown"
    mode_reason: str = ""
    analysis_profile: dict = field(default_factory=dict)
    # Teacher-guidance response (GF3): how the LLM applied guidance_text /
    # focus_bbox_rel, or an explicit "could not find it" statement when the
    # requested element was not found. Always empty string when the run had
    # no guidance input — never fabricated for an unguided run.
    guidance_note: str = ""


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
            # New fields default when absent so older exported artifacts
            # (produced before this extension) still round-trip (P4).
            label_ref=(p.get("label_ref") or None),
            expanded_name=p.get("expanded_name", "") or "",
            bbox=list(p["bbox"]) if p.get("bbox") else None,
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
        suggested_mode=d.get("suggested_mode", d.get("figure_mode_candidate", "unknown")),
        mode_reason=d.get("mode_reason", ""),
        analysis_profile=(
            dict(d.get("analysis_profile") or d.get("mode_analysis") or {})
            if isinstance(d.get("analysis_profile") or d.get("mode_analysis") or {}, dict)
            else {}
        ),
        # Absent in artifacts exported before this extension — defaults to ""
        # so old artifacts still round-trip (P4).
        guidance_note=d.get("guidance_note", ""),
    )
