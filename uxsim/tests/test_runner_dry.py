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
    assert actions[:4] == ["auth.login", "learning.course.list", "learning.course.enroll", "learning.topic.open"]
    chats = [s for s in steps if s.action_id == "learning.chat.ask"]
    assert [s.args.get("message") for s in chats] == ["Q1", "Q2"]  # 429 の後 confused で繰り返しを抜ける
    assert chats[-1].think.friction == "confused"
    assert chats[0].observation.startswith("AI の回答")
    meta = RunMeta.model_validate_json((run_dir / "meta.json").read_text())
    assert meta.spent["http_429"] == 1 and meta.spent["persona_llm_calls"] == llm.calls
    assert meta.finished_at
