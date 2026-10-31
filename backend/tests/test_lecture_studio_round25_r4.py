"""第 25 周 面 D 第 4 波（IK-0975〜0979 / IK-0872 ②）の再現テスト。

- IK-0975: preview-split（auto_paginate）は配信の正本 build_topic_slides と同じ入力の正規化を
  通してから分割する（式・図を一定の長さに数える）。枚数・境目が配信と一致する。
- IK-0976: 自動ページ分割は見出しだけ・式だけの段落を区切りの両側に分けない。
- IK-0977: 復元の印は式の写し（本文の ``$…$``・講義 DTO・根拠の式・記号の吹き出し・図スタジオの
  資料）にも運ばれ、判定は learner_context_common.equation_latex_is_reconstructed を通る。
- IK-0978: 共有教員の画面に所有者の表示名と［案をコピー］の知らせ（区画名・渡す相手）。
- IK-0979: 図の採用の知らせは音声の有無と届き方を見る（固定文をやめた）。
- IK-0872 ②: 付録は英語の要約を説明に採らず、本文が触れていない復元式を足さない。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import lecture  # noqa: E402
from core.label_vocab import RECONSTRUCTED_EQUATION_MARK, RECONSTRUCTED_EQUATION_NOTE  # noqa: E402

FRONTEND = ROOT / "frontend" / "public" / "js"


# ---------------------------------------------------------------------------
# IK-0976 読みの単位
# ---------------------------------------------------------------------------


class TestReadingUnits:
    def test_heading_is_not_left_at_slide_end(self):
        display = "\n\n".join(["あ" * 300, "## この章の見通し", "い" * 400])
        slides, mismatch = lecture.auto_paginate_slides(display, "", [])
        assert not mismatch
        assert len(slides) == 2
        assert not slides[0]["display_text"].rstrip().endswith("## この章の見通し")
        assert slides[1]["display_text"].startswith("## この章の見通し")

    def test_formula_stays_with_lead_in(self):
        display = "\n\n".join(["あ" * 560, "次の量を損失にします。", "[[FORMULA_0]]"])
        formulas = [{"id": "[[FORMULA_0]]", "latex": "L", "spoken": "L", "is_display": True}]
        slides, _ = lecture.auto_paginate_slides(display, "", formulas)
        assert len(slides) == 2
        assert "次の量を損失にします。" in slides[1]["display_text"]
        assert "[[FORMULA_0]]" in slides[1]["display_text"]

    def test_short_text_still_single_slide(self):
        slides, _ = lecture.auto_paginate_slides("## 見出し\n\n本文", "", [])
        assert len(slides) == 1

    def test_huge_joined_block_is_split_inside(self):
        # 見出しで結ばれた単位が目安の 2 倍を超えるときは単位の中を段落で切る（1 枚に溜めない）。
        units = ["## 見出し"] + ["う" * 500] * 4
        display = "\n\n".join(units)
        groups = lecture._group_units_by_size(units, 600)
        assert len(groups) >= 3
        slides, _ = lecture.auto_paginate_slides(display, "", [])
        assert len(slides) >= 3

    def test_spoken_alignment_kept_for_same_structure(self):
        d_units = ["あ" * 300, "## 見出し", "い" * 400]
        s_units = ["読み1", "読み2", "読み3"]
        slides, mismatch = lecture.auto_paginate_slides("\n\n".join(d_units), "\n\n".join(s_units), [])
        assert not mismatch and len(slides) == 2
        assert slides[0]["spoken_text"] == "読み1"
        assert "読み2" in slides[1]["spoken_text"] and "読み3" in slides[1]["spoken_text"]


# ---------------------------------------------------------------------------
# IK-0975 プレビューと配信の入力
# ---------------------------------------------------------------------------


def _math_heavy_topic() -> dict:
    # 第 25 周 第 8 波（IK-0976）: 文中の小さな式（$…$）は実文字数で数えるようになったので、
    # 「プレビューが配信と同じ正規化を通る」ことは表示式（$$…$$ = 60 字換算）で確かめる。
    para = "宇宙の構造は $$\\alpha$$ と $$\\beta$$ と $$\\gamma$$ と $$\\delta$$ で決まります。"
    body = "\n\n".join([para * 2 for _ in range(6)])
    return {
        "id": "t0",
        "student_material": {"source_format": "eg-markdown-v1", "source_text": body},
        "spoken_script": "",
    }


class TestPreviewMatchesDelivery:
    def test_prepare_counts_inline_math_like_delivery(self):
        topic = _math_heavy_topic()
        delivered, *_ = lecture.build_topic_slides(topic)
        display, spoken, formulas, figures = lecture.prepare_topic_slide_input(topic)
        previewed, _ = lecture.auto_paginate_slides(display, spoken, formulas, figures=figures)
        assert [s["display_text"] for s in previewed] == [s["display_text"] for s in delivered]
        # 生の本文を分割にかけると（従来のプレビュー）、式を数文字と数えて枚数が少なく出る。
        raw_slides, _ = lecture.auto_paginate_slides(
            topic["student_material"]["source_text"], "", []
        )
        assert len(raw_slides) < len(delivered)

    def test_preview_endpoint_uses_delivery_normalization(self):
        try:
            from fastapi.testclient import TestClient
        except Exception:  # pragma: no cover
            return
        import jwt

        from api.main import app

        topic = _math_heavy_topic()
        headers = {
            "Authorization": "Bearer " + jwt.encode(
                {"sub": "22222222-2222-2222-2222-222222222222", "role": "TEACHER",
                 "username": "u", "email": "u@test.com"},
                "test-secret-key", algorithm="HS256",
            )
        }
        r = TestClient(app).post(
            "/api/admin/lecture-studio/preview-split",
            headers=headers,
            json={
                "display_text": topic["student_material"]["source_text"],
                "spoken_text": "",
                "formulas": [],
                "auto_paginate": True,
            },
        )
        assert r.status_code == 200
        delivered, *_ = lecture.build_topic_slides(topic)
        assert len(r.json()["slides"]) == len(delivered)

    def test_preview_topic_context_borrows_saved_topic(self, monkeypatch):
        from api.routes.lecture_studio import scripts

        saved = {
            "id": "t1",
            "evidence_links": [{"kind": "equation", "target_id": "eq_a", "latex": "E=mc^2"}],
            "student_material": {"source_text": "保存済み"},
        }
        monkeypatch.setattr(
            scripts, "get_viewable_course_data", lambda uid, cid: {"topics": [saved]}
        )
        body = scripts.LecturePreviewSplitRequestWithPagination(
            display_text="編集中 ![[equation:eq_a]]", spoken_text="", auto_paginate=True,
            course_id="c1", topic_id="t1",
        )
        topic = scripts._preview_topic_context(body, {"id": "u1", "role": "TEACHER"})
        assert topic["student_material"]["source_text"] == "編集中 ![[equation:eq_a]]"
        assert topic["evidence_links"] == saved["evidence_links"]
        display, _s, formulas, _f = lecture.prepare_topic_slide_input(topic)
        assert "[[FORMULA_" in display and formulas[0]["latex"] == "E=mc^2"

    def test_preview_topic_context_fail_closed_on_unviewable(self, monkeypatch):
        from api.routes.lecture_studio import scripts

        monkeypatch.setattr(scripts, "get_viewable_course_data", lambda uid, cid: None)
        body = scripts.LecturePreviewSplitRequestWithPagination(
            display_text="本文", auto_paginate=True, course_id="c1", topic_id="t1",
        )
        topic = scripts._preview_topic_context(body, {"id": "u1", "role": "TEACHER"})
        assert "evidence_links" not in topic

    def test_studio_js_passes_topic_context(self):
        src = (FRONTEND / "admin-lecture-studio.js").read_text(encoding="utf-8")
        assert "payload.course_id = topicContext.courseId" in src
        assert "lsFetchSplitSlides(dText, sText, [], true, null, {" in src


# ---------------------------------------------------------------------------
# IK-0977 復元の印の写し
# ---------------------------------------------------------------------------


def _reconstructed_topic() -> dict:
    return {
        "id": "t11",
        "student_material": {
            "source_format": "eg-markdown-v1",
            "source_text": "状態方程式は $w(a) = w_0 + w_a(1 - a)$ と書けます。\n\n別の式 $x=1$ もあります。",
        },
        "spoken_script": "",
        "content_blocks": [
            {
                "type": "equations",
                "items": [
                    {"equation_id": "eq_w", "latex": "w(a)=w_0+w_a(1-a)", "reconstructed": True,
                     "raw_text": "w(a) = w0 + wa(1 − a)"},
                    {"equation_id": "eq_x", "latex": "x=1"},
                ],
            }
        ],
    }


class TestReconstructedMarkCopies:
    def test_body_copy_gets_mark(self):
        _slides, _d, _s, formulas = lecture.build_topic_slides(_reconstructed_topic())
        by_latex = {f["latex"].replace(" ", ""): f for f in formulas}
        marked = by_latex["w(a)=w_0+w_a(1-a)"]
        assert marked.get("reconstructed") is True
        assert marked["reconstructed_mark"] == RECONSTRUCTED_EQUATION_MARK
        assert marked["reconstructed_note"] == RECONSTRUCTED_EQUATION_NOTE
        assert not by_latex["x=1"].get("reconstructed")

    def test_slide_formulas_keep_mark(self):
        slides, *_ = lecture.build_topic_slides(_reconstructed_topic())
        flat = [f for s in slides for f in s["formulas"]]
        assert any(f.get("reconstructed_mark") for f in flat)

    def test_lecture_dto_keeps_mark(self):
        from schemas import LectureFormulaItem

        item = LectureFormulaItem(**{
            "id": "[[FORMULA_0]]", "latex": "x", "spoken": "x", "reconstructed": True,
            "reconstructed_mark": RECONSTRUCTED_EQUATION_MARK,
            "reconstructed_note": RECONSTRUCTED_EQUATION_NOTE,
        }).model_dump()
        assert item["reconstructed_mark"] == RECONSTRUCTED_EQUATION_MARK

    def test_flat_reconstruction_equal_to_raw_is_not_marked(self):
        from core.learner_context_common import equation_latex_is_reconstructed

        assert equation_latex_is_reconstructed({"reconstructed": True, "latex": "N = 15", "raw_text": "N=15"}) is False
        assert equation_latex_is_reconstructed(
            {"reconstructed": True, "latex": "w(a)=w_0+w_a(1-a)", "raw_text": "w(a) = w0 + wa(1 − a)"}
        ) is True

    def test_annotate_uses_canonical_judgment(self):
        out = lecture.annotate_reconstructed_formulas(
            [{"id": "a", "latex": "N=15", "raw_text": "N = 15", "reconstructed": True}]
        )
        assert "reconstructed_mark" not in out[0]

    def test_evidence_items_carry_mark(self):
        from core.course_content_builder import build_topic_evidence_items

        topic = _reconstructed_topic()
        topic["evidence_links"] = [{"kind": "equation", "target_id": "eq_w", "latex": "w(a)=w_0+w_a(1-a)"}]
        items = [i for i in build_topic_evidence_items(topic) if i.get("kind") == "equation"]
        assert items and items[0].get("reconstructed") is True
        assert items[0]["reconstructed_note"] == RECONSTRUCTED_EQUATION_NOTE

    def test_figure_grounding_text_marks_reconstructed(self):
        from api.routes import teaching_figures

        text = teaching_figures._grounding_to_text({
            "equations": [
                {"id": "eq_w", "label": "状態方程式", "latex": "w(a)=w_0", "reconstructed": True},
                {"id": "eq_x", "label": "x", "latex": "x=1"},
            ]
        })
        assert RECONSTRUCTED_EQUATION_NOTE in text.splitlines()[1]
        assert RECONSTRUCTED_EQUATION_NOTE not in text.splitlines()[2]

    def test_figure_prompt_forbids_calling_reconstruction_source(self):
        from core.teaching_figures import prompt

        instruction = prompt.build_turn_instruction()
        assert prompt.RECONSTRUCTED_EQUATION_CONSTRAINT in instruction
        assert RECONSTRUCTED_EQUATION_NOTE in prompt.RECONSTRUCTED_EQUATION_CONSTRAINT

    def test_symbol_fallback_marks_reconstructed(self):
        from core import symbol_lookup

        assert symbol_lookup._reconstructed_mark_fields({"reconstructed": True, "latex": "N_c=4", "raw_text": "Nc"})[
            "reconstructed_mark"
        ] == RECONSTRUCTED_EQUATION_MARK
        assert symbol_lookup._reconstructed_mark_fields({"latex": "x"}) == {}
        # 原文の LaTeX を表示する定義式には印を付けない。
        assert symbol_lookup._registry_latex_is_reconstructed(
            {"source_extraction": {"latex": "x"}, "reconstruction": {"status": "inferred", "latex": "y"}}
        ) is False
        assert symbol_lookup._registry_latex_is_reconstructed(
            {"source_extraction": {"latex": ""}, "reconstruction": {"status": "inferred", "latex": "y"}}
        ) is True

    def test_app_js_renders_mark_for_placeholders_and_popover(self):
        src = (FRONTEND / "app.js").read_text(encoding="utf-8")
        assert "preserveMath(formula.latex || formula.summary || id, true, formula)" in src
        assert "block.formula && block.formula.reconstructed" in src
        assert "eq.reconstructed" in src and "definition.reconstructed" in src


# ---------------------------------------------------------------------------
# IK-0978 / IK-0979
# ---------------------------------------------------------------------------


class TestHandoffAndAdoptNotices:
    def test_owner_name_only_for_non_savers(self):
        from core import lecture_studio_state as st

        facts = st.topic_save_facts(is_owner=False, owner_display_name="浦川")
        assert facts["owner_notice"] == "このコースの所有者: 浦川"
        assert "所有者（浦川）" in facts["proposal_copy_notice"]
        assert "{sections}" in facts["proposal_copy_notice"]
        owner = st.topic_save_facts(is_owner=True, owner_display_name="浦川")
        assert "owner_notice" not in owner and "proposal_copy_notice" not in owner

    def test_copy_notice_without_name(self):
        from core import lecture_studio_state as st

        facts = st.topic_save_facts(is_owner=False)
        assert "owner_notice" not in facts
        assert facts["proposal_copy_notice"].endswith("所有者に渡してください。")

    def test_copy_includes_handoff_and_questions(self):
        src = (FRONTEND / "admin-lecture-studio.js").read_text(encoding="utf-8")
        assert 'add("確認問題"' in src and 'add("申し送り"' in src
        assert "lsState.rewriteHandoff = { topicId:" in src
        assert "proposal_copy_notice" in src
        assert "所有者に渡してください。\", \"success\"" not in src

    def test_adopt_notice_parts(self):
        from core import lecture_studio_state as st

        none = st.figure_adopt_notice_parts(None)
        assert none["delivery"] == st.TOPIC_DELIVERY_NONE_NOTICE
        assert none["audio"] == st.TOPIC_AUDIO_SAVE_NOTICE
        immediate = st.figure_adopt_notice_parts({"status": "immediate", "message": "すぐ届きます。"})
        assert immediate["delivery"] == "すぐ届きます。"

    def test_figure_studio_has_no_fixed_adopt_notice(self):
        src = (FRONTEND / "admin-figure-studio.js").read_text(encoding="utf-8")
        assert "var ADOPT_NOTICE" not in src
        assert "studio.hasTopicAudio && parts.audio" in src
        studio = (FRONTEND / "admin-lecture-studio.js").read_text(encoding="utf-8")
        assert studio.count("adoptNoticeParts: (lsState.courseStructure || {}).figure_adopt_notice_parts") == 2


# ---------------------------------------------------------------------------
# IK-0872 ② 付け直しの付録
# ---------------------------------------------------------------------------


class TestAppendixReattach:
    def test_english_summary_not_used_as_description(self):
        from core.course_content_builder import _equation_material_description

        assert _equation_material_description(
            {"summary": "Background dark-energy pressure is defined through the equation-of-state relation."}
        ) == ""

    def test_unmentioned_reconstructed_equation_not_appended(self):
        from core.course_content_builder import GENERATED_EQUATIONS_HEADING, _ensure_required_equations_in_material

        topic = _reconstructed_topic()
        topic["linked_equation_ids"] = ["eq_w"]
        result = {"student_material": {"source_text": "本文は式に触れません。"}, "cautions": []}
        _ensure_required_equations_in_material(result, topic)
        assert "eq_w" not in result["student_material"]["source_text"]
        assert GENERATED_EQUATIONS_HEADING not in result["student_material"]["source_text"]
