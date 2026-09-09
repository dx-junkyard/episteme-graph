"""core/tts.py::strip_text_for_speech の追補（graph_dialogue_review_design.md §15）。

``\\(...\\)`` / ``\\[...\\]`` 形式の LaTeX と ANSI 残骸（``[0m``）を落とすようにした
（オーナー報告: 音声応答に生 LaTeX と色コード残骸が読み上げられていた）。既存の
除去規則（``$...$`` / markdown / 出典マーカー）と長さ上限は不変。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _path in (str(BACKEND), str(BACKEND / "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.tts import strip_text_for_speech  # noqa: E402


class TestLatexRemoval:
    def test_removes_inline_paren_latex(self):
        out = strip_text_for_speech(r"密度ゆらぎ \(\delta(t,\bm{x})\) を考える")
        assert "\\(" not in out and "\\delta" not in out
        assert "密度ゆらぎ" in out and "を考える" in out

    def test_removes_display_bracket_latex(self):
        out = strip_text_for_speech("式は \\[ a = b \\] です")
        assert "a = b" not in out
        assert "式は" in out and "です" in out

    def test_keeps_existing_dollar_math_behavior(self):
        assert "\\delta" not in strip_text_for_speech(r"値は $\delta$ です")


class TestControlSequenceRemoval:
    def test_removes_ansi_and_bare_residue(self):
        out = strip_text_for_speech("結論\x1b[0mです[0m。")
        assert "\x1b" not in out and "[0m" not in out
        assert "結論" in out and "です" in out

    def test_removes_c0_controls(self):
        assert "\x07" not in strip_text_for_speech("a\x07b")


class TestUnchangedBehavior:
    def test_source_markers_and_markdown_still_removed(self):
        out = strip_text_for_speech("**強調** [出典1] [ACTION_BUTTON:go] 本文")
        assert "**" not in out and "[出典1]" not in out and "ACTION_BUTTON" not in out
        assert "本文" in out

    def test_limit_is_still_applied(self):
        assert len(strip_text_for_speech("あ" * 100, limit=10)) == 10
