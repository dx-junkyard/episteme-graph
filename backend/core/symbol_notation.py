"""記号表記の正規化キー（SymbolRegistry）への読み取り専用の橋。

``core/deliberation/`` は A層（``src/episteme_graph/agents/``）の直接 import を
ガードレールで禁止している（W1・``test_deliberation_guardrails.py``）。一方、
表記ゆれ照合キーの正本は A層 ``symbol_registry.builder.normalize_symbol`` であり、
表示層が「この変種はこのキーと同一記号か」を判定するのに**同じ実装**が要る
（第2実装を作ると照合結果がずれる）。

そこで ``core/element_vocab.py`` / ``core/figure_presentation.py`` と同じ
「core 直下の明示的な seam」として、この1ファイルだけが A層の正規化関数を
再エクスポートする。追加のロジックは持たない（読むだけ・A層非改変）。
"""

from __future__ import annotations

from episteme_graph.agents.symbol_registry.builder import normalize_symbol

__all__ = ["normalize_symbol"]
