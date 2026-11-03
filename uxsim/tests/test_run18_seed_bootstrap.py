"""第 18 周: 種まき経路（seed_scenarios）にも通常の経路と同じコースの文脈を用意し、前提の部品を残す着地まで進める。

第 18 周 st-07 は、扉が空のため s-discuss-one-paper を種まきとして歩いたが、受講していないコースを
``course.open`` で開いて 404 になり、以降の手順が ``course_id`` 無しで空振りして blocked_precondition に
なった。外部呼び出しなし（製品は httpx.MockTransport、ペルソナは ScriptedPersonaLLM）。
"""
from __future__ import annotations

import json
import re

import httpx

from uxsim.config import Settings
from uxsim.llm import ScriptedPersonaLLM
from uxsim.oracles.findings import load_transcript
from uxsim.runner.api import SEED_PART_ROLES, run_campaign, seed_landing_steps
from uxsim.runner.client import EpistemeClient
from uxsim.runner.scenario import load_scenario
from uxsim.schema import RunMeta


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


SEED_YAML = (
    "id: s-seed\npopulation: students\ngoal: 論文と議論する\n"
    "stop_when: [gave_up, blocked, budget_exhausted, persona_stop]\nsteps:\n"
    "  - do: learning.course.open\n"
    "  - do: learning.discuss.opening\n"
    "  - repeat:\n      until: \"persona_decides_to_stop or steps > 3\"\n      max: 3\n"
    "      do: learning.discuss.ask\n      with: {message: 問い}\n"
    "  - do: learning.cycle.intention\n    with: {role: carryover_question, text: 持ち越し}\n"
    "  - do: learning.cycle.intention\n    with: {role: leave_note, text: 書き置き}\n"
)


def _data_root(tmp_path):
    _write(tmp_path, "archetypes/students/newcomer.yaml",
           "id: newcomer\npopulation: students\nsummary: 入門者\nhabits: {patience: low}\nvoice: よく質問する\n")
    _write(tmp_path, "domains/astro/knowledge/misconceptions.yaml", "m1: 誤解\n")
    _write(tmp_path, "domains/astro/knowledge/goals.yaml", "- id: g1\n  text: 目標\n")
    _write(tmp_path, "domains/astro/personas/students/st-1.yaml",
           "id: st-1\narchetype: students/newcomer\ndisplay_name: 学生一\nmisconceptions: [m1]\ngoals: [g1]\n")
    _write(tmp_path, "scenarios/goal/student/s-return.yaml",
           "id: s-return\npopulation: students\ngoal: 続き\n"
           "preconditions:\n  - text: 書き置きと持ち越しがある\n    check: return_door\n    requires: [leave_note, carryover]\n"
           "seed_scenarios: [s-seed]\nsteps:\n  - do: learning.cycle.return_door\n")
    _write(tmp_path, "scenarios/goal/student/s-seed.yaml", SEED_YAML)
    _write(tmp_path, "domains/astro/scenario_params/s-return.yaml", "{}\n")
    _write(tmp_path, "domains/astro/scenario_params/s-seed.yaml", "{}\n")
    _write(tmp_path, "campaigns/astro/c-r.yaml",
           "id: c-r\ndomain: astro\nsnapshot: snap\ncourse_id: c1\npersonas: {students: [st-1]}\n"
           "scenarios: {student: [s-return]}\n"
           "budget: {product_llm_calls: 20, persona_llm_calls: 60, wall_clock_minutes: 5}\n")
    return tmp_path


