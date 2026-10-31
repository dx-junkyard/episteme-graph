"""第 25 周 面 D（原稿スタジオ → 学生の受講）の行為・投影・審判（MockTransport・外部呼び出しなし・DB なし）。

行為 = ``uxsim/actions/studio_actions.py`` / 実行と投影 = ``uxsim/runner/studio_exec.py`` / 審判 = ``uxsim/oracles/studio.py``。
"""
from __future__ import annotations

import json

import httpx

from uxsim.actions.registry import REGISTRY
from uxsim.actions.studio_actions import ACTIONS
from uxsim.oracles import studio as oracle
from uxsim.oracles.findings import FindingFactory
from uxsim.runner import studio_exec as sx
from uxsim.runner.actions_exec import _Ctx
from uxsim.runner.client import EpistemeClient
from uxsim.runner.state import PersonaSession
from uxsim.schema import HttpTrace, RunMeta, TranscriptStep

LONG = "第一段落。" * 40 + "\n\n" + "第二段落。" * 40 + "\n\n" + "第三段落。" * 40
STRUCTURE = {
    "course_id": "c1", "title": "論文ゼミ", "course_status": "partial", "total_chunks": 2, "generated_chunks": 1,
    "audio_chunks": 0, "course_content_status": {"status": "completed"}, "figures_index": {},
    "chapters": [{"chapter_index": 0, "title": "第一章", "topics": [
        {"id": "t1", "topic_index": 0, "title": "短いトピック", "student_material": {"source_text": "短い本文"},
         "spoken_script": "短い読み上げ", "evidence_items": [{"kind": "claim", "id": "cl1", "title": "主張A", "summary": "本文"}],
         "evidence_links": [{"kind": "claim", "target_id": "cl1"}], "coverage": {}, "content_confidence": "exact_title"},
        {"id": "t2", "topic_index": 1, "title": "長いトピック", "student_material": {"source_text": LONG},
         "spoken_script": "読み上げ", "evidence_items": [], "coverage": {}},
    ]}],
}
SCRIPTS = [{"chunk_id": "k1", "chunk_index": 0, "text": "x", "display_text": "表示\n===\n二枚目", "spoken_text": "読み\n===\n二",
            "formulas": [], "status": "generated", "document_id": "d1", "section_title": "序論", "spoken_language": "ja",
            "slide_count": 2, "slide_mismatch": False, "audio_ready_slides": 0},
           {"chunk_id": "k2", "chunk_index": 1, "text": "y", "display_text": "y", "spoken_text": "", "formulas": [],
            "status": "ungenerated", "document_id": "d1"}]


def _split_body(display: int, spoken: int, wm: bool = False) -> dict:
    slides = [{"slide_index": i, "display_text": f"s{i}", "spoken_text": "", "formulas": []} for i in range(display)]
    if wm and slides:
        slides[0]["wm"] = {"level": "many", "level_label": "多い", "fact": "記号が同時に現れます。"}
    return {"slides": slides, "mismatch": False, "display_segment_count": display, "spoken_segment_count": spoken,
            "auto_paginated": display > 1, "spoken_degraded": False}


def _client(seen, routes):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for (method, frag), resp in routes.items():
            if request.method == method and frag in request.url.path:
                body = resp(request) if callable(resp) else resp
                if isinstance(body, tuple):
                    return httpx.Response(body[0], json=body[1])
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"detail": "not found"})
    return EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(handler))


def _run(action_id, args, session, client):
    client.take_traces()
    ctx = _Ctx(REGISTRY[action_id], args, session, client)
    assert sx.execute(action_id, ctx)
    return client.take_traces()


def _teacher_routes(split=lambda req: _split_body(3, 1)):
    return {("GET", "/lecture-studio/course-structure"): STRUCTURE, ("GET", "/lecture-scripts"): SCRIPTS,
            ("GET", "/lecture-studio/settings"): {"lecture_language": "ja"},
            ("POST", "/lecture-studio/preview-split"): split,
            ("PUT", "/lecture-studio/course-topics/"): {"course_id": "c1", "topic_id": "t2", "status": "edited"}}


