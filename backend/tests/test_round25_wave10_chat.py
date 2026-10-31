"""第 25 周 第 10 波・横断（学習チャット）: 地図の場所の明示焦点・わたしの地図の画面文脈・記録の問いの閉世界・
選択箇所の式・出所の確定・走り込み見出し・意図分類の手がかり・英語の生成文の出所。"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.learning as L  # noqa: E402
from core.assistant_context import selection_formula_lines, render_selection_block  # noqa: E402
from core.assistant_context.resolvers import learning as R  # noqa: E402
from core.assistant_context.schema import ScreenContext  # noqa: E402
from core.focus_document import (  # noqa: E402
    SOURCE_EXPLICIT,
    SOURCE_TOPIC,
    prefer_focus,
    resolve_focus_document,
    with_screen_place_documents,
)

FRONT = BACKEND.parent / "frontend" / "public" / "js"


class _P:
    def __init__(self, selection=None, view=None):
        self._d = {"screen": "learning", "selection": selection or {}, "view": view or {}}

    def model_dump(self):
        return self._d


class _B:
    def __init__(self, selection=None, view=None, selection_text=None):
        self.screen_context = _P(selection, view)
        self.selection_text = selection_text


class TestAtlasPlaceSelectedNow:
    """製品頭脳 A 4307: 今の往復で選んだ地図の場所は、置かれた論文が明示の焦点（トピックの段を外す）。"""

    def test_selected_now_replaces_topic_tier(self):
        focus = resolve_focus_document(allowed_document_ids=["a", "b"], topic_document_ids=["a"])
        out = with_screen_place_documents(focus, ["b"], allowed_document_ids=["a", "b"], selected_now=True)
        assert out.document_ids == ("b",) and out.source == SOURCE_EXPLICIT

    def test_topic_rows_dropped_when_place_has_adoptable_rows(self):
        focus = with_screen_place_documents(
            resolve_focus_document(allowed_document_ids=["a", "b"], topic_document_ids=["a"]),
            ["b"], allowed_document_ids=["a", "b"], selected_now=True,
        )
        rows = [{"document_id": "a", "score": 0.45}, {"document_id": "b", "score": 0.5}]
        assert [r["document_id"] for r in prefer_focus(rows, focus)] == ["b"]

    def test_not_selected_now_keeps_appending(self):
        focus = resolve_focus_document(allowed_document_ids=["a", "b"], topic_document_ids=["a"])
        out = with_screen_place_documents(focus, ["b"], allowed_document_ids=["a", "b"])
        assert out.document_ids == ("a", "b") and out.source == SOURCE_TOPIC

    def test_selected_now_without_placed_paper_keeps_focus(self):
        focus = resolve_focus_document(allowed_document_ids=["a"], topic_document_ids=["a"])
        assert with_screen_place_documents(focus, ["z"], allowed_document_ids=["a"], selected_now=True) == focus

    def test_selection_marks_now(self):
        assert L._screen_atlas_node_selected_now(_B({"atlas_node_id": "c_de"}), course_id="c") is True
        assert L._screen_atlas_node_selected_now(_B(view={"atlas_node_id": "c_de"}), course_id="c") is False
        assert L._screen_atlas_node_id(_B({"atlas_node_id": "n1"}, {"atlas_node_id": "n0"}), course_id="c") == "n1"

    def test_app_js_drops_selection_after_turn(self):
        src = (FRONT / "app.js").read_text(encoding="utf-8")
        assert "selection.atlas_node_id" in src
        assert "state.atlasNodeFreshForTurn = false;" in src
        assert 'addEventListener("atlas:closed"' in src


class TestPersonalMapScreenContext:
    """IK-1113: わたしの地図で見ている場所の事実が画面文脈に届く。"""

    def _sources(self, view):
        return {"personal_map": {"node_id": "pn1", "view": view}}

    def test_range_view_facts(self):
        view = {"available": True, "mode": "range", "center": None, "range_documents": [
            {"title": "Neutron star EOS", "nodes": [
                {"label": "理論の土台", "touched": True,
                 "verification": {"label": "直接検証の記録あり", "scope_line": "密度 1〜2 倍の範囲"}},
                {"label": "theory_op_0002"},
            ]}], "facts": ["この記録は、トピック『EOS』での記録です。"]}
        ctx = ScreenContext(screen="learning", selection={"personal_map_node_id": "pn1"})
        facts = R._personal_map_node_facts(ctx.selection, self._sources(view))
        joined = "\n".join(facts)
        assert "範囲のビュー" in joined and "『理論の土台』（直接検証の記録あり・記録された条件: 密度 1〜2 倍の範囲）" in joined
        assert "theory_op_0002" not in joined

    def test_point_view_facts(self):
        view = {"available": True, "mode": "near", "center": {"label": "状態方程式"},
                "upstream": [{"label": "理論の土台"}], "downstream": [], "facts": []}
        facts = R._personal_map_node_facts({"personal_map_node_id": "pn1"}, self._sources(view))
        assert facts[0].startswith("学習者は「わたしの地図」で『状態方程式』を中心に")
        assert any("前提にしているもの: 『理論の土台』" in f for f in facts)

    def test_mismatched_node_ignored(self):
        assert R._personal_map_node_facts({"personal_map_node_id": "other"}, self._sources({"available": True})) == []

    def test_resolve_view_includes_personal_map(self):
        ctx = ScreenContext(screen="learning", selection={"personal_map_node_id": "pn1"}, view={"mode": "chat"})
        view = {"available": True, "mode": "near", "center": {"label": "状態方程式"}, "facts": []}
        assert any("わたしの地図" in f for f in R.resolve_view(ctx, self._sources(view)))

    def test_front_exposes_refs_only(self):
        src = (FRONT / "personal-map-home.js").read_text(encoding="utf-8")
        assert "getScreenContext," in src and "personal_map_node_id" in src
        assert "selection.personal_map_node_id" in (FRONT / "app.js").read_text(encoding="utf-8")


class TestVerificationRecordQuestion:
    """製品頭脳 A 4302: 記録の有無を問う発話では閉世界の拘束を常に載せる。"""

    def test_detector(self):
        assert L._asks_about_verification_record("この部品の検証の記録はある？")
        assert L._asks_about_verification_record("Has this been verified?")
        assert not L._asks_about_verification_record("ネットワークを二つに分ける理由は？")

    def test_core_applies_constraint_on_question(self):
        src = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
        assert "if _screen_has_ledger or _asks_about_verification_record(_turn_question):" in src


class TestSelectionFormulas:
    """製品頭脳 A 4313: 選択に出てくる式の LaTeX を選択ブロックに添える。"""

    def test_match_by_lhs_symbol(self):
        formulas = [
            {"latex": r"\mathcal{L}_{\rm TOV} = \sum r^2", "label": "式 (4)", "mark": "［AI復元］"},
            {"latex": "x = 1", "label": ""},
        ]
        lines = selection_formula_lines("the residual LTOV is minimized", formulas)
        assert lines == [r"式 (4)［AI復元］: $\mathcal{L}_{\rm TOV} = \sum r^2$"]

    def test_block_keeps_quote_and_appends(self):
        block = render_selection_block("abc", "abc", ["式 (4): $L=1$"])
        assert "> abc" in block and "- 式 (4): $L=1$" in block
        assert render_selection_block("abc", "abc") .count("式") == 0


class TestGroundingFromCitedAnswer:
    """judge doubt r10 STILL: 本文が出典を使っていないと言うなら「教材に基づく」と名乗らない。"""

    SOURCES = [{"index": 1, "origin": "course_material", "chunk_id": "c1"},
               {"index": 2, "origin": "other_material", "chunk_id": "c2"}]

    def test_disclaimer_without_citation(self):
        ans = "検索で出た出典は無関係なので使っていません。札の意味は地図のとおりです。"
        assert L._grounding_from_cited_answer(ans, self.SOURCES, "course_material", topic_material_grounds=True) == "model_generated"

    def test_cited_other_only(self):
        assert L._grounding_from_cited_answer("結果です[出典2]。", self.SOURCES, "course_material",
                                              topic_material_grounds=False) == "other_material"

    def test_no_citation_no_disclaimer_kept(self):
        assert L._grounding_from_cited_answer("教材のとおりです。", self.SOURCES, "course_material",
                                              topic_material_grounds=True) == "course_material"


class TestRunInHeadingAndHint:
    def test_run_in_heading(self):
        assert L._source_section_note({"text": "Four-phase training.—We train"}) == "・節「Four-phase training」"
        assert L._source_section_note({"text": "We train. — no"}) == ""

    def test_hint_prefers_selection_text_over_stale_atlas(self):
        body = _B({}, {"atlas_node_label": "暗黒エネルギー"}, selection_text="一節")
        assert "一節を選んで" in L._intent_screen_hint(body, course_id="c")
        body = _B({"atlas_node_id": "n"}, {"atlas_node_label": "暗黒エネルギー"}, selection_text="一節")
        assert "暗黒エネルギー" in L._intent_screen_hint(body, course_id="c")


class TestGeneratedEnglishNote:
    def test_role_note(self):
        ctx = {"instance": {"component": {"label": "TOV residual constraint"},
                            "in_paper": {"narrative_role": "Directly supports the central thesis of the paper"}}}
        facts = R._component_facts(ctx)
        assert any(R.GENERATED_ENGLISH_NOTE in f for f in facts)
