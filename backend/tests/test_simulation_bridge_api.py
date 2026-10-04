"""検証シミュレーション層（SB層）API のテスト — 実 DB・実 LLM・実 sim-runner 不使用。

正本: ``docs/features/simulation_bridge_design.md`` §6（API 表）/ §2（SB1〜SB10）。

ルート関数を直接呼び、権限ゲート・セッション・store・LLM・sandbox を monkeypatch で差し替える
（``test_seminar_brief_api.py`` と同じ流儀）。overview だけは FastAPI の TestClient で
アプリ経由（``TeacherDisplayRoute`` の射影込み）でも確かめる。
"""

from __future__ import annotations

import copy
import sys
import types
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402

from core.simulation_bridge import schema  # noqa: E402
from core.simulation_bridge import store as real_store  # noqa: E402
from core.simulation_bridge.sandbox import SandboxClient  # noqa: E402
from routes import doubt as doubt_routes  # noqa: E402
from routes import simulation_bridge as sbr  # noqa: E402

DOC = str(uuid.UUID(int=1))
TEACHER = {"id": str(uuid.UUID(int=99)), "role": "TEACHER"}

GOOD_SOURCE = "def simulate(inputs, params):\n    return {'y': 2.0 * inputs.get('x', 1.0)}\n"
BAD_SOURCE = "import os\n\ndef simulate(inputs, params):\n    return {'y': os.getcwd()}\n"


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _Session:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True

    def execute(self, *_a, **_k):  # pragma: no cover — 直接 SQL を投げない（store は fake）
        raise AssertionError("fake store を通さない SQL")


class _FakeStore:
    """store の公開関数の in-memory 版（状態遷移・例外の語彙は本物と同じ）。"""

    DECIDE_ACTIONS = real_store.DECIDE_ACTIONS
    SimulationNotFoundError = real_store.SimulationNotFoundError
    SimulationStateError = real_store.SimulationStateError
    build_ledger_candidate = staticmethod(real_store.build_ledger_candidate)
    display_metrics = staticmethod(real_store.display_metrics)

    def __init__(self):
        self.programs: dict[str, dict] = {}
        self.wirings: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.ledger: list[dict] = []

    # programs
    def create_program(self, session, *, document_id, module_key, module_stable_key="", source_code,
                       io_contract, provenance, created_by, review_status="candidate", record_audit=None,
                       owner_user_id=None, course_id=None, supersede_unowned=False):
        pid = str(uuid.uuid4())
        row = {
            "id": pid, "document_id": document_id, "module_key": module_key,
            "module_stable_key": module_stable_key, "source_code": source_code,
            "io_contract": dict(io_contract or {}), "provenance": dict(provenance or {}),
            "review_status": review_status, "created_by": created_by, "created_at": "t",
            "decided_at": None, "superseded_at": None, "owner_user_id": owner_user_id,
        }
        self.programs[pid] = row
        if record_audit:
            record_audit(entity_id=pid, action="program_generated", new_status=review_status, actor_id=created_by)
        return dict(row)

    def list_programs(self, session, *, document_id, include_superseded=False, **_filters):
        return [dict(p) for p in reversed(list(self.programs.values())) if p["document_id"] == document_id]

    def get_program(self, session, program_id):
        p = self.programs.get(program_id)
        return dict(p) if p else None

    def decide_program(self, session, *, program_id, action, actor_id, note="", record_audit):
        p = self.programs.get(program_id)
        if p is None:
            raise self.SimulationNotFoundError("x")
        if p["review_status"] != "candidate":
            raise self.SimulationStateError("この理論プログラムは既に判断済みです。")
        p["review_status"] = "confirmed" if action == "confirm" else "dismissed"
        record_audit(entity_id=program_id, action=f"program_{p['review_status']}", actor_id=actor_id)
        return dict(p)

    # wirings
    def create_wiring(self, session, *, document_id, name, parts, edges, origin, created_by,
                      parent_wiring_id=None, revision_note="", review_status=None, record_audit=None,
                      owner_user_id=None, course_id=None):
        wid = str(uuid.uuid4())
        status = review_status or ("confirmed" if origin == "teacher" else "candidate")
        row = {
            "id": wid, "document_id": document_id, "name": name, "parts": copy.deepcopy(list(parts)),
            "edges": copy.deepcopy(list(edges)), "origin": origin, "review_status": status,
            "parent_wiring_id": parent_wiring_id, "revision_note": revision_note,
            "created_by": created_by, "created_at": "t", "owner_user_id": owner_user_id,
        }
        self.wirings[wid] = row
        return dict(row)

    def list_wirings(self, session, *, document_id, include_generic=True, **_filters):
        return [dict(w) for w in self.wirings.values() if w["document_id"] in (document_id, None, "")]

    def get_wiring(self, session, wiring_id):
        w = self.wirings.get(wiring_id)
        return dict(w) if w else None

    def decide_wiring(self, session, *, wiring_id, action, actor_id, note="", record_audit):
        w = self.wirings[wiring_id]
        w["review_status"] = "confirmed" if action == "confirm" else "dismissed"
        return dict(w)

    # runs
    def record_run(self, session, *, document_id, program_id, wiring_id, observation_claim_ids, inputs,
                   result, runner, status, created_by, record_audit=None, owner_user_id=None,
                   course_id=None, prediction=None):
        rid = str(uuid.uuid4())
        row = {
            "id": rid, "document_id": document_id, "program_id": program_id, "wiring_id": wiring_id,
            "observation_claim_ids": list(observation_claim_ids or []), "inputs": dict(inputs or {}),
            "result": copy.deepcopy(dict(result or {})), "runner": dict(runner or {}), "status": status,
            "ledger_candidate_id": None, "created_by": created_by, "created_at": "t",
            "owner_user_id": owner_user_id, "shared_at": None,
        }
        self.runs[rid] = row
        return dict(row)

    def list_runs(self, session, *, document_id, limit=50, **_filters):
        return [dict(r) for r in self.runs.values() if r["document_id"] == document_id]

    def get_run(self, session, run_id):
        r = self.runs.get(run_id)
        return copy.deepcopy(r) if r else None

    def set_run_ledger_candidate(self, session, *, run_id, candidate_id):
        self.runs[run_id]["ledger_candidate_id"] = candidate_id
        return dict(self.runs[run_id])

    def append_ledger_candidate(self, session, *, target_type, target_id, candidate, record_audit=None, actor_id=None, document_id=None, course_id=None):
        item = dict(candidate)
        item["target"] = (target_type, target_id)
        self.ledger.append(item)
        return dict(candidate)