def test_actions_registered_with_handlers_and_anchors():
    ids = [a.id for a in ACTIONS]
    assert len(ids) == len(set(ids))
    for aid in ids + ["learning.lecture.audio_status", "learning.lecture.sequence"]:
        assert aid in REGISTRY and aid in sx.HANDLERS, aid
    assert all(a.llm_cost == "product" for a in ACTIONS if a.id in (
        "admin.lecture_studio.rewrite_topic", "admin.figure_studio.turn", "admin.figure_studio.build_from_suggestion",
        "admin.figure_studio.suggestions_generate"))
    # パイプラインの再実行は行為にしない。音声生成（TTS）は第 6 周（第 7 周の前提）から押す行為を 1 つだけ持つ
    # （admin.lecture_studio.audio_generate — 言語を切り替えない = 原稿の作り直しを起こさない）
    assert not any("lecture-scripts/generate" in p for a in ACTIONS for _, p in a.all_api)
    assert [a.id for a in ACTIONS if any("lecture-audio/generate" in p for _, p in a.all_api)] == \
        ["admin.lecture_studio.audio_generate"]


def test_split_insert_save_flow_projects_indicator_and_digest():
    seen: list = []
    client = _client(seen, _teacher_routes())
    s = PersonaSession(persona_id="te-01", course_id="c1", role="TEACHER")
    _run("admin.lecture_studio.open_course", {}, s, client)
    shown = sx.project("admin.lecture_studio.open_course", s.last_status, s.last_body)
    assert "\n第一章\n" in shown and "第1章: 第一章" not in shown and "読み上げ言語: 日本語" in shown  # IK-0952
    assert "読み上げ原稿が未生成のスライドがあります" in shown  # k2 が ungenerated
    traces = _run("admin.lecture_studio.select_topic", {"prefer": "long"}, s, client)
    assert s.scratch["studio_topic"]["topic"]["id"] == "t2"
    sent = json.loads(seen[-1].content)
    assert sent["auto_paginate"] is True and sent["formulas"] == [] and "document_id" not in sent
    assert traces[-1].digest["studio_split"]["display_count"] == 3
    _run("admin.lecture_studio.insert_slide_marker", {"field": "material", "after_paragraph": 1}, s, client)
    assert "\n===\n" in s.scratch["studio_draft"]["material"]
    shown = sx.project("admin.lecture_studio.insert_slide_marker", s.last_status, s.last_body)
    assert "表示 3 枚（自動ページ分割） / 読み上げ 1 区切り ✓" in shown
    traces = _run("admin.lecture_studio.save_topic", {}, s, client)
    # 第 25 周 第 2 波: 保存の後に画面は course-structure を読み直す（lsRefreshTopicServerFacts）
    put_req = next(r for r in reversed(seen) if r.method == "PUT")
    put = json.loads(put_req.content)
    assert put_req.url.path.endswith("/course-topics/t2") and seen[-1].method == "GET"
    assert "===" in put["student_material"]["source_text"] and put["topic_index"] == 1
    saved = next(t.digest["studio_saved"] for t in traces if "studio_saved" in t.digest)
    assert saved["status"] == 200 and saved["preview"]["display_count"] == 3 and not saved["notice_shown_before"]
    assert sx.SAVE_OK in sx.project("admin.lecture_studio.save_topic", s.last_status, s.last_body)


