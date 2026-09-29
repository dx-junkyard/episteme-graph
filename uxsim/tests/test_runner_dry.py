"""API runner の乾式実行（製品は httpx.MockTransport、ペルソナは ScriptedPersonaLLM — 外部呼び出しなし）。"""
from __future__ import annotations

import json
import re

import httpx

from uxsim.config import Settings
from uxsim.llm import ScriptedPersonaLLM
from uxsim.oracles.findings import load_transcript
from uxsim.persona.compose import compose_persona
from uxsim.runner.api import run_campaign
from uxsim.runner.client import EpistemeClient
from uxsim.schema import RunMeta


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _data_root(tmp_path):
    _write(tmp_path, "archetypes/students/newcomer.yaml",
           "id: newcomer\npopulation: students\nsummary: 入門者\nhabits: {patience: low, asks_questions: often}\n"
           "voice: よく質問する\n")
    _write(tmp_path, "domains/astro/knowledge/misconceptions.yaml", "m1: 赤方偏移は距離そのものだ\n")
    _write(tmp_path, "domains/astro/knowledge/goals.yaml", "- id: g1\n  text: ゼミで発表する\n")
    _write(tmp_path, "domains/astro/personas/students/st-1.yaml",
           "id: st-1\narchetype: students/newcomer\ndisplay_name: 学生一\nknowledge: {knows: [赤方偏移]}\n"
           "misconceptions: [m1]\ngoals: [g1]\noverrides: {habits: {patience: high}}\n")
    _write(tmp_path, "scenarios/goal/student/s-x.yaml",
           "id: s-x\npopulation: students\ngoal: '{{goal_text}}'\nparams_required: [goal_text, seed_questions]\n"
           "steps:\n  - do: learning.course.list\n  - do: learning.course.enroll\n  - do: learning.topic.open\n"
           "  - repeat: {until: 'friction in [confused] or steps > 5', max: 5, do: learning.chat.ask,"
           " with: {message: '{{seed_questions | next}}'}}\n")
    _write(tmp_path, "domains/astro/scenario_params/s-x.yaml", "goal_text: 発表の準備\nseed_questions: [Q1, Q2]\n")
    _write(tmp_path, "campaigns/astro/c-x.yaml",
           "id: c-x\ndomain: astro\nsnapshot: snap\npersonas: {students: [st-1]}\nscenarios: {student: [s-x]}\n"
           "budget: {product_llm_calls: 10, persona_llm_calls: 20, wall_clock_minutes: 5}\n")
    return tmp_path


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/auth/login":
        return httpx.Response(200, json={"access_token": "tok", "token_type": "bearer"})
    assert request.headers.get("Authorization") == "Bearer tok"
    if path == "/api/learning/courses":
        return httpx.Response(200, json=[{"id": "c1", "title": "宇宙論入門", "is_enrollable": True}])
    if path == "/api/learning/courses/c1/enroll":
        return httpx.Response(200, json={"ok": True})
    if path == "/api/learning/courses/c1":
        return httpx.Response(200, json={"master_course": {"id": "c1", "title": "宇宙論入門",
                                                           "topics": [{"id": "t1", "title": "導入", "chapter_index": 0}]},
                                         "personal_layer": {}})
    if path == "/api/learning/courses/c1/topics/t1/material":
        return httpx.Response(200, json={"topic_id": "t1", "chunks": [{"id": "ch1", "text": "本文", "chunk_index": 0}]})
    if path == "/api/learning/courses/c1/topics/t1/chat":
        body = json.loads(request.content)
        if body["message"] == "Q2":
            return httpx.Response(429, json={"detail": "しばらく待ってください"})
        return httpx.Response(200, json={"answer": f"{body['message']} への答え",
                                         "stance": {"stance": "tutor", "source": "inferred", "label": "先生"}})
    return httpx.Response(404, json={"detail": "Not Found"})


def _persona_brain(system, messages):
    msg = messages[-1]["content"]
    allowed = re.findall(r"^- ([a-z_.]+) — ", msg, re.MULTILINE)
    return {"intent": "進む", "expectation": "何か出る", "action_id": allowed[0] if allowed else "none",
            "args": {}, "reaction_prev": "ふむ", "friction_prev": "confused" if "しばらく" in msg else "none"}


def test_compose_merges_overrides(tmp_path):
    root = _data_root(tmp_path)
    spec = compose_persona("astro", "st-1", root)
    assert spec.habits == {"patience": "high", "asks_questions": "often"}
    assert spec.misconceptions == [{"key": "m1", "text": "赤方偏移は距離そのものだ"}]
    assert spec.goals[0]["text"] == "ゼミで発表する"