MODULE = {
    "module_key": "m2:abc",
    "level": "outer",
    "label": "導く: E",
    "visual_label": "導く: E",
    "theory_object": "E",
    "inputs": [{"equation_id": "eq_1", "display_label": "式 (1)"}],
    "outputs": [{"equation_id": "eq_2", "display_label": "式 (2)"}],
    "foundation": [],
    "assumptions": [{"text": "線形の範囲", "origin_label": "導出の記録にある前提"}],
    "members": [],
    "structure_fingerprint": "secret",
}

EQUATIONS = [
    {"agent_equation_id": "eq_1", "label": "(1)", "latex": "y = 2 x", "plain_text": "", "defined_symbols": ["y"],
     "used_symbols": ["x"], "agent_payload": {}},
    {"agent_equation_id": "eq_2", "label": "(2)", "latex": "E = y", "plain_text": "", "defined_symbols": ["E"],
     "used_symbols": ["y"], "agent_payload": {}},
]

CATALOG_ROWS = [
    {"entry_id": "amp", "version_no": 1, "entry_type": schema.PART_TYPE_INSTRUMENT, "entry_type_label": "装置モデル",
     "name": "線形増幅器", "summary": "", "body": {"transfer": "params['gain'] * x",
                                               "params": {"gain": {"default": 2.0, "min": 0.0, "max": 10.0}}}},
    {"entry_id": "noise", "version_no": 1, "entry_type": schema.PART_TYPE_NOISE, "entry_type_label": "ノイズ源",
     "name": "白色ノイズ", "summary": "", "body": {"noise_kind": "gaussian",
                                              "params": {"sigma": {"default": 0.1, "min": 0.0, "max": 5.0}}}},
]