def test_save_failure_hides_detail_like_ui():
    seen: list = []
    routes = _teacher_routes()
    routes[("PUT", "/lecture-studio/course-topics/")] = (403, {"detail": "Forbidden"})
    client = _client(seen, routes)
    s = PersonaSession(persona_id="te-05", course_id="c1", role="TEACHER")
    _run("admin.lecture_studio.open_course", {}, s, client)
    _run("admin.lecture_studio.select_topic", {}, s, client)
    _run("admin.lecture_studio.save_topic", {}, s, client)
    shown = sx.project("admin.lecture_studio.save_topic", s.last_status, s.last_body)
    # 第 25 周 第 2 波: 画面はサーバの detail（lsApiDetailText）を出す。detail が無いときだけ固定文
    assert "Forbidden" in shown


def test_chunk_slides_sends_document_id_and_shows_wm_label():
    seen: list = []
    client = _client(seen, _teacher_routes(split=lambda req: _split_body(2, 2, wm=True)))
    s = PersonaSession(persona_id="te-03", course_id="c1", role="TEACHER")
    _run("admin.lecture_studio.open_course", {}, s, client)
    traces = _run("admin.lecture_studio.chunk_slides", {}, s, client)
    sent = json.loads(seen[-1].content)
    assert sent["document_id"] == "d1" and sent["auto_paginate"] is False
    shown = sx.project("admin.lecture_studio.chunk_slides", s.last_status, s.last_body)
    assert "要素の相互作用: 多い" in shown and "音声未生成" in shown and "記号が同時に現れます。" in shown
    assert traces[-1].digest["studio_split"]["wm"][0]["numeric_keys"] == []


def test_figure_turn_adopt_inserts_embed_and_notice():
    seen: list = []
    routes = _teacher_routes()
    routes[("POST", "/figure-studio/turn")] = {"reply": "描きました", "svg_source": "<svg><text>密度</text></svg>",
                                               "title": "関係図", "caption": "量の関係", "figure_kind": "concept_map",
                                               "degraded": False}
    routes[("POST", "/teaching-figures")] = {"figure_id": "f9", "figure": {}, "evidence_link": {"kind": "figure", "figure_id": "f9"},
                                             "linked_figure_ids": ["f9"], "image_snapshot_failed": False}
    # 第 4 波（IK-0979）: 採用の知らせは course-structure の figure_adopt_notice_parts から組む（固定文は廃止）
    parts = {"lead": "図を教材欄に入れました。トピックを保存すると確定します。",
             "audio": "保存すると、このトピックの生成済み音声は作り直しになります。",
             "delivery": "このコースは版を発行していないので、保存した内容はすぐ受講者に届きます。"}
    structure = json.loads(json.dumps(STRUCTURE))
    structure["figure_adopt_notice_parts"] = parts
    structure["chapters"][0]["topics"][0]["has_topic_audio"] = True
    routes[("GET", "/lecture-studio/course-structure")] = structure
    client = _client(seen, routes)
    s = PersonaSession(persona_id="te-01", course_id="c1", role="TEACHER")
    _run("admin.lecture_studio.open_course", {}, s, client)
    _run("admin.lecture_studio.select_topic", {"topic_hint": "1"}, s, client)
    _run("admin.figure_studio.turn", {"instruction": "関係図を"}, s, client)
    shown = sx.project("admin.figure_studio.turn", s.last_status, s.last_body)
    assert "図の中の文字: 密度" in shown and "［採用して挿入］［下書きとして保存］" in shown
    traces = _run("admin.figure_studio.adopt", {}, s, client)
    assert s.scratch["studio_draft"]["material"].endswith("![[figure:f9]]")
    assert any(t.digest.get("studio_adopt", {}).get("linked") for t in traces)
    shown = sx.project("admin.figure_studio.adopt", s.last_status, s.last_body)
    assert " ".join(parts.values()) in shown and sx.ADOPT_NOTICE_LEGACY not in shown
    traces = _run("admin.lecture_studio.save_topic", {}, s, client)
    saved = next(t.digest["studio_saved"] for t in traces if "studio_saved" in t.digest)
    assert saved["notice_shown_before"] is True and saved["figure_embeds"] == ["f9"]


