"""切り詰め正本 ``core/text_excerpt.py`` のテスト（CP5 / RC11）。

`docs/features/element_context_presentation_redesign.md` Phase 0。素スライス
（``text[:80]``）と ``_short_excerpt`` の分裂を1実装へ集約したので、
「文境界 → 語境界 → 文字数」の優先順・ellipsis・TeX トークン保護をここで固定する。
``looks_like_tex_math`` の移設後も既存 import 面（course_content_builder 経由）が
生きていることも併せて検査する。
"""
from __future__ import annotations

import pytest

from core.text_excerpt import (
    excerpt,
    first_sentence,
    looks_like_tex_math,
    normalize_whitespace,
)


# ---------------------------------------------------------------------------
# normalize_whitespace
# ---------------------------------------------------------------------------


class TestNormalizeWhitespace:
    def test_collapses_newlines_and_runs(self):
        assert normalize_whitespace("  a\n\n b\t\tc  ") == "a b c"

    def test_none_and_empty(self):
        assert normalize_whitespace(None) == ""
        assert normalize_whitespace("") == ""


# ---------------------------------------------------------------------------
# excerpt — 基本
# ---------------------------------------------------------------------------


class TestExcerptBasics:
    def test_short_text_is_returned_unchanged_without_ellipsis(self):
        assert excerpt("短い説明", 40) == "短い説明"
        assert "…" not in excerpt("短い説明", 40)

    def test_whitespace_is_normalized_before_measuring(self):
        assert excerpt("a\n  b", 10) == "a b"

    def test_empty_and_non_positive_limit(self):
        assert excerpt("", 10) == ""
        assert excerpt(None, 10) == ""
        assert excerpt("something", 0) == ""
        assert excerpt("something", -3) == ""

    def test_non_numeric_limit_is_fail_soft(self):
        assert excerpt("something", "nope") == ""  # type: ignore[arg-type]

    def test_custom_ellipsis(self):
        out = excerpt("あ" * 50, 10, ellipsis="...")
        assert out.endswith("...")
        assert out[:-3] == "あ" * 10


# ---------------------------------------------------------------------------
# excerpt — 切る位置の優先順
# ---------------------------------------------------------------------------


class TestExcerptCutPriority:
    def test_prefers_sentence_boundary(self):
        text = "This is a sentence. And here is another one that goes on and on."
        out = excerpt(text, 34)
        assert out == "This is a sentence.…"

    def test_prefers_japanese_sentence_boundary(self):
        text = "これは最初の文です。そしてこちらは二番目の文で、さらに長く続きます。"
        out = excerpt(text, 22)
        assert out == "これは最初の文です。…"

    def test_falls_back_to_word_boundary(self):
        # 文境界が無い英文は語境界で切る（単語途中で切らない = RC11 の再発防止）。
        text = "alpha bravo charlie delta echo foxtrot"
        out = excerpt(text, 22)
        assert out.endswith("…")
        assert out == "alpha bravo charlie…"

    def test_never_cuts_in_the_middle_of_a_word(self):
        text = "the density contrast is measured from its spatial mean value"
        out = excerpt(text, 44)
        assert "…" in out
        # 「…spatial mea」のような単語途中切りが起きないこと。
        assert out.rstrip("…").split()[-1] in text.split()

    def test_japanese_without_spaces_degrades_to_character_cut(self):
        text = "あ" * 100
        out = excerpt(text, 20)
        assert out == "あ" * 20 + "…"

    def test_sentence_boundary_below_40_percent_is_not_used(self):
        # 「ab.」で切ると limit の 40% を大きく下回るので採用しない。
        text = "ab. " + "c" * 100
        out = excerpt(text, 20)
        assert not out.startswith("ab.…")
        assert len(out.rstrip("…")) >= 8

    def test_word_boundary_below_40_percent_is_not_used(self):
        text = "ab " + "c" * 100
        out = excerpt(text, 20)
        assert not out.startswith("ab…")

    def test_decimal_point_is_not_a_sentence_boundary(self):
        text = "The value 3.14159 appears everywhere in this long explanation text."
        out = excerpt(text, 30)
        assert not out.startswith("The value 3.…")

    def test_abbreviation_period_is_not_a_sentence_boundary(self):
        text = "Fig. 5.2 shows the optical layout of the apparatus used in the run."
        out = excerpt(text, 30)
        assert not out.startswith("Fig.…")

    def test_trailing_separator_is_stripped_before_the_ellipsis(self):
        out = excerpt("alpha, bravo, charlie, delta, echo", 16)
        assert not out.rstrip("…").endswith(",")
        assert not out.rstrip("…").endswith(" ")


