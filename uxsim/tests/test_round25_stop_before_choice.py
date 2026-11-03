"""第 25 周 H7: 停止条件に当たった後に頭脳へ次の行為を選ばせない・transcript に止まった理由を残す。"""
from __future__ import annotations

import json

from uxsim.persona.agent import NO_ACTION, StepDecision
from uxsim.persona.prompt import REACTION_ONLY_SECTION, build_step_message
from uxsim.runner import api as api_mod
from uxsim.runner.api import STOP_NOTE_MARK, Budget, RunWriter, ScenarioRunner, stop_note
from uxsim.runner.scenario import ScenarioParams, parse_scenario
from uxsim.runner.state import PersonaSession
from uxsim.schema import HttpTrace

SCENARIO = {
    "id": "t-h7-probe", "population": "teachers", "goal": "地図のタブを読む",
    "steps": [{"do": "admin.atlas.open"}, {"do": "admin.atlas.gaps.list"}, {"do": "admin.atlas.edges.list"}],
    "stop_when": ["gave_up", "blocked", "budget_exhausted"],
}


class FakeAgent:
    """呼び出しを記録し、決めた反応を返す頭脳の代役。"""

    def __init__(self, frictions: list[str]) -> None:
        self.frictions = list(frictions)
        self.calls: list[dict] = []

    def step(self, **kw) -> StepDecision:
        self.calls.append(kw)
        friction = self.frictions.pop(0) if self.frictions else "none"
        only = kw.get("reaction_only") or kw.get("finishing")
        allowed = kw.get("allowed") or []
        return StepDecision(action_id=NO_ACTION if only else (allowed[0].id if allowed else NO_ACTION),
                            prev_reaction=f"反応({friction})", prev_friction=friction)


def _runner(tmp_path, monkeypatch, frictions, statuses):
    statuses = list(statuses)
    sess = PersonaSession(persona_id="te-9")

    def fake_execute(action_id, args, session, client):
        session.last_status = statuses.pop(0) if statuses else 200
        session.last_body = {}
        return [HttpTrace(method="GET", path="/x", status=session.last_status)]

    monkeypatch.setattr(api_mod, "execute", fake_execute)
    monkeypatch.setattr(api_mod, "project_observation", lambda a, st, b: f"画面({a},{st})")
    agent = FakeAgent(frictions)
    writer = RunWriter(tmp_path)
    r = ScenarioRunner(agent, parse_scenario(SCENARIO), ScenarioParams({}), sess, client=None, writer=writer,
                       budget=Budget(limits={}))
    return r, agent, writer


def _steps(writer):
    return [json.loads(line) for line in writer.transcript.read_text(encoding="utf-8").splitlines()]


def test_http_error_asks_reaction_only_and_stops_without_choosing(tmp_path, monkeypatch):
    # 1 回目の操作は 404。頭脳には反応だけを聞き、blocked なら次の行為を選ばせない
    r, agent, writer = _runner(tmp_path, monkeypatch, ["none", "blocked"], [404])
    out = r.run()
    assert out.stopped == "blocked"
    assert [bool(c.get("reaction_only")) for c in agent.calls] == [False, True]
    steps = _steps(writer)
    assert len(steps) == 1 and steps[0]["think"]["friction"] == "blocked"
    note = [n for n in steps[0]["runner_notes"] if STOP_NOTE_MARK in n]
    assert note and "blocked" in note[0] and "選ばせていない" in note[0]


def test_http_error_not_blocking_continues_with_one_reaction(tmp_path, monkeypatch):
    # 反応だけを聞いて止まらなければ、次の行為を別に聞く。反応は先に聞いたものが正本
    r, agent, writer = _runner(tmp_path, monkeypatch, ["none", "confused", "blocked-ignored", "none", "none"],
                               [404, 200, 200])
    out = r.run()
    assert out.stopped == ""
    assert [bool(c.get("reaction_only")) for c in agent.calls][:3] == [False, True, False]
    steps = _steps(writer)
    assert steps[0]["think"]["friction"] == "confused"
    assert not any(STOP_NOTE_MARK in n for s in steps for n in s["runner_notes"])


def test_no_probe_on_success(tmp_path, monkeypatch):
    # HTTP が成功し直前が blocked でもなければ、従来どおり 1 呼び出しで反応と次の行為を聞く
    r, agent, _ = _runner(tmp_path, monkeypatch, ["none"] * 5, [200, 200, 200])
    r.run()
    assert not any(c.get("reaction_only") for c in agent.calls)


def test_stop_note_names_unexecuted_choice():
    assert "admin.atlas.aliases.list は実行していない" in stop_note("gave_up", "admin.atlas.aliases.list")
    assert "選ばせていない" in stop_note("blocked", "none")


def test_reaction_only_prompt_does_not_list_actions():
    msg = build_step_message(goal="g", instruction="", allowed=[], observation="o", history=[], reaction_only=True)
    assert REACTION_ONLY_SECTION in msg and "# このステップで取れる行為" not in msg
