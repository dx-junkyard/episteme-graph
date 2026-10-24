"""第 25 周 面 D 第 1 周の偽所見の是正（harness_fixes_studio.md H1〜H10・MockTransport・外部呼び出しなし）。"""
from __future__ import annotations

import copy
import json

from uxsim.oracles import studio as oracle
from uxsim.runner import actions_exec
from uxsim.runner import studio_exec as sx
from uxsim.runner.state import PersonaSession, material_anchors, project_observation
from uxsim.runner.vision5_exec import course_tree
from uxsim.tests.test_r25_studio import (SCRIPTS, STRUCTURE, _client, _factory, _run, _step, _teacher_routes)


def _teacher(structure=None, scripts=None, extra=None, split=None):
    seen: list = []
    routes = _teacher_routes(split=split) if split else _teacher_routes()
    if structure is not None:
        routes[("GET", "/lecture-studio/course-structure")] = structure
    if scripts is not None:
        routes[("GET", "/lecture-scripts")] = scripts
    routes.update(extra or {})
    client = _client(seen, routes)
    s = PersonaSession(persona_id="te-01", course_id="c1", role="TEACHER")
    _run("admin.lecture_studio.open_course", {}, s, client)
    return s, client, seen


# H1 ------------------------------------------------------------------------

def test_chunk_slides_uses_open_topic_chunk_not_longest():
    st = copy.deepcopy(STRUCTURE)
    st["chapters"][0]["topics"][0]["linked_chunk_ids"] = ["k2"]
    scripts = copy.deepcopy(SCRIPTS)
    scripts[1]["display_text"] = "トピックの本文"
    s, client, seen = _teacher(structure=st, scripts=scripts)
    _run("admin.lecture_studio.select_topic", {"topic_hint": "1"}, s, client)
    _run("admin.lecture_studio.chunk_slides", {}, s, client)
    assert s.scratch["studio_chunk"]["chunk_id"] == "k2"  # === を含む k1 ではなく、トピックのチャンク
    assert json.loads(seen[-1].content)["display_text"] == "トピックの本文"


def test_chunk_slides_topic_without_chunks_is_unsupported():
    s, client, seen = _teacher()
    _run("admin.lecture_studio.select_topic", {"topic_hint": "1"}, s, client)
    n = len(seen)
    _run("admin.lecture_studio.chunk_slides", {}, s, client)
    assert len(seen) == n  # プレビューを問い合わせない
    shown = sx.project("admin.lecture_studio.chunk_slides", s.last_status, s.last_body)
    assert "紐づくチャンクは原稿一覧に無い" in shown
    assert any(x.startswith("unsupported_ui_state:") for x in s.scratch["runner_notes"])


def test_chunk_slides_hint_still_picks_from_whole_list():
    s, client, _ = _teacher()
    _run("admin.lecture_studio.select_topic", {"topic_hint": "1"}, s, client)
    _run("admin.lecture_studio.chunk_slides", {"chunk_hint": "序論"}, s, client)
    assert s.scratch["studio_chunk"]["chunk_id"] == "k1"


# H2 ------------------------------------------------------------------------

def test_marker_goes_to_sentence_boundary_near_other_field_marker():
    text, _, how = sx.insert_marker("一文目。二文目。三文目。四文目。", None, target_ratio=0.25)
    assert how == "sentence"
    assert text.startswith("一文目。\n===\n二文目。")


def test_marker_without_any_boundary_reports_end():
    text, _, how = sx.insert_marker("区切れない一文", None)
    assert how == "end" and text.endswith("\n===\n")


def test_spoken_marker_follows_material_marker():
    st = copy.deepcopy(STRUCTURE)
    st["chapters"][0]["topics"][1]["spoken_script"] = "はじめ。なか。おわり。"
    s, client, _ = _teacher(structure=st)
    _run("admin.lecture_studio.select_topic", {"prefer": "long"}, s, client)
    _run("admin.lecture_studio.insert_slide_marker", {"field": "material", "after_paragraph": 1}, s, client)
    _run("admin.lecture_studio.insert_slide_marker", {"field": "spoken"}, s, client)
    spoken = s.scratch["studio_draft"]["spoken"]
    assert "\n===\n" in spoken and not spoken.endswith("===\n")
    assert any("文の境目" in x for x in s.scratch["runner_notes"])


