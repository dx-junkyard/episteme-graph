"""画面文脈アダプター（Assistant Screen Adapter）— core の公開面。

設計: ``docs/features/assistant_screen_adapter_design.md``（SA1〜SA7）。

route は次の3つだけを使う:

- ``normalize_screen_context(raw)``: 画面が渡した参照を正規化（未知は ``None``）。
- ``resolve(ctx, sources)``: 権限ゲート済みの ``sources``（論文層 DTO 等）から
  事実文を導出する（決定論・非LLM・読み取り専用）。
- ``render_block(facts)``: 固定ヘッダ付きの独立ブロックへ描画する。

本パッケージを import した時点で画面ごとの解決器が登録される（``resolvers``）。
"""

from __future__ import annotations

from .registry import Resolver, register, registered_kinds, render_block, resolve
from .schema import (
    BLOCK_HEADER,
    KNOWN_SCREENS,
    MAX_BLOCK_CHARS,
    SCREEN_GRAPH_REVIEW,
    ScreenContext,
    normalize_screen_context,
)

from . import resolvers  # noqa: E402,F401  # import 副作用で解決器を登録する

__all__ = [
    "BLOCK_HEADER",
    "KNOWN_SCREENS",
    "MAX_BLOCK_CHARS",
    "Resolver",
    "SCREEN_GRAPH_REVIEW",
    "ScreenContext",
    "normalize_screen_context",
    "register",
    "registered_kinds",
    "render_block",
    "resolve",
]
