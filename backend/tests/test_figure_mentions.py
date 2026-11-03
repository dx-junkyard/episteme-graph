"""図を参照する本文の文（メンション文）の抽出 ``core/deliberation/figure_mentions.py``。

`docs/features/element_context_presentation_redesign.md` §11（RC-F2 / RC-F6）。
図番号の導出とメンション正規表現の断片は ``figure_context`` の同名ヘルパーを使い、
文分割は ``text_excerpt.split_sentences``（文境界の唯一実装）に相乗りする。
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.deliberation import figure_mentions as FM  # noqa: E402
from core.deliberation.figure_mentions import (  # noqa: E402
    find_figure_mentions,
    figure_mention_pattern,
    mention_block_ids,
)
from tests.guardrail_helpers import assert_module_tree_does_not_import  # noqa: E402

_MODULE_PATH = Path(FM.__file__)

_SENTENCE = "Figure 1 .1 shows a classification of dark matter candidates by mass, including WIMPs."


def _structure(*blocks, sections=None):
    return {
        "blocks": list(blocks),
        "sections": sections
        if sections is not None
        else [{"section_id": "s-cand", "title": " Candidates for dark matter "}],
    }


def _body(block_id, text, *, page=2, order=1, section_id="s-cand", block_type="body_paragraph"):
    return {
        "block_id": block_id, "block_type": block_type, "page": page, "order": order,
        "section_id": section_id, "text": text,
    }


_FIG = {"figure_label": "Figure 1.1", "figure_key": "fig_1_1", "caption_block_id": "cap"}


class TestFindFigureMentions:
    def test_finds_the_grobid_spaced_sentence_verbatim(self):
        structure = _structure(
            _body("b1", "Dark matter is abundant. " + _SENTENCE + " The mass range is wide.")
        )
        mentions, notes = find_figure_mentions(structure, _FIG)
        assert notes == []
        assert mentions == [{
            "text": _SENTENCE,
            "section_label": "Candidates for dark matter",
            "section_id": "s-cand",
            "block_id": "b1",
            "page": 2,
        }]

    def test_sentence_is_not_truncated(self):
        long_sentence = "As shown in Fig. 1.1, " + "the spectrum extends " * 30 + "to high masses."
        mentions, _ = find_figure_mentions(_structure(_body("b1", long_sentence)), _FIG)
        assert mentions[0]["text"] == " ".join(long_sentence.split())
        assert "…" not in mentions[0]["text"]

    def test_caption_block_is_skipped(self):
        structure = _structure(
            _body("cap", "Figure 1.1 shows the candidates in a caption-like paragraph."),
            _body("b2", "Nothing about figures here."),
        )
        assert find_figure_mentions(structure, _FIG) == ([], [])

    def test_only_body_paragraphs_are_scanned(self):
        structure = _structure(
            _body("h", "Figure 1.1 in a heading.", block_type="section_heading"),
            _body("c", "Figure 1.1: caption text.", block_type="figure_caption"),
        )
        assert find_figure_mentions(structure, _FIG) == ([], [])

    def test_longer_figure_numbers_do_not_match(self):
        structure = _structure(_body("b1", "See Fig. 1.10 for details. Figure 1.12 too. Fig. 11.1 also."))
        assert find_figure_mentions(structure, _FIG) == ([], [])

    def test_spaced_continuation_is_a_longer_number(self):
        """GROBID の ``Figure 1 .1`` は図 1.1 であって図 1 ではない。"""
        structure = _structure(_body("b1", _SENTENCE))
        assert find_figure_mentions(structure, {"figure_label": "Figure 1", "figure_key": "fig_1"}) == ([], [])

    def test_appendix_letter_is_kept(self):
        structure = _structure(
            _body("b1", "Figure 8 .1 shows the main result.", order=1),
            _body("b2", "Figure C.8 shows the appendix limits.", order=2),
        )
        for row in ({"figure_label": "Figure C.8"}, {"figure_key": "fig_c_8"}):
            mentions, _ = find_figure_mentions(structure, row)
            assert [m["block_id"] for m in mentions] == ["b2"], row

    def test_caption_duplicated_into_a_body_paragraph_is_not_a_mention(self):
        structure = _structure(
            _body("b1", "Figure 6 .17: Upper limits on the coupling constant. As Fig. 6.17 shows, it improves.")
        )
        mentions, _ = find_figure_mentions(structure, {"figure_label": "Figure 6.17"})
        assert [m["text"] for m in mentions] == ["As Fig. 6.17 shows, it improves."]

    def test_number_from_figure_key_when_label_is_missing(self):
        structure = _structure(_body("b1", "Figs. 5.21 and 5.22 compare the layouts."))
        mentions, _ = find_figure_mentions(structure, {"figure_key": "fig_5_21"})
        assert [m["text"] for m in mentions] == ["Figs. 5.21 and 5.22 compare the layouts."]

    def test_japanese_mention(self):
        structure = _structure(_body("b1", "結果を示す。図1.1に候補を示す。以上。"))
        mentions, _ = find_figure_mentions(structure, _FIG)
        assert [m["text"] for m in mentions] == ["図1.1に候補を示す。"]

    def test_document_order_by_page_then_order_and_section_resolution(self):
        structure = _structure(
            _body("late", "Later, Figure 1.1 is revisited.", page=5, order=0, section_id="s-late"),
            _body("early", "Early, Fig. 1.1 is introduced.", page=1, order=3, section_id="s-cand"),
            _body("orphan", "Fig. 1.1 without a known section.", page=6, order=0, section_id="s-missing"),
            sections=[
                {"section_id": "s-cand", "title": "Candidates"},
                {"section_id": "s-late", "title": "Discussion"},
            ],
        )
        mentions, _ = find_figure_mentions(structure, _FIG)
        assert [(m["block_id"], m["section_label"]) for m in mentions] == [
            ("early", "Candidates"), ("late", "Discussion"), ("orphan", ""),
        ]

    def test_identical_sentences_are_deduplicated(self):
        structure = _structure(
            _body("b1", "Fig. 1.1 shows the setup.", order=1),
            _body("b2", "Fig. 1.1 shows the setup.", order=2),
        )
        mentions, _ = find_figure_mentions(structure, _FIG)
        assert len(mentions) == 1
        assert mentions[0]["block_id"] == "b1"

    def test_cap_and_note(self):
        blocks = [_body(f"b{i}", f"Fig. 1.1 case number {i} is here.", order=i) for i in range(9)]
        mentions, notes = find_figure_mentions(_structure(*blocks), _FIG, limit=6)
        assert len(mentions) == 6
        assert notes == ["本文での言及に他 3 件あるが表示上限のため省略しました"]

    def test_no_number_or_bad_input_is_empty(self):
        assert find_figure_mentions(_structure(_body("b1", "Fig. 1.1 x.")), {}) == ([], [])
        assert find_figure_mentions(None, _FIG) == ([], [])
        assert find_figure_mentions({"blocks": "broken"}, _FIG) == ([], [])

    def test_input_is_not_mutated(self):
        structure = _structure(_body("b1", "Fig. 1.1 shows it."))
        before = repr(structure)
        find_figure_mentions(structure, _FIG)
        assert repr(structure) == before

    def test_mention_block_ids(self):
        structure = _structure(_body("b1", "Fig. 1.1 a."), _body("b2", "Fig. 1.1 b.", order=2))
        mentions, _ = find_figure_mentions(structure, _FIG)
        assert mention_block_ids(mentions) == {"b1", "b2"}
        assert mention_block_ids(None) == set()

    def test_pattern_shape_matches_crosslink(self):
        pattern = figure_mention_pattern(_FIG)
        assert pattern.search("figures 1.1 and 1.2")
        assert pattern.search("Fig 1.1")
        assert pattern.search("in Fig. 1.1.")
        assert not pattern.search("Fig. 1.10")


class TestModuleIsPure:
    def test_no_framework_db_or_llm_imports(self):
        assert_module_tree_does_not_import(
            [_MODULE_PATH.parent], ["fastapi", "sqlalchemy", "core.postgres", "core.llm", "openai"],
            glob=_MODULE_PATH.name,
        )

    def test_reuses_the_figure_context_helpers_and_sentence_splitter(self):
        source = _MODULE_PATH.read_text(encoding="utf-8")
        assert "from core.document_pipeline.figure_context import _derive_figure_number, _number_pattern_fragment" in source
        assert "split_sentences" in source
        assert "def _derive_figure_number" not in source
        assert "def _number_pattern_fragment" not in source
