"""第 17 周のハーネスの不足（偽所見の元）の是正: 音声ペルソナの送り方 / claim-refs の宛先 / 経路の前提 / 回答の調子の行。

外部呼び出しなし（製品は httpx.MockTransport、ペルソナは ScriptedPersonaLLM）。
"""
from __future__ import annotations

import json
import re

import httpx
import pytest

from uxsim.config import Settings
from uxsim.llm import ScriptedPersonaLLM
from uxsim.oracles.findings import load_transcript
from uxsim.persona.compose import compose_persona
from uxsim.runner.actions_exec import VOICE_DISCUSS_NOTE, execute
from uxsim.runner.api import run_campaign
from uxsim.runner.client import EpistemeClient
from uxsim.runner.scenario import ScenarioError, parse_scenario
from uxsim.runner.state import PersonaSession, persona_uses_voice, project_observation, stance_line_shown
from uxsim.schema import RunMeta


def _client(handler, seen):
    def wrap(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(wrap))


def _session(**kw):
    s = PersonaSession(persona_id="p1", course_id="c1", topic_id="t1")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _answer(_r):
    return httpx.Response(200, json={"answer": "a", "sources": []})


# ---------------------------------------------------------------------------
# 1. 音声中心のペルソナ
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [("mostly", True), ("yes", True), (True, True), ("sometimes", False),
                                            ("no", False), (None, False)])
def test_persona_uses_voice_values(value, expected):
    assert persona_uses_voice({"uses_voice": value}) is expected


def test_voice_first_archetype_composes_as_voice():
    spec = compose_persona("astrophysics", "st-07-m1-grb-voice")
    assert persona_uses_voice(spec.habits)


@pytest.mark.parametrize("action_id,args", [
    ("learning.chat.ask", {"message": "えっと、これって?"}),
    ("learning.chat.casual", {"message": "ねえ"}),
])
def test_voice_persona_chat_goes_casual_voice_like_app_js(action_id, args):
    seen: list[httpx.Request] = []
    s = _session(uses_voice=True)
    execute(action_id, args, s, _client(_answer, seen))
    body = json.loads(seen[-1].content)
    assert body["intent_mode"] == "casual" and body["screen_mode"] == "voice"
    assert any(n.startswith("runner_voice:") for n in s.scratch.get("runner_notes", []))


def test_voice_persona_ask_selection_also_voice():
    seen: list[httpx.Request] = []
    s = _session(uses_voice=True)
    execute("learning.chat.ask_selection", {"message": "ここ?", "selection_text": "選んだ文", "selection_segment_id": 0},
            s, _client(_answer, seen))
    body = json.loads(seen[-1].content)
    assert body["intent_mode"] == "casual" and body["screen_mode"] == "voice" and body["selection_text"] == "選んだ文"


def test_text_persona_chat_unchanged():
    seen: list[httpx.Request] = []
    s = _session()
    execute("learning.chat.ask", {"message": "質問"}, s, _client(_answer, seen))
    body = json.loads(seen[-1].content)
    assert body["intent_mode"] == "on_path" and "screen_mode" not in body
    assert not s.scratch.get("observation_notes")


def test_voice_persona_discuss_stays_text_and_marks_observation():
    seen: list[httpx.Request] = []
    s = _session(uses_voice=True)
    execute("learning.discuss.ask", {"message": "えっと", "screen_mode": "voice"}, s, _client(_answer, seen))
    body = json.loads(seen[-1].content)
    assert body["intent_mode"] == "discuss" and "screen_mode" not in body  # 画面は音声のまま議論に入れない
    assert s.scratch["observation_notes"] == [VOICE_DISCUSS_NOTE]
    assert any(n.startswith("runner_voice_discuss:") for n in s.scratch["runner_notes"])


# ---------------------------------------------------------------------------
# 2. claim-refs の宛先は実在の出典チャンクだけ
# ---------------------------------------------------------------------------

def _material_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/material"):
        return httpx.Response(200, json={"topic_id": "t8", "chunks": [{"id": "topic:t8", "text": "教材", "chunk_index": 0}]})
    if request.url.path.endswith("/chat"):
        return httpx.Response(200, json={"answer": "a [出典1]", "sources": [{"index": 1, "chunk_id": "real-ch"}]})
    return httpx.Response(200, json={"claims": []})


def test_topic_pseudo_chunk_id_is_not_remembered_and_claim_refs_waits():
    seen: list[httpx.Request] = []
    s = _session(topic_ids=["t8"], topics={"t8": {"id": "t8"}})
    client = _client(_material_handler, seen)
    execute("learning.topic.open", {"topic_id": "t8"}, s, client)
    assert "topic:t8" not in s.all("chunks")
    n = len(seen)
    traces = execute("learning.chunk.claim_refs", {}, s, client)
    assert len(seen) == n and traces[0].error == "precondition:chunk_id"
    # ペルソナが擬似 id を引数で渡しても宛先にしない
    traces = execute("learning.chunk.claim_refs", {"chunk_id": "topic:t8"}, s, client)
    assert len(seen) == n and traces[0].error == "precondition:chunk_id"