def _handler(state, *, enroll_status=200, intention_status=201):
    """受講していない固定コース c1（受講可能）。受講するまで GET /courses/c1 は 404（第 18 周の状況）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        state.setdefault("calls", []).append(f"{method} {path}")
        if path == "/api/auth/login":
            return httpx.Response(200, json={"access_token": "tok", "token_type": "bearer"})
        if path == "/api/learning/courses":
            return httpx.Response(200, json=[{"id": "c1", "title": "ゼミ", "is_enrollable": not state.get("enrolled")}])
        if path == "/api/learning/courses/c1/enroll" and method == "POST":
            if enroll_status < 300:
                state["enrolled"] = True
                return httpx.Response(enroll_status, json={"enrolled": True})
            return httpx.Response(enroll_status, json={"detail": "受講できません"})
        if path == "/api/learning/courses/c1":
            if not state.get("enrolled"):
                return httpx.Response(404, json={"detail": "Course not found"})
            return httpx.Response(200, json={"id": "c1", "title": "ゼミ", "topics": [{"id": "t1", "title": "主結果"}]})
        if path == "/api/learning/courses/c1/discuss/opening":
            return httpx.Response(200, json={"available": True, "documents": []})
        if path.startswith("/api/learning/courses/c1/topics/") and path.endswith("/chat"):
            return httpx.Response(200, json={"answer": "応答", "sources": []})
        if path == "/api/learning/courses/c1/cycle/intention":
            if intention_status >= 300:
                return httpx.Response(intention_status, json={"detail": "保存できません"})
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


def _brain(*, stop_after_discuss: bool = False):
    goals: list[str] = []

    def brain(system, messages):
        msg = messages[-1]["content"]
        goals.append(msg)
        allowed = re.findall(r"^- ([a-z_.]+) — ", msg, re.MULTILINE)
        action = allowed[0] if allowed else "none"
        out = {"intent": "進む", "expectation": "何か出る", "action_id": action, "args": {},
               "reaction_prev": "ふむ", "friction_prev": "none"}
        if stop_after_discuss and action == "learning.discuss.ask":
            out["stop"] = True  # 議論を 1 往復でやめる（着地の手順を自分では歩かない）
        return out

    brain.goals = goals  # type: ignore[attr-defined]
    return brain


def _run(tmp_path, *, brain=None, **handler_kw):
    root = _data_root(tmp_path / "data")
    state: dict = {}
    settings = Settings(base_url="http://sandbox.test", runs_dir=tmp_path / "runs")
    brain = brain or _brain()
    run_dir = run_campaign(root / "campaigns/astro/c-r.yaml", settings=settings, llm=ScriptedPersonaLLM(brain),
                           provision=False, data_root=root, client_factory=lambda: EpistemeClient(
                               "http://sandbox.test", transport=httpx.MockTransport(_handler(state, **handler_kw))))
    meta = RunMeta.model_validate_json((run_dir / "meta.json").read_text())
    return meta, load_transcript(run_dir), state, brain


def test_seed_gets_course_bootstrap_then_main_scenario_runs(tmp_path):
    meta, steps, state, _ = _run(tmp_path)
    # 種まきの前に runner が受講 → コースを開く（通常の経路と同じ文脈）
    assert "POST /api/learning/courses/c1/enroll" in state["calls"]
    boot = [s for s in steps if s.runner_notes and s.runner_notes[0].startswith("runner_driven: 種まき経路")]
    assert [s.action_id for s in boot] == ["learning.course.enroll", "learning.course.open"]
    assert all(s.scenario_id == "s-seed" for s in boot)
    # 種まき経路の course.open / discuss.opening は course_id を持って歩けた（precondition で空振りしない）
    seed_steps = [s for s in steps if s.scenario_id == "s-seed" and s not in boot]
    assert all(not any((h.error or "").startswith("precondition:") for h in s.http) for s in seed_steps)
    assert state["leave_note"] and state["carryover_question"]
    # 前提は満たされ、経路本体が歩かれた
    assert meta.blocked_precondition == []
    rec = meta.seeded_precondition[0]
    assert rec["seed_id"] == "s-seed" and rec["satisfied"] is True
    assert rec["unmet_before"] == ["carryover", "leave_note"] and len(rec["bootstrap"]) == 2
    assert any(n.startswith("seeded_precondition:") and "前提は満たされた" in n for n in meta.notes)
    assert [s.scenario_id for s in steps if s.action_id == "learning.cycle.return_door"][-1] == "s-return"


def test_seed_goal_tells_persona_to_leave_note_and_carryover(tmp_path):
    _, _, _, brain = _run(tmp_path)
    seed_prompts = [g for g in brain.goals if "論文と議論する" in g]
    assert seed_prompts and all("次の自分への書き置き" in g and "持ち越す問い" in g for g in seed_prompts)


def test_seed_stopped_early_still_lands_on_leave_note_and_carryover(tmp_path):
    meta, steps, state, _ = _run(tmp_path, brain=_brain(stop_after_discuss=True))
    assert state["leave_note"] and state["carryover_question"]
    rec = meta.seeded_precondition[0]
    assert rec["stopped"] == "persona_stop" and rec["landing"] == ["carryover", "leave_note"]
    assert rec["satisfied"] is True and meta.blocked_precondition == []
    assert any(n.startswith("seed_landing:") for s in steps for n in s.runner_notes)


def test_seed_that_cannot_leave_the_parts_blocks_main_scenario(tmp_path):
    meta, steps, state, _ = _run(tmp_path, intention_status=500)
    assert meta.blocked_precondition and meta.blocked_precondition[0]["seeded"] == ["s-seed"]
    assert meta.seeded_precondition[0]["satisfied"] is False
    assert any("それでも満たされていない" in n for n in meta.notes)
    # 経路本体（ペルソナの扉）は歩いていない
    assert not any(s.scenario_id == "s-return" and not (s.runner_notes or [""])[0].startswith("runner_driven:")
                   for s in steps if s.action_id == "learning.cycle.return_door")


def test_seed_bootstrap_failure_skips_seed_and_blocks(tmp_path):
    meta, steps, state, _ = _run(tmp_path, enroll_status=403)
    rec = meta.seeded_precondition[0]
    assert rec["stopped"] == "bootstrap_failed" and rec["satisfied"] is False
    assert meta.blocked_precondition and meta.blocked_precondition[0]["seeded"] == []
    # 種まき経路そのもの（ペルソナの手）は歩いていない
    assert not any(s.scenario_id == "s-seed" and s.action_id == "learning.cycle.intention" for s in steps)


def test_real_seed_scenarios_can_leave_the_required_parts():
    """実データ: 前提を持つ経路の種まき経路は、要求する部品を残す手順を持つ（他の種まきにも同じ穴が無いか）。"""
    for sid in ("s-return-and-journey", "s-return-next-day"):
        sc = load_scenario(sid)
        for pre in sc.precondition_checks:
            parts = [p for p in pre["requires"] if p in SEED_PART_ROLES]
            for seed_id in sc.seed_scenarios:
                seed = load_scenario(seed_id)
                found = {p for p, _ in seed_landing_steps(seed, parts)}
                assert found == set(parts), (sid, seed_id, parts, found)