# ---------------------------------------------------------------------------
# excerpt — TeX トークン保護
# ---------------------------------------------------------------------------


class TestExcerptTexSafety:
    def test_does_not_cut_inside_a_tex_command_token(self):
        text = "abcdefghij" + r"\alphabet" + "z" * 40
        out = excerpt(text, 13)
        assert out == "abcdefghij…"
        assert "\\al…" not in out

    def test_returns_empty_when_no_safe_cut_exists(self):
        # 先頭から1個の巨大な TeX トークンしかない → 壊れた TeX を出すくらいなら空。
        assert excerpt("\\" + "a" * 100, 10) == ""

    def test_cut_right_after_a_complete_token_is_allowed(self):
        text = r"\alpha " + "b" * 100
        out = excerpt(text, 12)
        assert out.startswith("\\alpha")
        assert out.endswith("…")


# ---------------------------------------------------------------------------
# first_sentence
# ---------------------------------------------------------------------------


class TestFirstSentence:
    def test_japanese(self):
        assert first_sentence("最初の文です。次の文です。") == "最初の文です。"

    def test_english(self):
        assert first_sentence("First one. Second one.") == "First one."

    def test_abbreviation_is_not_a_boundary(self):
        assert first_sentence("Fig. 5.2 shows the setup. Next.") == "Fig. 5.2 shows the setup."

    def test_no_boundary_returns_whole_text(self):
        assert first_sentence("no terminator here") == "no terminator here"

    def test_empty(self):
        assert first_sentence(None) == ""


# ---------------------------------------------------------------------------
# looks_like_tex_math — 移設後の挙動と既存 import 面
# ---------------------------------------------------------------------------


class TestLooksLikeTexMath:
    @pytest.mark.parametrize(
        "text",
        [
            r"\begin{aligned} x &= y \end{aligned}",
            r"\begin{pmatrix} a & b \end{pmatrix}",
            r"\frac{R_{\Lambda_c}}{R_{\Lambda_c}^{SM}} - \alpha_R \frac{R_D}{R_D^{SM}}",
        ],
    )
    def test_detects_tex(self, text):
        assert looks_like_tex_math(text)

    @pytest.mark.parametrize(
        "text",
        ["", "The sum rule provides a powerful cross-check of the data.", "δ(t,x) を定義する式"],
    )
    def test_prose_is_not_tex(self, text):
        assert not looks_like_tex_math(text)

    def test_course_content_builder_reexports_the_same_function(self):
        """移設しても既存 import 面（公開名・旧称）が壊れていないこと。"""
        from core import text_excerpt
        from core.course_content_builder import (  # noqa: PLC0415
            _looks_like_tex_math,
            looks_like_tex_math as reexported,
        )

        assert reexported is text_excerpt.looks_like_tex_math
        assert _looks_like_tex_math is text_excerpt.looks_like_tex_math

    def test_element_context_still_imports_via_course_content_builder(self):
        """学習者射影の import 経路（EC 設計書 §5.1）が生きていること。"""
        from core import element_context, text_excerpt

        assert element_context.looks_like_tex_math is text_excerpt.looks_like_tex_math


# ---------------------------------------------------------------------------
# CP5: 素スライスを新規に書かない足場が整っていること
# ---------------------------------------------------------------------------


class TestModuleIsPure:
    def test_module_has_no_db_or_framework_dependency(self):
        source = __import__("pathlib").Path(
            __import__("core.text_excerpt", fromlist=["__file__"]).__file__
        ).read_text(encoding="utf-8")
        for forbidden in ("fastapi", "sqlalchemy", "core.postgres", "openai"):
            assert forbidden not in source
