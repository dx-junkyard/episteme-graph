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
# Iterative contextual-analysis pipeline vocabularies
# (docs/features/contextual_figure_analysis_iterative_verification.md)
#
# Wave 1 scaffolding only: the state machine that drives context hypothesis ->
# independent visual observation -> alignment -> gap-driven re-verification ->
# convergence lives in a later wave (iterative.py, agent.py). This module only
# defines the data shapes and controlled vocabularies they will use.
# ---------------------------------------------------------------------------

# AlignmentItem.status: how a text-expected element/relation lines up against
# the independent visual observation of the same figure.
ALIGNMENT_STATUSES = (
    "supported_by_both", "visual_only", "text_only", "contradicted", "unresolved",
)

# IterativeAnalysisRecord.convergence_status: terminal state of the
# gap-driven re-verification loop for one figure.
CONVERGENCE_STATUSES = (
    "converged", "max_iterations_reached", "no_progress", "aborted_error",
    "aborted_cost_limit", "not_run",
)

# AlternativeHypothesis.status.
HYPOTHESIS_STATUSES = ("active", "rejected", "selected")

# VerificationTask.status.
VERIFICATION_TASK_STATUSES = ("open", "resolved", "refuted", "unresolved")

# AlignmentItem.severity (only meaningful for contradicted/unresolved items).
ALIGNMENT_SEVERITIES = ("high", "medium", "low")


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


@dataclass
class IterativeConfig:
    """Engine-facing configuration for the iterative pipeline (Wave 1 input
    only — the engine that reads this lives in a later wave's ``iterative.py``).
    """
    enabled: bool = False
    max_iterations: int = 3  # bound on gap-driven re-verification rounds
    vision_call_budget: int | None = None  # None = unlimited; engine manages spend
    model_name: str = ""  # audit-record hint; actual model resolution is llm_client's


# ---------------------------------------------------------------------------
# Iterative analysis records
# (docs/features/contextual_figure_analysis_iterative_verification.md)
#
# These dataclasses hold the intermediate/terminal artifacts of the
# hypothesis -> observation -> alignment -> verification loop. They are
# produced by repair.py's ``parse_*`` functions from raw LLM JSON and
# validated by validator.py's ``validate_*`` methods (Wave 1). The state
# machine that actually drives the loop is out of scope for this wave.
# ---------------------------------------------------------------------------

@dataclass
class ExpectedElement:
    """One element a reader would expect to see in the figure, inferred from
    text alone (before the image is shown to the model)."""
    element_id: str  # "exp_1" style, LLM-assigned
    name: str
    evidence_quote: str = ""  # verbatim quote from caption/nearby text
    expected_labels: list = field(default_factory=list)  # label strings expected in-figure
    importance: str = "primary"  # primary | secondary
    confidence: float = 0.0


@dataclass
class ExpectedRelation:
    """An expected relation/connection between two ``ExpectedElement``s,
    inferred from text alone."""
    relation_id: str  # "rel_1" style, LLM-assigned
    from_element_id: str
    to_element_id: str
    relation: str  # verb phrase
    direction: str = ""  # "from->to" | "bidirectional" | "unknown"
    evidence_quote: str = ""
    confidence: float = 0.0


@dataclass
class ContextHypothesis:
    """Text-only expectation model for one figure (step 1 of the pipeline —
    the model never sees the image while producing this)."""
    role_in_paper: str = ""
    overall_subject: str = ""
    expected_elements: list = field(default_factory=list)  # list[ExpectedElement]
    expected_relations: list = field(default_factory=list)  # list[ExpectedRelation]
    expected_visual_cues: list = field(default_factory=list)  # list[str]
    unstated_points: list = field(default_factory=list)  # list[str]
    falsification_conditions: list = field(default_factory=list)  # list[str]
    evidence_quotes: list = field(default_factory=list)  # list[str]
    reason: str = ""
    confidence: float = 0.0


@dataclass
class ObservedElement:
    """One thing directly visible in the image (step 2 of the pipeline — no
    caption/nearby text/candidates are shown to the model for this step)."""
    observation_id: str  # "obs_1" style, LLM-assigned
    kind: str = "other"  # box|arrow|line|axis|legend|label|panel|region|symbol|other
    description: str = ""  # visual description only, never a meaning interpretation
    label_text: str = ""  # text actually read off the image, or "" if none
    region_hint: str = ""  # textual position hint (e.g. "upper-left"); never a coordinate


@dataclass
class ObservedConnection:
    """A visually observed connection between two ``ObservedElement``s."""
    from_observation_id: str
    to_observation_id: str
    connector: str = ""  # arrow | line | adjacency | other
    direction: str = ""  # "from->to" | "bidirectional" | "unknown"
    description: str = ""


