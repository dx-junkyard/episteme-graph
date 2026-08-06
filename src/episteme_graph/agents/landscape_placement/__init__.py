"""LandscapePlacementAgent — 論文 → 基準地図アンカーの配置候補生成
(Phase L1, knowledge_landscape_design.md §7.3)."""
from .agent import LandscapePlacementAgent
from .schema import (
    ClaimSummary,
    DomainOption,
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
    "DomainOption",
    "SkeletonNodeOption",
    "ClaimSummary",
    "PERSPECTIVES",
]