def test_suggestions_error_is_shown_as_empty_like_ui():
    seen: list = []
    routes = _teacher_routes()
    routes[("GET", "/figure-suggestions")] = (500, {"detail": "boom"})
    client = _client(seen, routes)
    s = PersonaSession(persona_id="te-01", course_id="c1", role="TEACHER")
    _run("admin.lecture_studio.open_course", {}, s, client)
    _run("admin.lecture_studio.select_topic", {}, s, client)
    traces = _run("admin.figure_studio.suggestions", {}, s, client)
    assert "図の提案はまだありません" in sx.project("admin.figure_studio.suggestions", s.last_status, s.last_body)
    assert traces[-1].digest["studio_suggestions"]["shown_empty_on_error"] is True


def _student_session():
    s = PersonaSession(persona_id="st-08", course_id="c1", topic_id="t2")
    s.material = {"topic_id": "t2", "chunks": [{"id": "topic:t2", "text": "a", "figures": [
        {"figure_id": "f9", "caption": "量の関係", "image_url": "/api/learning/courses/c1/figures/f9/image"}]},
        {"id": "topic:t2", "text": "b"}]}
    return s


def test_lecture_sequence_without_audio_does_not_start():
    seen: list = []
    client = _client(seen, {("GET", "/audio-status"): {"has_audio": False, "total_slides": 3, "note": "音声はまだありません。"}})
    s = _student_session()
    traces = _run("learning.lecture.sequence", {}, s, client)
    assert not any("/sequence" in r.url.path for r in seen)
    shown = sx.project("learning.lecture.sequence", s.last_status, s.last_body)
    assert "押せない" in shown and sx.LECTURE_DISABLED_HINT in shown
    dig = traces[-1].digest["studio_student"]
    assert dig["material"]["pages"] == 2 and dig["audio_total_slides"] == 3


def test_lecture_sequence_with_audio_projects_deck_and_figure_view():
    seen: list = []
    seq = {"course_id": "c1", "topic_id": "t2", "total_slides": 2, "segments": [{"chunk_id": "topic:t2", "chunk_index": 0,
           "text": "", "spoken_text": "", "slides": [{"slide_index": 0, "display_text": "一枚目", "has_audio": True},
                                                      {"slide_index": 1, "display_text": "二枚目", "has_audio": False}]}]}
    client = _client(seen, {("GET", "/audio-status"): {"has_audio": True, "total_slides": 2},
                            ("GET", "/sequence"): seq,
                            ("GET", "/figures/f9/image"): {"ok": True}})
    s = _student_session()
    traces = _run("learning.lecture.sequence", {}, s, client)
    shown = sx.project("learning.lecture.sequence", s.last_status, s.last_body)
    # 第 25 周 studio H16: 再生開始は 1 枚目だけ。▶（learning.lecture.next）で 2 枚目へ
    assert "スライド 1 / 2" in shown and "二枚目" not in shown
    _run("learning.lecture.next", {}, s, client)
    shown = sx.project("learning.lecture.next", s.last_status, s.last_body)
    assert "スライド 2 / 2  🔇 音声未生成" in shown and "最後のスライド" in shown
    assert traces[-1].digest["studio_student"]["sequence"]["total_slides"] == 2
    traces = _run("learning.lecture.figure_view", {}, s, client)
    assert traces[-1].digest["studio_figure_view"]["status"] == 200
    assert "図の画像が表示された" in sx.project("learning.lecture.figure_view", s.last_status, s.last_body)


def test_project_ignores_other_faces():
    assert sx.project("learning.chat.ask", 200, {"answer": "x"}) is None


# ---------------------------------------------------------------------------
# 審判
# ---------------------------------------------------------------------------

def _factory():
    return FindingFactory(RunMeta(run_id="r1", campaign_id="c-x", domain="astro", snapshot="s",
                                  started_at="2026-10-22T00:00:00+00:00", finished_at="2026-10-22T01:00:00+00:00"))