def test_claim_refs_uses_answer_source_chunk():
    seen: list[httpx.Request] = []
    s = _session()
    client = _client(_material_handler, seen)
    execute("learning.chat.ask", {"message": "q"}, s, client)
    execute("learning.chunk.claim_refs", {}, s, client)
    assert seen[-1].url.path == "/api/learning/courses/c1/chunks/real-ch/claim-refs"


def test_claim_refs_prefers_opened_source_chunk():
    seen: list[httpx.Request] = []
    s = _session()
    s.remember("source_chunks", "old")
    client = _client(lambda r: httpx.Response(200, json={"text": "原文", "claims": []}), seen)
    execute("learning.source_chunk.open", {"chunk_id": "opened"}, s, client)
    execute("learning.chunk.claim_refs", {}, s, client)
    assert seen[-1].url.path == "/api/learning/courses/c1/chunks/opened/claim-refs"


# ---------------------------------------------------------------------------
# 4. 回答の調子の行（app.js renderStanceLine と同じ条件）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stance,discuss,shown", [
    ({"stance": "tutor", "source": "inferred", "label": "ふつうの質問として"}, False, False),
    ({"stance": "casual_light", "source": "explicit", "label": "気軽に"}, False, False),
    ({"stance": "casual_light", "source": "inferred", "label": "気軽に"}, False, True),
    ({"stance": "casual_light", "source": "inferred", "label": "気軽に"}, True, False),
    (None, False, False),
])
def test_stance_line_condition(stance, discuss, shown):
    assert stance_line_shown(stance, discuss=discuss) is shown


def test_stance_line_projection():
    body = {"answer": "x", "stance": {"stance": "tutor", "source": "inferred", "label": "ふつうの質問として"}}
    assert "回答の調子" not in project_observation("learning.chat.ask", 200, body)
    body["stance"] = {"stance": "casual_light", "source": "inferred", "label": "気軽に"}
    assert "〔回答の調子: 気軽に答えました〕" in project_observation("learning.chat.ask", 200, body)
    assert "回答の調子" not in project_observation("learning.discuss.ask", 200, body)
    body["stance"] = {"stance": "tutor", "source": "explicit", "label": "楽屋"}
    assert "回答の調子" not in project_observation("learning.chat.backstage", 200, body)


# ---------------------------------------------------------------------------
# 3. 経路の前提（preconditions の check と seed_scenarios）
# ---------------------------------------------------------------------------

def test_parse_preconditions_structured_and_strings():
    sc = parse_scenario({"id": "s-a", "steps": [{"do": "learning.course.list"}],
                         "preconditions": ["事実文だけ", {"text": "扉", "check": "return_door",
                                                       "requires": ["leave_note", "carryover"]}],
                         "seed_scenarios": ["s-seed"]})
    assert sc.preconditions[0] == {"text": "事実文だけ"}
    assert sc.precondition_checks == [{"text": "扉", "check": "return_door", "requires": ["leave_note", "carryover"]}]
    assert sc.seed_scenarios == ["s-seed"]
    with pytest.raises(ScenarioError):
        parse_scenario({"id": "s-b", "steps": [], "preconditions": [{"text": "x", "check": "unknown"}]})
    with pytest.raises(ScenarioError):
        parse_scenario({"id": "s-c", "steps": [], "preconditions": [{"check": "return_door", "requires": ["nope"]}]})


def test_real_return_scenarios_declare_check_and_seed():
    from uxsim.runner.scenario import load_scenario

    for sid in ("s-return-and-journey", "s-return-next-day"):
        sc = load_scenario(sid)
        assert sc.precondition_checks and sc.seed_scenarios == ["s-discuss-one-paper"]
        for seed in sc.seed_scenarios:
            load_scenario(seed)


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _data_root(tmp_path, *, seed: bool):
    _write(tmp_path, "archetypes/students/newcomer.yaml",
           "id: newcomer\npopulation: students\nsummary: 入門者\nhabits: {patience: low}\nvoice: よく質問する\n")
    _write(tmp_path, "domains/astro/knowledge/misconceptions.yaml", "m1: 誤解\n")
    _write(tmp_path, "domains/astro/knowledge/goals.yaml", "- id: g1\n  text: 目標\n")
    _write(tmp_path, "domains/astro/personas/students/st-1.yaml",
           "id: st-1\narchetype: students/newcomer\ndisplay_name: 学生一\nmisconceptions: [m1]\ngoals: [g1]\n")
    seed_line = "seed_scenarios: [s-seed]\n" if seed else ""
    _write(tmp_path, "scenarios/goal/student/s-return.yaml",
           "id: s-return\npopulation: students\ngoal: 続き\n"
           "preconditions:\n  - text: 書き置きと持ち越しがある\n    check: return_door\n    requires: [leave_note, carryover]\n"
           + seed_line + "steps:\n  - do: learning.cycle.return_door\n")
    _write(tmp_path, "scenarios/goal/student/s-seed.yaml",
           "id: s-seed\npopulation: students\ngoal: 種まき\nsteps:\n"
           "  - do: learning.cycle.intention\n    with: {role: carryover_question, text: 持ち越し}\n"
           "  - do: learning.cycle.intention\n    with: {role: leave_note, text: 書き置き}\n")
    _write(tmp_path, "domains/astro/scenario_params/s-return.yaml", "{}\n")
    _write(tmp_path, "domains/astro/scenario_params/s-seed.yaml", "{}\n")
    _write(tmp_path, "campaigns/astro/c-r.yaml",
           "id: c-r\ndomain: astro\nsnapshot: snap\npersonas: {students: [st-1]}\nscenarios: {student: [s-return]}\n"
           "budget: {product_llm_calls: 10, persona_llm_calls: 40, wall_clock_minutes: 5}\n")
    return tmp_path


