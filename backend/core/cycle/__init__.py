"""理解サイクル（Understanding Cycle, UCサイクル）Phase 1 — B層拡張モジュール。

正本は ``docs/features/understanding_cycle_design.md``（UC1〜UC10）。migration 不要
（``interest_traces`` の kind に ``intention`` / ``anchor_mark`` を追加するのみ）。
tension（``backend/core/tension/``）・structure_anchor（``backend/core/structure_anchor/``）
と同型の独立モジュールで、いずれの worker・digest・教員向け集約からも構造的に除外される
（本人専用メモ。監査記帳も行わない）。FastAPI には依存しない。
"""

from core.cycle.schema import (
    INTENTION_ROLES,
    KIND_ANCHOR_MARK,
    KIND_INTENTION,
    QUICK_LABEL_KEYS,
    QUICK_LABELS,
    ROLE_CARRYOVER_QUESTION,
    ROLE_LEAVE_NOTE,
    ROLE_OPENING_MOTIVE,
    ROLE_REVISIT_ANSWER,
)

__all__ = [
    "INTENTION_ROLES",
    "KIND_ANCHOR_MARK",
    "KIND_INTENTION",
    "QUICK_LABEL_KEYS",
    "QUICK_LABELS",
    "ROLE_CARRYOVER_QUESTION",
    "ROLE_LEAVE_NOTE",
    "ROLE_OPENING_MOTIVE",
    "ROLE_REVISIT_ANSWER",
]