def _step(seq, action, digest, persona="te-01"):
    return TranscriptStep(seq=seq, persona_id=persona, scenario_id="x", action_id=action,
                          screen="admin" if action.startswith("admin.") else "learning", affordance="a",
                          http=[HttpTrace(method="GET", path="/p", status=200, digest=digest)])


def _saved(**over):
    base = {"course_id": "c1", "topic_id": "t2", "status": 200, "text_sha": "x", "figure_embeds": ["f9"],
            "notice_shown_before": True, "preview": {"display_count": 3, "error": False}}
    base.update(over)
    return {"studio_saved": base}


def _student(pages=3, total=3, has_audio=False, figs=("f9",), note=True):
    return {"studio_student": {"course_id": "c1", "topic_id": "t2", "status": 200, "has_audio": has_audio,
                               "audio_total_slides": total, "note_present": note, "sequence": None,
                               "material": {"topic_id": "t2", "pages": pages, "topic_based": True,
                                            "figure_ids": list(figs), "raw_figure_embeds": []}}}


def test_oracle_clean_run_has_no_findings():
    steps = [_step(1, "admin.figure_studio.adopt", {"studio_adopt": {"course_id": "c1", "topic_id": "t2", "figure_id": "f9",
                                                                      "linked": True}}),
             _step(2, "admin.lecture_studio.save_topic", _saved()),
             _step(3, "learning.lecture.audio_status", _student(), persona="st-08")]
    assert oracle.check(steps, _factory()) == []


def test_oracle_cross_lane_mismatches():
    steps = [_step(1, "admin.figure_studio.adopt", {"studio_adopt": {"course_id": "c1", "topic_id": "t2", "figure_id": "f9",
                                                                      "linked": False}}),
             _step(2, "admin.lecture_studio.save_topic", _saved(notice_shown_before=False)),
             _step(3, "learning.lecture.audio_status", _student(pages=4, total=3, has_audio=True, figs=()), persona="st-08")]
    hyps = [f.hypothesis for f in oracle.check(steps, _factory())]
    assert any("区画の数と、音声の準備状態" in h for h in hyps)  # S1
    assert any("インジケータ" in h for h in hyps)  # X1
    assert any("用意済み" in h for h in hyps)  # X2
    assert any("図として解決されていない" in h for h in hyps)  # X3
    assert any("登録に入っておらず" in h for h in hyps)  # T5
    assert any("その事実文が画面に出ていない" in h for h in hyps)  # T6
    assert all(not any(ch.isdigit() for ch in h) for h in hyps)  # 仮説に数を書かない


def test_oracle_teacher_rules():
    steps = [_step(1, "admin.lecture_studio.chunk_slides", {"studio_split": {"wm": [
                {"keys": ["level", "level_label", "density"], "level_label": "多い", "numeric_keys": ["density"]}]}}),
             _step(2, "admin.lecture_studio.right_pane_stumble", {"studio_stumble": {"numeric_axes": ["誤り率"]}}),
             _step(3, "admin.figure_studio.suggestions", {"studio_suggestions": {"status": 500, "shown_empty_on_error": True}}),
             _step(4, "admin.lecture_studio.rewrite_topic", {"studio_rewrite": {"figures_restored": True, "restore_shown": False}})]
    found = oracle.check(steps, _factory())
    assert {f.oracle for f in found} == {"A", "B"}
    assert len(found) == 4


def test_oracle_figure_view_blocked_for_adopted():
    steps = [_step(1, "admin.figure_studio.adopt", {"studio_adopt": {"course_id": "c1", "topic_id": "t2", "figure_id": "f9",
                                                                      "linked": True}}),
             _step(2, "learning.lecture.figure_view", {"studio_figure_view": {"course_id": "c1", "figure_id": "f9",
                                                                             "status": 404, "image_url_kind": "learning"}},
                   persona="st-08")]
    found = oracle.check(steps, _factory())
    assert [f.severity_label for f in found] == ["blocked"]
