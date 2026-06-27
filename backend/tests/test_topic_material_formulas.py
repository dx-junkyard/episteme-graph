"""未解決の数式: 教材エンドポイントの数式抽出ヘルパの回帰テスト。

`get_topic_material` が content_blocks の equations を UI 用 formulas に変換する際、
LaTeX が無くても reading(plain_text) / 原文(raw_text) を持つ式は落とさず渡すこと
（`![[equation:id]]` 埋め込みを「未解決」にしないため）を検証する。外部依存なし。
"""

from __future__ import annotations


def test_latexless_equation_with_raw_text_is_kept():
    from api.routes.learning import _topic_formulas_from_content_blocks

    topic = {
        "content_blocks": [
            {
                "type": "equations",
                "items": [
                    {"equation_id": "eq_delta_fourier", "label": "delta-fourier",
                     "latex": "", "raw_text": "delta(k) = ..."},
                    {"equation_id": "eq_F2", "label": "eq:F2",
                     "latex": "", "plain_text": "F2 は a と b の和"},
                    {"equation_id": "eq_clean", "label": "g", "latex": "g = h"},
                ],
            },
        ],
    }
    formulas = _topic_formulas_from_content_blocks(topic)
    by_id = {f["id"]: f for f in formulas}

    # LaTeX が無くても raw_text / plain_text があれば残る。
    assert "eq_delta_fourier" in by_id
    assert by_id["eq_delta_fourier"]["raw_text"] == "delta(k) = ..."
    assert "eq_F2" in by_id
    assert by_id["eq_F2"]["plain_text"] == "F2 は a と b の和"
    assert by_id["eq_clean"]["latex"] == "g = h"


def test_fully_empty_equation_is_dropped():
    from api.routes.learning import _topic_formulas_from_content_blocks

    topic = {
        "content_blocks": [
            {
                "type": "equations",
                "items": [
                    {"equation_id": "eq_empty", "label": "eq:empty"},
                    {"equation_id": "eq_ok", "latex": "x = y"},
                ],
            },
        ],
    }
    ids = {f["id"] for f in _topic_formulas_from_content_blocks(topic)}
    # 本体が一切無い式だけ除外される。
    assert "eq_empty" not in ids
    assert "eq_ok" in ids


def test_non_equation_blocks_ignored_and_empty_topic_safe():
    from api.routes.learning import _topic_formulas_from_content_blocks

    assert _topic_formulas_from_content_blocks({}) == []
    assert _topic_formulas_from_content_blocks(
        {"content_blocks": [{"type": "summary", "text": "..."}]}
    ) == []
