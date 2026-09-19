"""core/text_hygiene.py — 制御シーケンス除去（graph_dialogue_review_design.md §15）。

- ANSI エスケープ（ESC 付き）・ESC が落ちた裸の SGR 残骸（``[0m``）・C0 制御文字を落とす。
- ``\\n`` / ``\\t`` と通常の角括弧テキストは壊さない。
- 適用先3箇所（対話応答 / grounding の事実文 / artifact スニペット）で効いている。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _path in (str(BACKEND), str(BACKEND / "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.text_hygiene import strip_control_sequences  # noqa: E402


class TestStripControlSequences:
    def test_removes_ansi_escape_sequences(self):
        assert strip_control_sequences("\x1b[0m結論\x1b[1;32mです") == "結論です"

    def test_removes_bare_sgr_residue(self):
        assert strip_control_sequences("結論[0mです[1m。") == "結論です。"

    def test_removes_c0_controls_but_keeps_newline_and_tab(self):
        assert strip_control_sequences("a\x00b\x07c\nd\te") == "abc\nd\te"

    def test_keeps_ordinary_bracket_text(self):
        for text in ("[出典1] を参照", "配列 a[0] の値", "[ACTION_BUTTON:x]"):
            assert strip_control_sequences(text) == text

    def test_non_string_and_empty_are_safe(self):
        assert strip_control_sequences("") == ""
        assert strip_control_sequences(None) == ""  # type: ignore[arg-type]

    def test_module_is_pure(self):
        src = (BACKEND / "core" / "text_hygiene.py").read_text(encoding="utf-8")
        for forbidden in ("fastapi", "sqlalchemy", "core.llm", "openai", "get_session"):
            assert forbidden not in src, forbidden


class TestApplicationSites:
    def test_render_block_scrubs_control_residue(self):
        from core.assistant_context.registry import render_block

        block = render_block(["\x1b[0m選択中のノード[0m: A"])
        assert "\x1b" not in block
        assert "[0m" not in block
        assert "選択中のノード: A" in block

    def test_render_block_drops_facts_that_are_only_residue(self):
        from core.assistant_context.registry import render_block

        assert render_block(["\x1b[0m"]) == ""

    def test_truncate_snippet_scrubs_control_residue(self):
        from core.graph_paper_layer.schema import truncate_snippet

        assert truncate_snippet("本文\x1b[0m の一部[0m") == "本文 の一部"