# H3 ------------------------------------------------------------------------

def test_figure_inserted_after_heading():
    st = copy.deepcopy(STRUCTURE)
    st["chapters"][0]["topics"][0]["student_material"] = {"source_text": "## 導入\n\n本文。\n\n## 結果\n本文2。"}
    extra = {("POST", "/figure-studio/turn"): {"reply": "描きました", "svg_source": "<svg><text>x</text></svg>",
                                                "title": "t", "caption": "c", "figure_kind": "concept_map"},
             ("POST", "/teaching-figures"): {"figure_id": "f9", "linked_figure_ids": ["f9"]}}
    s, client, _ = _teacher(structure=st, extra=extra)
    _run("admin.lecture_studio.select_topic", {"topic_hint": "1"}, s, client)
    _run("admin.figure_studio.turn", {"instruction": "図を"}, s, client)
    _run("admin.figure_studio.adopt", {"after_heading": "導入"}, s, client)
    assert s.scratch["studio_draft"]["material"].startswith("## 導入\n![[figure:f9]]\n本文。")
    assert any("見出し「導入」の直後" in x for x in s.scratch["runner_notes"])


def test_heading_without_blank_line_adds_newline():
    pos, prefix, head = sx.heading_insert_pos("## 結果\n本文", "1")
    assert (pos, prefix, head) == (len("## 結果"), "\n", "結果")
    assert sx.heading_insert_pos("本文だけ", "導入") is None


# H5 ------------------------------------------------------------------------

ANCHORS = {"anchors": {"material.lecture-toggle": {"title": "9. **レクチャー**", "body": "長い本文。" * 200,
                                                   "manual_anchor": "student/02-student.md#interactive-lecture"},
                       "composer.input": {"title": "6. AI チャット", "body": "入力欄。"}}}


class _Fake:
    def __init__(self, body):
        self.body, self.calls, self.traces = body, [], []

    def call(self, method, path, json=None, params=None):
        from uxsim.schema import HttpTrace
        self.calls.append((method, path, json))
        self.traces.append(HttpTrace(method=method, path=path, status=200))
        return 200, self.body, None

    def take_traces(self):
        t, self.traces = self.traces, []
        return t


def test_help_inspect_shows_one_tooltip_in_full():
    s = PersonaSession(persona_id="st-08", course_id="C", topic_id="t1")
    actions_exec.execute("learning.help.inspect", {"anchor_id": "material.lecture-toggle"}, s, _Fake(ANCHORS))
    obs = project_observation("learning.help.inspect", s.last_status, s.last_body)
    assert "📖 使い方" in obs and "9. レクチャー" in obs
    assert "AI チャット" not in obs and "manual_anchor" not in obs
    assert obs.count("長い本文。") == 200  # 画面のツールチップは本文を切らない


def test_help_inspect_without_anchor_shows_banner_and_names():
    s = PersonaSession(persona_id="st-08", course_id="C", topic_id="t1")
    actions_exec.execute("learning.help.inspect", {}, s, _Fake(ANCHORS))
    obs = project_observation("learning.help.inspect", s.last_status, s.last_body)
    assert obs.startswith("使い方を確認中") and "長い本文" not in obs
    assert "（操作に使う番号 — 画面には出ない" in obs and "composer.input" in obs


# H6 ------------------------------------------------------------------------

def test_visible_anchors_are_only_embedded_ones_with_full_titles():
    long_title = "Modified DCF approximation relating the magnetic field strength to the velocity dispersion"
    body = {"chunks": [
        {"text": "一頁目。", "evidence_items": [{"kind": "claim", "id": "c1", "title": "根拠だけ"}]},
        {"text": "二頁目 ![[component:k1]] と ![[equation:eq_3]]", "evidence_items": [
            {"kind": "component", "id": "k1", "title": long_title},
            {"kind": "equation", "id": "eq_3", "title": "英語の題", "latex": "B_0 = 1"}]}]}
    anchors = material_anchors(body)
    assert [(a["kind"], a["id"], a["segment"]) for a in anchors] == [("component", "k1", 1), ("equation", "eq_3", 1)]
    assert anchors[0]["title"] == long_title and anchors[1]["title"] == "$B_0 = 1$"


# H9 ------------------------------------------------------------------------