@dataclass
class VisualObservationSet:
    """Independent visual observation of one figure (step 2 output)."""
    panels: list = field(default_factory=list)  # list[dict] {panel_id, description, region_hint}
    elements: list = field(default_factory=list)  # list[ObservedElement]
    connections: list = field(default_factory=list)  # list[ObservedConnection]
    ocr_labels: list = field(default_factory=list)  # list[str]
    repeated_motifs: list = field(default_factory=list)  # list[dict] {description, observation_ids}
    unreadable_regions: list = field(default_factory=list)  # list[dict] {region_hint, reason}
    undecidable_elements: list = field(default_factory=list)  # list[dict] {observation_id, note}
    visual_mode_guess: str = "unknown"  # FIGURE_MODES vocabulary, from vision alone
    reason: str = ""
    confidence: float = 0.0


@dataclass
class AlignmentItem:
    """Reconciliation of one expected element/relation against the visual
    observations (step 3 output). ``status`` is the ALIGNMENT_STATUSES
    vocabulary above."""
    item_id: str  # "align_1" style, LLM-assigned
    item_kind: str  # "element" | "relation"
    label: str  # short human-readable name
    status: str  # ALIGNMENT_STATUSES
    expected_ref: str = ""  # a ContextHypothesis element_id/relation_id, or ""
    observation_refs: list = field(default_factory=list)  # list[str] VisualObservationSet observation_id(s)
    label_ref: str = ""  # verbatim figure.inner_labels text, or ""
    text_evidence: str = ""  # verbatim quote from caption/nearby text
    visual_evidence: str = ""  # description drawn from the visual observations
    severity: str = "medium"  # ALIGNMENT_SEVERITIES
    reason: str = ""
    confidence: float = 0.0


@dataclass
class AlternativeHypothesis:
    """One of 2-3 competing overall readings kept open when the alignment is
    genuinely ambiguous (step 4 of the pipeline)."""
    hypothesis_id: str  # "hyp_1" style, LLM-assigned
    description: str
    supporting_evidence: list = field(default_factory=list)  # list[str]
    counter_evidence: list = field(default_factory=list)  # list[str]
    unverified_conditions: list = field(default_factory=list)  # list[str]
    status: str = "active"  # HYPOTHESIS_STATUSES
    confidence: float = 0.0


@dataclass
class VerificationTask:
    """A gap-driven re-verification task generated from a text_only/
    contradicted/unresolved alignment item (step 5 of the pipeline)."""
    task_id: str  # "task_1" style, LLM-assigned
    question: str
    target_item_ids: list = field(default_factory=list)  # list[str] AlignmentItem.item_id
    region_hint: str = ""
    focus_bbox_rel: list | None = None  # image-relative [x0, y0, x1, y1] 0..1, or None
    success_condition: str = ""
    refutation_condition: str = ""
    status: str = "open"  # VERIFICATION_TASK_STATUSES


@dataclass
class VerificationIterationRecord:
    """Audit record of one gap-driven re-verification iteration."""
    iteration_index: int
    executed_task_ids: list = field(default_factory=list)  # list[str]
    findings: list = field(default_factory=list)  # list[dict] {task_id, outcome, observation, evidence}
    changes: list = field(default_factory=list)  # list[str] human-readable diff descriptions
    vision_call_used: bool = True
    notes: str = ""


@dataclass
class IterativeAnalysisRecord:
    """Full iterative-pipeline artifact for one figure (Wave 1 scaffolding).

    Populated by a later wave's state machine; ``enabled=False``/``None``
    (see ``ApparatusRecord.iterative_analysis``) means the one-shot path was
    used instead, which remains the default until that wave lands.
    """
    enabled: bool = True
    context_hypothesis: ContextHypothesis | None = None
    visual_observations: VisualObservationSet | None = None
    alignment_items: list = field(default_factory=list)  # list[AlignmentItem]
    alternative_hypotheses: list = field(default_factory=list)  # list[AlternativeHypothesis]
    verification_iterations: list = field(default_factory=list)  # list[VerificationIterationRecord]
    open_verification_tasks: list = field(default_factory=list)  # list[VerificationTask], unexecuted
    unresolved_conflicts: list = field(default_factory=list)  # list[dict]
    review_questions: list = field(default_factory=list)  # list[dict]
    convergence_status: str = "not_run"  # CONVERGENCE_STATUSES
    stage_failures: list = field(default_factory=list)  # list[str] "stage: reason"
    llm_calls: int = 0
    vision_calls: int = 0
    model: str = ""


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
    # Iterative contextual-analysis pipeline (Wave 1 scaffolding — the state
    # machine that populates this lives in a later wave's iterative.py). None
    # for every record produced by the current one-shot path, and for
    # artifacts exported before this extension (P4 — old artifacts still
    # round-trip via _iterative_analysis_record_from_dict returning None for
    # a missing/non-dict value).
    iterative_analysis: IterativeAnalysisRecord | None = None


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
        iterative_analysis=_iterative_analysis_record_from_dict(d.get("iterative_analysis")),
    )


