"""学習チャットの様相（stance）層（Phase 1 入口統合）。

正本設計書: ``docs/features/learning_chat_entry_unification_design.md``。

* 語彙・純関数の正本: :mod:`core.learning_stance.schema`
* 非LLM 一次判定: :mod:`core.learning_stance.heuristic`
* 表示ラベルの正本: ``core/label_vocab.py`` の ``LEARNING_STANCE_LABELS``

FastAPI / sqlalchemy / core.llm を import しない（ガードレールが構造的に固定）。
"""

from __future__ import annotations

from core.learning_stance.heuristic import prejudge
from core.learning_stance.schema import (
    SOURCE_EXPLICIT,
    SOURCE_INFERRED,
    STANCE_CASUAL_LIGHT,
    STANCE_CYCLE_DIFF,
    STANCE_CYCLE_ELICIT,
    STANCE_DISCUSS,
    STANCE_SOURCES,
    STANCE_TUTOR,
    STANCES,
    build_stance_dto,
    resolve_stance,
)

__all__ = [
    "SOURCE_EXPLICIT",
    "SOURCE_INFERRED",
    "STANCES",
    "STANCE_CASUAL_LIGHT",
    "STANCE_CYCLE_DIFF",
    "STANCE_CYCLE_ELICIT",
    "STANCE_DISCUSS",
    "STANCE_SOURCES",
    "STANCE_TUTOR",
    "build_stance_dto",
    "prejudge",
    "resolve_stance",
]