def test_dry_campaign_writes_transcript(tmp_path):
    root = _data_root(tmp_path / "data")
    settings = Settings(base_url="http://sandbox.test", runs_dir=tmp_path / "runs")
    llm = ScriptedPersonaLLM(_persona_brain)
    run_dir = run_campaign(root / "campaigns/astro/c-x.yaml", settings=settings, llm=llm, provision=False,
                           data_root=root, client_factory=lambda: EpistemeClient(
                               "http://sandbox.test", transport=httpx.MockTransport(_handler)))
    steps = load_transcript(run_dir)
    actions = [s.action_id for s in steps]
    # 2 手目は runner のコース一覧の先取り（ペルソナの判断ではない — runner_notes に残る）
    assert actions[:5] == ["auth.login", "learning.course.list", "learning.course.list", "learning.course.enroll",
                           "learning.topic.open"]
    assert steps[1].runner_notes and steps[1].runner_notes[0].startswith("runner_driven:")
    assert not steps[2].runner_notes
    chats = [s for s in steps if s.action_id == "learning.chat.ask"]
    assert [s.args.get("message") for s in chats] == ["Q1", "Q2"]  # 429 の後 confused で繰り返しを抜ける
    assert chats[-1].think.friction == "confused"
    assert chats[0].observation.startswith("AI の回答")
    meta = RunMeta.model_validate_json((run_dir / "meta.json").read_text())
    assert meta.spent["http_429"] == 1 and meta.spent["persona_llm_calls"] == llm.calls
    assert meta.finished_at


def _enrolled_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/auth/login":
        return httpx.Response(200, json={"access_token": "tok", "token_type": "bearer"})
    if path == "/api/learning/courses":
        return httpx.Response(200, json=[{"id": "c9", "title": "公開", "is_enrollable": True},
                                         {"id": "c2", "title": "受講中", "is_enrollable": False}])
    if path == "/api/learning/courses/c2":
        return httpx.Response(200, json={"master_course": {"id": "c2", "topics": [
            {"id": "t0", "title": "済", "status": "completed"}, {"id": "t1", "title": "いま", "status": "in_progress"}]}})
    if path == "/api/learning/courses/c2/topics/t1/chat":
        return httpx.Response(200, json={"answer": "答え"})
    return httpx.Response(404, json={"detail": "Not Found"})


def test_course_open_first_uses_prefetched_course_and_default_topic(tmp_path):
    """経路が course.list を経ずに course.open から始まっても precondition で止まらない（第 7 周）。"""
    root = _data_root(tmp_path / "data")
    _write(root, "scenarios/goal/student/s-x.yaml",
           "id: s-x\npopulation: students\ngoal: '{{goal_text}}'\nparams_required: [goal_text, seed_questions]\n"
           "steps:\n  - do: learning.course.open\n  - do: learning.chat.ask\n    with: {message: '{{seed_questions | next}}'}\n")
    settings = Settings(base_url="http://sandbox.test", runs_dir=tmp_path / "runs")
    run_dir = run_campaign(root / "campaigns/astro/c-x.yaml", settings=settings,
                           llm=ScriptedPersonaLLM(_persona_brain), provision=False, data_root=root,
                           client_factory=lambda: EpistemeClient("http://sandbox.test",
                                                                 transport=httpx.MockTransport(_enrolled_handler)))
    steps = load_transcript(run_dir)
    assert [s.action_id for s in steps] == ["auth.login", "learning.course.list", "learning.course.open",
                                            "learning.chat.ask"]
    prefetch, opened, chat = steps[1], steps[2], steps[3]
    assert any("course_id=c2" in n for n in prefetch.runner_notes)  # 受講中を優先（公開の c9 ではない）
    assert opened.http[0].path == "/api/learning/courses/c2"
    assert any("topic_id=t1" in n for n in opened.runner_notes)  # 最初の in_progress
    assert chat.http[0].path == "/api/learning/courses/c2/topics/t1/chat" and chat.http[0].status == 200
    assert not any((t.error or "").startswith("precondition:") for s in steps for t in s.http)


def test_prefetch_falls_back_to_first_enrollable():
    from uxsim.runner.actions_exec import execute, prefetch_courses
    from uxsim.runner.state import PersonaSession

    def handler(request):
        if request.url.path == "/api/learning/courses":
            return httpx.Response(200, json=[{"id": "c7", "is_enrollable": True}, {"id": "c8", "is_enrollable": True}])
        if request.url.path == "/api/learning/courses/c7":
            return httpx.Response(200, json={"master_course": {"id": "c7", "topics": [{"id": "a"}]}})
        return httpx.Response(404, json={})

    client = EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(handler))
    s = PersonaSession(persona_id="st-1")
    _, notes = prefetch_courses(s, client)
    assert s.course_id == "" and s.scratch["default_course_id"] == "c7"
    assert any("受講登録はしていない" in n for n in notes)
    execute("learning.course.open", {}, s, client)
    assert s.course_id == "c7" and s.topic_id == "a"  # in_progress が無ければ先頭
    assert any("topic_id=a" in n for n in s.scratch["runner_notes"])