@pytest.fixture()
def env(monkeypatch):
    fake = _FakeStore()
    session = _Session()
    audits: list[tuple] = []
    gate_calls: list[dict] = []
    state = {"session": session, "store": fake, "audits": audits, "gate_calls": gate_calls,
             "equations": list(EQUATIONS), "sandbox": SandboxClient("", timeout_s=5)}

    monkeypatch.setattr(sbr, "store", fake)
    monkeypatch.setattr(sbr, "_pg_session", lambda: session)
    monkeypatch.setattr(sbr, "_ensure_document_viewable", lambda *a, **k: [])
    monkeypatch.setattr(sbr, "_ensure_document_editable", lambda *a, **k: [])
    monkeypatch.setattr(sbr, "build_theory_modules_for_document",
                        lambda d, **k: {"available": True, "facts": [], "modules": [copy.deepcopy(MODULE)]})
    monkeypatch.setattr(sbr, "_saved_module_records", lambda s, d: {})
    monkeypatch.setattr(sbr, "_equation_rows",
                        lambda s, d, ids: [e for e in state["equations"] if ids is None or e["agent_equation_id"] in ids])
    monkeypatch.setattr(sbr, "_symbol_rows", lambda s, d, eqs: [])
    monkeypatch.setattr(sbr, "observation_claim_targets",
                        lambda s, **k: [{"claim_id": "c1", "label": "E を測る", "identified_via": "dsl_measures"}])
    monkeypatch.setattr(sbr, "parts_catalog", types.SimpleNamespace(
        load_parts_catalog=lambda s: copy.deepcopy(CATALOG_ROWS),
        catalog_by_id=lambda rows: {r["entry_id"]: r for r in rows},
    ))
    monkeypatch.setattr(sbr, "record_review_event", lambda *a, **k: audits.append(a))
    monkeypatch.setattr(sbr, "_sandbox_client", lambda: state["sandbox"])
    monkeypatch.setattr(sbr, "_user_display_names", lambda s, ids: {})
    monkeypatch.setattr(sbr, "_can_decide_ledger", lambda d, u: True)

    def _quota(gate, **kw):
        gate_calls.append(kw)

    monkeypatch.setattr(sbr, "consume_daily_quota", _quota)
    return state


def _structured_returning(payload):
    calls = []

    def _fn(messages=None, response_format=None, **kwargs):
        calls.append(kwargs)
        return response_format(**payload) if isinstance(payload, dict) else payload

    _fn.calls = calls
    return _fn


