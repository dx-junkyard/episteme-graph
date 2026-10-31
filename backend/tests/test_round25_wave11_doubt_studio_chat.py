"""第 25 周 第 11 波・面 B / 面 D / 横断の取りこぼし（IK-1147 / 1148 / 1150 / 1157 / 1158 / 1144 再発 / B 4330）。"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.learning as L  # noqa: E402
import routes.doubt as D  # noqa: E402
import services  # noqa: E402
from core.doubt.schema import REACHABILITY_LABELS, REACHABILITY_LEVELS  # noqa: E402
from core.formula_facts import (  # noqa: E402
    OFFSCREEN_FORMULA_FACT_BLOCK_HEADING,
    OFFSCREEN_FORMULA_FACT_INSTRUCTION,
    offscreen_formula_fact_block,
)
from core.symbol_lookup import _mentions_symbol, _plain_symbol_re, symbol_key  # noqa: E402

ROOT = BACKEND.parent


class TestEnglishSourceDisclaim:  # IK-1150
    def test_passages_not_used(self):
        answer = ("The passages retrieved from the Cep B paper (2407.01221) are not about this map item, "
                  "so I have not used them here.")
        assert L._answer_disclaims_sources(answer)

    def test_without_using_the_sources(self):
        assert L._answer_disclaims_sources("I answer without using the retrieved passages.")

    def test_bare_paper_content_negation_is_not_a_disclaimer(self):
        assert not L._answer_disclaims_sources("The paper does not use a fixed grid.")

    def test_grounding_becomes_model_generated(self):
        answer = "The passages retrieved are not about this item, so I have not used them here."
        assert L._grounding_from_cited_answer(
            answer, [{"index": 1, "origin": "course_material"}], "course_material",
            topic_material_grounds=False,
        ) == "model_generated"


class TestUnrecordedLedgerLineForScreen:  # IK-1147
    def test_public_entry_returns_unrecorded_line(self, monkeypatch):
        monkeypatch.setattr(D, "ledger_target_label", lambda *a, **k: "")
        line = D.learner_unrecorded_ledger_line(object(), "claim", "x", document_id="d1")
        assert line["exists"] is False
        assert line["fact_line"]

    def test_document_required_target_without_document_is_none(self):
        assert D.learner_unrecorded_ledger_line(object(), "equation", "eq_3", document_id=None) is None

    def test_screen_path_falls_back(self):
        src = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
        assert "or learner_unrecorded_ledger_line(" in src


class TestReachabilityByDocument:  # IK-1148
    def test_value_and_label(self):
        assert "by_document" in REACHABILITY_LEVELS
        assert REACHABILITY_LABELS["by_document"] == "本文・文献の読み直しで確かめられる"

    def test_migration_check_matches_vocabulary(self):
        sql = (BACKEND / "db" / "097_verification_proposal_reachability_by_document.sql").read_text(encoding="utf-8")
        for level in REACHABILITY_LEVELS:
            assert f"'{level}'" in sql

    def test_js_mirror_and_manual(self):
        js = (ROOT / "frontend" / "public" / "js" / "doubt-atlas.js").read_text(encoding="utf-8")
        assert 'by_document: "本文・文献の読み直しで確かめられる"' in js
        manual = (ROOT / "docs" / "manual" / "teacher" / "18-admin-doubt-atlas.md").read_text(encoding="utf-8")
        assert "本文・文献の読み直しで確かめられる" in manual


class TestSplitSubscriptMention:  # IK-1157
    def _m(self, text, sym="N_c"):
        return _mentions_symbol(text, symbol_key(sym), _plain_symbol_re(sym))

    def test_split_forms(self):
        assert self._m("constraints at N c = 64 collocation points:")
        assert self._m("at Nc = 64 points")
        assert self._m("at $N_c=64$")

    def test_no_false_positive_on_words(self):
        assert not self._m("N cells are used")
        assert not self._m("the N c of the grid")


class TestOffscreenFormulaBlock:  # IK-1158
    def test_block_heading(self):
        block = offscreen_formula_fact_block("（この箇所の式: 式 (4)［AI復元］）")
        assert block.startswith(OFFSCREEN_FORMULA_FACT_BLOCK_HEADING)
        assert offscreen_formula_fact_block("") == ""

    def test_instruction_says_not_on_screen_and_no_number(self):
        assert "表示されていない" in OFFSCREEN_FORMULA_FACT_INSTRUCTION
        assert "番号で式を呼ばず" in OFFSCREEN_FORMULA_FACT_INSTRUCTION

    def test_shown_material_block_has_no_appended_fact_line(self):
        src = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
        assert '+ (f"\\n{_topic_formula_facts}" if _topic_formula_facts else "")' not in src
        assert "offscreen_formula_fact_block(_topic_formula_facts)" in src


class TestRunInHeadingInSourceList:  # IK-1144 再発
    def test_location_hint_uses_run_in_heading(self):
        hint = services.chunk_location_hint(
            section_title="", text="Four-phase training.—Naive joint optimisation fails because ..."
        )
        assert "節「Four-phase training」" in hint

    def test_source_note_and_list_share_one_rule(self):
        assert L._RUN_IN_HEADING_RE is services.RUN_IN_HEADING_RE


class TestIntentHintElementWithoutLabel:  # 製品頭脳 B 4330
    class _P:
        def __init__(self, selection):
            self._d = {"screen": "learning", "selection": selection, "view": {}}

        def model_dump(self):
            return self._d

    class _B:
        def __init__(self, payload):
            self.screen_context = payload
            self.selection_text = ""

    def test_element_id_without_label_still_hints(self):
        body = self._B(self._P({"course_id": "c1", "element_id": "comp_014", "element_type": "component"}))
        hint = L._intent_screen_hint(body, course_id="c1")
        assert "この部品" in hint
        assert "comp_014" not in hint
