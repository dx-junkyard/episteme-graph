"""LandscapePlacementAgent — 論文 → 基準地図アンカーの配置候補生成
(Phase L1, knowledge_landscape_design.md §7.3)."""
from .agent import LandscapePlacementAgent
from .schema import (
    CategoryGapRecord,
    ClaimSummary,
    DEFAULT_MAX_GAPS_PER_DOCUMENT,
    DomainOption,
    GAP_LAYERS,
    LandscapePlacementInput,
    LandscapePlacementResult,
    PERSPECTIVES,
    PlacementCandidate,
    SkeletonNodeOption,
    UnplacedDomain,
)

__all__ = [
    "LandscapePlacementAgent",
    "LandscapePlacementInput",
    "LandscapePlacementResult",
    "PlacementCandidate",
    "UnplacedDomain",
    "CategoryGapRecord",
    "DomainOption",
    "SkeletonNodeOption",
    "ClaimSummary",
    "PERSPECTIVES",
    "GAP_LAYERS",
    "DEFAULT_MAX_GAPS_PER_DOCUMENT",
]
