"""NarrativeAnnotator — reader-facing narrative for the main graph (issue #360)."""
from .agent import NarrativeAnnotator
from .schema import NarrativeAnnotationResult

__all__ = ["NarrativeAnnotator", "NarrativeAnnotationResult"]
