"""ContextualExplanationAgent — two-layer (generic + contextual) explanations
for pipeline elements (Phase 2, hierarchical_context_explanation_design.md §5.1)."""
from .agent import ContextualExplanationAgent
from .schema import (
    ContextualExplanationResult,
    ElementExplanationInput,
    ElementExplanationResult,
)

__all__ = [
    "ContextualExplanationAgent",
    "ContextualExplanationResult",
    "ElementExplanationInput",
    "ElementExplanationResult",
]
