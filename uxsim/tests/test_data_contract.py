"""データ（archetypes / domains / scenarios / campaigns）がハーネスの読み方で全部読めること。

データ作者の YAML とハーネスの契約。ここが落ちたら、どちらかが README の形からずれている。
"""
from __future__ import annotations

import pytest
import yaml

from uxsim.config import UXSIM_ROOT
from uxsim.persona.compose import compose_persona
from uxsim.runner.api import load_campaign, persona_fields
from uxsim.runner.scenario import load_params, load_scenario, parse_scenario

DOMAINS = sorted(p.name for p in (UXSIM_ROOT / "domains").glob("*") if p.is_dir()) \
    if (UXSIM_ROOT / "domains").is_dir() else []
SCENARIO_FILES = sorted((UXSIM_ROOT / "scenarios").rglob("*.yaml")) if (UXSIM_ROOT / "scenarios").is_dir() else []
CAMPAIGN_FILES = sorted((UXSIM_ROOT / "campaigns").rglob("*.yaml")) if (UXSIM_ROOT / "campaigns").is_dir() else []


def _personas(domain):
    return sorted(p.stem for p in (UXSIM_ROOT / "domains" / domain / "personas").rglob("*.yaml"))


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_scenario_parses(path):
    sc = parse_scenario(yaml.safe_load(path.read_text(encoding="utf-8")), path)
    assert sc.id == path.stem


@pytest.mark.parametrize("domain", DOMAINS)
def test_personas_compose(domain):
    for pid in _personas(domain):
        spec = compose_persona(domain, pid)
        assert spec.population in ("students", "teachers")
        fields = persona_fields(spec)
        assert fields["display_name"]


@pytest.mark.parametrize("path", CAMPAIGN_FILES, ids=lambda p: p.stem)
def test_campaign_references_resolve(path):
    campaign = load_campaign(path)
    domain = campaign["domain"]
    if domain == "*":
        return
    personas = campaign.get("personas") or {}
    specs = {pid: compose_persona(domain, pid) for pid in (personas.get("teachers") or []) + (personas.get("students") or [])}
    missing: list[str] = []
    for role, pop in (("teacher", "teachers"), ("student", "students")):
        for entry in (campaign.get("scenarios") or {}).get(role) or []:
            sid = entry["id"] if isinstance(entry, dict) else entry
            sc = load_scenario(sid)
            for spec in specs.values():
                if spec.population != pop:
                    continue
                params = load_params(domain, sid, spec.id, archetype_id=spec.archetype_id)
                gaps = sc.missing_params(params)
                if gaps:
                    missing.append(f"{sid}/{spec.id}: {gaps}")
    assert not missing, missing


def _generic_handler(request):
    import httpx

    path = request.url.path
    if path == "/api/auth/login":
        return httpx.Response(200, json={"access_token": "tok"})
    if path == "/api/learning/courses" and request.method == "GET":
        return httpx.Response(200, json=[{"id": "c1", "title": "コース", "is_enrollable": True}])
    if path == "/api/learning/courses/c1":
        return httpx.Response(200, json={"master_course": {"id": "c1", "title": "コース", "topics": [
            {"id": "t1", "title": "導入", "chapter_index": 0, "check_questions": [{"question": "説明せよ"}]}]}})
    if path.endswith("/chat"):
        return httpx.Response(200, json={"answer": "答え", "stance": None})
    if path == "/api/admin/course-builder/chat":
        return httpx.Response(200, json={"answer": "案", "course_draft": {"title": "下書き", "topics": []}})
    return httpx.Response(200, json={})


@pytest.mark.parametrize("path", [p for p in CAMPAIGN_FILES if "regression" not in p.stem], ids=lambda p: p.stem)
def test_real_campaign_dry_run(path, tmp_path):
    """実データの campaign を、偽の製品と台本ペルソナで最後まで回せる（外部呼び出しなし）。"""
    import re

    import httpx

    from uxsim.config import Settings
    from uxsim.llm import ScriptedPersonaLLM
    from uxsim.oracles.findings import load_transcript
    from uxsim.runner.api import run_campaign
    from uxsim.runner.client import EpistemeClient

    def brain(system, messages):
        allowed = re.findall(r"^- ([a-z_.]+) — ", messages[-1]["content"], re.MULTILINE)
        return {"action_id": allowed[0] if allowed else "none", "args": {"message": "質問"},
                "friction_prev": "none", "stop": len(allowed) > 3}

    run_dir = run_campaign(path, settings=Settings(base_url="http://sandbox.test", runs_dir=tmp_path),
                           llm=ScriptedPersonaLLM(brain), provision=False, max_steps=40,
                           client_factory=lambda: EpistemeClient("http://sandbox.test",
                                                                 transport=httpx.MockTransport(_generic_handler)))
    steps = load_transcript(run_dir)
    assert steps and all(not s.action_id.startswith("unsupported") for s in steps)
