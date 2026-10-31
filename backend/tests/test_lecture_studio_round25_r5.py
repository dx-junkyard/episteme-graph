"""第 25 周 面 D 第 5 波（IK-0895 / IK-1008 / IK-1010 / IK-1011 / IK-0872 ② / IK-0878）の再現テスト。

- IK-0895 + IK-1008: 確からしさの印と表示の可否（壊れた復元は ``latex_withheld``）を、式の単位で
  ``learner_context_common.equation_display_fields`` の 1 関数から受け取る。記号の吹き出し・講義・
  本文の写し・根拠の式が同じ組を持ち、埋め込みは式の id の記録（根拠リンク ∪ content_blocks）で
  印を決める（描く LaTeX が根拠リンク側でも印が落ちない）。
- IK-1010: 教員が加えた資料（教材図）は解析の候補と区画を分け、教材欄に埋め込んだ順で先頭に。
- IK-1011: 申し送りが挙げる根拠は教員の画面で読める材料に限る（照合できない引用・引用の無い
  取り下げには「根拠の表示なし」の事実文）。
- IK-0872 ②: 付録の項目名は印字番号 / 定める記号から付け、本文が「追わない」と言う式・既に
  埋め込んだ式と同じ記号を定める式は足さない。
- IK-0878: 両方の欄に区切りを入れる 1 操作と、統合の告知の「揃える」導線。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import course_content_builder as ccb  # noqa: E402
from core import lecture  # noqa: E402
from core import lecture_studio_state as studio_state  # noqa: E402
from core import symbol_lookup  # noqa: E402
from core.label_vocab import RECONSTRUCTED_EQUATION_MARK, RECONSTRUCTED_EQUATION_NOTE  # noqa: E402
from core.learner_context_common import (  # noqa: E402
    equation_display_fields,
    is_broken_reconstructed_latex,
)

FRONTEND = ROOT / "frontend" / "public"
STUDIO_JS = (FRONTEND / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "js" / "app.js").read_text(encoding="utf-8")

BROKEN = "s = 1 Dkin � (2 -\\alpha B) � -H' aH2"
BULLETS = "• S8 = 0.823 ± 0.011 (GR), • S8 = 0.832"


def _fn(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    nxt = re.search(r"\n  function [A-Za-z0-9_]+\(", src[start + 10:])
    return src[start: start + 10 + nxt.start()] if nxt else src[start:]


# ---------------------------------------------------------------------------
# IK-0895 + IK-1008: 印と表示の可否は式の単位で 1 関数から
# ---------------------------------------------------------------------------


class TestEquationDisplayFields:
    def test_not_reconstructed_adds_nothing(self):
        assert equation_display_fields({"latex": "a=b"}) == {}

    def test_reconstructed_carries_mark_and_note(self):
        fields = equation_display_fields({"latex": "a=b", "reconstructed": True})
        assert fields == {
            "reconstructed": True,
            "reconstructed_mark": RECONSTRUCTED_EQUATION_MARK,
            "reconstructed_note": RECONSTRUCTED_EQUATION_NOTE,
        }

    def test_broken_reconstruction_is_withheld(self):
        fields = equation_display_fields({"latex": BROKEN, "reconstructed": True})
        assert fields["latex"] == "" and fields["latex_withheld"] is True
        assert fields["reconstructed_mark"] == RECONSTRUCTED_EQUATION_MARK

    def test_bullet_list_of_values_is_not_a_formula(self):
        assert is_broken_reconstructed_latex(BULLETS)

    def test_lecture_reexports_the_same_judgement(self):
        assert lecture.is_broken_reconstructed_latex(BROKEN)
        out = lecture.annotate_reconstructed_formulas([{"latex": BROKEN, "reconstructed": True}])
        assert out[0]["latex_withheld"] is True and out[0]["latex"] == ""


class TestSymbolLookupWithholds:
    def test_fallback_equation_withheld_but_listed(self):
        fields = symbol_lookup._reconstructed_mark_fields({"reconstructed": True}, latex=BROKEN)
        assert fields["latex_withheld"] is True and fields["latex"] == ""
        src = (BACKEND / "core" / "symbol_lookup.py").read_text(encoding="utf-8")
        assert 'if eq.get("latex") or eq.get("latex_withheld")' in src

    def test_mark_helper_goes_through_the_single_function(self):
        src = (BACKEND / "core" / "symbol_lookup.py").read_text(encoding="utf-8")
        body = src[src.index("def _reconstructed_mark_fields("): src.index("def _registry_latex_is_reconstructed(")]
        assert "equation_display_fields" in body
        assert "RECONSTRUCTED_EQUATION_MARK" not in body  # 印を自前で組まない


class TestEquationEmbedCarriesUnitMark:
    def test_embed_marked_by_block_record_even_when_link_latex_differs(self):
        topic = {
            "student_material": {"source_text": "作用は次。\n\n![[equation:eq_s]]\n"},
            "evidence_links": [
                {"kind": "equation", "target_id": "eq_s", "latex": r"S = \int d^4x\,\sqrt{-g}\left(L\right)"},
            ],
            "content_blocks": [{"type": "equations", "items": [
                {"equation_id": "eq_s", "latex": r"S = \int d^4 x \sqrt{-g} [ L ]", "reconstructed": True,
                 "raw_text": "S = Z d4x √−g"},
            ]}],
        }
        _display, _spoken, formulas, _figures = lecture.prepare_topic_slide_input(topic)
        assert formulas and formulas[0]["reconstructed"] is True
        assert formulas[0]["reconstructed_mark"] == RECONSTRUCTED_EQUATION_MARK

    def test_embed_not_marked_when_no_record_is_reconstructed(self):
        topic = {
            "student_material": {"source_text": "![[equation:eq_s]]"},
            "evidence_links": [{"kind": "equation", "target_id": "eq_s", "latex": "S = 1"}],
            "content_blocks": [{"type": "equations", "items": [{"equation_id": "eq_s", "latex": "S = 1"}]}],
        }
        _display, _spoken, formulas, _figures = lecture.prepare_topic_slide_input(topic)
        assert not formulas[0].get("reconstructed")
        assert "reconstructed_mark" not in formulas[0]

    def test_evidence_item_withholds_broken_reconstruction(self):
        topic = {
            "evidence_links": [{"kind": "equation", "target_id": "eq_b", "latex": BROKEN, "reconstructed": True}],
        }
        items = [i for i in ccb.build_topic_evidence_items(topic) if i.get("kind") == "equation"]
        assert items and items[0]["latex_withheld"] is True and items[0]["latex"] == ""


class TestFrontendWithheld:
    def test_material_renderer_does_not_draw_withheld_latex(self):
        body = _fn(APP_JS, "renderMaterialEquationBody")
        assert "formula.latex_withheld" in body
        assert body.index("formula.latex_withheld") < body.index("renderMaterialKatex(latex")

    def test_symbol_popover_lists_withheld_equations(self):
        assert "eq.latex || eq.latex_withheld" in APP_JS
        assert "definition.latex || definition.latex_withheld" in APP_JS


# ---------------------------------------------------------------------------
# IK-1010: 教員が加えた資料は区画を分ける
# ---------------------------------------------------------------------------


class TestTeacherAddedEvidence:
    def test_state_returns_separate_heading(self):
        items = [
            {"kind": "equation", "role": "equation"},
            {"kind": "figure", "role": "teaching_figure", "teaching": True},
        ]
        state = studio_state.topic_evidence_state(
            {"content_source": "source_excerpt", "content_confidence": "none"}, items
        )
        assert state["status"] == "missing"
        assert state["unmapped_heading"] == studio_state.EVIDENCE_UNMAPPED_HEADING
        assert state["teacher_added_heading"] == studio_state.EVIDENCE_TEACHER_ADDED_HEADING

    def test_teacher_figure_alone_does_not_change_status(self):
        state = studio_state.topic_evidence_state({}, [{"kind": "figure", "role": "teaching_figure"}])
        assert state["status"] == "none"
        assert "unmapped_heading" not in state
        assert state["teacher_added_heading"] == studio_state.EVIDENCE_TEACHER_ADDED_HEADING

    def test_studio_splits_sections_and_orders_by_embed_position(self):
        split = _fn(STUDIO_JS, "lsSplitTeacherAddedEvidence")
        assert '"![[figure:" + figId' in split
        chips = _fn(STUDIO_JS, "lsCourseEvidenceChipsHtml")
        assert "teacher_added_heading" in chips and "unmapped_heading" in chips
        assert chips.index("split.teacher") < chips.index("split.machine")
        pane = _fn(STUDIO_JS, "lsCourseEvidenceHtml")
        assert "lsSplitTeacherAddedEvidence(topic, items)" in pane


# ---------------------------------------------------------------------------
# IK-1011: 申し送りの根拠は教員の画面で読める材料だけ
# ---------------------------------------------------------------------------


class TestHandoffBasis:
    VISIBLE = ["前の版の注意点: 偏光と磁場の対応は原文に無い", "図1: 装置の配置"]

    def test_unverifiable_quote_is_marked(self):
        notes = studio_state.qualify_handoff_basis(
            ["論理ブロックの要約に「偏光は磁場と対応する」とあるため注意点を取り下げました"], self.VISIBLE
        )
        assert notes[0].endswith(studio_state.HANDOFF_UNVERIFIED_BASIS_NOTE)

    def test_removal_without_quote_is_marked(self):
        notes = studio_state.qualify_handoff_basis(["材料に記述があったため注意点を外しました"], self.VISIBLE)
        assert notes[0].endswith(studio_state.HANDOFF_UNVERIFIED_BASIS_NOTE)

    def test_verifiable_quote_is_kept_as_is(self):
        note = "「偏光と磁場の対応は原文に無い」は根拠リンクで確かめられないため外しました"
        assert studio_state.qualify_handoff_basis([note], self.VISIBLE) == [note]

    def test_plain_request_is_kept(self):
        note = "式 (9) の係数を原文で確認してください"
        assert studio_state.qualify_handoff_basis([note], self.VISIBLE) == [note]

    def test_visible_texts_exclude_grounding_only_material(self):
        topic = {"title": "t", "content_blocks": [{"type": "components", "items": [{"summary": "論理ブロックの要約"}]}]}
        texts = studio_state.teacher_visible_texts(topic, {"cautions": ["注意"]}, [])
        assert "論理ブロックの要約" not in "\n".join(texts)
        assert "注意" in texts

    def test_prompt_and_route_wire_the_rule(self):
        assert "逐語の引用" in ccb.TOPIC_DRAFT_HANDOFF_RULE
        assert "教員の画面に出ない材料は根拠に挙げない" in ccb.TOPIC_DRAFT_HANDOFF_RULE
        src = (BACKEND / "api" / "routes" / "lecture_studio" / "topics.py").read_text(encoding="utf-8")
        assert "studio_state.qualify_handoff_basis(" in src


# ---------------------------------------------------------------------------
# IK-0872 ②: 付録の名前と本文との整合
# ---------------------------------------------------------------------------


def _appendix(items, *, text, linked):
    topic = {"content_blocks": [{"type": "equations", "items": items}], "linked_equation_ids": list(linked)}
    result = {"student_material": {"source_text": text}, "cautions": []}
    ccb._ensure_required_equations_in_material(result, topic)
    return result["student_material"]["source_text"]


class TestAppendixNamesAndConsistency:
    ITEMS = [
        {"equation_id": "eq65", "latex": r"S = \int d^4x \sqrt{-g}(\sum_i \mathcal{L}_i)"},
        {"equation_id": "eq66", "latex": r"S = \int d^4 x"},
        {"equation_id": "eqL4", "latex": r"\mathcal{L}_4 = G_4 R"},
        {"equation_id": "eqc", "latex": r"c_s^2 = \alpha_B"},
    ]
    TEXT = "作用は次。\n\n![[equation:eq65]]\n\n個々の $\\mathcal{L}_i$ の形はここでは追わず、押さえてください。"

    def test_declined_family_and_duplicate_symbol_are_not_added(self):
        text = _appendix(self.ITEMS, text=self.TEXT, linked=["eq65", "eq66", "eqL4", "eqc"])
        assert "![[equation:eqL4]]" not in text
        assert "![[equation:eq66]]" not in text
        assert "この節の式" not in text

    def test_item_named_by_defined_symbol(self):
        text = _appendix(self.ITEMS, text=self.TEXT, linked=["eq65", "eq66", "eqL4", "eqc"])
        assert "- $c_s^2$ を定める式" in text
        assert "![[equation:eqc]]" in text

    def test_nameless_item_is_not_added(self):
        text = _appendix([{"equation_id": "eq_x", "latex": r"\int f\,dx"}], text="本文", linked=["eq_x"])
        assert "この節の式" not in text
        assert "![[equation:eq_x]]" not in text

    def test_defined_symbol_rejects_expressions(self):
        assert ccb._equation_defined_symbol({"latex": r"a + b = c"}) == ""
        assert ccb._equation_defined_symbol({"latex": r"\frac{a}{b} = c"}) == ""
        assert ccb._equation_defined_symbol({"latex": r"\mathcal{L}_4 = x"}) == r"\mathcal{L}_4"


# ---------------------------------------------------------------------------
# IK-0878: 両方の欄に区切り / 揃える導線
# ---------------------------------------------------------------------------


class TestSlideMarkerAlignment:
    def test_buttons_exist_with_anchors(self):
        assert 'data-ui-anchor="lecture-studio.insert-slide-marker-both"' in STUDIO_JS
        assert 'data-ui-anchor="lecture-studio.align-slide-markers" hidden' in STUDIO_JS

    def test_align_button_follows_mismatch(self):
        assert 'alignBtnEl.hidden = !(split.mismatch && !split.error)' in STUDIO_JS

    def test_align_keeps_display_and_rebuilds_spoken(self):
        body = _fn(STUDIO_JS, "lsAlignSpokenSlideMarkers")
        assert 'line.trim() !== "==="' in body  # 読み上げ欄の区切りを外してから入れ直す
        assert "off > 0 && off < display.length" in body  # 先頭・末尾は区切りにしない

    def test_anchors_registered(self):
        from core.help_kb import admin_ui_anchors as anchors

        for anchor in ("lecture-studio.insert-slide-marker-both", "lecture-studio.align-slide-markers"):
            assert anchor in anchors.KNOWN_ADMIN_UI_ANCHOR_IDS
            assert anchors.ADMIN_UI_ANCHORS[anchor].startswith("teacher/14-admin-lecture-studio.md#")


def test_cache_busting_versions():
    admin_html = (FRONTEND / "admin.html").read_text(encoding="utf-8")
    index_html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert re.search(r"admin-lecture-studio\.js\?v=[^\"]*studio5", admin_html)
    assert re.search(r"app\.js\?v=[^\"]*studio5", index_html)
