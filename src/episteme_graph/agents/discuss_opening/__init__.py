"""DiscussOpeningAgent — 開幕画面の「議論のきっかけ」候補生成
(Phase 1, discuss_opening_authoring_design.md §4.1)."""
from .agent import DiscussOpeningAgent
from .schema import (
    AssumptionFact,
    AuthorChoice,
    DiscussOpeningInput,
    DiscussOpeningResult,
    DiscussionSeed,
    ROLE_DISCUSSION_SEED,
    ThesisFacts,
)

__all__ = [
    "DiscussOpeningAgent",
    "DiscussOpeningInput",
    "DiscussOpeningResult",
    "DiscussionSeed",
    "AssumptionFact",
    "AuthorChoice",
    "ThesisFacts",
    "ROLE_DISCUSSION_SEED",
]
