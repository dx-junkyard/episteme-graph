"""理論モジュール層（Theory Module Layer）— 読み時導出の core。

正本: ``docs/features/theory_module_layer_design.md``（TM1〜TM10・§5・§8.1）。
FastAPI / sqlalchemy / LLM / embedding を import しない純関数だけを置く。
"""

from __future__ import annotations

from core.theory_modules.builder import build_theory_modules

__all__ = ["build_theory_modules"]