def _door_handler(state):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/login":
            return httpx.Response(200, json={"access_token": "tok", "token_type": "bearer"})
        if path == "/api/learning/courses":
            return httpx.Response(200, json=[{"id": "c1", "title": "受講中", "is_enrollable": False}])
        if path == "/api/learning/courses/c1":
            # 種まき経路の前に runner がコースを開く（第 18 周: 種まきにもコースの文脈を用意する）
            return httpx.Response(200, json={"id": "c1", "title": "受講中", "topics": []})
        if path == "/api/learning/courses/c1/cycle/intention":
            body = json.loads(request.content)
            state[body.get("role")] = {"text": body.get("text")}
            return httpx.Response(201, json={"trace_id": "tr1"})
        if path == "/api/learning/courses/c1/cycle/return-door":
            state["door_gets"] = state.get("door_gets", 0) + 1
            if state.get("leave_note") and state.get("carryover_question"):
                return httpx.Response(200, json={"empty": False, "leave_note": state["leave_note"],
                                                 "carryover": state["carryover_question"], "last_tension": None})
            return httpx.Response(200, json={"empty": True, "fact": "まだ残っていません"})
        return httpx.Response(404, json={"detail": "Not Found"})
    return handler


def _brain(system, messages):
    msg = messages[-1]["content"]
    allowed = re.findall(r"^- ([a-z_.]+) — ", msg, re.MULTILINE)
    return {"intent": "進む", "expectation": "何か出る", "action_id": allowed[0] if allowed else "none",
            "args": {}, "reaction_prev": "ふむ", "friction_prev": "none"}


def _run(tmp_path, *, seed: bool):
    root = _data_root(tmp_path / "data", seed=seed)
    state: dict = {}
    settings = Settings(base_url="http://sandbox.test", runs_dir=tmp_path / "runs")
    run_dir = run_campaign(root / "campaigns/astro/c-r.yaml", settings=settings, llm=ScriptedPersonaLLM(_brain),
                           provision=False, data_root=root, client_factory=lambda: EpistemeClient(
                               "http://sandbox.test", transport=httpx.MockTransport(_door_handler(state))))
    return run_dir, state


def test_unmet_precondition_without_seed_blocks_and_skips(tmp_path):
    run_dir, state = _run(tmp_path, seed=False)
    meta = RunMeta.model_validate_json((run_dir / "meta.json").read_text())
    assert len(meta.blocked_precondition) == 1
    blocked = meta.blocked_precondition[0]
    assert blocked["scenario_id"] == "s-return" and blocked["seeded"] == []
    assert blocked["unmet"][0]["missing"] == ["leave_note", "carryover"]
    assert any(n.startswith("blocked_precondition:") for n in meta.notes)
    steps = load_transcript(run_dir)
    # 経路の本体（ペルソナの return_door）は歩いていない。runner が確かめた 1 行だけ
    door_steps = [s for s in steps if s.action_id == "learning.cycle.return_door"]
    assert len(door_steps) == 1 and door_steps[0].runner_notes[0].startswith("runner_driven:")
    assert state["door_gets"] == 1


def test_unmet_precondition_with_seed_runs_seed_then_scenario(tmp_path):
    run_dir, state = _run(tmp_path, seed=True)
    meta = RunMeta.model_validate_json((run_dir / "meta.json").read_text())
    assert meta.blocked_precondition == []
    steps = load_transcript(run_dir)
    scen = [s.scenario_id for s in steps if s.action_id == "learning.cycle.intention"]
    assert scen == ["s-seed", "s-seed"]
    assert any(n.startswith("seeded_precondition:") for s in steps for n in s.runner_notes)
    # 確かめる（不足）→ 種まき → 確かめる（満たす）→ 経路本体の扉
    assert state["door_gets"] == 3
    assert meta.seeded_precondition and meta.seeded_precondition[0]["satisfied"] is True
    assert [s.scenario_id for s in steps if s.action_id == "learning.cycle.return_door"][-1] == "s-return"
