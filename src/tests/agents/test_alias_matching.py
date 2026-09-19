"""``agents/alias_matching.py``（語境界付き alias 照合）の単体テスト。

正本: `docs/architecture/knowledge_structure_review_2026-09-12.md` §4 Phase 0 の
P0-2（F-7 / K-3: 部分文字列一致で alias ``SM`` が ``cosmological`` に当たり、
原本に 0 回の "Standard Model" が成果へ注入された）。
"""
from __future__ import annotations

from episteme_graph.agents.alias_matching import (
    SHORT_ALIAS_MAX_LEN,
    alias_pattern,
    mentioned_canonicals,
    text_mentions_alias,
    text_mentions_any,
)


# --- 短い alias（3文字以下）は大文字小文字を区別した単語完全一致 -------------

def test_short_alias_does_not_match_inside_a_word():
    """F-7 の回帰: ``SM`` は ``cosmological`` の中に当たってはならない。"""
    assert not text_mentions_alias("cosmological constant", "SM")


def test_short_alias_matches_as_a_standalone_word():
    assert text_mentions_alias("the SM predicts", "SM")


def test_short_alias_is_case_sensitive():
    assert not text_mentions_alias("the sm predicts", "SM")
    assert not text_mentions_alias("The SM predicts", "sm")


def test_short_alias_matches_at_string_boundaries_and_punctuation():
    assert text_mentions_alias("SM", "SM")
    assert text_mentions_alias("beyond the SM.", "SM")
    assert text_mentions_alias("(SM)", "SM")
    assert not text_mentions_alias("SMEFT", "SM")
    assert not text_mentions_alias("BSM", "SM")


def test_short_alias_threshold_is_three_characters():
    assert SHORT_ALIAS_MAX_LEN == 3
    # 3文字までは大小区別
    assert not text_mentions_alias("the qcd sector", "QCD")
    assert text_mentions_alias("the QCD sector", "QCD")
    # 4文字からは大小無視
    assert text_mentions_alias("the lhcb detector", "LHCb")


# --- 長い alias は大小無視の語境界一致 ---------------------------------------

def test_long_alias_is_case_insensitive_on_word_boundaries():
    assert text_mentions_alias("the standard model predicts", "Standard Model")
    assert text_mentions_alias("The Standard Model predicts", "standard model")


def test_long_alias_does_not_match_a_longer_word():
    assert not text_mentions_alias("standard modeling of clouds", "standard model")
    assert not text_mentions_alias("nonstandard model", "standard model")


def test_alias_with_non_word_characters_is_escaped_not_treated_as_regex():
    assert text_mentions_alias("we measure B->D(*) decays", "B->D(*)")
    assert not text_mentions_alias("B to D decays", "B->D(*)")


# --- 空 alias は何にも当たらない ---------------------------------------------

def test_empty_alias_never_matches():
    assert not text_mentions_alias("anything at all", "")
    assert not text_mentions_alias("anything at all", "   ")
    assert alias_pattern("") is None
    assert alias_pattern("   ") is None


def test_empty_text_never_matches():
    assert not text_mentions_alias("", "SM")
    assert not text_mentions_any("", ["SM"])


# --- text_mentions_any -------------------------------------------------------

def test_text_mentions_any_accepts_a_bare_string():
    assert text_mentions_any("the SM predicts", "SM")
    assert not text_mentions_any("cosmological", "SM")


def test_text_mentions_any_is_a_disjunction():
    assert text_mentions_any("the QCD sector", ["SM", "QCD"])
    assert not text_mentions_any("the electroweak sector", ["SM", "QCD"])


def test_text_mentions_any_handles_empty_and_non_iterable_inputs():
    assert not text_mentions_any("the SM predicts", [])
    assert not text_mentions_any("the SM predicts", None)
    assert not text_mentions_any("the SM predicts", 42)


# --- mentioned_canonicals ----------------------------------------------------

_TABLE = {
    "Standard Model": ["SM", "standard-model"],
    "Quantum Chromodynamics": ["QCD"],
    "Dark Matter": ["DM"],
}


def test_mentioned_canonicals_returns_table_order_without_duplicates():
    text = "The QCD sector and the SM sector; again the SM and QCD."
    assert mentioned_canonicals(text, _TABLE) == [
        "Standard Model",
        "Quantum Chromodynamics",
    ]


def test_mentioned_canonicals_matches_the_canonical_name_itself():
    assert mentioned_canonicals("dark matter searches", _TABLE) == ["Dark Matter"]


def test_mentioned_canonicals_does_not_match_substrings():
    """``SM`` が ``cosmological`` に当たれば canonical が丸ごと誤注入される。"""
    assert mentioned_canonicals("cosmological constant", _TABLE) == []


def test_mentioned_canonicals_tolerates_odd_tables():
    assert mentioned_canonicals("the SM predicts", {"": ["SM"]}) == []
    assert mentioned_canonicals("the SM predicts", {"Standard Model": "SM"}) == [
        "Standard Model"
    ]
    assert mentioned_canonicals("the SM predicts", {"Standard Model": None}) == []
    assert mentioned_canonicals("", _TABLE) == []
    assert mentioned_canonicals("text", None) == []  # type: ignore[arg-type]
