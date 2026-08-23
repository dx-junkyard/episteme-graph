"""構造の降下路（Phase 3 — 足場ダイヤル・楽屋 v1）の降下エンジン。

正本設計書: ``docs/features/structure_descent_design.md``（SD1〜SD6）。
足場ダイヤル・楽屋・（v2 の）点検口はすべて本パッケージを通る（別実装禁止）。
非LLM・決定論・読み取り専用（FastAPI / LLM / routes / services 非 import）。
"""

from core.descent.engine import (
    BACKSTAGE_DECLARATION,
    SUPPORTED_ELEMENT_TYPES,
    build_backstage_path,
    build_ladder,
)

__all__ = [
    "BACKSTAGE_DECLARATION",
    "SUPPORTED_ELEMENT_TYPES",
    "build_backstage_path",
    "build_ladder",
]
