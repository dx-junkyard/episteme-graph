"""figure_context (`core/document_pipeline/figure_context.py`) の純関数ユニットテスト。

設計: /Users/Shared/issues/apparatus_diagram_understanding_design.md §4-3
（G1: nearby_text 空 / G3: 文脈収集が図近傍限定、の2ギャップ解消の検証）。
DB / MinIO / LLM への実接続は行わない。

観点:
  - セクションスコープ収集（同一 section_id のみ・caption 自身除外）
  - caption 直近（前後2ブロック）の最優先採用と window 境界
  - 参照メンション（"Fig. N" / "Figure N" / "図N"、negative lookahead、前後1ブロック）
  - 図番号導出（figure_label / figure_key / どちらも無し）
  - 予算適用（max_items 超過での停止 / max_chars 超過での切り詰め・80文字閾値）
  - dedupe（caption 直近と参照メンションが同一ブロックを指しても重複しない）
  - 略語辞書（フル(略語) / 略語(フル) / 複数形 / initials 不一致の棄却 / first-wins）
  - inner_labels による図単位の絞り込み（完全一致・大小無視・prefix+3文字ルール）
  - inner_labels 無し時のフォールバック絞り込み（caption/nearby 出現のみ残す）
  - structure=None・blocks 空 → 例外にせず空の FigureContext
  - source_counts の内訳整合性
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SRC = ROOT / "src"
for _p in (str(BACKEND), str(BACKEND / "api"), str(SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.document_pipeline import figure_context as fc  # noqa: E402


def _block(
    text: str,
    *,
    page: int,
    order: int,
    block_id: str | None = None,
    block_type: str = "paragraph",
    section_id: str | None = None,
) -> dict:
    return {
        "block_id": block_id,
        "page": page,
        "order": order,
        "text": text,
        "block_type": block_type,
        "bbox": None,
        "section_id": section_id,
    }


def _structure(blocks: list[dict]) -> dict:
    return {"blocks": blocks}


# ---------------------------------------------------------------------------
# 図番号の導出
# ---------------------------------------------------------------------------


class TestDeriveFigureNumber:
    def test_from_figure_label(self):
        assert fc._derive_figure_number("Figure 5.2", None) == "5.2"

    def test_from_figure_label_simple_integer(self):
        assert fc._derive_figure_number("Fig. 12: overview", None) == "12"

    def test_from_figure_key_when_label_missing(self):
        assert fc._derive_figure_number(None, "fig_5_2") == "5.2"

    def test_none_when_neither_available(self):
        assert fc._derive_figure_number(None, "p3_i0") is None
        assert fc._derive_figure_number("", None) is None
        assert fc._derive_figure_number(None, None) is None


# ---------------------------------------------------------------------------
# structure が None / blocks 空 の縮退
# ---------------------------------------------------------------------------


class TestDegenerateInputs:
    def test_structure_none_returns_empty_context(self):
        ctx = fc.collect_figure_context(None, {"caption_block_id": "cap"})
        assert ctx.nearby_text == []
        assert ctx.abbreviations == {}
        assert ctx.source_counts == {
            "abbreviation_defs": 0,
            "caption_adjacent": 0,
            "reference_mentions": 0,
            "section_body": 0,
        }

    def test_empty_blocks_returns_empty_context(self):
        ctx = fc.collect_figure_context(_structure([]), {"caption_block_id": "cap"})
        assert ctx.nearby_text == []
        assert ctx.abbreviations == {}
        assert ctx.source_counts == {
            "abbreviation_defs": 0,
            "caption_adjacent": 0,
            "reference_mentions": 0,
            "section_body": 0,
        }


# ---------------------------------------------------------------------------
# セクションスコープ + caption 直近の優先順位・window 境界
# ---------------------------------------------------------------------------


class TestSectionScopeAndCaptionAdjacency:
    def test_section_scoped_residual_excludes_other_section_and_caption_self(self):
        blocks = [
            _block("Leading unrelated paragraph zero.", page=1, order=0, block_id="b0", section_id="sA"),
            _block(
                "Figure 9: some caption.", page=1, order=1, block_id="cap",
                block_type="figure_caption", section_id="sA",
            ),
            _block("Body text directly after caption two.", page=1, order=2, block_id="b2", section_id="sA"),
            _block("Body text three still in section sA.", page=1, order=3, block_id="b3", section_id="sA"),
            _block("Body four in section sA reached via residual pool.", page=1, order=4, block_id="b4", section_id="sA"),
            _block("Different section text that must not appear.", page=1, order=5, block_id="b5", section_id="sB"),
            _block("Far body six in section sA reached via residual pool.", page=1, order=6, block_id="b6", section_id="sA"),
        ]
        figure_row = {"caption_block_id": "cap", "caption_text": "Figure 9: some caption."}

        ctx = fc.collect_figure_context(_structure(blocks), figure_row)

        assert ctx.nearby_text == [
            "Leading unrelated paragraph zero.",
            "Body text directly after caption two.",
            "Body text three still in section sA.",
            "Body four in section sA reached via residual pool.",
            "Far body six in section sA reached via residual pool.",
        ]
        assert "Different section text that must not appear." not in ctx.nearby_text
        assert "Figure 9: some caption." not in ctx.nearby_text
        assert ctx.source_counts["caption_adjacent"] == 3
        assert ctx.source_counts["section_body"] == 2
        assert ctx.source_counts["reference_mentions"] == 0

    def test_caption_adjacent_window_is_exactly_two_before_and_after(self):
        blocks = [
            _block(f"Far pre block {i}.", page=1, order=i, block_id=f"pre{i}", section_id="sX")
            for i in range(2)
        ] + [
            _block(f"Near pre block {i}.", page=1, order=2 + i, block_id=f"near_pre{i}", section_id="sX")
            for i in range(2)
        ] + [
            _block("Figure 1: caption.", page=1, order=4, block_id="cap", block_type="figure_caption", section_id="sX"),
        ] + [
            _block(f"Near post block {i}.", page=1, order=5 + i, block_id=f"near_post{i}", section_id="sX")
            for i in range(2)
        ] + [
            _block(f"Far post block {i}.", page=1, order=7 + i, block_id=f"post{i}", section_id="sX")
            for i in range(2)
        ]
        figure_row = {"caption_block_id": "cap", "caption_text": "Figure 1: caption."}

        ctx = fc.collect_figure_context(_structure(blocks), figure_row)

        assert ctx.nearby_text == [
            "Near pre block 0.",
            "Near pre block 1.",
            "Near post block 0.",
            "Near post block 1.",
            "Far pre block 0.",
            "Far pre block 1.",
            "Far post block 0.",
            "Far post block 1.",
        ]
        assert ctx.source_counts["caption_adjacent"] == 4
        assert ctx.source_counts["section_body"] == 4


# ---------------------------------------------------------------------------
# 参照メンション
# ---------------------------------------------------------------------------


class TestReferenceMentions:
    def _figure_row(self, **overrides) -> dict:
        row = {"caption_block_id": "cap", "figure_label": "Figure 5.2", "caption_text": "Figure 5.2: caption."}
        row.update(overrides)
        return row

    def test_mention_pulls_in_hit_plus_neighbors_across_pages_and_sections(self):
        blocks = [
            _block("Figure 5.2: caption.", page=1, order=0, block_id="cap", block_type="figure_caption", section_id="s1"),
            _block("Filler paragraph unrelated one.", page=1, order=1, block_id="f1", section_id="s1"),
            _block("Filler paragraph unrelated two.", page=1, order=2, block_id="f2", section_id="s1"),
            _block("Prefix paragraph before the mention hit.", page=2, order=0, block_id="prefix", section_id="s2"),
            _block("As shown in Fig. 5.2, something happens here.", page=2, order=1, block_id="hit_en", section_id="s2"),
            _block("Suffix paragraph after the mention hit.", page=2, order=2, block_id="suffix", section_id="s2"),
            _block(
                "This paragraph wrongly mentions Fig. 5.23 and must be excluded due to lookahead.",
                page=3, order=0, block_id="false_positive", section_id="s3",
            ),
            _block("Buffer paragraph separating the false positive from the next real hit.", page=3, order=1, block_id="buffer", section_id="s3"),
            _block("日本語言及: 図5.2 のテストです。", page=4, order=0, block_id="hit_ja", section_id="s4"),
            _block("Trailing Japanese neighbor paragraph after hit.", page=4, order=1, block_id="ja_trailing", section_id="s4"),
        ]

        ctx = fc.collect_figure_context(_structure(blocks), self._figure_row())

        assert ctx.nearby_text == [
            "Filler paragraph unrelated one.",
            "Filler paragraph unrelated two.",
            "Prefix paragraph before the mention hit.",
            "As shown in Fig. 5.2, something happens here.",
            "Suffix paragraph after the mention hit.",
            "Buffer paragraph separating the false positive from the next real hit.",
            "日本語言及: 図5.2 のテストです。",
            "Trailing Japanese neighbor paragraph after hit.",
        ]
        assert "This paragraph wrongly mentions Fig. 5.23 and must be excluded due to lookahead." not in ctx.nearby_text
        assert ctx.source_counts["caption_adjacent"] == 2
        assert ctx.source_counts["reference_mentions"] == 6
        assert ctx.source_counts["section_body"] == 0

    def test_no_figure_number_skips_mention_search_entirely(self):
        blocks = [
            _block("Some caption without a parseable number.", page=1, order=0, block_id="cap", block_type="figure_caption", section_id="s1"),
            _block("Filler 1 same section directly after caption.", page=1, order=1, block_id="f1", section_id="s1"),
            _block("Filler 2 same section still adjacent window.", page=1, order=2, block_id="f2", section_id="s1"),
            _block("Filler 3 same section counts as body residual.", page=1, order=3, block_id="f3", section_id="s1"),
            _block(
                "As shown in Fig. 5.2, mention text here that should be skipped since no number is derivable.",
                page=2, order=0, block_id="other", section_id="s2",
            ),
        ]
        figure_row = {"caption_block_id": "cap", "caption_text": "Some caption without a parseable number."}

        ctx = fc.collect_figure_context(_structure(blocks), figure_row)

        assert ctx.nearby_text == [
            "Filler 1 same section directly after caption.",
            "Filler 2 same section still adjacent window.",
            "Filler 3 same section counts as body residual.",
        ]
        assert ctx.source_counts["reference_mentions"] == 0
        assert ctx.source_counts["section_body"] == 1

    def test_figure_number_present_pulls_in_cross_section_mention(self):
        blocks = [
            _block("Figure 5.2: caption.", page=1, order=0, block_id="cap", block_type="figure_caption", section_id="s1"),
            _block("Filler 1 same section directly after caption.", page=1, order=1, block_id="f1", section_id="s1"),
            _block("Filler 2 same section still adjacent window.", page=1, order=2, block_id="f2", section_id="s1"),
            _block("Filler 3 same section counts as body residual.", page=1, order=3, block_id="f3", section_id="s1"),
            _block(
                "As shown in Fig. 5.2, mention text here should now be pulled in.",
                page=2, order=0, block_id="other", section_id="s2",
            ),
        ]

        ctx = fc.collect_figure_context(_structure(blocks), self._figure_row())

        assert ctx.nearby_text == [
            "Filler 1 same section directly after caption.",
            "Filler 2 same section still adjacent window.",
            "Filler 3 same section counts as body residual.",
            "As shown in Fig. 5.2, mention text here should now be pulled in.",
        ]
        assert ctx.source_counts["caption_adjacent"] == 2
        assert ctx.source_counts["reference_mentions"] == 2
        assert ctx.source_counts["section_body"] == 0


# ---------------------------------------------------------------------------
# dedupe
# ---------------------------------------------------------------------------


class TestDedupe:
    def test_caption_adjacent_block_matching_mention_regex_is_not_duplicated(self):
        blocks = [
            _block("Figure 5.2: caption.", page=1, order=0, block_id="cap", block_type="figure_caption", section_id="s1"),
            _block(
                "Fig. 5.2 also appears again right here inside a caption-adjacent block.",
                page=1, order=1, block_id="b1", section_id="s1",
            ),
            _block("Second caption-adjacent block with no mention.", page=1, order=2, block_id="b2", section_id="s1"),
        ]
        figure_row = {"caption_block_id": "cap", "figure_label": "Figure 5.2", "caption_text": "Figure 5.2: caption."}

        ctx = fc.collect_figure_context(_structure(blocks), figure_row)

        assert ctx.nearby_text == [
            "Fig. 5.2 also appears again right here inside a caption-adjacent block.",
            "Second caption-adjacent block with no mention.",
        ]
        assert ctx.source_counts["caption_adjacent"] == 2
        assert ctx.source_counts["reference_mentions"] == 0
        assert ctx.source_counts["section_body"] == 0


# ---------------------------------------------------------------------------
# 予算適用
# ---------------------------------------------------------------------------


class TestBudget:
    def test_max_items_stops_collection(self):
        blocks = [
            _block("Figure 1: caption.", page=1, order=0, block_id="cap", block_type="figure_caption", section_id="s1"),
            _block("Post block one.", page=1, order=1, block_id="b1", section_id="s1"),
            _block("Post block two.", page=1, order=2, block_id="b2", section_id="s1"),
            _block("Residual block three.", page=1, order=3, block_id="b3", section_id="s1"),
            _block("Residual block four.", page=1, order=4, block_id="b4", section_id="s1"),
        ]
        figure_row = {"caption_block_id": "cap", "caption_text": "Figure 1: caption."}

        ctx = fc.collect_figure_context(_structure(blocks), figure_row, max_items=2)

        assert ctx.nearby_text == ["Post block one.", "Post block two."]
        assert ctx.source_counts["caption_adjacent"] == 2
        assert ctx.source_counts["section_body"] == 0

    def test_max_chars_skips_block_and_stops_when_remaining_below_threshold(self):
        blocks = [
            _block("Figure 1: caption.", page=1, order=0, block_id="cap", block_type="figure_caption", section_id="s1"),
            _block("A" * 50, page=1, order=1, block_id="b1", section_id="s1"),
            _block("B" * 100, page=1, order=2, block_id="b2", section_id="s1"),
            _block("C" * 3, page=1, order=3, block_id="b3", section_id="s1"),
        ]
        figure_row = {"caption_block_id": "cap", "caption_text": "Figure 1: caption."}

        ctx = fc.collect_figure_context(_structure(blocks), figure_row, max_items=10, max_chars=100)

        # remaining after "A"*50 = 50 < 80 threshold -> "B"*100 is skipped and collection stops
        # entirely, so "C"*3 (which would otherwise fit) is never considered.
        assert ctx.nearby_text == ["A" * 50]
        assert ctx.source_counts["caption_adjacent"] == 1
        assert ctx.source_counts["section_body"] == 0

    def test_max_chars_truncates_block_when_remaining_at_or_above_threshold(self):
        blocks = [
            _block("Figure 1: caption.", page=1, order=0, block_id="cap", block_type="figure_caption", section_id="s1"),
            _block("F" * 100, page=1, order=1, block_id="b1", section_id="s1"),
            _block("D" * 150, page=1, order=2, block_id="b2", section_id="s1"),
            _block("E" * 5, page=1, order=3, block_id="b3", section_id="s1"),
        ]
        figure_row = {"caption_block_id": "cap", "caption_text": "Figure 1: caption."}

        ctx = fc.collect_figure_context(_structure(blocks), figure_row, max_items=10, max_chars=200)

        # remaining after "F"*100 = 100 >= 80 threshold -> "D"*150 is truncated to 100 chars
        # and collection stops; "E"*5 is never considered even though it would technically fit.
        assert ctx.nearby_text == ["F" * 100, "D" * 100]
        assert ctx.source_counts["caption_adjacent"] == 2
        assert ctx.source_counts["section_body"] == 0


# ---------------------------------------------------------------------------
# 略語辞書の抽出
# ---------------------------------------------------------------------------


class TestAbbreviationExtraction:
    def test_full_then_abbreviation_pattern(self):
        blocks = [_block(
            "external cavity diode laser (ECDL) as tunable source.",
            page=1, order=0, block_id="b1",
        )]
        result = fc._extract_abbreviations(blocks)
        assert result["ECDL"] == "external cavity diode laser"

    def test_plural_registers_both_singular_and_plural_keys(self):
        blocks = [_block(
            "RF photodetectors (RFPDs) sit near the cavity output.",
            page=1, order=0, block_id="b1",
        )]
        result = fc._extract_abbreviations(blocks)
        assert result["RFPDs"] == "RF photodetectors"
        assert result["RFPD"] == "RF photodetectors"

    def test_abbreviation_then_full_pattern(self):
        blocks = [_block(
            "ECDL (external cavity diode laser) provides the tunable source.",
            page=1, order=0, block_id="b1",
        )]
        result = fc._extract_abbreviations(blocks)
        assert result["ECDL"] == "external cavity diode laser"

    def test_initials_mismatch_is_rejected(self):
        blocks = [_block(
            "the previous section (ECDL) for reference.",
            page=1, order=0, block_id="b1",
        )]
        result = fc._extract_abbreviations(blocks)
        assert "ECDL" not in result

    def test_first_definition_wins_for_duplicate_key(self):
        blocks = [
            _block(
                "external cavity diode laser (ECDL) as tunable source.",
                page=1, order=0, block_id="b1",
            ),
            _block(
                "Every clean diode laser (ECDL) was also considered as an alternative.",
                page=1, order=1, block_id="b2",
            ),
        ]
        result = fc._extract_abbreviations(blocks)
        assert result["ECDL"] == "external cavity diode laser"

    # 文中埋め込み定義: pattern1 の group(1) は先行する無関係な語まで飲み込むが、
    # _trim_full_to_match が initials の一致する最長接尾辞へ決定論的に縮める。
    def test_mid_sentence_definition_is_trimmed_to_matching_suffix(self):
        blocks = [_block(
            "The setup uses an external cavity diode laser (ECDL) to probe the cavity.",
            page=1, order=0, block_id="b1",
        )]
        result = fc._extract_abbreviations(blocks)
        assert result["ECDL"] == "external cavity diode laser"

    def test_mid_sentence_plural_definition_registers_both_keys(self):
        blocks = [_block(
            "In this scheme two RF photodetectors (RFPDs) are used for detection.",
            page=1, order=0, block_id="b1",
        )]
        result = fc._extract_abbreviations(blocks)
        assert result["RFPDs"] == "RF photodetectors"
        assert result["RFPD"] == "RF photodetectors"

    def test_mid_sentence_with_no_matching_suffix_is_rejected(self):
        blocks = [_block(
            "This was discussed in the previous section (ECDL) for reference.",
            page=1, order=0, block_id="b1",
        )]
        result = fc._extract_abbreviations(blocks)
        assert "ECDL" not in result


class TestInitialsMatch:
    def test_matching_acronym(self):
        assert fc._initials_match("external cavity diode laser", "ECDL") is True

    def test_first_letter_mismatch_rejected(self):
        assert fc._initials_match("the previous section", "ECDL") is False

    def test_subsequence_not_present_rejected(self):
        assert fc._initials_match("elephant", "ECDL") is False


# ---------------------------------------------------------------------------
# 図ごとの絞り込み（inner_labels 有無）
# ---------------------------------------------------------------------------


class TestFilterAbbreviationsForFigure:
    def test_inner_labels_exact_and_prefix_rules(self):
        abbreviations = {
            "EOM": "electro-optic modulator",
            "PD": "photodetector",
            "UNRELATED": "something else entirely",
        }
        inner_labels = [{"text": "EOM"}, {"text": "PDaux"}]

        filtered = fc._filter_abbreviations_for_figure(abbreviations, inner_labels, "", [])

        assert filtered == {"EOM": "electro-optic modulator", "PD": "photodetector"}

    def test_inner_labels_prefix_rule_handles_multiple_suffixes(self):
        abbreviations = {"RFPD": "RF photodetector"}

        filtered_s = fc._filter_abbreviations_for_figure(abbreviations, [{"text": "RFPDs"}], "", [])
        filtered_p = fc._filter_abbreviations_for_figure(abbreviations, [{"text": "RFPDp"}], "", [])

        assert filtered_s == {"RFPD": "RF photodetector"}
        assert filtered_p == {"RFPD": "RF photodetector"}

    def test_inner_labels_case_insensitive_exact_match(self):
        abbreviations = {"EOM": "electro-optic modulator"}
        filtered = fc._filter_abbreviations_for_figure(abbreviations, [{"text": "eom"}], "", [])
        assert filtered == {"EOM": "electro-optic modulator"}

    def test_fallback_without_inner_labels_keeps_only_caption_or_nearby_occurrences(self):
        abbreviations = {
            "ECDL": "external cavity diode laser",
            "EOM": "electro-optic modulator",
        }
        caption_text = "Figure 5.2: setup using an ECDL as source."
        nearby_text = ["The EOM performs phase modulation."]

        filtered = fc._filter_abbreviations_for_figure(abbreviations, None, caption_text, nearby_text)

        assert filtered == abbreviations

    def test_fallback_without_inner_labels_drops_unmentioned_keys(self):
        filtered = fc._filter_abbreviations_for_figure(
            {"XYZ": "something never mentioned anywhere nearby"},
            None,
            "Figure 5.2: setup using an ECDL as source.",
            ["The EOM performs phase modulation."],
        )
        assert filtered == {}


# ---------------------------------------------------------------------------
# 統合: source_counts の整合性 + 略語辞書の図単位絞り込み込み
# ---------------------------------------------------------------------------


class TestIntegrationWithAbbreviations:
    def test_abbreviations_are_filtered_by_inner_labels_end_to_end(self):
        blocks = [
            _block(
                "Figure 5.2: caption.", page=1, order=0, block_id="cap",
                block_type="figure_caption", section_id="s1",
            ),
            _block(
                "external cavity diode laser (ECDL) is the tunable source.",
                page=1, order=1, block_id="b1", section_id="s1",
            ),
            _block(
                "An unrelated acronym appears here too (XYZQ) for testing purposes only.",
                page=1, order=2, block_id="b2", section_id="s1",
            ),
        ]
        figure_row = {"caption_block_id": "cap", "figure_label": "Figure 5.2", "caption_text": "Figure 5.2: caption."}
        inner_labels = [{"text": "ECDL"}]

        ctx = fc.collect_figure_context(_structure(blocks), figure_row, inner_labels=inner_labels)

        assert ctx.abbreviations == {"ECDL": "external cavity diode laser"}
        assert ctx.source_counts["abbreviation_defs"] == 1

    def test_source_counts_total_matches_nearby_text_length(self):
        blocks = [
            _block(
                "Figure 5.2: caption.", page=1, order=0, block_id="cap",
                block_type="figure_caption", section_id="s1",
            ),
            _block("Post block one.", page=1, order=1, block_id="b1", section_id="s1"),
            _block("Post block two.", page=1, order=2, block_id="b2", section_id="s1"),
            _block("Residual block three.", page=1, order=3, block_id="b3", section_id="s1"),
        ]
        figure_row = {"caption_block_id": "cap", "caption_text": "Figure 5.2: caption."}

        ctx = fc.collect_figure_context(_structure(blocks), figure_row)

        assert (
            ctx.source_counts["caption_adjacent"]
            + ctx.source_counts["reference_mentions"]
            + ctx.source_counts["section_body"]
        ) == len(ctx.nearby_text)


class TestBrokenSpacingMentions:
    """GROBID 由来の図番号スペース混入表記（"Fig. 3 .3"）の回帰テスト。

    実データ（アクシオン論文 document 07699cf5、本文92ブロック中87件が壊れ表記）で
    参照メンション検出が全滅していたバグの再発ガード。crosslink.py 側の同種修正と
    同じ規約（番号内 ``.`` の前後空白許容 + 否定先読み ``(?!\\.?[0-9])``）。
    """

    def _figure_row(self) -> dict:
        return {
            "caption_block_id": "cap",
            "figure_label": "Figure 3.3",
            "caption_text": "Figure 3.3: sensitivity.",
        }

    def _blocks(self, mention_text: str) -> list[dict]:
        # caption 直後2ブロックは caption_adjacent として先に消費されるため、
        # メンションブロックはフィラー2つを挟んで adjacent 窓の外に置く。
        return [
            _block("Figure 3.3: sensitivity.", page=1, order=0, block_id="cap",
                   block_type="figure_caption", section_id="s1"),
            _block("Filler paragraph one.", page=1, order=1, block_id="f1", section_id="s1"),
            _block("Filler paragraph two.", page=1, order=2, block_id="f2", section_id="s1"),
            _block(mention_text, page=2, order=0, block_id="hit", section_id="s2"),
        ]

    def test_space_before_decimal_point_matches(self):
        ctx = fc.collect_figure_context(
            _structure(self._blocks(
                "The parameter dependence of the sensitivity is summarized in Fig. 3 .3."
            )),
            self._figure_row(),
        )
        assert ctx.source_counts["reference_mentions"] == 1

    def test_space_after_decimal_point_matches(self):
        ctx = fc.collect_figure_context(
            _structure(self._blocks("As shown in Fig. 3. 3, the response scales.")),
            self._figure_row(),
        )
        assert ctx.source_counts["reference_mentions"] == 1

    def test_sentence_ending_period_after_number_matches(self):
        ctx = fc.collect_figure_context(
            _structure(self._blocks("The setup is illustrated in Fig. 3.3.")),
            self._figure_row(),
        )
        assert ctx.source_counts["reference_mentions"] == 1

    def test_longer_number_still_excluded(self):
        ctx = fc.collect_figure_context(
            _structure(self._blocks("Unrelated statistics appear in Fig. 3 .35 only.")),
            self._figure_row(),
        )
        assert ctx.source_counts["reference_mentions"] == 0
