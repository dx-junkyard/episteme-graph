"""Episteme Graph – Knowledge schema for particle physics learning.

Defines the ontology types, node/edge models, equation representations,
and paper structure used throughout the knowledge extraction pipeline.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Ontology – domain-specific vocabulary for particle physics / QFT
# ---------------------------------------------------------------------------

class OntologyType(str, Enum):
    """Node type taxonomy optimised for graduate-level particle physics."""

    # --- Physics-domain types ---
    THEORETICAL_FRAMEWORK = "TheoreticalFramework"
    """High-level theory or formalism (e.g. QED, QCD, Standard Model)."""

    PHYSICAL_PHENOMENON = "PhysicalPhenomenon"
    """Observable phenomenon (e.g. Compton scattering, Higgs mechanism)."""

    PHYSICAL_QUANTITY = "PhysicalQuantity"
    """Measurable quantity (e.g. cross-section, coupling constant, mass)."""

    EQUATION = "Equation"
    """Named equation or fundamental relation (e.g. Dirac equation)."""

    THEOREM = "Theorem"
    """Mathematical or physical theorem (e.g. Noether's theorem, CPT theorem)."""

    MATHEMATICAL_OBJECT = "MathematicalObject"
    """Formal mathematical structure (e.g. Lie group, spinor, tensor)."""

    EXPERIMENT = "Experiment"
    """Experimental setup or result (e.g. LEP, LHC Run-2)."""

    PARTICLE = "Particle"
    """Elementary or composite particle (e.g. electron, pion, gluon)."""

    SYMMETRY = "Symmetry"
    """Symmetry principle (e.g. gauge symmetry, Lorentz invariance)."""

    # --- General-purpose types (retained for broader applicability) ---
    CONCEPT = "Concept"
    """General concept that does not fit a more specific category."""

    METHOD = "Method"
    """Computational or analytical technique (e.g. dimensional regularisation)."""

    RESULT = "Result"
    """A concrete result, prediction, or numerical value from a study."""

    DEFINITION = "Definition"
    """Formal definition of a term or quantity."""

    TOOL = "Tool"
    """Software, library, or instrument (e.g. MadGraph, ROOT)."""


# ---------------------------------------------------------------------------
# Edge / Relation types
# ---------------------------------------------------------------------------

class RelationType(str, Enum):
    """Allowed edge labels in the knowledge graph."""

    REQUIRES = "REQUIRES"
    """Source concept requires target as prerequisite knowledge."""

    DERIVES = "DERIVES"
    """Source is mathematically derived from target."""

    PART_OF = "PART_OF"
    """Source is a component or sub-topic of target."""

    RELATED_TO = "RELATED_TO"
    """General semantic relation."""

    APPLIES = "APPLIES"
    """Source method/theorem is applied to target."""

    CONSTRAINS = "CONSTRAINS"
    """Source experimentally or theoretically constrains target."""

    GENERALISES = "GENERALISES"
    """Source is a generalisation of target."""

    SPECIALISES = "SPECIALISES"
    """Source is a specialisation / special case of target."""

    EQUIVALENT_TO = "EQUIVALENT_TO"
    """Source and target are mathematically or physically equivalent."""

    PREDICTS = "PREDICTS"
    """Source framework predicts target phenomenon / quantity."""

    BREAKS = "BREAKS"
    """Source mechanism breaks target symmetry."""


# ---------------------------------------------------------------------------
# Variable definition (used inside equations)
# ---------------------------------------------------------------------------

class VariableDefinition(BaseModel):
    """A single variable appearing in a key equation."""

    symbol: str = Field(
        ...,
        description="LaTeX representation of the variable (e.g. '\\\\mathcal{L}').",
    )
    name: str = Field(
        ...,
        description="Human-readable name (e.g. 'Lagrangian density').",
    )
    physical_meaning: str = Field(
        default="",
        description=(
            "Physical interpretation of the variable in the context of the "
            "equation (e.g. 'Total Lagrangian density of the QED interaction')."
        ),
    )
    unit: str = Field(
        default="",
        description="SI or natural unit if applicable (e.g. 'GeV', 'dimensionless').",
    )


# ---------------------------------------------------------------------------
# Key equation
# ---------------------------------------------------------------------------

class KeyEquation(BaseModel):
    """A physically important equation extracted from a paper."""

    latex: str = Field(
        ...,
        description="The equation in LaTeX (e.g. '\\\\mathcal{L} = ...').",
    )
    label: str = Field(
        default="",
        description="Short descriptive label (e.g. 'QED Lagrangian').",
    )
    description: str = Field(
        default="",
        description="Plain-language explanation of what the equation expresses.",
    )
    variables: list[VariableDefinition] = Field(
        default_factory=list,
        description="Definitions for every variable appearing in the equation.",
    )
    derivation_notes: str = Field(
        default="",
        description=(
            "Brief notes on how this equation is derived or which "
            "assumptions are required."
        ),
    )


# ---------------------------------------------------------------------------
# Node / Edge models for the knowledge graph
# ---------------------------------------------------------------------------

class KnowledgeNode(BaseModel):
    """A single entity (node) in the knowledge graph."""

    id: str = Field(..., description="Unique identifier for the node.")
    name: str = Field(..., description="Display name of the entity.")
    ontology_type: OntologyType = Field(
        ..., description="Ontology category for this entity."
    )
    description: str = Field(
        default="", description="Short description of the entity."
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternative names or notations.",
    )
    latex_symbol: str = Field(
        default="",
        description="Representative LaTeX symbol if applicable.",
    )
    metadata: dict = Field(
        default_factory=dict,
        description="Arbitrary additional metadata.",
    )


class KnowledgeEdge(BaseModel):
    """A directed relation (edge) in the knowledge graph."""

    source_id: str = Field(..., description="ID of the source node.")
    target_id: str = Field(..., description="ID of the target node.")
    relation: RelationType = Field(
        ..., description="Type of the relation."
    )
    weight: float = Field(
        default=1.0,
        description="Edge weight / confidence (0.0–1.0).",
    )
    description: str = Field(
        default="", description="Optional description of the relation."
    )


# ---------------------------------------------------------------------------
# Paper structure – top-level extraction result
# ---------------------------------------------------------------------------

class PaperSection(BaseModel):
    """One logical section extracted from a paper."""

    title: str = Field(..., description="Section heading.")
    content: str = Field(default="", description="Raw text content.")
    summary: str = Field(default="", description="Brief summary.")


class PaperStructure(BaseModel):
    """Complete structured representation of a parsed academic paper."""

    title: str = Field(..., description="Paper title.")
    authors: list[str] = Field(default_factory=list)
    abstract: str = Field(default="")
    sections: list[PaperSection] = Field(default_factory=list)

    # --- Knowledge graph entities ---
    nodes: list[KnowledgeNode] = Field(
        default_factory=list,
        description="Extracted knowledge-graph nodes.",
    )
    edges: list[KnowledgeEdge] = Field(
        default_factory=list,
        description="Extracted knowledge-graph edges.",
    )

    # --- Equation extraction (NEW for physics) ---
    key_equations: list[KeyEquation] = Field(
        default_factory=list,
        description=(
            "Important equations extracted from the paper, each with full "
            "variable definitions and physical interpretation."
        ),
    )

    # --- Chunk-level data for RAG ---
    chunks: list[str] = Field(
        default_factory=list,
        description="Text chunks suitable for vector-store indexing.",
    )

    source_file: str = Field(
        default="", description="Original file name or identifier."
    )
