"""第 25 周 面 D 第 1 周（原稿スタジオ → 学生の受講）の是正のガードレール（2026-10-23）。

IK-0872（付録の式の名前・説明・対象）/ IK-0873（読み取り専用区画の教員向け投影・推定失敗の要約）/
IK-0874（書き換えの根拠に学ぶ単位・空なら LLM を呼ばない・無変化の告知）/ IK-0875（根拠の状態は
1 つの値）/ IK-0876（トピックの論文）/ IK-0877（保存の音声の副作用・トピック単位の状態）/
IK-0878（区切りが自動分割を止めた事実）/ IK-0879（音声ゼロでも講義に入れる）/ IK-0880（図は段落で・
付録の連結行）/ IK-0881（読み上げの両括弧形と講義の言語）/ IK-0883（教材コンテキストの内部表記）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

from core import course_content_builder as ccb  # noqa: E402
from core import lecture_studio_state as studio_state  # noqa: E402

FRONT = ROOT / "frontend" / "public" / "js"
STUDIO_JS = (FRONT / "admin-lecture-studio.js").read_text(encoding="utf-8")
APP_JS = (FRONT / "app.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# IK-0872 付録「この節で使う数式」
# ---------------------------------------------------------------------------


def _appendix(items, *, text="本文", linked=(), cautions=()):
    topic = {"content_blocks": [{"type": "equations", "items": items}], "linked_equation_ids": list(linked)}
    result = {"student_material": {"source_text": text}, "cautions": list(cautions)}
    ccb._ensure_required_equations_in_material(result, topic)
    return result["student_material"]["source_text"]


class TestAppendix:
    def test_tex_label_key_is_not_a_name(self):
        text = _appendix([{"equation_id": "eq_dcf", "label": "eq:modified_dcf", "latex": "a=b"}], linked=["eq_dcf"])
        assert "eq:modified_dcf" not in text
        assert "eq_dcf\n" not in text.replace("![[equation:eq_dcf]]", "")  # TeX ラベル由来の ID も名前にしない
        assert "- この節の式" in text

    def test_digit_only_label_becomes_printed_number(self):
        text = _appendix([{"equation_id": "eq_x", "label": "6", "latex": r"S=\int R"}], linked=["eq_x"])
        assert "- 式 (6)" in text
        assert "\n- 6" not in text

    def test_no_fixed_central_sentence(self):
        text = _appendix([{"equation_id": "eq_x", "label": "6", "latex": "S=1"}], linked=["eq_x"])
        assert "中心的な関係式" not in text

    def test_english_reading_is_not_a_description(self):
        text = _appendix(
            [{"equation_id": "eq_x", "label": "6", "latex": "S=1", "plain_text": "S equals the spacetime integral"}],
            linked=["eq_x"],
        )
        assert "S equals" not in text

    def test_uninferred_summary_is_not_a_description(self):
        text = _appendix(
            [{"equation_id": "eq_x", "label": "6", "latex": "S=1", "summary": "Equation semantics could not be inferred."}],
            linked=["eq_x"],
        )
        assert "could not be inferred" not in text

    def test_section_equation_unmentioned_by_body_is_not_appended(self):
        text = _appendix([{"equation_id": "eq_dcf", "label": "7", "latex": "D_c = H_0^{-1}"}], text="DCF 法にはまだ入らない。")
        assert ccb.GENERATED_EQUATIONS_HEADING not in text

    def test_body_mentioned_equation_is_appended(self):
        text = _appendix([{"equation_id": "eq_dcf", "label": "7", "latex": "D_c = H_0^{-1}"}], text="式 (7) で距離を定める。")
        assert "![[equation:eq_dcf]]" in text

    def test_caution_mentioned_equation_is_not_appended(self):
        text = _appendix(
            [{"equation_id": "eq_frag", "label": "9", "latex": "x"}],
            linked=["eq_frag"],
            cautions=["式 (9) は式の断片なので埋め込まなかった。"],
        )
        assert "eq_frag" not in text

    def test_uninformative_summary_matches_a_layer_literal(self):
        src = (ROOT / "src" / "episteme_graph" / "agents" / "equation_semantics" / "repair.py").read_text(encoding="utf-8")
        literals = re.findall(r'summary="([^"]+)"', src)
        assert literals and all(ccb.is_uninformative_equation_summary(lit) for lit in literals)


class TestAppendixFigureLine:
    def test_connected_figure_embed_is_kept_as_body_paragraph(self):
        text = (
            "本文\n\n" + ccb.GENERATED_EQUATIONS_HEADING + "\n- 式 (3)\n"
            "![[equation:eq_3]]![[figure:fig-1]]"
        )
        stripped = ccb.strip_generated_reference_appendix(text)
        assert ccb.GENERATED_EQUATIONS_HEADING not in stripped
        assert stripped.endswith("![[figure:fig-1]]")
        assert "![[equation:eq_3]]" not in stripped


# ---------------------------------------------------------------------------
# IK-0881 読み上げ
# ---------------------------------------------------------------------------


class TestSpokenFallback:
    def test_bracketed_placeholder_is_replaced_without_leftover_brackets(self):
        from core.document_pipeline.persistence import _spoken_text_from_formulas

        out = _spoken_text_from_formulas("前文 [[eq_5]] 後文", [{"id": "eq_5", "label": "5"}])
        assert out == "前文 式 (5) 後文"

    def test_fallback_is_japanese_by_default_and_never_bare_english(self):
        from core.document_pipeline.persistence import _spoken_text_from_formulas

        out = _spoken_text_from_formulas("A eq_x B", [{"id": "eq_x", "label": "eq:x"}])
        assert "equation" not in out and "数式" in out

    def test_bare_id_does_not_hit_longer_id(self):
        from core.document_pipeline.persistence import _spoken_text_from_formulas

        out = _spoken_text_from_formulas("eq_10", [{"id": "eq_1", "spoken": "いち"}])
        assert out == "eq_10"


# ---------------------------------------------------------------------------
# IK-0875 / IK-0876 / IK-0874 純関数
# ---------------------------------------------------------------------------


class TestEvidenceState:
    def test_nothing_is_none_not_weak(self):
        state = studio_state.topic_evidence_state({}, [])
        assert state["status"] == "none"
        assert "ありません" in state["message"]

    def test_topic_summary_source_is_not_evidence(self):
        state = studio_state.topic_evidence_state({"summary": "x"}, [{"kind": "source", "role": "summary"}])
        assert state["status"] == "none"

    def test_units_only_says_before_content_generation(self):
        state = studio_state.topic_evidence_state({"units": [{"stable_key": "k1:a"}]}, [])
        assert state["message"] == studio_state.EVIDENCE_UNITS_ONLY_MESSAGE

    def test_missing_with_items_gets_unmapped_heading(self):
        state = studio_state.topic_evidence_state(
            {"coverage": {"status": "missing"}}, [{"kind": "equation", "role": "equation"}]
        )
        assert state["unmapped_heading"] == studio_state.EVIDENCE_UNMAPPED_HEADING

    def test_labels_are_japanese(self):
        for status in studio_state.EVIDENCE_STATE_MESSAGES:
            assert re.search(r"[぀-鿿]", studio_state.EVIDENCE_STATE_LABELS[status])


class TestTopicDocuments:
    def test_units_then_chunks_within_course(self):
        topic = {"units": [{"stable_key": "k1:u"}], "material_chunk_ids": ["c1", "c2"]}
        ids = studio_state.topic_document_ids(
            topic,
            course_document_ids=["d1", "d2"],
            unit_rows_by_key={"k1:u": {"document_id": "d2"}},
            chunk_document_by_id={"c1": "d1", "c2": "dx"},
        )
        assert ids == ["d2", "d1"]  # dx はコースの外なので出さない

    def test_single_course_document_is_used_when_topic_has_nothing(self):
        assert studio_state.topic_document_ids({}, course_document_ids=["d1"]) == ["d1"]

    def test_multiple_course_documents_do_not_guess(self):
        assert studio_state.topic_document_ids({}, course_document_ids=["d1", "d2"]) == []


class TestRewriteHelpers:
    def test_state_only_keys_are_not_grounding(self):
        assert studio_state.rewrite_grounding_is_empty({"coverage": {"status": "missing"}, "content_confidence": "", "units": []})

    def test_units_are_grounding(self):
        assert not studio_state.rewrite_grounding_is_empty({"units": [{"label": "x"}]})

    def test_heading_only_draft_has_no_body(self):
        assert not studio_state.draft_has_body({"student_material": {"source_text": "## 題\n\n### 小見出し"}})
        assert studio_state.draft_has_body({"student_material": {"source_text": "## 題\n本文"}})

    def test_draft_changed_ignores_whitespace(self):
        a = {"student_material": {"source_text": "本文\n"}, "cautions": ["x"]}
        b = {"student_material": {"source_text": "本文"}, "cautions": [" x "]}
        assert not studio_state.draft_changed(a, b)

    def test_unit_grounding_has_no_addresses(self):
        rows = {"k1:u": {"label": "宇宙の膨張", "summary": "要約", "unit_kind": "section_block", "document_id": "d1"}}
        out = studio_state.unit_grounding_for_prompt({"units": [{"stable_key": "k1:u", "unit_id": "uu"}]}, rows)
        assert out and out[0]["label"] == "宇宙の膨張"
        assert not ({"stable_key", "unit_id", "document_id"} & set(out[0]))


class TestRewriteEndpointSkipsWithoutGrounding:
    def test_no_quota_no_llm(self, monkeypatch):
        import routes.lecture_studio._shared as shared_mod
        import routes.lecture_studio.topics as topics_mod

        course = {"topics": [{"id": "t1", "title": "T", "student_material": {"source_text": "## T"}}]}
        monkeypatch.setattr(shared_mod, "get_editable_course_data", lambda uid, cid: course)
        calls = {"quota": 0, "llm": 0}
        monkeypatch.setattr(topics_mod, "consume_lecture_rewrite_quota", lambda *a, **k: calls.__setitem__("quota", 1))

        def _llm(*a, **k):
            calls["llm"] += 1
            raise AssertionError("LLM must not be called")

        monkeypatch.setattr(topics_mod, "generate_text_with_structured_output", _llm)
        monkeypatch.setattr(topics_mod, "generate_text", _llm)
        out = topics_mod.rewrite_lecture_studio_course_topic(
            "c1", "t1", {"prompt": "やさしく"}, current_user={"id": "u1", "role": "TEACHER"},
        )
        assert out["skipped"] is True and out["changed"] is False
        assert out["notice"] == studio_state.REWRITE_NO_GROUNDING_NOTICE
        assert calls == {"quota": 0, "llm": 0}


# ---------------------------------------------------------------------------
# IK-0873 読み取り専用区画の教員向け投影
# ---------------------------------------------------------------------------


class TestStudioTeacherView:
    def test_readonly_areas_are_projected_but_editable_cautions_are_not(self):
        import routes.lecture_studio.topics as topics_mod

        topic = {
            "id": "t1",
            "cautions": ["eq_blk_003_0066 は式ではないので使わない"],
            "content_blocks": [{"type": "equations", "items": [
                {"equation_id": "eq_blk_003_0066", "label": "6", "latex": "S=1"},
            ]}],
        }
        view = topics_mod._topic_studio_view(topic, {"document_ids": [], "audio_topics": {"t1"}})
        assert view["cautions_display"] == ["式 (6) は式ではないので使わない"]
        assert topic["cautions"] == ["eq_blk_003_0066 は式ではないので使わない"]
        assert view["has_topic_audio"] is True
        assert view["evidence_state"]["status"] in studio_state.EVIDENCE_STATE_MESSAGES
        titles = [item.get("title", "") for item in view["evidence_items"]]
        assert not any("eq_blk_" in t for t in titles)


# ---------------------------------------------------------------------------
# IK-0878 区切り / IK-0879 講義の入口
# ---------------------------------------------------------------------------


class TestSlideNotes:
    def test_marker_suppresses_and_mismatch_says_how_to_fix(self):
        from core.lecture import auto_paginate_suppressed, slide_split_notes

        assert auto_paginate_suppressed("a\n===\nb", "x")
        notes = slide_split_notes(suppressed=True, mismatch=True, display_count=2, spoken_count=1)
        assert len(notes) == 2 and "同じ数の区切り" in notes[1]

    def test_preview_split_reports_suppression(self):
        import routes.lecture_studio.scripts as scripts_mod

        body = scripts_mod.LecturePreviewSplitRequestWithPagination(
            display_text="前半\n===\n後半", spoken_text="読み上げ", auto_paginate=True,
        )
        out = scripts_mod.preview_split_slides(body, current_user={"id": "u", "role": "TEACHER"})
        assert out["auto_paginate_suppressed"] is True
        assert out["mismatch"] is True and out["notes"]


class TestLectureEntry:
    def test_note_when_no_audio(self):
        from core.lecture import LECTURE_NO_AUDIO_NOTE, lecture_availability_note

        assert lecture_availability_note(has_audio=True, total_slides=3) == ""
        assert lecture_availability_note(has_audio=False, total_slides=3) == LECTURE_NO_AUDIO_NOTE
        assert "管理画面" not in LECTURE_NO_AUDIO_NOTE

    def test_app_js_enters_lecture_with_slides_only(self):
        assert "state.topicHasAudio || state.topicHasSlides" in APP_JS
        assert "if (!state.topicHasAudio && !state.topicHasSlides)" in APP_JS
        assert "管理画面で音声を生成してください" not in APP_JS


# ---------------------------------------------------------------------------
# IK-0883 教材コンテキスト
# ---------------------------------------------------------------------------


class TestMaterialContext:
    def test_field_text_has_no_python_repr(self):
        import routes.admin as admin_mod

        text = admin_mod._material_field_text(
            [{"text": "距離", "claim_ids": ["c1"], "equation_ids": ["eq_7"]}, "温度"],
            {"eq_7": "式 (7) `D=1`"},
        )
        assert text == "距離（式 (7) `D=1`）・温度"
        assert "{" not in text and "claim_ids" not in text

    def test_handles_in_answer_become_names(self):
        import routes.admin as admin_mod
        from core.course_units import UnitCandidate

        cand = UnitCandidate("U141", "u", "k1:a", "section_block", "宇宙の膨張", "", "d")
        out = admin_mod._replace_unit_handles_in_answer("U141 と U9 を扱う", [cand])
        assert out == "「宇宙の膨張」 と U9 を扱う"


# ---------------------------------------------------------------------------
# 画面（admin-lecture-studio.js）
# ---------------------------------------------------------------------------


class TestStudioUiStatic:
    def test_evidence_state_comes_from_server(self):
        assert "topic.evidence_state" in STUDIO_JS
        assert "Claimまたはコンポーネントの対応付けが弱い" not in STUDIO_JS

    def test_topic_documents_are_used(self):
        assert "lsTopicDocumentId(topic) || lsCoursePrimaryDocumentId()" in STUDIO_JS
        assert "course.documents" in STUDIO_JS

    def test_save_and_rewrite_notices(self):
        assert "data.audio_invalidated" in STUDIO_JS
        assert "topic_audio_save_notice" in STUDIO_JS
        assert "data.skipped" in STUDIO_JS
        assert "data.changed === false" in STUDIO_JS

    def test_figure_insert_is_a_paragraph_before_appendix(self):
        assert "lsMoveCursorBeforeGeneratedAppendix(materialEl);" in STUDIO_JS
        assert "lsAsParagraph(materialEl," in STUDIO_JS
        assert "generated_appendix_headings" in STUDIO_JS

    def test_versions_bumped(self):
        admin_html = (ROOT / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
        index_html = (ROOT / "frontend" / "public" / "index.html").read_text(encoding="utf-8")
        assert "admin-lecture-studio.js?v=reproduction-wave2-20260919-1" not in admin_html
        assert "app.js?v=round23-polish-20261009" not in index_html
