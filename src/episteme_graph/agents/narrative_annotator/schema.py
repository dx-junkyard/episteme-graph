"""NarrativeAnnotator data models (issue #360).

The annotator adds a reading layer on top of the TheoryOperationGraph:
- per main node: ``narrative_role`` — what this stage contributes to the
  paper's argument (1–2 sentences)
- per main edge: ``transition_text`` — what the edge hands from one stage to
  the next (1 sentence)
- per graph: ``graph_summary`` — central thesis → support structure in 3–5
  sentences

Annotations NEVER modify the graph itself (nodes / edges / layers / backing
status stay untouched) and are always provisional (maturity_source =
"llm_proposed"); a teacher confirms them later.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

NARRATIVE_VERSION = "v1"

# Length caps keep the annotations display-sized; the validator enforces them.
MAX_NARRATIVE_ROLE_CHARS = 400
MAX_TRANSITION_TEXT_CHARS = 300
MAX_GRAPH_SUMMARY_CHARS = 1500

MATURITY_SOURCES = ["llm_proposed", "teacher_approved"]


@dataclass
class NodeNarrative:
    component_id: str
    narrative_role: str
    reason: str = ""
    confidence: float = 0.0


@dataclass
class EdgeNarrative:
    edge_id: str
    transition_text: str
    reason: str = ""
    confidence: float = 0.0


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class NarrativeAnnotationResult:
    document_id: str
    narrative_version: str
    cartridge_id: str | None
    graph_summary: str
    node_narratives: list[NodeNarrative]
    edge_narratives: list[EdgeNarrative]
    review_notes: list[str]
    confidence: float
    maturity_source: str = "llm_proposed"
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "NarrativeAnnotationResult":
        nodes = [NodeNarrative(**n) for n in d.get("node_narratives", [])]
        edges = [EdgeNarrative(**e) for e in d.get("edge_narratives", [])]
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            narrative_version=d.get("narrative_version", NARRATIVE_VERSION),
            cartridge_id=d.get("cartridge_id"),
            graph_summary=d.get("graph_summary", ""),
            node_narratives=nodes,
            edge_narratives=edges,
            review_notes=list(d.get("review_notes", [])),
            confidence=float(d.get("confidence", 0.0)),
            maturity_source=d.get("maturity_source", "llm_proposed"),
            validation_issues=issues,
        )

    @classmethod
    def make_fallback(
        cls,
        document_id: str,
        cartridge_id: str | None,
        reason: str,
    ) -> "NarrativeAnnotationResult":
        return cls(
            document_id=document_id,
            narrative_version=NARRATIVE_VERSION,
            cartridge_id=cartridge_id,
            graph_summary="",
            node_narratives=[],
            edge_narratives=[],
            review_notes=[f"Narrative annotation failed: {reason}"],
            confidence=0.0,
            validation_issues=[ValidationIssue(
                rule_id="narrative_annotation_failed",
                severity="error",
                message=reason,
                field="graph_summary",
            )],
        )


@dataclass
class NarrativeLLMInput:
    document_id: str
    cartridge_id: str | None
    central_thesis_text: str
    main_nodes: list[dict]
    main_edges: list[dict]
    normalized_terms: list[dict] | None = None
