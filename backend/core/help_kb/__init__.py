"""``help_kb``: 非ベクトルKB共通基盤（正本は ``docs/features/manual_help_kb_design.md``）。

- ``index.py``: 見出し節単位索引 + トークン重なり検索の汎用エンジン
  （``core/admin_assistant/knowledge.py`` の委譲先）。
- ``manual.py``: ``docs/manual/<audience>/`` の audience 別索引（学生/教員/管理者）。
- ``validator.py``: front-matter / 見出し / リンク整合性の検証（起動時 fail-open）。

FastAPI を import しない（core 層の規約）。索引の削除 API は持たない。
"""

from __future__ import annotations

from .manual import (
    clear_manual_cache,
    excluded_sections,
    manual_available,
    search_manual,
)

__all__ = [
    "clear_manual_cache",
    "excluded_sections",
    "manual_available",
    "search_manual",
]