# ---------------------------------------------------------------------------
# Iterative analysis deserialization helpers
#
# All permissive: unknown keys are ignored, missing keys fall back to the
# dataclass defaults. These reconstruct the nested dataclasses that
# ``dataclasses.asdict`` (used by ApparatusSemanticsResult.to_dict) flattens
# into plain dicts/lists — needed for ApparatusRecord.iterative_analysis to
# round-trip through to_json()/from_dict() (P4).
# ---------------------------------------------------------------------------

def _expected_element_from_dict(d: dict) -> ExpectedElement:
    d = d if isinstance(d, dict) else {}
    return ExpectedElement(
        element_id=str(d.get("element_id", "") or ""),
        name=str(d.get("name", "") or ""),
        evidence_quote=str(d.get("evidence_quote", "") or ""),
        expected_labels=list(d.get("expected_labels") or []),
        importance=str(d.get("importance", "primary") or "primary"),
        confidence=float(d.get("confidence", 0.0) or 0.0),
    )


def _expected_relation_from_dict(d: dict) -> ExpectedRelation:
    d = d if isinstance(d, dict) else {}
    return ExpectedRelation(
        relation_id=str(d.get("relation_id", "") or ""),
        from_element_id=str(d.get("from_element_id", "") or ""),
        to_element_id=str(d.get("to_element_id", "") or ""),
        relation=str(d.get("relation", "") or ""),
        direction=str(d.get("direction", "") or ""),
        evidence_quote=str(d.get("evidence_quote", "") or ""),
        confidence=float(d.get("confidence", 0.0) or 0.0),
    )


def _context_hypothesis_from_dict(d: dict | None) -> ContextHypothesis | None:
    if not isinstance(d, dict):
        return None
    return ContextHypothesis(
        role_in_paper=str(d.get("role_in_paper", "") or ""),
        overall_subject=str(d.get("overall_subject", "") or ""),
        expected_elements=[
            _expected_element_from_dict(e) for e in (d.get("expected_elements") or [])
        ],
        expected_relations=[
            _expected_relation_from_dict(r) for r in (d.get("expected_relations") or [])
        ],
        expected_visual_cues=list(d.get("expected_visual_cues") or []),
        unstated_points=list(d.get("unstated_points") or []),
        falsification_conditions=list(d.get("falsification_conditions") or []),
        evidence_quotes=list(d.get("evidence_quotes") or []),
        reason=str(d.get("reason", "") or ""),
        confidence=float(d.get("confidence", 0.0) or 0.0),
    )


def _observed_element_from_dict(d: dict) -> ObservedElement:
    d = d if isinstance(d, dict) else {}
    return ObservedElement(
        observation_id=str(d.get("observation_id", "") or ""),
        kind=str(d.get("kind", "other") or "other"),
        description=str(d.get("description", "") or ""),
        label_text=str(d.get("label_text", "") or ""),
        region_hint=str(d.get("region_hint", "") or ""),
    )


def _observed_connection_from_dict(d: dict) -> ObservedConnection:
    d = d if isinstance(d, dict) else {}
    return ObservedConnection(
        from_observation_id=str(d.get("from_observation_id", "") or ""),
        to_observation_id=str(d.get("to_observation_id", "") or ""),
        connector=str(d.get("connector", "") or ""),
        direction=str(d.get("direction", "") or ""),
        description=str(d.get("description", "") or ""),
    )


def _visual_observation_set_from_dict(d: dict | None) -> VisualObservationSet | None:
    if not isinstance(d, dict):
        return None
    return VisualObservationSet(
        panels=[dict(p) for p in (d.get("panels") or []) if isinstance(p, dict)],
        elements=[_observed_element_from_dict(e) for e in (d.get("elements") or [])],
        connections=[_observed_connection_from_dict(c) for c in (d.get("connections") or [])],
        ocr_labels=list(d.get("ocr_labels") or []),
        repeated_motifs=[dict(m) for m in (d.get("repeated_motifs") or []) if isinstance(m, dict)],
        unreadable_regions=[
            dict(r) for r in (d.get("unreadable_regions") or []) if isinstance(r, dict)
        ],
        undecidable_elements=[
            dict(u) for u in (d.get("undecidable_elements") or []) if isinstance(u, dict)
        ],
        visual_mode_guess=str(d.get("visual_mode_guess", "unknown") or "unknown"),
        reason=str(d.get("reason", "") or ""),
        confidence=float(d.get("confidence", 0.0) or 0.0),
    )


