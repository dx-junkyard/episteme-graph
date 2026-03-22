"""Episteme Graph – Knowledge extraction pipeline for physics papers.

Parses academic papers (PDF / text) and produces a structured
`PaperStructure` containing knowledge-graph nodes, edges, key equations
with variable definitions, and text chunks for RAG.
"""

from __future__ import annotations

import json
import logging
import re
import textwrap
from typing import Any

from backend.core.schema import (
    KeyEquation,
    KnowledgeEdge,
    KnowledgeNode,
    OntologyType,
    PaperSection,
    PaperStructure,
    RelationType,
    VariableDefinition,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ontology labels (used inside the extraction prompt)
# ---------------------------------------------------------------------------

_ONTOLOGY_LABELS: list[str] = [t.value for t in OntologyType]
_RELATION_LABELS: list[str] = [r.value for r in RelationType]

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a specialist knowledge-extraction engine for **particle physics
    and quantum field theory** papers.  Your task is to analyse the supplied
    text and return a single JSON object that conforms EXACTLY to the schema
    described below.  Do NOT output anything other than valid JSON.

    ── ONTOLOGY TYPES (use ONLY these labels for node types) ──
    {ontology_labels}

    Guidance for ontology assignment:
    • TheoreticalFramework – overarching theories or formalisms
      (e.g. QED, QCD, Standard Model, Effective Field Theory).
    • PhysicalPhenomenon – observable processes
      (e.g. Compton scattering, beta decay, Higgs mechanism).
    • PhysicalQuantity – measurable quantities
      (e.g. cross-section σ, coupling constant α, decay width Γ).
    • Equation – named equations or fundamental relations
      (e.g. Dirac equation, Klein-Gordon equation).
    • Theorem – theorems or no-go results
      (e.g. Noether's theorem, CPT theorem, Goldstone's theorem).
    • MathematicalObject – formal mathematical structures
      (e.g. SU(3) group, Dirac spinor, metric tensor).
    • Experiment – experiments, detectors, or datasets
      (e.g. ATLAS, CMS, Belle II, LEP).
    • Particle – elementary or composite particles
      (e.g. electron, muon, gluon, proton, Higgs boson).
    • Symmetry – symmetry principles
      (e.g. gauge symmetry, Lorentz invariance, chiral symmetry).
    • Concept – general concepts not covered above.
    • Method – computational or analytical techniques
      (e.g. dimensional regularisation, Monte Carlo integration).
    • Result – concrete results, predictions, or numerical values.
    • Definition – formal definitions of terms or quantities.
    • Tool – software, libraries, or instruments
      (e.g. MadGraph, Pythia, ROOT).

    ── RELATION TYPES (use ONLY these labels for edges) ──
    {relation_labels}

    Key relation semantics:
    • REQUIRES – A *requires* B as prerequisite knowledge.
    • DERIVES  – A is mathematically derived from B.
    • PART_OF  – A is a component / sub-topic of B.
    • APPLIES  – method / theorem A is applied to B.
    • PREDICTS – framework A predicts phenomenon / quantity B.
    • BREAKS   – mechanism A breaks symmetry B.

    ── KEY EQUATIONS ──
    For every important equation in the text:
    1. Extract the full LaTeX representation.
    2. Assign a short descriptive label.
    3. Provide a plain-language description of what the equation expresses.
    4. List ALL variables with:
       - `symbol`: LaTeX form (e.g. "\\\\mathcal{{L}}"),
       - `name`: human-readable name,
       - `physical_meaning`: interpretation in the paper's context,
       - `unit`: natural / SI unit if stated.
    5. Include brief `derivation_notes` if the paper discusses derivation.

    DO NOT skip equations that are central to the paper's argument.
    DO NOT omit variables – every symbol must be defined.

    ── OUTPUT SCHEMA ──
    {{
      "title": "<paper title>",
      "authors": ["<author1>", ...],
      "abstract": "<abstract text>",
      "sections": [
        {{"title": "<heading>", "content": "<text>", "summary": "<1-2 sentences>"}}
      ],
      "nodes": [
        {{
          "id": "<unique-slug>",
          "name": "<display name>",
          "ontology_type": "<one of the ontology labels>",
          "description": "<short description>",
          "aliases": ["<alt name>", ...],
          "latex_symbol": "<LaTeX if applicable>"
        }}
      ],
      "edges": [
        {{
          "source_id": "<node id>",
          "target_id": "<node id>",
          "relation": "<one of the relation labels>",
          "weight": <0.0-1.0>,
          "description": "<optional note>"
        }}
      ],
      "key_equations": [
        {{
          "latex": "<LaTeX string>",
          "label": "<short label>",
          "description": "<plain-language explanation>",
          "variables": [
            {{
              "symbol": "<LaTeX>",
              "name": "<name>",
              "physical_meaning": "<meaning>",
              "unit": "<unit or empty>"
            }}
          ],
          "derivation_notes": "<how it is derived>"
        }}
      ]
    }}
""")

_EXTRACTION_USER_PROMPT = textwrap.dedent("""\
    Analyse the following paper text and return the JSON described in your
    instructions.  Be thorough – extract all relevant entities, relations,
    and equations.

    --- PAPER TEXT START ---
    {text}
    --- PAPER TEXT END ---
""")


# ---------------------------------------------------------------------------
# Chunking utility
# ---------------------------------------------------------------------------

def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 200) -> list[str]:
    """Split *text* into overlapping chunks for RAG indexing."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks


# ---------------------------------------------------------------------------
# Public extraction API
# ---------------------------------------------------------------------------

class PaperExtractor:
    """Extracts structured knowledge from a physics paper using an LLM.

    Parameters
    ----------
    llm_callable:
        An async callable ``(system: str, user: str) -> str`` that sends a
        prompt to the LLM and returns the raw completion text.
    """

    def __init__(self, llm_callable):
        self._llm = llm_callable

    # ----- main entry point ------------------------------------------------

    async def extract(self, raw_text: str, source_file: str = "") -> PaperStructure:
        """Run the full extraction pipeline on *raw_text*."""
        raw_json = await self._call_llm(raw_text)
        data = self._parse_json(raw_json)
        structure = self._finalize_structure(data, raw_text, source_file)
        return structure

    # ----- internal helpers ------------------------------------------------

    async def _call_llm(self, text: str) -> str:
        system = _EXTRACTION_SYSTEM_PROMPT.format(
            ontology_labels=", ".join(_ONTOLOGY_LABELS),
            relation_labels=", ".join(_RELATION_LABELS),
        )
        user = _EXTRACTION_USER_PROMPT.format(text=text)
        return await self._llm(system, user)

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        """Extract and parse JSON from LLM output (tolerant of markdown fences)."""
        # Strip markdown code fences if present
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse LLM JSON output: %s", exc)
            return {}

    @staticmethod
    def _finalize_structure(
        data: dict[str, Any],
        raw_text: str,
        source_file: str,
    ) -> PaperStructure:
        """Build a validated ``PaperStructure`` from raw LLM output dict."""

        # -- sections -------------------------------------------------------
        sections = [
            PaperSection(**s)
            for s in data.get("sections", [])
        ]

        # -- nodes ----------------------------------------------------------
        nodes: list[KnowledgeNode] = []
        for n in data.get("nodes", []):
            try:
                onto = OntologyType(n.get("ontology_type", "Concept"))
            except ValueError:
                onto = OntologyType.CONCEPT
            nodes.append(
                KnowledgeNode(
                    id=n.get("id", ""),
                    name=n.get("name", ""),
                    ontology_type=onto,
                    description=n.get("description", ""),
                    aliases=n.get("aliases", []),
                    latex_symbol=n.get("latex_symbol", ""),
                )
            )

        # -- edges ----------------------------------------------------------
        edges: list[KnowledgeEdge] = []
        for e in data.get("edges", []):
            try:
                rel = RelationType(e.get("relation", "RELATED_TO"))
            except ValueError:
                rel = RelationType.RELATED_TO
            edges.append(
                KnowledgeEdge(
                    source_id=e.get("source_id", ""),
                    target_id=e.get("target_id", ""),
                    relation=rel,
                    weight=float(e.get("weight", 1.0)),
                    description=e.get("description", ""),
                )
            )

        # -- key equations --------------------------------------------------
        equations: list[KeyEquation] = []
        for eq in data.get("key_equations", []):
            variables = [
                VariableDefinition(
                    symbol=v.get("symbol", ""),
                    name=v.get("name", ""),
                    physical_meaning=v.get("physical_meaning", ""),
                    unit=v.get("unit", ""),
                )
                for v in eq.get("variables", [])
            ]
            equations.append(
                KeyEquation(
                    latex=eq.get("latex", ""),
                    label=eq.get("label", ""),
                    description=eq.get("description", ""),
                    variables=variables,
                    derivation_notes=eq.get("derivation_notes", ""),
                )
            )

        # -- chunks for RAG -------------------------------------------------
        chunks = _chunk_text(raw_text)

        return PaperStructure(
            title=data.get("title", ""),
            authors=data.get("authors", []),
            abstract=data.get("abstract", ""),
            sections=sections,
            nodes=nodes,
            edges=edges,
            key_equations=equations,
            chunks=chunks,
            source_file=source_file,
        )
