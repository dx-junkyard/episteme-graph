"""第 25 周 面 D 第 6 波（IK-0895 / IK-1032 / IK-1033 / IK-1034 / IK-0976 の残り / IK-1010 の残り +
オーナー判断 1・2）の再現テスト。

- IK-0895: 凍結した写し（content_blocks の式・根拠リンク）に復元の状態が無い旧 freeze でも、生きた式の
  記録（採用 run の equation_semantics artifact・chunks.formulas）の索引から読み時に引き、本文・講義・
  付録・部品の文脈・チャットの 5 か所が同じ 1 関数（``equation_is_reconstructed``）の答えを使う。
  新しい freeze は写しに ``reconstruction_checked`` を書く。
- IK-1032: ［付録を付け直す］の結果の分岐（replaced / removed / added / unchanged）ごとの知らせ。
- IK-1033: 記号の吹き出しの定義の文に添える式は、その記号を左辺で定める式だけ。
- IK-1034: 「トピック概要」は 1 項目（別名は ``alt_ids``）。
- IK-0976 の残り: 見出しの直後の導入 1 文は本体と、式の直後の解説 1 文は式と結ぶ。
- IK-1010 の残り: 根拠ありのトピックでも、教員の資料と並ぶ解析の候補の区画に見出し。
- オーナー判断 1: 保存済みの付録は書き直さない。所有者の［付録をまとめて付け直す］（decision_context）。
- オーナー判断 2: 音声生成は所有者だけ（editor には保存と同じ理由文）。
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
from core import decision_context  # noqa: E402
from core import lecture  # noqa: E402
from core import lecture_studio_state as studio_state  # noqa: E402
from core import symbol_lookup  # noqa: E402
from core.formula_facts import formula_fact_line  # noqa: E402
from core.label_vocab import RECONSTRUCTED_EQUATION_MARK, RECONSTRUCTED_EQUATION_NOTE  # noqa: E402
from core.learner_context_common import (  # noqa: E402
    RECONSTRUCTION_CHECKED_KEY,
    build_equation_reconstruction_index,
    equation_display_fields,
    equation_is_reconstructed,
    overlay_equation_reconstruction,
    reconstructed_equation_note,
)
from tests.guardrail_helpers import extract_function_source  # noqa: E402


def _js_fn(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    nxt = re.search(r"\n  function [A-Za-z0-9_]+\(", src[start + 10:])
    return src[start: start + 10 + nxt.start()] if nxt else src[start:]

FRONTEND = ROOT / "frontend" / "public"
STUDIO_JS = (FRONTEND / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "js" / "app.js").read_text(encoding="utf-8")
LEARNING_SRC = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
LECTURE_ROUTE_SRC = (BACKEND / "api" / "routes" / "lecture.py").read_text(encoding="utf-8")
SERVICES_SRC = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")
COMPONENT_CONTEXT_SRC = (BACKEND / "core" / "component_context.py").read_text(encoding="utf-8")
TOPICS_SRC = (BACKEND / "api" / "routes" / "lecture_studio" / "topics.py").read_text(encoding="utf-8")
SCRIPTS_SRC = (BACKEND / "api" / "routes" / "lecture_studio" / "scripts.py").read_text(encoding="utf-8")

DOC = "11111111-1111-1111-1111-111111111111"
OTHER_DOC = "22222222-2222-2222-2222-222222222222"
LIVE_EQ4 = {
    "equation_id": "eq_4",
    "label": "4",
    "reconstruction": {"status": "inferred", "latex": r"L_{\mathrm{TOV}} = \sum_i r_i^2"},
    "source_extraction": {"latex": r"L_TOV = sum r"},
}
COPY_EQ4 = {"equation_id": "eq_4", "label": "4", "latex": r"L_{\mathrm{TOV}} = \sum_i r_i^2", "raw_text": "L"}


def _index(records_by_doc=None):
    return build_equation_reconstruction_index(records_by_doc or {DOC: [LIVE_EQ4]})


def _topic(text="損失は次。\n\n![[equation:eq_4]]\n"):
    return {
        "student_material": {"source_text": text},
        "evidence_links": [{"kind": "equation", "target_id": "eq_4", "latex": COPY_EQ4["latex"], "document_id": DOC}],
        "content_blocks": [{"type": "equations", "items": [dict(COPY_EQ4)]}],
    }


# ---------------------------------------------------------------------------
# IK-0895: 生きた式の記録から読み時に引く
# ---------------------------------------------------------------------------


class TestReconstructionIndex:
    def test_frozen_copy_alone_has_no_mark(self):
        """旧 freeze の写しは状態を持たない（第 6 周の 253 件 0 件の形）。"""
        _d, _s, formulas, _f = lecture.prepare_topic_slide_input(_topic())
        assert not formulas[0].get("reconstructed")

    def test_lookup_by_id_number_and_latex(self):
        index = _index()
        assert index.lookup({"equation_id": "eq_4"}) is True
        assert index.lookup({"target_id": "equation:eq_4"}) is True
        assert index.lookup({"label": "(4)"}) is True
        assert index.lookup({"latex": r"L_{\mathrm{TOV}}  =  \sum_i r_i^2"}) is True
        assert index.lookup({"equation_id": "eq_99"}) is None

    def test_ambiguous_id_across_documents_falls_through(self):
        index = _index({DOC: [LIVE_EQ4], OTHER_DOC: [{"equation_id": "eq_4", "latex": "x = 1"}]})
        assert index.lookup({"equation_id": "eq_4", "document_id": DOC}) is True
        assert index.lookup({"equation_id": "eq_4", "document_id": OTHER_DOC}) is False
        # 論文が分からず id の答えが割れる → LaTeX の鍵で決める
        assert index.lookup({"equation_id": "eq_4", "latex": COPY_EQ4["latex"]}) is True

    def test_chunk_formula_flag_counts(self):
        index = _index({DOC: [{"id": "eq_9", "latex": "a=b+c", "reconstructed": True, "raw_text": "a b c"}]})
        assert index.lookup({"equation_id": "eq_9"}) is True

    def test_overlay_does_not_mutate_and_marks_copies(self):
        topic = _topic()
        out = overlay_equation_reconstruction(topic, _index())
        assert "reconstructed" not in topic["content_blocks"][0]["items"][0]
        item = out["content_blocks"][0]["items"][0]
        assert item["reconstructed"] is True and item[RECONSTRUCTION_CHECKED_KEY] is True
        assert out["evidence_links"][0][RECONSTRUCTION_CHECKED_KEY] is True

    def test_live_record_wins_over_frozen_state(self):
        topic = _topic()
        topic["content_blocks"][0]["items"][0][RECONSTRUCTION_CHECKED_KEY] = True  # 凍結時は「復元でない」
        out = overlay_equation_reconstruction(topic, _index())
        assert equation_is_reconstructed(out["content_blocks"][0]["items"][0]) is True

    def test_checked_state_is_not_reopened_by_source_comparison(self):
        """決まった答えは、写しの LaTeX と原文の照合で引き直さない。"""
        item = {"latex": "x=1", "raw_text": "x=1", "reconstructed": True, RECONSTRUCTION_CHECKED_KEY: True}
        assert equation_is_reconstructed(item) is True


class TestFivePlacesShareOneAnswer:
    """本文・講義・付録・部品の文脈・チャットが同じ ``equation_is_reconstructed`` の答え。"""

    def test_body_lecture_and_appendix(self):
        text = (
            "損失は次。\n\n![[equation:eq_4]]\n\n"
            + ccb.GENERATED_EQUATIONS_HEADING + "\n- 式 (4)\n![[equation:eq_4]]\n"
        )
        topic = overlay_equation_reconstruction(_topic(text), _index())
        slides, _display, _spoken, formulas = lecture.build_topic_slides(topic)
        assert slides and formulas
        assert all(f.get("reconstructed") for f in formulas)
        assert all(f.get("reconstructed_mark") == RECONSTRUCTED_EQUATION_MARK for f in formulas)
        assert all(RECONSTRUCTION_CHECKED_KEY not in f for f in formulas)  # 内部の印は画面に出さない

    def test_evidence_items(self):
        topic = overlay_equation_reconstruction(_topic(), _index())
        items = [i for i in ccb.build_topic_evidence_items(topic) if i.get("kind") == "equation"]
        assert items and all(i.get("reconstructed_mark") == RECONSTRUCTED_EQUATION_MARK for i in items)

    def test_component_context(self):
        from core import component_context

        # 記録自身には状態が無い（旧い artifact の形）が、chunks.formulas が復元と言う。
        record = {"equation_id": "eq_7", "label": "7", "source_extraction": {"latex": "y = 2"}}
        index = _index({DOC: [record, {"id": "eq_7", "latex": "y = 2x", "reconstructed": True}]})
        items = component_context._build_equations(
            {"linked_equation_ids": ["eq_7"]}, {"eq_7": record}, index, DOC
        )
        assert items[0]["label_note"] == RECONSTRUCTED_EQUATION_NOTE
        assert reconstructed_equation_note(record) is None  # 索引なしでは記録自身の判定

    def test_chat_fact_line(self):
        line = formula_fact_line([{"id": "eq_4", "label": "4", "latex": COPY_EQ4["latex"]}], index=_index(), document_id=DOC)
        assert f"式 (4)［{RECONSTRUCTED_EQUATION_MARK}］" in line
        assert f"［{RECONSTRUCTED_EQUATION_MARK}］" not in formula_fact_line(
            [{"id": "eq_4", "label": "4", "latex": COPY_EQ4["latex"]}]
        )

    def test_display_fields_take_the_index(self):
        assert equation_display_fields(COPY_EQ4, index=_index())["reconstructed"] is True
        assert equation_display_fields(COPY_EQ4) == {}

    def test_routes_route_through_the_overlay(self):
        assert "overlay_topic_equation_reconstruction(topic, course_data)" in extract_function_source(
            LEARNING_SRC, "get_topic_material"
        )
        assert "overlay_topic_equation_reconstruction(topic)" in extract_function_source(
            LEARNING_SRC, "_topic_formula_fact_line"
        )
        assert "overlay_topic_equation_reconstruction(topic_info, course_data)" in extract_function_source(
            LECTURE_ROUTE_SRC, "get_lecture_sequence"
        )
        assert "load_equation_reconstruction_index" in extract_function_source(SERVICES_SRC, "_formula_fact_line")
        assert "load_equation_reconstruction_index" in COMPONENT_CONTEXT_SRC

    def test_single_judgement_function(self):
        import inspect

        from core import learner_context_common as lcc

        assert "equation_is_reconstructed(" in inspect.getsource(lcc.equation_display_fields)
        assert "equation_is_reconstructed(" in inspect.getsource(lcc.reconstructed_equation_note)


class TestFreezeWritesState:
    def test_content_block_item_carries_checked(self):
        blocks = ccb._content_blocks("", [], [], [LIVE_EQ4], [])
        item = blocks[0]["items"][0]
        assert item["reconstructed"] is True and item[RECONSTRUCTION_CHECKED_KEY] is True

    def test_not_reconstructed_record_has_no_reconstructed_key(self):
        rec = {"equation_id": "e", "latex": "a=b", "reconstruction": {"status": "none"}}
        item = ccb._content_blocks("", [], [], [rec], [])[0]["items"][0]
        assert "reconstructed" not in item and item[RECONSTRUCTION_CHECKED_KEY] is True

    def test_record_without_state_is_left_for_read_time(self):
        item = ccb._content_blocks("", [], [], [{"equation_id": "e", "latex": "a=b"}], [])[0]["items"][0]
        assert RECONSTRUCTION_CHECKED_KEY not in item

    def test_fallback_chunk_formula_state_is_carried(self):
        blocks = ccb._content_blocks(
            "", [], [], [], [], fallback_formulas=[{"id": "f1", "latex": "a=b+c", "reconstructed": True}]
        )
        assert blocks[0]["items"][0]["reconstructed"] is True


# ---------------------------------------------------------------------------
# IK-1032: 付け直しの結果の分岐ごとの知らせ
# ---------------------------------------------------------------------------


class TestAppendixReattachOutcome:
    def test_removed_when_nothing_to_list(self):
        text = "本文。\n\n" + ccb.GENERATED_EQUATIONS_HEADING + "\n- 式 (1)\n![[equation:eq_old]]\n"
        outcome = ccb.reattach_generated_appendix({}, text)
        assert outcome["outcome"] == "removed" and outcome["changed"]
        assert studio_state.appendix_reattach_notice(outcome["outcome"]) == studio_state.APPENDIX_REATTACH_REMOVED_NOTICE
        assert "外しました" in studio_state.APPENDIX_REATTACH_REMOVED_NOTICE

    def test_unchanged(self):
        outcome = ccb.reattach_generated_appendix({}, "本文だけ。")
        assert outcome["outcome"] == "unchanged"
        assert studio_state.appendix_reattach_notice("unchanged") == studio_state.APPENDIX_REATTACH_UNCHANGED_NOTICE

    def test_added_and_replaced_notices(self):
        topic = {
            "content_blocks": [{"type": "equations", "items": [{"equation_id": "eqc", "latex": r"c_s^2 = \alpha_B"}]}],
            "linked_equation_ids": ["eqc"],
        }
        added = ccb.reattach_generated_appendix(topic, "本文。")
        assert added["outcome"] == "added"
        replaced = ccb.reattach_generated_appendix(
            topic, "本文。\n\n" + ccb.GENERATED_EQUATIONS_HEADING + "\n- 古い\n![[equation:eq_old]]\n"
        )
        assert replaced["outcome"] == "replaced"
        assert studio_state.appendix_reattach_notice("replaced") == studio_state.APPENDIX_REATTACH_APPLIED_NOTICE
        assert studio_state.appendix_reattach_notice("added") == studio_state.APPENDIX_REATTACH_ADDED_NOTICE

    def test_route_returns_outcome_and_notice(self):
        src = extract_function_source(TOPICS_SRC, "reattach_lecture_studio_topic_appendix")
        assert '"outcome"' in src and "appendix_reattach_notice(" in src


# ---------------------------------------------------------------------------
# オーナー判断 1: 保存済みの付録は書き直さない（所有者の一括確定）
# ---------------------------------------------------------------------------


class TestReattachAllOwnerOperation:
    def test_basis_in_catalog(self):
        assert decision_context.BASIS_LECTURE_STUDIO_APPENDIX_REATTACH_ALL in decision_context.BASIS_VALUES

    def test_route_is_owner_only_and_records_decision_context(self):
        src = extract_function_source(TOPICS_SRC, "reattach_all_lecture_studio_topic_appendices")
        assert "ROLE_SYSTEM_ADMIN" in src and "status_code=403" in src
        assert "APPENDIX_REATTACH_ALL_OWNER_ONLY_NOTICE" in src
        assert "build_decision_context(" in src and "BASIS_LECTURE_STUDIO_APPENDIX_REATTACH_ALL" in src
        assert "attach_decision_context(" in src and "record_review_event(" in src
        assert "topic_appendix_is_stale(" in src  # いま古いトピックだけ（提示との積）
        assert "DELETE FROM topic_lecture_audio_cache" in src  # 保存と同じ副作用
        # 監査に本文を載せない
        call = src.split("record_review_event(")[1]
        assert "source_text" not in call.split("delivery = ")[0]

    def test_structure_lists_stale_topics_without_rewriting(self):
        src = extract_function_source(TOPICS_SRC, "get_lecture_studio_course_structure")
        assert "appendix_stale_topics" in src and "APPENDIX_STALE_COURSE_NOTICE" in src
        assert "reattach_generated_appendix(" not in src  # 読み時に付け直さない
        assert "書き直さない" in studio_state.APPENDIX_STALE_COURSE_NOTICE

    def test_studio_button_and_anchor(self):
        assert 'data-ui-anchor="lecture-studio.reattach-appendix-all"' in STUDIO_JS
        fn = _js_fn(STUDIO_JS, "lsBindReattachAllAppendicesButton")
        assert "appendix_reattach_all_confirm" in fn and "/appendix/reattach-all" in fn
        assert "topic_ids" in fn

    def test_anchor_registered_and_documented(self):
        from core.help_kb.admin_ui_anchors import ADMIN_UI_ANCHORS

        assert ADMIN_UI_ANCHORS["lecture-studio.reattach-appendix-all"].endswith("#reattach-appendix-all")
        manual = (ROOT / "docs" / "manual" / "teacher" / "14-admin-lecture-studio.md").read_text(encoding="utf-8")
        assert "{#reattach-appendix-all}" in manual


# ---------------------------------------------------------------------------
# オーナー判断 2: 音声生成は所有者だけ
# ---------------------------------------------------------------------------


class TestAudioGenerationOwnerOnly:
    def test_route_checks_owner(self):
        src = extract_function_source(SCRIPTS_SRC, "batch_generate_audio")
        assert "_ensure_audio_generation_owner(course_id, current_user)" in src
        helper = extract_function_source(SCRIPTS_SRC, "_ensure_audio_generation_owner")
        assert "ensure_course_owner(" in helper and "TOPIC_AUDIO_GENERATE_FORBIDDEN_DETAIL" in helper

    def test_editor_facts_carry_reason(self):
        facts = studio_state.topic_save_facts(is_owner=False)
        assert facts["audio_generate_notice"] == studio_state.TOPIC_AUDIO_GENERATE_OWNER_ONLY_NOTICE
        assert "audio_generate_notice" not in studio_state.topic_save_facts(is_owner=True)

    def test_studio_readiness_has_owner_state(self):
        fn = _js_fn(STUDIO_JS, "lsAudioReadinessState")
        assert 'return "owner_only"' in fn
        guidance = _js_fn(STUDIO_JS, "lsAudioReadinessGuidance")
        assert "audio_generate_notice" in guidance


# ---------------------------------------------------------------------------
# IK-1033: 定義の文に添える式は記号を左辺で定める式だけ
# ---------------------------------------------------------------------------


class TestSymbolDefinitionEquation:
    def test_lhs_check(self):
        f = symbol_lookup.equation_defines_symbol
        assert f(r"P(R_{\star}) = 0", ["m(r)"]) is False
        assert f(r"\frac{dm}{dr} = 4\pi r^2 \rho", ["m(r)"]) is True
        assert f(r"m(r) = \int_0^r 4\pi r'^2 \rho\,dr'", ["m(r)"]) is True
        assert f(r"\rho = M/V", [r"\rho"]) is True
        assert f(r"P = K\rho^\gamma", [r"\rho"]) is False
        assert f(r"\int f dx", ["m"]) is True  # = の無い式は判定しない

    def test_candidate_keeps_text_and_drops_unrelated_equation(self):
        row = {
            "document_id": DOC,
            "canonical_symbol": "m(r)",
            "definition_evidence_texts": ["where m(r) is the stellar mass encompassing r."],
            "defining_equation_ids": ["eq_bc"],
        }
        equations = {(DOC, "eq_bc"): {"latex": r"P(R_{\star}) = 0", "label": "3"}}
        cands = symbol_lookup._definition_candidates(row, equations=equations, evidence={}, block_order={})
        assert cands and cands[0]["text"].startswith("where m(r)")
        assert cands[0]["latex"] == "" and cands[0]["equation_label"] == ""

    def test_defining_equation_kept(self):
        row = {"document_id": DOC, "canonical_symbol": "m", "definition_evidence_texts": ["m is the mass"],
               "defining_equation_ids": ["eq_m"]}
        equations = {(DOC, "eq_m"): {"latex": r"m = \int \rho\,dV", "label": "2"}}
        cands = symbol_lookup._definition_candidates(row, equations=equations, evidence={}, block_order={})
        assert cands[0]["latex"] == r"m = \int \rho\,dV"


# ---------------------------------------------------------------------------
# IK-1034: トピック概要は 1 項目
# ---------------------------------------------------------------------------


class TestTopicSummarySingleItem:
    def test_one_item_with_alias(self):
        items = [i for i in ccb.build_topic_evidence_items({"summary": "概要の文。"}) if i.get("title") == "トピック概要"]
        assert len(items) == 1
        assert items[0]["id"] == "topic_summary" and items[0]["alt_ids"] == ["summary"]

    def test_alias_still_resolves_embed(self):
        items = ccb.build_topic_evidence_items({"summary": "概要の文。"})
        text, dropped = ccb.drop_unresolved_evidence_embeds("前\n\n![[source:summary]]\n\n後", items)
        assert "![[source:summary]]" in text and not dropped

    def test_frontends_register_alias(self):
        assert "item.alt_ids" in APP_JS
        assert "raw.alt_ids" in STUDIO_JS


# ---------------------------------------------------------------------------
# IK-0976 の残り: 導入 1 文と本体・式と直後の解説
# ---------------------------------------------------------------------------


class TestReadingBlocks:
    def test_heading_lead_in_joins_body(self):
        units = ["## 何で調べるのか", "二つの方法を組み合わせます。", "- 方法 A\n- 方法 B", "## 次の節"]
        assert lecture._reading_blocks(units) == [(0, 3), (3, 4)]

    def test_colon_intro_joins_next(self):
        units = ["本文の段落です。", "次の表のとおりです：", "| a | b |\n|---|---|"]
        assert lecture._reading_blocks(units) == [(0, 1), (1, 3)]

    def test_explanation_after_formula_joins_formula(self):
        units = ["損失は次です。", "[[FORMULA_0]]", "大事なのは微分できることです。", "次の段落。"]
        assert lecture._reading_blocks(units) == [(0, 3), (3, 4)]

    def test_long_paragraph_after_heading_is_not_a_lead_in(self):
        long_para = "長い説明。" * 60
        units = ["## 見出し", long_para, "- 項目"]
        assert lecture._reading_blocks(units) == [(0, 2), (2, 3)]

    def test_lead_in_does_not_pull_next_heading(self):
        units = ["## A", "導入です。", "## B", "本文。"]
        assert lecture._reading_blocks(units) == [(0, 2), (2, 4)]


# ---------------------------------------------------------------------------
# IK-1010 の残り: 根拠ありのトピックでも解析の区画に見出し
# ---------------------------------------------------------------------------


class TestAnalysisHeading:
    ITEMS = [
        {"kind": "figure", "id": "f1", "role": "teaching_figure"},
        {"kind": "equation", "id": "eq_1", "title": "式 (1)"},
    ]

    def test_sufficient_topic_gets_analysis_heading(self):
        state = studio_state.topic_evidence_state({"content_confidence": "exact_title"}, self.ITEMS)
        assert state["status"] == "sufficient"
        assert state["teacher_added_heading"] == studio_state.EVIDENCE_TEACHER_ADDED_HEADING
        assert state["analysis_heading"] == studio_state.EVIDENCE_ANALYSIS_HEADING

    def test_no_teacher_items_no_analysis_heading(self):
        state = studio_state.topic_evidence_state({"content_confidence": "exact_title"}, self.ITEMS[1:])
        assert "analysis_heading" not in state

    def test_studio_draws_analysis_section(self):
        assert 'data-evidence-section="analysis"' in _js_fn(STUDIO_JS, "lsCourseEvidenceChipsHtml")
        assert "analysis_heading" in _js_fn(STUDIO_JS, "lsCourseEvidenceHtml")


def test_cache_busting_versions():
    admin_html = (FRONTEND / "admin.html").read_text(encoding="utf-8")
    index_html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert re.search(r"admin-lecture-studio\.js\?v=[^\"]*studio6", admin_html)
    assert re.search(r"app\.js\?v=[^\"]*studio6", index_html)