def test_course_tree_moves_topic_ids_to_address_line():
    out = course_tree({"title": "C", "chapters": [{"title": "第1章"}],
                       "topics": [{"id": "t0", "title": "問題設定", "chapter_index": 0}]})
    assert "- 問題設定" in out and "- t0" not in out
    assert out.splitlines()[-1] == "（操作に使う番号 — 画面には出ない。topic_id に渡す）: 問題設定=t0"


def test_materials_list_moves_document_id_to_address_line():
    out = project_observation("admin.materials.list", 200, [{"title": "論文A", "document_id": "d-1", "status": "completed"}])
    assert "document_id=" not in out and "論文A=d-1" in out


# H10 -----------------------------------------------------------------------

def test_readonly_flags_and_coverage_digest_from_select_topic():
    st = copy.deepcopy(STRUCTURE)
    t = st["chapters"][0]["topics"][1]
    t["cautions"] = ["式 eq_blk_003_0065 は使わない", "Equation semantics could not be inferred."]
    t["student_material"] = {"source_text": "本文 ![[equation:eq_blk_003_0066]]"}
    s, client, _ = _teacher(structure=st)
    traces = _run("admin.lecture_studio.select_topic", {"prefer": "long"}, s, client)
    ro = traces[-1].digest["studio_readonly"]
    assert ro["internal_ids"] == {"cautions": ["eq_blk_003_0065"]}  # 埋め込み記法の中は数えない
    assert ro["failure_phrases"] == {"cautions": ["could not be inferred"]}
    cov = traces[-1].digest["studio_coverage"]
    assert cov["center_status"] == "weak" and cov["right_pane_has_items"] is False


def test_rewrite_digest_records_empty_grounding_and_unchanged():
    st = copy.deepcopy(STRUCTURE)
    extra = {("POST", "/draft/rewrite"): {"student_material": {"source_text": LONG_T2(st)}, "spoken_script": "読み上げ"}}
    s, client, _ = _teacher(structure=st, extra=extra)
    _run("admin.lecture_studio.select_topic", {"prefer": "long"}, s, client)
    traces = _run("admin.lecture_studio.rewrite_topic", {"prompt": "やさしく"}, s, client)
    rw = next(t.digest["studio_rewrite"] for t in traces if "studio_rewrite" in t.digest)
    assert rw["grounding_empty"] and rw["llm_called"] and rw["unchanged"]


def LONG_T2(st):
    return st["chapters"][0]["topics"][1]["student_material"]["source_text"]


def test_oracle_t7_t8_t9():
    steps = [_step(1, "admin.lecture_studio.select_topic", {
                "studio_readonly": {"internal_ids": {"cautions": ["eq_blk_1"]}, "failure_phrases": {"evidence": ["could not be inferred"]}},
                "studio_coverage": {"topic_id": "t2", "center_status": "weak", "right_pane_has_items": False}}),
             _step(2, "admin.lecture_studio.select_topic", {
                 "studio_coverage": {"topic_id": "t1", "center_status": "missing", "right_pane_has_items": True,
                                     "right_pane_kinds": ["equation"]}}),
             _step(3, "admin.lecture_studio.rewrite_topic", {"studio_rewrite": {
                 "status": 200, "grounding_empty": True, "llm_called": True, "unchanged": True, "reported_applied": True}})]
    found = oracle.check(steps, _factory())
    assert len(found) == 6
    hyps = [f.hypothesis for f in found]
    assert all(not any(ch.isdigit() for ch in h) for h in hyps)
    assert any("内部 ID" in h for h in hyps) and any("失敗した状態" in h for h in hyps)
    assert sum("区画ごとに違って" in h for h in hyps) == 2
    assert any("空の材料" in h for h in hyps) and any("反映しました" in h for h in hyps)


def test_oracle_t9_quiet_when_grounded_or_skipped():
    steps = [_step(1, "admin.lecture_studio.rewrite_topic", {"studio_rewrite": {
        "status": 200, "grounding_empty": False, "llm_called": True, "unchanged": False, "reported_applied": True}}),
             _step(2, "admin.lecture_studio.rewrite_topic", {"studio_rewrite": {
                 "status": 200, "grounding_empty": True, "llm_called": False, "unchanged": False, "reported_applied": True}})]
    assert oracle.check(steps, _factory()) == []