def _assert_no_forbidden_keys(value, path="$"):
    if isinstance(value, dict):
        for k, v in value.items():
            assert k not in schema.FORBIDDEN_DTO_KEYS, f"{path}.{k}"
            _assert_no_forbidden_keys(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _assert_no_forbidden_keys(v, f"{path}[{i}]")


def _seed_program(env, *, status="confirmed", source=GOOD_SOURCE, ast_ok=True):
    program = env["store"].create_program(
        None, document_id=DOC, module_key=MODULE["module_key"], source_code=source,
        io_contract={}, provenance={"ast_ok": ast_ok, "reconstructed_inputs": False}, created_by=TEACHER["id"],
    )
    env["store"].programs[program["id"]]["review_status"] = status
    return program["id"]


def _seed_wiring(env, *, status="confirmed"):
    wiring = env["store"].create_wiring(
        None, document_id=DOC, name="増幅＋ノイズ",
        parts=[{"slot": "amp", "entry_id": "amp", "version_no": 1, "params": {}},
               {"slot": "noise", "entry_id": "noise", "version_no": 1, "params": {}}],
        edges=[{"from_slot": "amp", "to_slot": "noise"}], origin="teacher", created_by=TEACHER["id"],
    )
    env["store"].wirings[wiring["id"]]["review_status"] = status
    return wiring["id"]


# ---------------------------------------------------------------------------
# 登録
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_main_registers_the_router_under_admin_prefix(self):
        main_src = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")
        assert 'app.include_router(simulation_bridge_routes.admin_router, prefix="/api/admin")' in main_src

    def test_router_uses_the_layer_projection_not_the_teacher_display_route(self):
        # TeacherDisplayRoute は ``k`` / ``weight`` 等のキーを落とすため、記号をキーにした写像を持つ
        # 本層では使わない（物理記号 ``k`` が消える）。本層専用の射影だけを掛ける。
        from display_route import TeacherDisplayRoute

        assert sbr.admin_router.route_class is sbr.SimulationDisplayRoute
        assert sbr.admin_router.route_class is not TeacherDisplayRoute

    def test_layer_projection_keeps_symbol_keys_and_drops_forbidden_keys(self):
        payload = {
            "run": {
                "result": {
                    "theory_outputs": {"k": 1.0, "count": 2.0, "x": 3.0},
                    "comparison": {"metrics": {"by_symbol": {"k": {"relative_difference": 0.0}}}, "confidence": 0.9},
                }
            }
        }
        out = sbr._project_simulation_payload(payload)
        assert out["run"]["result"]["theory_outputs"] == {"k": 1.0, "count": 2.0, "x": 3.0}
        assert "k" in out["run"]["result"]["comparison"]["metrics"]["by_symbol"]
        assert "confidence" not in out["run"]["result"]["comparison"]
        paths = {r.path for r in sbr.admin_router.routes}
        assert "/simulation/documents/{document_id}/overview" in paths
        assert all(p.startswith("/simulation/") for p in paths)

    def test_no_delete_routes(self):
        for route in sbr.admin_router.routes:
            assert "DELETE" not in (route.methods or set())


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


class TestOverview:
    KEYS = {"document_id", "available", "facts", "sandbox_configured", "indicator_id", "modules", "programs",
            "wirings", "runs", "observation_targets", "parts_catalog", "can_decide_ledger"}

    def test_shape(self, env):
        out = sbr.get_simulation_overview(DOC, current_user=TEACHER)
        assert set(out) == self.KEYS
        assert out["indicator_id"] == "simulation-agreement"
        assert out["sandbox_configured"] is False
        module = out["modules"][0]
        assert set(module) == {"module_key", "level", "label", "visual_label", "theory_object", "inputs",
                               "outputs", "assumptions", "has_equation_text", "reconstructed_inputs", "program",
                               "input_symbols", "output_symbols", "subtitle", "output_symbols_note", "simulable", "simulability_note",
                               "equations", "to_sink"}
        assert module["has_equation_text"] is True
        assert module["program"] is None
        assert "structure_fingerprint" not in module
        assert out["observation_targets"][0]["claim_id"] == "c1"
        assert out["parts_catalog"][0]["entry_id"] == "amp"
        _assert_no_forbidden_keys(out)

    def test_overview_through_the_app(self, env, monkeypatch):
        from fastapi.testclient import TestClient

        from api.main import app
        from dependencies import _require_teacher

        app.dependency_overrides[_require_teacher] = lambda: TEACHER
        try:
            client = TestClient(app)
            response = client.get(f"/api/admin/simulation/documents/{DOC}/overview")
        finally:
            app.dependency_overrides.pop(_require_teacher, None)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == self.KEYS
        assert body["modules"][0]["module_key"] == MODULE["module_key"]


class TestCanDecideLedger:
    """第 22 周: 台帳候補の確定・見送りボタンの活性（台帳 API の権限ゲートと同じ判定・fail-closed）。"""

    def test_system_admin_without_lookup(self, monkeypatch):
        import services

        monkeypatch.setattr(services, "resolve_document_access", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
        assert sbr._can_decide_ledger(DOC, {"id": "u-admin", "role": sbr.ROLE_SYSTEM_ADMIN}) is True

    def test_follows_document_edit_permission(self, monkeypatch):
        import services

        monkeypatch.setattr(services, "resolve_document_access",
                            lambda uid, ref: types.SimpleNamespace(found=True, can_edit=uid == "editor"))
        assert sbr._can_decide_ledger(DOC, {"id": "editor", "role": "TEACHER"}) is True
        assert sbr._can_decide_ledger(DOC, {"id": "viewer", "role": "TEACHER"}) is False

    def test_lookup_failure_is_false(self, monkeypatch):
        import services

        def _boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(services, "resolve_document_access", _boom)
        assert sbr._can_decide_ledger(DOC, {"id": "t", "role": "TEACHER"}) is False


# ---------------------------------------------------------------------------
# generate / decide
# ---------------------------------------------------------------------------


class TestGenerate:
    def test_generate_consumes_quota_and_persists_candidate(self, env, monkeypatch):
        fn = _structured_returning({"source_code": GOOD_SOURCE, "io_contract": {"outputs": [{"symbol": "E"}]}, "facts": []})
        monkeypatch.setattr(sbr, "generate_text_with_structured_output", fn)
        out = sbr.generate_simulation_program(DOC, body=sbr.GenerateProgramRequest(module_key=MODULE["module_key"]),
                                              current_user=TEACHER)
        assert len(env["gate_calls"]) == 1
        assert out["program"]["review_status"] == "candidate"
        assert out["program"]["provenance"]["ast_ok"] is True
        assert env["session"].commits == 1
        assert env["audits"], "監査が記帳されていない"
        _assert_no_forbidden_keys(out)

    def test_ast_rejected_draft_is_kept_as_candidate(self, env, monkeypatch):
        fn = _structured_returning({"source_code": BAD_SOURCE, "io_contract": {}, "facts": []})
        monkeypatch.setattr(sbr, "generate_text_with_structured_output", fn)
        out = sbr.generate_simulation_program(DOC, body=sbr.GenerateProgramRequest(module_key=MODULE["module_key"]),
                                              current_user=TEACHER)
        assert len(env["gate_calls"]) == 1
        assert out["program"]["review_status"] == "candidate"
        assert out["program"]["provenance"]["ast_ok"] is False
        assert schema.FACT_AST_REJECTED in out["facts"]

    def test_no_equation_text_is_422_without_quota(self, env, monkeypatch):
        env["equations"] = [dict(e, latex="", plain_text="") for e in EQUATIONS]
        monkeypatch.setattr(sbr, "generate_text_with_structured_output",
                            lambda **k: pytest.fail("LLM を呼んではならない"))
        with pytest.raises(HTTPException) as exc:
            sbr.generate_simulation_program(DOC, body=sbr.GenerateProgramRequest(module_key=MODULE["module_key"]),
                                            current_user=TEACHER)
        assert exc.value.status_code == 422
        assert env["gate_calls"] == []

    def test_unknown_module_is_404(self, env):
        with pytest.raises(HTTPException) as exc:
            sbr.generate_simulation_program(DOC, body=sbr.GenerateProgramRequest(module_key="nope"),
                                            current_user=TEACHER)
        assert exc.value.status_code == 404

    def test_confirm_of_ast_rejected_program_is_422(self, env):
        pid = _seed_program(env, status="candidate", source=BAD_SOURCE, ast_ok=False)
        with pytest.raises(HTTPException) as exc:
            sbr.decide_simulation_program(pid, body=sbr.DecideRequest(action="confirm"), current_user=TEACHER)
        assert exc.value.status_code == 422
        assert env["store"].programs[pid]["review_status"] == "candidate"

    def test_decided_twice_is_409(self, env):
        pid = _seed_program(env, status="candidate")
        sbr.decide_simulation_program(pid, body=sbr.DecideRequest(action="confirm"), current_user=TEACHER)
        with pytest.raises(HTTPException) as exc:
            sbr.decide_simulation_program(pid, body=sbr.DecideRequest(action="dismiss"), current_user=TEACHER)
        assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------


def _run_body(program_id, wiring_id=None, **extra):
    return sbr.RunRequest(program_id=program_id, wiring_id=wiring_id, observation_claim_ids=["c1"],
                          values={"x": 1.0}, seed=7, **extra)


class TestRuns:
    def test_program_not_confirmed_is_422(self, env):
        pid = _seed_program(env, status="candidate")
        with pytest.raises(HTTPException) as exc:
            sbr.create_simulation_run(DOC, body=_run_body(pid), current_user=TEACHER)
        assert exc.value.status_code == 422

    def test_wiring_not_confirmed_is_422(self, env):
        pid = _seed_program(env)
        wid = _seed_wiring(env, status="candidate")
        with pytest.raises(HTTPException) as exc:
            sbr.create_simulation_run(DOC, body=_run_body(pid, wid), current_user=TEACHER)
        assert exc.value.status_code == 422

    def test_sandbox_not_configured_records_failed_run(self, env):
        pid = _seed_program(env)
        out = sbr.create_simulation_run(DOC, body=_run_body(pid), current_user=TEACHER)
        run = out["run"]
        assert run["status"] == "failed"
        assert schema.FACT_SANDBOX_NOT_CONFIGURED in run["result"]["facts"]
        assert env["session"].commits == 1

    def test_rejected_source_is_not_sent_to_sandbox(self, env):
        pid = _seed_program(env, source=BAD_SOURCE)
        env["sandbox"] = SandboxClient("http://sim", timeout_s=5,
                                       transport=lambda payload: pytest.fail("sandbox を呼んではならない"))
        out = sbr.create_simulation_run(DOC, body=_run_body(pid), current_user=TEACHER)
        assert out["run"]["status"] == "rejected"
        assert schema.FACT_AST_REJECTED in out["run"]["result"]["facts"]

    def test_success_path_with_wiring(self, env):
        pid = _seed_program(env)
        wid = _seed_wiring(env)
        payloads = []

        def transport(payload):
            payloads.append(payload)
            if payload["entrypoint"] == schema.PROGRAM_ENTRYPOINT:
                return {"ok": True, "outputs": {"E": 2.0}}
            return {"ok": True, "outputs": {"observed": {"E": {"mean": 2.01, "std": 0.1, "samples": [2.0]}},
                                            "chain": ["amp", "noise"]}}

        env["sandbox"] = SandboxClient("http://sim", timeout_s=5, transport=transport)
        out = sbr.create_simulation_run(DOC, body=_run_body(pid, wid), current_user=TEACHER)
        run = out["run"]
        assert run["status"] == "succeeded"
        assert [p["entrypoint"] for p in payloads] == [schema.PROGRAM_ENTRYPOINT, schema.INSTRUMENT_ENTRYPOINT]
        assert payloads[1]["inputs"] == {"E": 2.0}
        assert payloads[1]["params"]["amp"]["gain"] == 2.0
        assert "def simulate_instrument" in payloads[1]["source"]
        comparison = run["result"]["comparison"]
        assert comparison["agreement_level"] == "match"
        # 第 21 周 R5: 値は「軸名: 段階」
        assert run["result"]["labels"]["agreement"] == "一致（理論の出力と模擬観測）: 一致"
        assert run["result"]["labels"]["separation"].split(": ", 1)[1] in ("分離できる", "境界", "埋もれる", "未測定")
        assert run["result"]["labels"]["reference"].startswith("記載値との一致: ")
        assert "増幅＋ノイズ" in run["result"]["statement"]
        _assert_no_forbidden_keys(out)

    def test_sandbox_failure_is_not_500(self, env):
        pid = _seed_program(env)
        env["sandbox"] = SandboxClient("http://sim", timeout_s=5, transport=lambda p: {"ok": False, "error": "boom"})
        out = sbr.create_simulation_run(DOC, body=_run_body(pid), current_user=TEACHER)
        assert out["run"]["status"] == "failed"
        assert "boom" in out["run"]["runner"]["error"]


# ---------------------------------------------------------------------------
# 台帳候補・改訂案
# ---------------------------------------------------------------------------


def _succeeded_run(env, *, claim_ids=("c1", "c2")):
    pid = _seed_program(env)
    wid = _seed_wiring(env)
    run = env["store"].record_run(
        None, document_id=DOC, program_id=pid, wiring_id=wid, observation_claim_ids=list(claim_ids),
        inputs={"values": {"x": 1.0}, "seed": 7},
        result={"comparison": {"agreement_level": "rough", "facts": ["f"], "metrics": {}},
                "statement": "このモデルと配線『増幅＋ノイズ』のもとでは、…", "wiring_name": "増幅＋ノイズ",
                "reconstructed_inputs": False},
        runner={}, status="succeeded", created_by=TEACHER["id"],
    )
    return run["id"]


class TestLedgerCandidate:
    def test_appends_one_candidate_per_claim(self, env):
        rid = _succeeded_run(env)
        out = sbr.add_simulation_ledger_candidate(rid, body=None, current_user=TEACHER)
        assert len(out["candidates"]) == 2
        assert [c["target"] for c in env["store"].ledger] == [("claim", "c1"), ("claim", "c2")]
        assert env["store"].runs[rid]["ledger_candidate_id"] == out["candidates"][0]["candidate_id"]
        assert all(c["level"] == "rough" for c in out["candidates"])
        _assert_no_forbidden_keys(out)

    def test_empty_claims_is_422(self, env):
        rid = _succeeded_run(env, claim_ids=())
        with pytest.raises(HTTPException) as exc:
            sbr.add_simulation_ledger_candidate(rid, body=None, current_user=TEACHER)
        assert exc.value.status_code == 422

    def test_failed_run_is_422(self, env):
        rid = _succeeded_run(env)
        env["store"].runs[rid]["status"] = "failed"
        with pytest.raises(HTTPException) as exc:
            sbr.add_simulation_ledger_candidate(rid, body=None, current_user=TEACHER)
        assert exc.value.status_code == 422


class TestProposeRevision:
    def test_creates_llm_candidate_wiring(self, env, monkeypatch):
        rid = _succeeded_run(env)
        fn = _structured_returning({"changes": [{"op": "set_param", "slot": "noise", "param": "sigma",
                                                 "value": 0.05, "reason": "ばらつきを下げる"}]})
        monkeypatch.setattr(sbr, "generate_text_with_structured_output", fn)
        out = sbr.propose_simulation_revision(rid, body=None, current_user=TEACHER)
        assert len(env["gate_calls"]) == 1
        wiring = out["wiring"]
        assert wiring["origin"] == "llm_candidate"
        assert wiring["review_status"] == "candidate"
        assert wiring["name"].endswith("（改訂案）")
        assert wiring["parent_wiring_id"] == env["store"].runs[rid]["wiring_id"]
        assert out["proposal"]["changes"][0]["param"] == "sigma"

    def test_empty_proposal_returns_null_wiring(self, env, monkeypatch):
        rid = _succeeded_run(env)
        monkeypatch.setattr(sbr, "generate_text_with_structured_output",
                            _structured_returning({"changes": [{"op": "add", "slot": "z", "entry_id": "ghost"}]}))
        before = len(env["store"].wirings)
        out = sbr.propose_simulation_revision(rid, body=None, current_user=TEACHER)
        assert out["wiring"] is None
        assert out["proposal"]["changes"] == []
        assert out["proposal"]["dropped"]
        assert len(env["store"].wirings) == before


class TestWirings:
    def test_invalid_wiring_is_422(self, env):
        body = sbr.CreateWiringRequest(name="x", parts=[sbr.WiringPart(slot="a", entry_id="ghost")], edges=[])
        with pytest.raises(HTTPException) as exc:
            sbr.create_simulation_wiring(DOC, body=body, current_user=TEACHER)
        assert exc.value.status_code == 422

    def test_teacher_wiring_is_confirmed_and_enriched(self, env):
        body = sbr.CreateWiringRequest(name="増幅", parts=[sbr.WiringPart(slot="amp", entry_id="amp", version_no=1)])
        out = sbr.create_simulation_wiring(DOC, body=body, current_user=TEACHER)
        assert out["wiring"]["review_status"] == "confirmed"
        assert out["wiring"]["parts"][0]["name"] == "線形増幅器"
        assert out["wiring"]["parts"][0]["entry_type"] == schema.PART_TYPE_INSTRUMENT


# ---------------------------------------------------------------------------
# doubt.py — 台帳候補の確定（本物の store.decide_ledger_candidate を fake セッションで）
# ---------------------------------------------------------------------------


class _LedgerSession:
    def __init__(self, status):
        import json

        self.status = status
        self.candidates = json.dumps([{"candidate_id": "cand-1", "run_id": "r1", "wiring_name": "増幅",
                                       "condition": "入力 x=1.0", "statement": "s", "level": "match",
                                       "status": "candidate"}])
        self.updates: list[dict] = []
        self.commits = 0

    def execute(self, statement, params=None):
        sql = str(statement)

        class _R:
            def __init__(self, rows):
                self.rows = rows

            def fetchone(self):
                return self.rows[0] if self.rows else None

        if sql.strip().startswith("SELECT verification_status"):
            return _R([(self.status, "[]", self.candidates)])
        if "UPDATE epistemic_ledger" in sql:
            self.updates.append(dict(params or {}))
            return _R([])
        raise AssertionError(sql)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


class TestDoubtSimulationCandidates:
    @pytest.mark.parametrize("before,after", [
        ("untested", "indirectly_supported"),
        ("unknown", "indirectly_supported"),
        ("refuted", "refuted"),
        ("directly_verified", "directly_verified"),
    ])
    def test_confirm_raises_only_to_indirectly_supported(self, monkeypatch, before, after):
        session = _LedgerSession(before)
        audits = []
        monkeypatch.setattr(doubt_routes, "_pg_session", lambda: session)
        monkeypatch.setattr(doubt_routes, "_require_editable_ledger_target", lambda *a, **k: DOC)
        monkeypatch.setattr(doubt_routes, "_record_doubt_event", lambda *a, **k: audits.append(a))
        out = doubt_routes.confirm_simulation_candidate("claim", "c1", "cand-1", current_user=TEACHER)
        assert out["verification_status"] == after
        assert out["candidate"]["status"] == "confirmed"
        assert session.updates[0]["verification_status"] == after
        assert audits and audits[0][0] == "simulation"
        _assert_no_forbidden_keys(out)

    def test_dismiss_then_confirm_is_422(self, monkeypatch):
        import json

        session = _LedgerSession("untested")
        monkeypatch.setattr(doubt_routes, "_pg_session", lambda: session)
        monkeypatch.setattr(doubt_routes, "_require_editable_ledger_target", lambda *a, **k: DOC)
        monkeypatch.setattr(doubt_routes, "_record_doubt_event", lambda *a, **k: None)
        out = doubt_routes.dismiss_simulation_candidate("claim", "c1", "cand-1", current_user=TEACHER)
        assert out["candidate"]["status"] == "dismissed"
        session.candidates = session.updates[-1]["candidates"]
        assert json.loads(session.candidates)[0]["status"] == "dismissed"
        with pytest.raises(HTTPException) as exc:
            doubt_routes.confirm_simulation_candidate("claim", "c1", "cand-1", current_user=TEACHER)
        assert exc.value.status_code == 422

    def test_routes_use_the_editable_ledger_gate(self):
        from tests.guardrail_helpers import extract_function_source

        src = (BACKEND / "api" / "routes" / "doubt.py").read_text(encoding="utf-8")
        assert "_require_editable_ledger_target(" in extract_function_source(src, "_decide_simulation_candidate")


# ---------------------------------------------------------------------------
# 第 22 周 A4: 未測定の実行は教員側からも台帳候補にしない・配線なしの相対差・判断済み候補の読み出し
# ---------------------------------------------------------------------------


class TestA4LedgerAndMetrics:
    def test_teacher_cannot_ledger_unmeasured_run(self, env):
        rid = _succeeded_run(env)
        env["store"].runs[rid]["result"]["comparison"] = {"agreement_level": "unmeasured", "metrics": {}}
        with pytest.raises(HTTPException) as exc:
            sbr.add_simulation_ledger_candidate(rid, body=None, current_user=TEACHER)
        assert exc.value.status_code == 422
        assert exc.value.detail == schema.FACT_UNMEASURED_NOT_LEDGERABLE
        assert env["store"].ledger == []

    def test_reference_only_run_can_be_ledgered(self, env):
        rid = _succeeded_run(env)
        env["store"].runs[rid]["result"]["comparison"] = {
            "agreement_level": "unmeasured", "separation_level": "unmeasured", "reference_level": "match",
            "metrics": {},
        }
        out = sbr.add_simulation_ledger_candidate(rid, body=None, current_user=TEACHER)
        assert len(out["candidates"]) == 2

    def test_teacher_dto_hides_no_wiring_relative_difference(self):
        run = {
            "id": "r", "wiring_id": None, "status": "succeeded",
            "result": {"comparison": {"agreement_level": "unmeasured", "metrics": {
                "relative_difference": 0.0,
                "by_symbol": {"k": {"relative_difference": 0.0, "separation_ratio": None,
                                    "reference_relative_difference": None}},
            }}},
        }
        out = sbr._run_dto(run)
        metrics = out["result"]["comparison"]["metrics"]
        assert metrics["by_symbol"] == {}
        assert metrics["relative_difference"] is None
        assert run["result"]["comparison"]["metrics"]["relative_difference"] == 0.0

    def test_decided_candidates_are_readable_with_reason(self):
        import json

        class _S:
            def execute(self, statement, params=None):
                class _R:
                    def fetchone(self_inner):
                        return (json.dumps([
                            {"candidate_id": "a", "run_id": "r1", "status": "candidate"},
                            {"candidate_id": "b", "run_id": "r1", "status": "dismissed",
                             "dismiss_reason": "入力が論文と違う", "dismissed_by": "t-uuid",
                             "dismissed_at": "2026-10-04T00:00:00+00:00"},
                            {"candidate_id": "c", "run_id": "r2", "status": "confirmed",
                             "confirmed_by": "t-uuid", "confirmed_at": "2026-10-04T01:00:00+00:00"},
                        ]),)
                return _R()

        pending = doubt_routes._simulation_candidates_for(_S(), "claim", "c1")
        decided = doubt_routes._simulation_candidates_for(_S(), "claim", "c1", decided=True)
        assert [c["candidate_id"] for c in pending] == ["a"]
        assert [c["candidate_id"] for c in decided] == ["b", "c"]
        assert decided[0]["dismiss_reason"] == "入力が論文と違う"
        assert decided[0]["decided_at"].startswith("2026-10-04")
        assert "t-uuid" not in repr(decided)

    def test_ledger_get_returns_decided_key(self):
        src = (BACKEND / "api" / "routes" / "doubt.py").read_text(encoding="utf-8")
        assert '"simulation_candidates_decided": simulation_candidates_decided' in src
