"""ApparatusSemanticsAgent.

Vision LLM agent identifying experimental apparatus/instruments depicted in
paper figures, using the figure image + caption + nearby text + optional
frozen knowledge-library retrieval candidates (docs/features/
image_pipeline_knowledge_library_design.md §5). Output is always
candidate-only (review_status='review_required', source_backing_status never
'source_backed') — confirmation is a human-only operation performed elsewhere
(the library promotion flow, §6-4).
"""
from .agent import ApparatusSemanticsAgent
from .schema import (
    MATCH_STATUSES,
    REVIEW_STATUS_DEFAULT,
    REVIEW_STATUSES,
    SOURCE_BACKING_STATUSES,
    ApparatusConnection,
    ApparatusPart,
    ApparatusRecord,
    ApparatusSemanticsResult,
    CartridgeContext,
    FigureImageInput,
    LibraryCandidate,
    ValidationIssue,
)

__all__ = [
    "ApparatusSemanticsAgent",
    "MATCH_STATUSES",
    "REVIEW_STATUS_DEFAULT",
    "REVIEW_STATUSES",
    "SOURCE_BACKING_STATUSES",
    "ApparatusConnection",
    "ApparatusPart",
    "ApparatusRecord",
    "ApparatusSemanticsResult",
    "CartridgeContext",
    "FigureImageInput",
    "LibraryCandidate",
    "ValidationIssue",
]