def _alignment_item_from_dict(d: dict) -> AlignmentItem:
    d = d if isinstance(d, dict) else {}
    return AlignmentItem(
        item_id=str(d.get("item_id", "") or ""),
        item_kind=str(d.get("item_kind", "") or ""),
        label=str(d.get("label", "") or ""),
        status=str(d.get("status", "") or ""),
        expected_ref=str(d.get("expected_ref", "") or ""),
        observation_refs=list(d.get("observation_refs") or []),
        label_ref=str(d.get("label_ref", "") or ""),
        text_evidence=str(d.get("text_evidence", "") or ""),
        visual_evidence=str(d.get("visual_evidence", "") or ""),
        severity=str(d.get("severity", "medium") or "medium"),
        reason=str(d.get("reason", "") or ""),
        confidence=float(d.get("confidence", 0.0) or 0.0),
    )


def _alternative_hypothesis_from_dict(d: dict) -> AlternativeHypothesis:
    d = d if isinstance(d, dict) else {}
    return AlternativeHypothesis(
        hypothesis_id=str(d.get("hypothesis_id", "") or ""),
        description=str(d.get("description", "") or ""),
        supporting_evidence=list(d.get("supporting_evidence") or []),
        counter_evidence=list(d.get("counter_evidence") or []),
        unverified_conditions=list(d.get("unverified_conditions") or []),
        status=str(d.get("status", "active") or "active"),
        confidence=float(d.get("confidence", 0.0) or 0.0),
    )


def _verification_task_from_dict(d: dict) -> VerificationTask:
    d = d if isinstance(d, dict) else {}
    return VerificationTask(
        task_id=str(d.get("task_id", "") or ""),
        question=str(d.get("question", "") or ""),
        target_item_ids=list(d.get("target_item_ids") or []),
        region_hint=str(d.get("region_hint", "") or ""),
        focus_bbox_rel=list(d["focus_bbox_rel"]) if d.get("focus_bbox_rel") else None,
        success_condition=str(d.get("success_condition", "") or ""),
        refutation_condition=str(d.get("refutation_condition", "") or ""),
        status=str(d.get("status", "open") or "open"),
    )


def _verification_iteration_record_from_dict(d: dict) -> VerificationIterationRecord:
    d = d if isinstance(d, dict) else {}
    return VerificationIterationRecord(
        iteration_index=int(d.get("iteration_index", 0) or 0),
        executed_task_ids=list(d.get("executed_task_ids") or []),
        findings=[dict(f) for f in (d.get("findings") or []) if isinstance(f, dict)],
        changes=list(d.get("changes") or []),
        vision_call_used=bool(d.get("vision_call_used", True)),
        notes=str(d.get("notes", "") or ""),
    )


def _iterative_analysis_record_from_dict(d: dict | None) -> IterativeAnalysisRecord | None:
    if not isinstance(d, dict):
        return None
    return IterativeAnalysisRecord(
        enabled=bool(d.get("enabled", True)),
        context_hypothesis=_context_hypothesis_from_dict(d.get("context_hypothesis")),
        visual_observations=_visual_observation_set_from_dict(d.get("visual_observations")),
        alignment_items=[
            _alignment_item_from_dict(a) for a in (d.get("alignment_items") or [])
        ],
        alternative_hypotheses=[
            _alternative_hypothesis_from_dict(h) for h in (d.get("alternative_hypotheses") or [])
        ],
        verification_iterations=[
            _verification_iteration_record_from_dict(v)
            for v in (d.get("verification_iterations") or [])
        ],
        open_verification_tasks=[
            _verification_task_from_dict(t) for t in (d.get("open_verification_tasks") or [])
        ],
        unresolved_conflicts=[
            dict(c) for c in (d.get("unresolved_conflicts") or []) if isinstance(c, dict)
        ],
        review_questions=[
            dict(q) for q in (d.get("review_questions") or []) if isinstance(q, dict)
        ],
        convergence_status=str(d.get("convergence_status", "not_run") or "not_run"),
        stage_failures=list(d.get("stage_failures") or []),
        llm_calls=int(d.get("llm_calls", 0) or 0),
        vision_calls=int(d.get("vision_calls", 0) or 0),
        model=str(d.get("model", "") or ""),
    )
