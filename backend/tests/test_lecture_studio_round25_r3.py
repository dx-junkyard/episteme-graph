"""第 25 周 面 D 第 3 周（原稿スタジオ → 学生の受講）の是正のガードレール（2026-10-23 第 3 波）。

IK-0947（空のトピックは空だと言う・講義の案内は空の読むへ誘導しない・公開の応答に準備状態）/
IK-0948（下書きの欄の規則表を 2 つの生成指示が共有・申し送りは別キー）/ IK-0949（自作の説明図は
根拠に数えず別の印・前置は 1 箇所）/ IK-0950（保存できない教員への知らせは置き換え・見出しの印・
案をコピー）/ IK-0951（題名に TeX を書かない指示と検出）/ IK-0952（内部語・章番号の二重）/
IK-0909 の残り（キーごとに予算）/ IK-0914 の残り（読み上げ欄が空でも区切りの説明）。
IK-0944 / IK-0946 は test_lecture_reading_lecture_contract.py、IK-0945 は test_course_entry_gate.py。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

from core import course_content_builder as ccb  # noqa: E402
from core import label_vocab  # noqa: E402
from core import lecture as lecture_mod  # noqa: E402
from core import lecture_studio_state as studio_state  # noqa: E402
from core.course_data import course_titles_with_raw_tex, title_has_raw_tex  # noqa: E402

FRONT = ROOT / "frontend" / "public"
STUDIO_JS = (FRONT / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
ADMIN_JS = (FRONT / "js" / "admin.js").read_text(encoding="utf-8")
APP_JS = (FRONT / "js" / "app.js").read_text(encoding="utf-8")
ADMIN_HTML = (FRONT / "admin.html").read_text(encoding="utf-8")


def _fn(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    depth, started = 0, False
    for j in range(src.index("{", start), len(src)):
        if src[j] == "{":
            depth += 1
            started = True
        elif src[j] == "}":
            depth -= 1
            if started and depth == 0:
                return src[start:j + 1]
    raise AssertionError(name)


# ---------------------------------------------------------------------------
# IK-0909 の残り: キーごとに予算を配る
# ---------------------------------------------------------------------------


class TestRewriteEvidenceFairShare:
    def _evidence(self):
        return {
            "summary": "概要" * 2000,
            "source_excerpt": "原文" * 3000,
            "figures": [{"figure_id": f"f{i}", "caption": "図の説明" * 60} for i in range(10)],
            "evidence_links": [{"kind": "claim", "summary": "主張の本文" * 30} for _ in range(20)],
            "content_blocks": [{"type": "equations", "items": [
                {"equation_id": f"eq_{i}", "latex": "a" * 200} for i in range(60)
            ]}],
            "linked_component_ids": [f"comp_{i}" for i in range(30)],
            "units": [{"label": "U"}],
        }

    def test_lists_are_not_dropped_wholesale(self):
        compact, trimmed = studio_state.compact_rewrite_evidence(self._evidence())
        text = json.dumps(compact, ensure_ascii=False, indent=2)
        assert len(text) <= studio_state.REWRITE_EVIDENCE_LIMIT
        for key in ("evidence_links", "content_blocks", "linked_component_ids", "summary", "source_excerpt"):
            assert compact.get(key), key
        assert compact["content_blocks"][0]["items"], "式の一覧の塊の中身を 1 つは残す"
        assert list(compact)[0] == "units"
        assert "evidence_links" in trimmed and "content_blocks" in trimmed


# ---------------------------------------------------------------------------
# IK-0948: 欄の規則表と申し送り
# ---------------------------------------------------------------------------


class TestDraftFieldRules:
    def test_both_prompts_read_the_rule_table(self):
        import routes.lecture_studio.topics as topics_mod

        assert ccb.TOPIC_DRAFT_FIELD_RULES in ccb._COURSE_CONTENT_DRAFT_PROMPT
        assert ccb.TOPIC_DRAFT_FIELD_RULES in topics_mod._COURSE_TOPIC_DRAFT_PROMPT
        assert ccb.TOPIC_DRAFT_HANDOFF_RULE in topics_mod._COURSE_TOPIC_DRAFT_PROMPT
        assert "handoff_notes" in topics_mod._COURSE_TOPIC_DRAFT_PROMPT
        # 生成指示の書式（format）が規則表の文字で壊れない
        topics_mod._COURSE_TOPIC_DRAFT_PROMPT.format(
            topic_json="{}", evidence_json="{}", draft_json="{}", instructor_prompt="")

    def test_split_moves_work_notes(self):
        keep, handoff = ccb.split_cautions_and_handoff([
            "比熱の符号に注意する。",
            "eq_ne_density は evidence_links に内容が無いので確認してください。",
            "内部ラベル comp_007 を外しました。",
        ])
        assert keep == ["比熱の符号に注意する。"]
        assert len(handoff) == 2

    def test_rewrite_route_returns_handoff_separately(self, monkeypatch):
        import routes.lecture_studio._shared as shared_mod
        import routes.lecture_studio.topics as topics_mod

        topic = {"id": "t1", "title": "T", "student_material": {"source_text": "## T\n本文"}}
        monkeypatch.setattr(shared_mod, "get_editable_course_data", lambda uid, cid: {"topics": [topic]})
        monkeypatch.setattr(topics_mod, "consume_lecture_rewrite_quota", lambda *a, **k: None)
        monkeypatch.setattr(topics_mod, "_rewrite_unit_grounding", lambda cd, t: [])
        monkeypatch.setattr(topics_mod, "_topic_figures_for_prompt", lambda t: [])
        monkeypatch.setattr(
            topics_mod, "_topic_save_facts_for", lambda cid, user: studio_state.topic_save_facts(is_owner=True))
        monkeypatch.setattr(topics_mod, "structured_call", lambda prompt, *a, **k: {
            "student_material": {"source_text": "## T\n新しい本文"},
            "cautions": ["適用条件に注意。", "evidence_links に式が無い（eq_ne_density）。"],
            "handoff_notes": ["図の説明は材料に無かった。"],
        })
        out = topics_mod.rewrite_lecture_studio_course_topic(
            "c1", "t1", {"prompt": "やさしく"}, current_user={"id": "u1", "role": "TEACHER"})
        assert out["cautions"] == ["適用条件に注意。"]
        assert "図の説明は材料に無かった。" in out["handoff_notes"]
        assert any("eq_ne_density" in n for n in out["handoff_notes"])

    def test_studio_shows_handoff_in_notice_not_cautions(self):
        body = _fn(STUDIO_JS, "lsRewriteCourseTopicDraft")
        assert "data.handoff_notes" in body
        assert "topic.cautions = data.cautions" in body


# ---------------------------------------------------------------------------
# IK-0949: 自作の説明図
# ---------------------------------------------------------------------------


class TestTeachingFigureEvidence:
    def _topic(self):
        return {"evidence_links": [{
            "kind": "figure", "target_id": "fig-1", "figure_id": "fig-1", "caption": "放射の模式図",
            "extra": {"figure_id": "fig-1", "caption": "放射の模式図", "teaching": True},
        }]}

    def test_item_marked_and_title_without_kind_prefix(self):
        items = ccb.build_topic_evidence_items(self._topic())
        fig = [i for i in items if i["kind"] == "figure"][0]
        assert fig["teaching"] is True
        assert fig["origin_label"] == label_vocab.TEACHING_FIGURE_ORIGIN_LABEL
        assert not fig["title"].startswith("図:")

    def test_teaching_figure_does_not_count_as_evidence(self):
        topic = self._topic()
        state = studio_state.topic_evidence_state(topic, ccb.build_topic_evidence_items(topic))
        assert state["status"] == "none"

    def test_studio_kind_text_single_place(self):
        assert "lsEvidenceItemKindText(item)" in _fn(STUDIO_JS, "lsCourseEvidenceChipsHtml")
        assert "item.origin_label" in _fn(STUDIO_JS, "lsEvidenceItemKindText")


# ---------------------------------------------------------------------------
# IK-0950: 保存できない教員
# ---------------------------------------------------------------------------


class TestUnsavableRewrite:
    def test_notice_is_replaced_not_appended(self, monkeypatch):
        import routes.lecture_studio._shared as shared_mod
        import routes.lecture_studio.topics as topics_mod

        topic = {"id": "t1", "title": "T", "student_material": {"source_text": "## T\n本文"}}
        monkeypatch.setattr(shared_mod, "get_editable_course_data", lambda uid, cid: {"topics": [topic]})
        monkeypatch.setattr(topics_mod, "consume_lecture_rewrite_quota", lambda *a, **k: None)
        monkeypatch.setattr(topics_mod, "_rewrite_unit_grounding", lambda cd, t: [])
        monkeypatch.setattr(topics_mod, "_topic_figures_for_prompt", lambda t: [])
        monkeypatch.setattr(
            topics_mod, "_topic_save_facts_for", lambda cid, user: studio_state.topic_save_facts(is_owner=False))
        monkeypatch.setattr(topics_mod, "structured_call", lambda prompt, *a, **k: {
            "student_material": {"source_text": "## T\n新しい本文"}})
        out = topics_mod.rewrite_lecture_studio_course_topic(
            "c1", "t1", {"prompt": "やさしく"}, current_user={"id": "u1", "role": "TEACHER"})
        assert out["notice"] == studio_state.REWRITE_PROPOSAL_UNSAVABLE_NOTICE
        assert "確定" not in out["notice"]
        assert out["can_save"] is False

    def test_share_mark_for_editor(self):
        facts = studio_state.topic_save_facts(is_owner=False)
        assert label_vocab.COURSE_ROLE_LABELS["editor"] in facts["share_mark"]
        assert "share_mark" not in studio_state.topic_save_facts(is_owner=True)

    def test_studio_ui(self):
        assert 'id="ls-rewrite-copy-btn"' in ADMIN_HTML
        assert 'data-ui-anchor="lecture-studio.rewrite-copy"' in ADMIN_HTML
        assert "s.share_mark" in _fn(STUDIO_JS, "lsRenderCourseStructure")
        body = _fn(STUDIO_JS, "lsRewriteCourseTopicDraft")
        assert 'data.can_save === false ? "" : data.save_notice' in body


# ---------------------------------------------------------------------------
# IK-0951: 題名の TeX
# ---------------------------------------------------------------------------


class TestTitleRawTex:
    def test_detector(self):
        assert title_has_raw_tex(r"\alpha_M=0 の簡約")
        assert title_has_raw_tex("有効音速 c_s^2(k,a)")
        assert not title_has_raw_tex("宇宙論の基礎")
        assert not title_has_raw_tex("zero_recoil_limit の扱い")

    def test_course_titles(self):
        data = {"title": "C", "chapters": [{"title": "第1章 Cep B"}],
                "topics": [{"title": r"z<2 での \mu>1"}, {"title": "普通の題名"}]}
        assert course_titles_with_raw_tex(data) == [r"z<2 での \mu>1"]

    def test_builder_prompt_forbids_tex_titles(self):
        import routes.admin as admin_mod

        assert "トピック名には数式の記法" in admin_mod._COURSE_BUILDER_SYSTEM_PROMPT

    def test_create_and_studio_report(self):
        src = (ROOT / "backend" / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
        body = src[src.index("def create_course("):src.index("def list_courses(")]
        assert "course_titles_with_raw_tex(" in body
        assert "s.title_notice" in _fn(STUDIO_JS, "lsRenderCourseStructure")
        assert "data.title_notice" in ADMIN_JS


# ---------------------------------------------------------------------------
# IK-0952: 見出し
# ---------------------------------------------------------------------------


class TestStudioCourseHeader:
    def test_no_internal_chunk_word(self):
        assert "チャンクなし" not in _fn(STUDIO_JS, "lsRenderCourseStructure")

    def test_chapter_number_not_doubled(self):
        body = _fn(STUDIO_JS, "lsChapterHeading")
        pattern = re.search(r"/\^第(.+?)/\.test", body).group(0)
        assert "第" in pattern
        assert "lsChapterHeading(ci, chapter.title)" in _fn(STUDIO_JS, "lsRenderCourseStructure")

    def test_no_topics_message_is_shown(self):
        body = _fn(STUDIO_JS, "lsRenderCourseStructure")
        assert 'topicAudio.status !== "no_topics"' not in body


# ---------------------------------------------------------------------------
# IK-0947: 空のトピック
# ---------------------------------------------------------------------------


class TestEmptyTopic:
    def test_lecture_note_does_not_point_to_empty_reading(self):
        note = lecture_mod.lecture_availability_note(has_audio=False, total_slides=0, has_reading_material=False)
        assert note == label_vocab.TOPIC_MATERIAL_EMPTY_NOTICE
        assert "読む" not in note
        assert lecture_mod.lecture_availability_note(has_audio=False, total_slides=0) == lecture_mod.LECTURE_NO_SLIDES_NOTE

    def test_material_route_states_empty(self, monkeypatch):
        import routes.learning as learning_mod

        course = {"topics": [{"id": "t1", "title": "T"}], "sources": []}
        monkeypatch.setattr(learning_mod, "get_course_data", lambda uid, cid: course)
        monkeypatch.setattr(learning_mod, "get_course_chunks_ordered", lambda cd: [])
        out = learning_mod.get_topic_material("c1", "t1", current_user={"id": "u1"})
        assert out.chunks == []
        assert out.preparation_notice == label_vocab.TOPIC_MATERIAL_EMPTY_NOTICE

    def test_publish_note(self):
        import routes.admin as admin_mod

        note = admin_mod._course_empty_topics_note({"topics": [{"id": "t1", "title": "T"}]})
        assert note == label_vocab.COURSE_PUBLISHED_WITH_EMPTY_TOPICS_NOTICE
        full = {"topics": [{"id": "t1", "title": "T", "student_material": {"source_text": "本文"}}]}
        assert admin_mod._course_empty_topics_note(full) == ""
        assert not re.search(r"[0-9]", label_vocab.COURSE_PUBLISHED_WITH_EMPTY_TOPICS_NOTICE)

    def test_learner_renders_empty_notice(self):
        body = _fn(APP_JS, "renderMaterial") if "function renderMaterial(" in APP_JS else APP_JS
        assert "state.topicMaterial.length === 0 && state.topicMaterialNotice" in body


# ---------------------------------------------------------------------------
# IK-0914 の残り
# ---------------------------------------------------------------------------


class TestSlideIndicatorNotes:
    def test_empty_spoken_branch_draws_notes(self):
        body = _fn(STUDIO_JS, "lsRenderSlideIndicatorEl")
        branch = body[body.index("split.spokenCount === 0"):body.index("} else {", body.index("split.spokenCount === 0"))]
        assert "split.notes" in branch
