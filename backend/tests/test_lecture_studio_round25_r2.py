"""第 25 周 面 D 第 2 周（原稿スタジオ → 学生の受講）の是正のガードレール（2026-10-23）。

IK-0908（editor は保存できないことを保存の前から言う・403 は日本語）/ IK-0909（書き換えの根拠は
キーごとに予算・学ぶ単位は必ず残す・失敗状態の文は材料から外す）/ IK-0910（採用・保存の後に
サーバの派生を取り直す）/ IK-0911（役割・共有の権限語を日本語の 1 つの表から）/ IK-0912（問いの
枠の語を内容語に数えない・TeX を外した教材とも照合）/ IK-0913（分割インジケータの係留）/
IK-0914（先頭・末尾・連続の区切りの事実文）/ IK-0915（保存の届き方）/ IK-0872・IK-0880 の残り
（保存済みの付録は読み時に付け直さず、明示の操作と G層の道案内）/ IK-0873 の残り（根拠リンクの
式の見出しの英語要約と重複）。
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
from core import lecture_studio_state as studio_state  # noqa: E402

FRONT = ROOT / "frontend" / "public"
STUDIO_JS = (FRONT / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
ADMIN_JS = (FRONT / "js" / "admin.js").read_text(encoding="utf-8")
ADMIN_HTML = (FRONT / "admin.html").read_text(encoding="utf-8")
MANUAL = (ROOT / "docs" / "manual" / "teacher" / "14-admin-lecture-studio.md").read_text(encoding="utf-8")

_DIGIT_RE = re.compile(r"[0-9０-９]")


# ---------------------------------------------------------------------------
# IK-0908 / IK-0915 保存の可否と届き方
# ---------------------------------------------------------------------------


class TestSaveFacts:
    def test_owner_can_save_without_notice(self):
        out = studio_state.topic_save_facts(is_owner=True)
        assert out == {"can_save": True}

    def test_editor_cannot_save_and_is_told_why(self):
        out = studio_state.topic_save_facts(is_owner=False, delivered=True)
        assert out["can_save"] is False
        assert out["save_notice"] == studio_state.TOPIC_SAVE_OWNER_ONLY_NOTICE
        assert "所有者" in out["save_notice"] and "依頼" in out["save_notice"]

    def test_system_admin_can_save(self):
        assert studio_state.topic_save_facts(is_owner=False, is_system_admin=True)["can_save"] is True

    def test_delivery_immediate_without_release(self):
        out = studio_state.topic_save_facts(is_owner=True, delivered=True)
        assert out["delivery"]["status"] == "immediate"
        assert "すぐ受講者に届きます" in out["delivery"]["message"]

    def test_delivery_release_names_version_and_how_to_deliver(self):
        out = studio_state.topic_save_facts(is_owner=True, delivered=True, release_version_no=3)
        assert out["delivery"]["status"] == "release"
        assert "版 3" in out["delivery"]["message"] and "新しい版を発行" in out["delivery"]["message"]

    def test_private_course_without_release_says_nothing(self):
        assert "delivery" not in studio_state.topic_save_facts(is_owner=True, delivered=False)

    def test_course_is_delivered(self):
        assert studio_state.course_is_delivered(visibility="public", is_published=True)
        assert studio_state.course_is_delivered(visibility="group", is_published=False, group_id="g")
        assert not studio_state.course_is_delivered(visibility="private", is_published=False)


class _FakeResult:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class _FakeSession:
    def __init__(self, owner_id):
        self.owner_id = owner_id
        self.updated = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "UPDATE learning_courses" in sql:
            self.updated = True
        return _FakeResult(row=(self.owner_id,))

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class TestSaveEndpoint:
    def _setup(self, monkeypatch, owner_id):
        import routes.lecture_studio.topics as topics_mod

        course = {"topics": [{"id": "t1", "title": "T", "student_material": {"source_text": "本文"}}]}
        monkeypatch.setattr(topics_mod, "get_viewable_course_data", lambda uid, cid: course)
        session = _FakeSession(owner_id)
        monkeypatch.setattr(topics_mod, "_pg_session", lambda: session)
        monkeypatch.setattr(topics_mod, "record_review_event", lambda *a, **k: None)
        return topics_mod, session

    def test_forbidden_detail_is_japanese_fact(self, monkeypatch):
        from fastapi import HTTPException

        topics_mod, session = self._setup(monkeypatch, owner_id="owner")
        try:
            topics_mod.save_lecture_studio_course_topic(
                "c1", "t1", {"student_material": {"source_text": "x"}},
                current_user={"id": "editor", "role": "TEACHER"},
            )
        except HTTPException as exc:
            assert exc.status_code == 403
            assert exc.detail == studio_state.TOPIC_SAVE_FORBIDDEN_DETAIL
        else:  # pragma: no cover
            raise AssertionError("403 が返るべき")
        assert session.updated is False

    def test_save_response_carries_delivery(self, monkeypatch):
        topics_mod, _ = self._setup(monkeypatch, owner_id="owner")
        monkeypatch.setattr(
            topics_mod, "_topic_save_facts_for",
            lambda cid, user: studio_state.topic_save_facts(is_owner=True, delivered=True, release_version_no=2),
        )
        out = topics_mod.save_lecture_studio_course_topic(
            "c1", "t1", {"student_material": {"source_text": "x"}},
            current_user={"id": "owner", "role": "TEACHER"},
        )
        assert out["delivery"]["status"] == "release"


# ---------------------------------------------------------------------------
# IK-0909 書き換えの根拠の予算
# ---------------------------------------------------------------------------


def _big_evidence():
    eq_items = [
        {"equation_id": f"eq_{i}", "semantic_kind": "Equation semantics could not be inferred.",
         "latex": "a" * 200, "label": str(i)}
        for i in range(60)
    ]
    return {
        "summary": "概要",
        "evidence_links": [{"kind": "claim", "summary": "主張の本文" * 30} for _ in range(20)],
        "content_blocks": [{"type": "equations", "items": eq_items}],
        "units": [{"kind": "節", "label": "JCMT POL-2 の観測", "summary": "偏光の観測"}],
    }


class TestCompactRewriteEvidence:
    def test_units_survive_and_json_is_valid(self):
        compact, trimmed = studio_state.compact_rewrite_evidence(_big_evidence())
        text = json.dumps(compact, ensure_ascii=False, indent=2)
        assert len(text) <= studio_state.REWRITE_EVIDENCE_LIMIT
        assert json.loads(text)["units"][0]["label"] == "JCMT POL-2 の観測"
        assert list(compact)[0] == "units"
        assert trimmed  # 何かを省いた
        assert "units" not in trimmed

    def test_uninformative_state_text_is_dropped(self):
        compact, _ = studio_state.compact_rewrite_evidence({
            "content_blocks": [{"type": "equations", "items": [
                {"equation_id": "eq_1", "semantic_kind": "Equation semantics could not be inferred."},
            ]}],
        })
        assert "could not be inferred" not in json.dumps(compact)

    def test_small_evidence_is_untouched(self):
        evidence = {"summary": "概要", "units": [{"label": "U"}]}
        compact, trimmed = studio_state.compact_rewrite_evidence(evidence)
        assert compact == {"units": [{"label": "U"}], "summary": "概要"} and trimmed == []

    def test_notice_names_trimmed_material_without_keys(self):
        notice = studio_state.rewrite_grounding_trimmed_notice(["content_blocks", "evidence_links"])
        assert "式・部品の一覧" in notice and "content_blocks" not in notice
        assert "学ぶ単位は省いていません" in notice

    def test_rewrite_prompt_keeps_units(self, monkeypatch):
        import routes.lecture_studio._shared as shared_mod
        import routes.lecture_studio.topics as topics_mod

        evidence = _big_evidence()
        topic = {
            "id": "t1", "title": "T", "student_material": {"source_text": "## T\n本文があります"},
            "evidence_links": evidence["evidence_links"], "content_blocks": evidence["content_blocks"],
            "units": [{"stable_key": "k1:x", "label": "JCMT POL-2 の観測"}],
        }
        monkeypatch.setattr(shared_mod, "get_editable_course_data", lambda uid, cid: {"topics": [topic]})
        monkeypatch.setattr(topics_mod, "consume_lecture_rewrite_quota", lambda *a, **k: None)
        monkeypatch.setattr(topics_mod, "_rewrite_unit_grounding", lambda cd, t: evidence["units"])
        monkeypatch.setattr(topics_mod, "_topic_figures_for_prompt", lambda t: [])
        monkeypatch.setattr(
            topics_mod, "_topic_save_facts_for",
            lambda cid, user: studio_state.topic_save_facts(is_owner=False),
        )
        captured = {}

        def _call(prompt, *a, **k):
            captured["prompt"] = prompt
            return {"student_material": {"source_text": "## T\n新しい本文"}, "spoken_script": "話す"}

        monkeypatch.setattr(topics_mod, "structured_call", _call)
        out = topics_mod.rewrite_lecture_studio_course_topic(
            "c1", "t1", {"prompt": "やさしく"}, current_user={"id": "u1", "role": "TEACHER"},
        )
        prompt = captured["prompt"]
        block = prompt.split("根拠候補:\n", 1)[1].split("\n\n現在の授業用ドラフト:", 1)[0]
        assert json.loads(block)["units"][0]["label"] == "JCMT POL-2 の観測"
        assert "could not be inferred" not in block
        assert out["grounding_trimmed"] and out["grounding_notice"]
        assert out["save_notice"] == studio_state.TOPIC_SAVE_OWNER_ONLY_NOTICE


# ---------------------------------------------------------------------------
# IK-0873 の残り 根拠リンクの式の見出し
# ---------------------------------------------------------------------------


class TestStudioEquationTitles:
    def test_english_summary_title_falls_back_to_role(self):
        items = [{"kind": "equation", "title": "Fragment of the Horndeski Lagrangian…",
                  "semantic_kind": "Fragment of the Horndeski Lagrangian that defines L_4.",
                  "role_in_argument": "premise"}]
        out = studio_state.studio_equation_titles(items)
        assert out[0]["title"] == "前提式"
        assert items[0]["title"].startswith("Fragment")  # 入力を mutate しない

    def test_human_english_label_is_kept(self):
        items = [{"kind": "equation", "title": "Horndeski action", "semantic_kind": "Defines the action.",
                  "role_in_argument": "definition"}]
        assert studio_state.studio_equation_titles(items)[0]["title"] == "Horndeski action"

    def test_duplicates_get_distinguishing_section(self):
        items = [
            {"kind": "equation", "title": "前提式", "section_label": "2.1 設定"},
            {"kind": "equation", "title": "前提式", "section_label": "3 結果"},
            {"kind": "equation", "title": "式 (6)"},
        ]
        titles = [i["title"] for i in studio_state.studio_equation_titles(items)]
        assert titles[0] == "前提式（2.1 設定）" and titles[1] == "前提式（3 結果）" and titles[2] == "式 (6)"

    def test_same_section_duplicates_use_reading(self):
        items = [
            {"kind": "equation", "title": "前提式", "section_label": "2", "plain_text": "alpha T equals zero"},
            {"kind": "equation", "title": "前提式", "section_label": "2", "plain_text": "alpha M equals zero"},
        ]
        titles = [i["title"] for i in studio_state.studio_equation_titles(items)]
        assert titles[0] != titles[1] and "（2）" not in titles[0]


# ---------------------------------------------------------------------------
# IK-0872 / IK-0880 の残り 付録の付け直し（明示の操作・読み時には付け直さない）
# ---------------------------------------------------------------------------

_OLD_APPENDIX = (
    "本文で作用 S を使う。\n\n### この節で使う数式\n"
    "- （本文を特定できない要素）: この節の説明で参照する中心的な関係式です。\n"
    "![[equation:eq_j]]![[figure:fig_1]]"
)


def _topic(text):
    return {
        "id": "t1",
        "student_material": {"source_text": text},
        "linked_equation_ids": ["eq_j"],
        "content_blocks": [{"type": "equations", "items": [{"equation_id": "eq_j", "label": "3", "latex": r"S=\int d^4x\sqrt{-g}\,\mathcal{L}"}]}],
    }


class TestAppendixReattach:
    def test_old_appendix_is_stale_and_reattach_fixes_it(self):
        topic = _topic(_OLD_APPENDIX)
        assert ccb.topic_appendix_is_stale(topic) is True
        out = ccb.reattach_generated_appendix(topic)
        assert out["changed"] and out["had_appendix"]
        text = out["source_text"]
        assert "中心的な関係式" not in text and "本文を特定できない要素" not in text
        assert "- 式 (3)" in text
        # 付録の行に連結されていた図は本文の段落として残る（IK-0880）。
        assert "\n\n![[figure:fig_1]]" in text
        assert "![[equation:eq_j]]![[figure:" not in text
        assert topic["student_material"]["source_text"] == _OLD_APPENDIX  # mutate しない

    def test_reattach_is_idempotent(self):
        once = ccb.reattach_generated_appendix(_topic(_OLD_APPENDIX))["source_text"]
        assert ccb.topic_appendix_is_stale(_topic(once)) is False

    def test_topic_without_appendix_is_not_stale(self):
        assert ccb.topic_appendix_is_stale(_topic("本文で作用 S を使う。")) is False

    def test_studio_view_carries_appendix_state(self):
        import routes.lecture_studio.topics as topics_mod

        view = topics_mod._topic_studio_view(_topic(_OLD_APPENDIX), {"document_ids": [], "audio_topics": set()})
        assert view["appendix_state"] == {"stale": True, "message": studio_state.APPENDIX_STALE_NOTICE}
        fresh = topics_mod._topic_studio_view(_topic("本文。"), {"document_ids": [], "audio_topics": set()})
        assert "appendix_state" not in fresh

    def test_reattach_endpoint_is_read_only(self, monkeypatch):
        import routes.lecture_studio._shared as shared_mod
        import routes.lecture_studio.topics as topics_mod

        topic = _topic(_OLD_APPENDIX)
        monkeypatch.setattr(shared_mod, "get_editable_course_data", lambda uid, cid: {"topics": [topic]})

        def _no_db():
            raise AssertionError("付け直しは DB を書かない")

        monkeypatch.setattr(topics_mod, "_pg_session", _no_db)
        out = topics_mod.reattach_lecture_studio_topic_appendix(
            "c1", "t1", {"student_material": {"source_text": _OLD_APPENDIX}},
            current_user={"id": "u1", "role": "TEACHER"},
        )
        assert out["changed"] is True and out["notice"] == studio_state.APPENDIX_REATTACH_APPLIED_NOTICE
        assert topic["student_material"]["source_text"] == _OLD_APPENDIX

    def test_confirm_says_audio_and_slides_move(self):
        assert "音声は作り直し" in studio_state.APPENDIX_REATTACH_CONFIRM
        assert "スライドの区切り" in studio_state.APPENDIX_REATTACH_CONFIRM


class _MapRows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return self._rows


class _RowsSession:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, stmt, params=None):
        return _MapRows(self.rows)


class TestNextStepAppendixStale:
    def test_rule_is_guidance_only_and_lists_topics_without_counts(self):
        from core.admin_assistant import capabilities as caps
        from core.admin_assistant import next_steps as ns

        rule = ns.RULE_CATALOG[ns.RULE_COURSE_TOPIC_APPENDIX_STALE]
        cap = caps.get_capability(rule["capability_id"])
        assert cap is not None and cap.kind == caps.KIND_GUIDANCE_ONLY
        rows = [{"id": "c1", "title": "宇宙論", "created_at": None,
                 "data": {"topics": [{**_topic(_OLD_APPENDIX), "title": "設定"}, {**_topic("本文。"), "title": "結果"}]}}]
        steps = ns._eval_course_topic_appendix_stale(_RowsSession(rows), "u1")
        assert len(steps) == 1
        step = steps[0][0]
        assert "『設定』" in step.reason and "『結果』" not in step.reason
        assert len(step.locate_plan["steps"]) == 1  # コースの選択まで（以降は理由の文が言う）
        assert not _DIGIT_RE.search(step.reason)

    def test_no_stale_topic_no_step(self):
        from core.admin_assistant import next_steps as ns

        rows = [{"id": "c1", "title": "宇宙論", "created_at": None, "data": {"topics": [_topic("本文。")]}}]
        assert ns._eval_course_topic_appendix_stale(_RowsSession(rows), "u1") == []


# ---------------------------------------------------------------------------
# IK-0914 区切りの事実文
# ---------------------------------------------------------------------------


class TestIgnoredMarkers:
    def test_trailing_and_consecutive_markers_are_reported(self):
        from core.lecture import has_ignored_slide_markers, slide_split_notes

        assert has_ignored_slide_markers("a\n===\nb\n===")
        assert has_ignored_slide_markers("===\na")
        assert has_ignored_slide_markers("a\n===\n===\nb")
        assert not has_ignored_slide_markers("a\n===\nb")
        assert not has_ignored_slide_markers("a")
        notes = slide_split_notes(
            suppressed=True, mismatch=False, display_count=2, spoken_count=2,
            display_text="a\n===\nb", spoken_text="x\n===\ny\n===",
        )
        assert any("読み上げ欄の先頭・末尾" in n for n in notes)
        assert not any("教材欄と" in n for n in notes)

    def test_preview_split_passes_texts(self):
        import routes.lecture_studio.scripts as scripts_mod

        body = scripts_mod.LecturePreviewSplitRequestWithPagination(
            display_text="前半\n===\n後半", spoken_text="読み上げ\n===\n", auto_paginate=True,
        )
        out = scripts_mod.preview_split_slides(body, current_user={"id": "u", "role": "TEACHER"})
        assert any("スライドにならず" in n for n in out["notes"])


# ---------------------------------------------------------------------------
# IK-0912 教材が問いに関わるかの判定
# ---------------------------------------------------------------------------


class TestMaterialEngagesMessage:
    def _body(self, message):
        from types import SimpleNamespace

        return SimpleNamespace(message=message, selection_text="", element_id=None, chunk_id=None)

    def test_frame_words_do_not_count_against_material(self):
        import routes.learning as learning_mod

        material = r"TOV 方程式のラグランジアン $\mathcal{L}_{\rm TOV}$ は星の内部構造を与える。"
        assert learning_mod._topic_material_engages_message(
            self._body("教材の L TOV ってやつの式、言葉で言うとどういう意味？"), material,
        ) is True

    def test_tex_wrapped_name_matches(self):
        import routes.learning as learning_mod

        material = r"作用 $S=\int d^4x \sqrt{-g}\,\mathcal{L}_{\rm Horndeski}$ を置く。"
        assert learning_mod._topic_material_engages_message(self._body("Horndeski の作用とは？"), material)

    def test_unrelated_question_still_not_material(self):
        import routes.learning as learning_mod

        material = r"TOV 方程式のラグランジアン $\mathcal{L}_{\rm TOV}$ は星の内部構造を与える。"
        assert learning_mod._topic_material_engages_message(
            self._body("ブラックホールの蒸発とホーキング放射を説明して"), material,
        ) is False


# ---------------------------------------------------------------------------
# IK-0911 役割・共有の権限語
# ---------------------------------------------------------------------------


class TestRoleLabels:
    def test_labels_are_japanese(self):
        for table in (label_vocab.COURSE_ROLE_LABELS, label_vocab.COURSE_SHARE_PERMISSION_LABELS):
            for value in table.values():
                assert not re.search(r"\b(owner|editor|viewer)\b", value)

    def test_permission_out_carries_label(self):
        from schemas import CourseGroupPermissionOut

        out = CourseGroupPermissionOut(course_id="c", group_id="g", permission="editor")
        assert out.permission_label == label_vocab.COURSE_SHARE_PERMISSION_LABELS["editor"]

    def test_admin_js_uses_server_labels_and_mirrors_options(self):
        assert "c.role_label" in ADMIN_JS
        assert "p.permission_label" in ADMIN_JS
        assert ">viewer</span>" not in ADMIN_JS and ">editor</span>" not in ADMIN_JS
        assert "editor (編集可)" not in ADMIN_JS
        for key, label in label_vocab.COURSE_SHARE_PERMISSION_LABELS.items():
            assert f'<option value="{key}">{label}</option>' in ADMIN_JS

    def test_course_list_returns_role_label(self):
        src = (ROOT / "backend" / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
        assert '"role_label": COURSE_ROLE_LABELS.get(' in src


# ---------------------------------------------------------------------------
# UI（IK-0908 / IK-0910 / IK-0913 / 付録）
# ---------------------------------------------------------------------------


class TestStudioUiStatic:
    def test_save_button_follows_server_can_save(self):
        assert "cs.can_save !== false" in STUDIO_JS
        assert "saveBtn.disabled = !lsCourseCanSave();" in STUDIO_JS
        assert 'document.getElementById("ls-save-btn").disabled = !lsCourseCanSave();' in STUDIO_JS
        assert "data-topic-save-notice" in STUDIO_JS

    def test_error_detail_is_shown(self):
        assert "lsApiDetailText(body) || \"保存に失敗しました\"" in STUDIO_JS

    def test_server_facts_refetched_after_adopt_and_save(self):
        assert "lsRefreshTopicServerFacts(topic, { keepDraft: true });" in STUDIO_JS
        assert "lsRefreshTopicServerFacts(topic);" in STUDIO_JS

    def test_delivery_notice_is_rendered(self):
        assert "data-topic-delivery-notice" in STUDIO_JS
        assert "data.delivery.message" in STUDIO_JS

    def test_reattach_button_and_confirm_from_server(self):
        assert 'data-ui-anchor="lecture-studio.reattach-appendix"' in STUDIO_JS
        assert "appendix_reattach_confirm" in STUDIO_JS
        assert "/appendix/reattach" in STUDIO_JS
        # 確認文を JS にミラーしない。
        assert studio_state.APPENDIX_REATTACH_CONFIRM not in STUDIO_JS

    def test_slides_indicator_anchor_and_manual(self):
        assert 'data-ui-anchor="lecture-studio.slides-indicator"' in STUDIO_JS
        assert "{#slides-indicator}" in MANUAL and "{#reattach-appendix}" in MANUAL
        assert "WMレンズ" not in MANUAL.split("{#slide-wm-label}")[0].splitlines()[-1]

    def test_versions_bumped(self):
        assert "admin-lecture-studio.js?v=round25-doubt-20261023-2" not in ADMIN_HTML
        assert "admin.js?v=round25-share-r2-20261023-1" not in ADMIN_HTML
