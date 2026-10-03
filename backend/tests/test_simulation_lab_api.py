"""検証シミュレーション層 v1.1「主語は学習者」— 学習者向け API のテスト（実 DB・実 LLM・実 sim-runner 不使用）。

正本: ``docs/features/simulation_bridge_design.md`` §14（SB11〜SB15・§14.3）。

ルート関数を直接呼び、受講ゲート・セッション・store・LLM・sandbox を monkeypatch で差し替える
（``test_simulation_bridge_api.py`` と同じ流儀）。学習者向け DTO が ``LearnerDisplayRoute`` の射影
（``project_for_learner``）を通ってもキーを失わないことは、射影を直接当てて確かめ、1 本は
TestClient でアプリ経由でも確かめる。
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

from core.display_projection import project_for_learner  # noqa: E402
from core.simulation_bridge import schema, service  # noqa: E402
from core.simulation_bridge import store as real_store  # noqa: E402
from core.simulation_bridge.sandbox import SandboxClient  # noqa: E402
from routes import simulation_bridge as sbr  # noqa: E402
from routes import simulation_lab as lab  # noqa: E402

COURSE = "course-1"
DOC = str(uuid.UUID(int=1))
OTHER_DOC = str(uuid.UUID(int=2))
STUDENT = {"id": str(uuid.UUID(int=10)), "role": "STUDENT"}
OTHER_STUDENT = {"id": str(uuid.UUID(int=11)), "role": "STUDENT"}
TEACHER = {"id": str(uuid.UUID(int=99)), "role": "TEACHER"}

GOOD_SOURCE = (
    "def simulate(inputs, params):\n"
    "    x = inputs.get('x', 1.0)\n"
    "    return {'k': 2.0 * x, 'count': 3.0, 'n': 1.0}\n"
)
BAD_SOURCE = "import os\n\ndef simulate(inputs, params):\n    return {'k': os.getcwd()}\n"


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _Session:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass

    def execute(self, *_a, **_k):  # pragma: no cover — 直接 SQL を投げない（store は fake）
        raise AssertionError("fake store を通さない SQL")


class _OwnerAwareStore:
    """store の公開関数の in-memory 版（所有者・共有の規則は本物と同じ意味で絞る）。"""

    DECIDE_ACTIONS = real_store.DECIDE_ACTIONS
    SimulationNotFoundError = real_store.SimulationNotFoundError
    SimulationStateError = real_store.SimulationStateError
    build_ledger_candidate = staticmethod(real_store.build_ledger_candidate)
    is_owner = staticmethod(real_store.is_owner)

    def __init__(self):
        self.programs: dict[str, dict] = {}
        self.wirings: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.ledger: list[dict] = []
        self.audits: list[dict] = []

    @staticmethod
    def _owner_ok(row, owner_user_id, include_unowned):
        if not owner_user_id:
            return True
        return row.get("owner_user_id") == owner_user_id or (include_unowned and not row.get("owner_user_id"))

    # programs
    def create_program(self, session, *, document_id, module_key, module_stable_key="", source_code,
                       io_contract, provenance, created_by, review_status="candidate", record_audit=None,
                       owner_user_id=None, course_id=None, supersede_unowned=False):
        pid = str(uuid.uuid4())
        for row in self.programs.values():
            same_owner = row.get("owner_user_id") == owner_user_id or (supersede_unowned and not row.get("owner_user_id"))
            if (row["document_id"] == document_id and row["module_key"] == module_key and same_owner
                    and row["review_status"] in ("candidate", "confirmed")):
                row["review_status"] = "superseded"
        row = {
            "id": pid, "document_id": document_id, "module_key": module_key,
            "module_stable_key": module_stable_key, "source_code": source_code,
            "io_contract": dict(io_contract or {}), "provenance": dict(provenance or {}),
            "review_status": review_status, "created_by": created_by, "created_at": "t",
            "owner_user_id": owner_user_id, "course_id": course_id,
        }
        self.programs[pid] = row
        if record_audit:
            record_audit(entity_id=pid, action="program_generated")
        return dict(row)

    def list_programs(self, session, *, document_id, include_superseded=False, owner_user_id=None,
                      include_unowned=False):
        return [
            dict(p) for p in reversed(list(self.programs.values()))
            if p["document_id"] == document_id
            and (include_superseded or p["review_status"] != "superseded")
            and self._owner_ok(p, owner_user_id, include_unowned)
        ]

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
        record_audit(entity_id=program_id, action="program_confirmed")
        return dict(p)

    # wirings
    def create_wiring(self, session, *, document_id, name, parts, edges, origin, created_by,
                      parent_wiring_id=None, revision_note="", review_status=None, record_audit=None,
                      owner_user_id=None, course_id=None):
        wid = str(uuid.uuid4())
        status = review_status or ("candidate" if origin == "llm_candidate" else "confirmed")
        row = {
            "id": wid, "document_id": document_id, "name": name, "parts": copy.deepcopy(list(parts)),
            "edges": copy.deepcopy(list(edges)), "origin": origin, "review_status": status,
            "parent_wiring_id": parent_wiring_id, "revision_note": revision_note,
            "created_by": created_by, "created_at": "t", "owner_user_id": owner_user_id, "course_id": course_id,
        }
        self.wirings[wid] = row
        return dict(row)

    def list_wirings(self, session, *, document_id, include_generic=True, owner_user_id=None, include_unowned=False):
        return [
            dict(w) for w in self.wirings.values()
            if w["document_id"] in (document_id, None, "") and self._owner_ok(w, owner_user_id, include_unowned)
        ]

    def get_wiring(self, session, wiring_id):
        w = self.wirings.get(wiring_id)
        return dict(w) if w else None

    # runs
    def record_run(self, session, *, document_id, program_id, wiring_id, observation_claim_ids, inputs,
                   result, runner, status, created_by, record_audit=None, owner_user_id=None,
                   course_id=None, prediction=None):
        rid = str(uuid.uuid4())
        row = {
            "id": rid, "document_id": document_id, "program_id": program_id, "wiring_id": wiring_id,
            "observation_claim_ids": list(observation_claim_ids or []), "inputs": copy.deepcopy(dict(inputs or {})),
            "result": copy.deepcopy(dict(result or {})), "runner": dict(runner or {}), "status": status,
            "ledger_candidate_id": None, "created_by": created_by, "created_at": "t",
            "owner_user_id": owner_user_id, "course_id": course_id, "shared_at": None,
            "prediction": copy.deepcopy(list(prediction or [])),
        }
        self.runs[rid] = row
        return copy.deepcopy(row)

    def list_runs(self, session, *, document_id, limit=50, owner_user_id=None, include_unowned=False,
                  include_shared=False):
        return [
            copy.deepcopy(r) for r in self.runs.values()
            if r["document_id"] == document_id
            and (self._owner_ok(r, owner_user_id, include_unowned) or (include_shared and r.get("shared_at")))
        ]

    def get_run(self, session, run_id):
        r = self.runs.get(run_id)
        return copy.deepcopy(r) if r else None

    def set_run_shared(self, session, *, run_id, owner_user_id):
        row = self.runs.get(run_id)
        if row is None:
            raise self.SimulationNotFoundError("x")
        if not real_store.is_owner(row, owner_user_id):
            raise self.SimulationStateError("x")
        if not row.get("shared_at"):
            row["shared_at"] = "2026-10-03T00:00:00+00:00"
        return copy.deepcopy(row)

    def set_run_ledger_candidate(self, session, *, run_id, candidate_id):
        self.runs[run_id]["ledger_candidate_id"] = candidate_id
        return copy.deepcopy(self.runs[run_id])

    def append_ledger_candidate(self, session, *, target_type, target_id, candidate, record_audit=None, actor_id=None, document_id=None, course_id=None):
        item = dict(candidate)
        item["target"] = (target_type, target_id)
        self.ledger.append(item)
        if record_audit:
            self.audits.append(item)
        return dict(candidate)


MODULE = {
    "module_key": "m2:abc",
    "level": "outer",
    "label": "導く: k",
    "visual_label": "導く: k",
    "theory_object": "k",
    "inputs": [{"equation_id": "eq_1", "display_label": "式 (1)"}],
    "outputs": [{"equation_id": "eq_2", "display_label": "式 (2)"}],
    "foundation": [],
    "assumptions": [],
    "members": [],
    "structure_fingerprint": "secret",
}

EQUATIONS = [
    {"agent_equation_id": "eq_1", "label": "(1)", "latex": "k = 2 x", "plain_text": "", "defined_symbols": ["k"],
     "used_symbols": ["x"], "agent_payload": {}},
    {"agent_equation_id": "eq_2", "label": "(2)", "latex": "n = 1", "plain_text": "", "defined_symbols": ["n"],
     "used_symbols": [], "agent_payload": {}},
]

CATALOG_ROWS = [
    {"entry_id": "amp", "version_no": 1, "entry_type": schema.PART_TYPE_INSTRUMENT, "entry_type_label": "装置モデル",
     "name": "線形増幅器", "summary": "", "body": {"transfer": "params['gain'] * x",
                                               "params": {"gain": {"default": 2.0, "min": 0.0, "max": 10.0}}}},
    {"entry_id": "noise", "version_no": 1, "entry_type": schema.PART_TYPE_NOISE, "entry_type_label": "ノイズ源",
     "name": "白色ノイズ", "summary": "", "body": {"noise_kind": "gaussian",
                                              "params": {"sigma": {"default": 0.1, "min": 0.0, "max": 5.0}}}},
]


def _transport(payload):
    if payload["entrypoint"] == schema.PROGRAM_ENTRYPOINT:
        return {"ok": True, "outputs": {"k": 2.0, "count": 3.0, "n": 1.0}}
    return {"ok": True, "outputs": {"observed": {
        "k": {"mean": 2.01, "std": 0.1, "samples": [2.0]},
        "count": {"mean": 3.0, "std": 0.1, "samples": [3.0]},
        "n": {"mean": 1.0, "std": 0.1, "samples": [1.0]},
    }}}


@pytest.fixture()
def env(monkeypatch):
    fake = _OwnerAwareStore()
    session = _Session()
    state = {
        "session": session, "store": fake, "gate_calls": [], "traces": [], "shared_marks": [],
        "sandbox": SandboxClient("http://sim", timeout_s=5, transport=_transport),
        "course_docs": {DOC}, "focus": (DOC,),
    }
    for module in (lab, sbr):
        monkeypatch.setattr(module, "store", fake)
        monkeypatch.setattr(module, "_pg_session", lambda: session)
        monkeypatch.setattr(module, "build_theory_modules_for_document",
                            lambda d, **k: {"available": True, "facts": [], "modules": [copy.deepcopy(MODULE)]})
        monkeypatch.setattr(module, "observation_claim_targets",
                            lambda s, **k: [{"claim_id": "c1", "label": "k を測る", "identified_via": "dsl_measures"}])
        monkeypatch.setattr(module, "parts_catalog", types.SimpleNamespace(
            load_parts_catalog=lambda s: copy.deepcopy(CATALOG_ROWS),
            catalog_by_id=lambda rows: {r["entry_id"]: r for r in rows},
        ))
        monkeypatch.setattr(module, "_sandbox_client", lambda: state["sandbox"])
        monkeypatch.setattr(module, "consume_daily_quota", lambda gate, **kw: state["gate_calls"].append(kw))
    monkeypatch.setattr(service, "saved_module_records", lambda s, d: {})
    monkeypatch.setattr(service, "equation_rows",
                        lambda s, d, ids: [e for e in EQUATIONS if ids is None or e["agent_equation_id"] in ids])
    monkeypatch.setattr(service, "symbol_rows", lambda s, d, eqs: [])
    monkeypatch.setattr(lab, "get_accessible_course_data",
                        lambda uid, cid: {"id": cid, "sources": []} if cid == COURSE else None)
    monkeypatch.setattr(lab, "list_course_source_document_ids", lambda data: set(state["course_docs"]))
    monkeypatch.setattr(lab, "learner_focus_document",
                        lambda *a, **k: types.SimpleNamespace(document_ids=tuple(state["focus"])))
    monkeypatch.setattr(lab, "_document_titles", lambda s, ids: {DOC: "論文A"})

    def _trace(user_id, course_id, topic_id, kind, text, context_label="", extra_payload=None, status="open"):
        state["traces"].append({"user_id": user_id, "course_id": course_id, "kind": kind, "text": text,
                                "payload": dict(extra_payload or {}), "status": status})
        return "trace-1"

    monkeypatch.setattr(lab, "record_interest_trace", _trace)
    monkeypatch.setattr(lab, "mark_verification_attempt_shared",
                        lambda uid, rid: state["shared_marks"].append((uid, rid)) or True)
    monkeypatch.setattr(sbr, "_ensure_document_viewable", lambda *a, **k: [])
    monkeypatch.setattr(sbr, "_ensure_document_editable", lambda *a, **k: [])
    monkeypatch.setattr(sbr, "record_review_event", lambda *a, **k: None)
    monkeypatch.setattr(sbr, "_user_display_names", lambda s, ids: {STUDENT["id"]: "学生A"})
    return state


def _structured(payload):
    def _fn(messages=None, response_format=None, **kwargs):
        return response_format(**payload)

    return _fn


def _seed_program(env, *, owner=STUDENT, status="confirmed", source=GOOD_SOURCE, ast_ok=True):
    program = env["store"].create_program(
        None, document_id=DOC, module_key=MODULE["module_key"], source_code=source, io_contract={},
        provenance={"ast_ok": ast_ok, "reconstructed_inputs": False}, created_by=owner["id"],
        owner_user_id=owner["id"], course_id=COURSE,
    )
    env["store"].programs[program["id"]]["review_status"] = status
    return program["id"]


def _seed_wiring(env, *, owner=STUDENT, status="confirmed"):
    wiring = env["store"].create_wiring(
        None, document_id=DOC, name="増幅＋ノイズ",
        parts=[{"slot": "amp", "entry_id": "amp", "version_no": 1, "params": {}},
               {"slot": "noise", "entry_id": "noise", "version_no": 1, "params": {}}],
        edges=[{"from_slot": "amp", "to_slot": "noise"}], origin="learner", created_by=owner["id"],
        owner_user_id=owner["id"], course_id=COURSE,
    )
    env["store"].wirings[wiring["id"]]["review_status"] = status
    return wiring["id"]


def _run_body(program_id, wiring_id=None, **extra):
    data = {
        "program_id": program_id, "wiring_id": wiring_id, "observation_claim_ids": ["c1"],
        "values": [{"symbol": "x", "value": 1.0}], "params": [{"name": "a", "value": 2}], "seed": 7,
    }
    data.update(extra)
    return lab.LearnerRunRequest(**data)


def _walk_keys(value, out=None):
    out = set() if out is None else out
    if isinstance(value, dict):
        for k, v in value.items():
            out.add(k)
            _walk_keys(v, out)
    elif isinstance(value, list):
        for v in value:
            _walk_keys(v, out)
    return out


# ---------------------------------------------------------------------------
# 受講ゲート・教材の範囲
# ---------------------------------------------------------------------------


class TestGates:
    def test_unknown_course_is_404(self, env):
        with pytest.raises(HTTPException) as exc:
            lab.get_simulation_lab_overview("nope", document_id="", topic_id="", current_user=STUDENT)
        assert exc.value.status_code == 404

    def test_document_outside_course_sources_is_404(self, env):
        with pytest.raises(HTTPException) as exc:
            lab.get_simulation_lab_overview(COURSE, document_id=OTHER_DOC, topic_id="", current_user=STUDENT)
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException) as exc:
            lab.generate_simulation_lab_program(
                COURSE, body=lab.GenerateRequest(module_key=MODULE["module_key"], document_id=OTHER_DOC),
                current_user=STUDENT,
            )
        assert exc.value.status_code == 404
        assert env["gate_calls"] == []

    def test_default_document_is_the_focus_document(self, env):
        out = lab.get_simulation_lab_overview(COURSE, document_id="", topic_id="t1", current_user=STUDENT)
        assert out["document_id"] == DOC
        assert out["document_title"] == "論文A"
        assert out["available"] is True

    def test_no_focus_returns_select_fact(self, env):
        env["focus"] = ()
        out = lab.get_simulation_lab_overview(COURSE, document_id="", topic_id="", current_user=STUDENT)
        assert out["document_id"] is None
        assert out["available"] is False
        assert schema.FACT_SELECT_DOCUMENT in out["facts"]
        assert out["documents"] == [{"document_id": DOC, "title": "論文A"}]


# ---------------------------------------------------------------------------
# 所有（SB12）
# ---------------------------------------------------------------------------


class TestOwnership:
    def test_other_users_program_is_404_everywhere(self, env):
        pid = _seed_program(env, owner=OTHER_STUDENT, status="candidate")
        with pytest.raises(HTTPException) as exc:
            lab.edit_simulation_lab_program(pid, body=lab.EditRequest(source_code=GOOD_SOURCE), current_user=STUDENT)
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException) as exc:
            lab.adopt_simulation_lab_program(pid, current_user=STUDENT)
        assert exc.value.status_code == 404
        env["store"].programs[pid]["review_status"] = "confirmed"
        with pytest.raises(HTTPException) as exc:
            lab.create_simulation_lab_run(COURSE, body=_run_body(pid), current_user=STUDENT)
        assert exc.value.status_code == 404

    def test_other_users_run_is_404(self, env):
        pid = _seed_program(env, owner=OTHER_STUDENT)
        out = lab.create_simulation_lab_run(COURSE, body=_run_body(pid), current_user=OTHER_STUDENT)
        rid = out["run"]["id"]
        for fn in (lab.get_simulation_lab_run, lambda r, current_user: lab.share_simulation_lab_run(
                r, body=None, current_user=current_user)):
            with pytest.raises(HTTPException) as exc:
                fn(rid, current_user=STUDENT)
            assert exc.value.status_code == 404
        listed = lab.list_simulation_lab_runs(COURSE, document_id=DOC, topic_id="", current_user=STUDENT)
        assert listed["runs"] == []

    def test_overview_lists_only_own_rows(self, env):
        _seed_program(env, owner=OTHER_STUDENT)
        _seed_wiring(env, owner=OTHER_STUDENT)
        mine = _seed_program(env, owner=STUDENT, status="candidate")
        out = lab.get_simulation_lab_overview(COURSE, document_id=DOC, topic_id="", current_user=STUDENT)
        assert [p["id"] for p in out["programs"]] == [mine]
        assert out["wirings"] == []
        assert out["modules"][0]["program"]["id"] == mine

    def test_no_owner_or_creator_ids_in_learner_dtos(self, env):
        pid = _seed_program(env)
        wid = _seed_wiring(env)
        lab.create_simulation_lab_run(COURSE, body=_run_body(pid, wid), current_user=STUDENT)
        out = lab.get_simulation_lab_overview(COURSE, document_id=DOC, topic_id="", current_user=STUDENT)
        keys = _walk_keys(out)
        assert not keys & {"owner_user_id", "created_by", "decided_by", "confidence", "shared_at", "course_id_raw"}


# ---------------------------------------------------------------------------
# 生成・編集・採用（SB11）
# ---------------------------------------------------------------------------


class TestPrograms:
    def test_generate_consumes_learner_quota_and_stores_owner(self, env, monkeypatch):
        monkeypatch.setattr(lab, "generate_text_with_structured_output",
                            _structured({"source_code": GOOD_SOURCE, "io_contract": {}, "facts": []}))
        seen = {}
        real_usage = lab.usage_context

        def _usage(**kw):
            seen.update(kw)
            return real_usage(**kw)

        monkeypatch.setattr(lab, "usage_context", _usage)
        out = lab.generate_simulation_lab_program(
            COURSE, body=lab.GenerateRequest(module_key=MODULE["module_key"]), current_user=STUDENT,
        )
        assert len(env["gate_calls"]) == 1
        assert env["gate_calls"][0]["user_id"] == STUDENT["id"]
        assert seen["feature"] == "learning:simulation_codegen"
        row = env["store"].programs[out["program"]["id"]]
        assert row["owner_user_id"] == STUDENT["id"]
        assert row["course_id"] == COURSE
        assert out["program"]["review_status"] == "candidate"
        assert out["program"]["provenance"]["ast_ok"] is True
        assert set(out["program"]) == {"id", "module_key", "review_status", "source_code", "io_contract",
                                       "provenance", "facts", "created_at"}

    def test_edit_supersedes_own_previous_version_only(self, env):
        mine = _seed_program(env, status="confirmed")
        theirs = _seed_program(env, owner=OTHER_STUDENT, status="confirmed")
        out = lab.edit_simulation_lab_program(mine, body=lab.EditRequest(source_code=GOOD_SOURCE),
                                              current_user=STUDENT)
        new_id = out["program"]["id"]
        assert env["store"].programs[mine]["review_status"] == "superseded"
        assert env["store"].programs[theirs]["review_status"] == "confirmed"
        assert env["store"].programs[new_id]["review_status"] == "candidate"
        assert env["store"].programs[new_id]["owner_user_id"] == STUDENT["id"]
        assert out["program"]["provenance"]["ast_ok"] is True
        assert schema.FACT_LEARNER_CODE_EDITED in out["facts"]
        assert env["gate_calls"] == []  # 自分で書く経路は LLM を呼ばない

    def test_edit_saves_ast_failed_code_but_adopt_rejects_it(self, env):
        mine = _seed_program(env, status="candidate")
        out = lab.edit_simulation_lab_program(mine, body=lab.EditRequest(source_code=BAD_SOURCE),
                                              current_user=STUDENT)
        program = out["program"]
        assert program["provenance"]["ast_ok"] is False
        assert program["provenance"]["ast_reasons"]
        assert schema.FACT_LEARNER_CODE_REJECTED in out["facts"]
        with pytest.raises(HTTPException) as exc:
            lab.adopt_simulation_lab_program(program["id"], current_user=STUDENT)
        assert exc.value.status_code == 422
        assert env["store"].programs[program["id"]]["review_status"] == "candidate"

    def test_adopt_confirms_own_candidate(self, env):
        pid = _seed_program(env, status="candidate")
        out = lab.adopt_simulation_lab_program(pid, current_user=STUDENT)
        assert out["program"]["review_status"] == "confirmed"


# ---------------------------------------------------------------------------
# 実行・予想・痕跡（SB14 / SB15）
# ---------------------------------------------------------------------------


class TestRuns:
    def test_run_requires_adopted_program(self, env):
        pid = _seed_program(env, status="candidate")
        with pytest.raises(HTTPException) as exc:
            lab.create_simulation_lab_run(COURSE, body=_run_body(pid), current_user=STUDENT)
        assert exc.value.status_code == 422

    def test_run_requires_own_confirmed_wiring(self, env):
        pid = _seed_program(env)
        candidate = _seed_wiring(env, status="candidate")
        with pytest.raises(HTTPException) as exc:
            lab.create_simulation_lab_run(COURSE, body=_run_body(pid, candidate), current_user=STUDENT)
        assert exc.value.status_code == 422
        theirs = _seed_wiring(env, owner=OTHER_STUDENT)
        with pytest.raises(HTTPException) as exc:
            lab.create_simulation_lab_run(COURSE, body=_run_body(pid, theirs), current_user=STUDENT)
        assert exc.value.status_code == 404

    def test_prediction_must_be_numbers(self, env):
        pid = _seed_program(env)
        with pytest.raises(HTTPException) as exc:
            lab.create_simulation_lab_run(
                COURSE, body=_run_body(pid, prediction=[{"symbol": "k", "value": "たぶん 2"}]), current_user=STUDENT,
            )
        assert exc.value.status_code == 422
        assert env["store"].runs == {}

    def test_unknown_observation_claim_is_422(self, env):
        pid = _seed_program(env)
        with pytest.raises(HTTPException) as exc:
            lab.create_simulation_lab_run(COURSE, body=_run_body(pid, observation_claim_ids=["ghost"]),
                                          current_user=STUDENT)
        assert exc.value.status_code == 422

    def test_prediction_is_stored_and_returned_as_list(self, env):
        pid = _seed_program(env)
        wid = _seed_wiring(env)
        out = lab.create_simulation_lab_run(
            COURSE, body=_run_body(pid, wid, prediction=[{"symbol": "k", "value": 2.5}]), current_user=STUDENT,
        )
        run = out["run"]
        assert run["status"] == "succeeded"
        assert run["prediction"] == [{"symbol": "k", "value": 2.5}]
        stored = env["store"].runs[run["id"]]
        assert stored["prediction"] == [{"symbol": "k", "value": 2.5}]
        assert stored["owner_user_id"] == STUDENT["id"]
        assert stored["inputs"]["values"] == {"x": 1.0}
        assert run["inputs"]["values"] == [{"symbol": "x", "value": 1.0}]
        assert run["inputs"]["params"] == [{"name": "a", "value": 2.0}]
        result = run["result"]
        assert result["theory_outputs"] == [
            {"symbol": "count", "value": 3.0}, {"symbol": "k", "value": 2.0}, {"symbol": "n", "value": 1.0},
        ]
        assert [o["symbol"] for o in result["observed"]] == ["count", "k", "n"]
        assert set(result["observed"][0]) == {"symbol", "mean", "std"}
        by_symbol = result["comparison"]["metrics"]["by_symbol"]
        assert isinstance(by_symbol, list)
        assert [b["symbol"] for b in by_symbol] == ["count", "k", "n"]
        assert run["shared"] is False
        assert result["labels"]["agreement"]

    def test_succeeded_run_writes_one_trace_without_numbers(self, env):
        pid = _seed_program(env)
        wid = _seed_wiring(env)
        out = lab.create_simulation_lab_run(COURSE, body=_run_body(pid, wid, topic_id="t1"), current_user=STUDENT)
        assert len(env["traces"]) == 1
        trace = env["traces"][0]
        assert trace["kind"] == "verification_attempt"
        assert trace["course_id"] == COURSE
        payload = trace["payload"]
        assert set(payload) == {"run_id", "document_id", "module_key", "agreement_level", "separation_level", "shared"}
        assert payload["run_id"] == out["run"]["id"]
        assert payload["shared"] is False
        assert payload["agreement_level"] in schema.AGREEMENT_LEVELS
        assert not any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in payload.values())

    def test_failed_run_writes_no_trace(self, env):
        pid = _seed_program(env)
        env["sandbox"] = SandboxClient("", timeout_s=5)
        out = lab.create_simulation_lab_run(COURSE, body=_run_body(pid), current_user=STUDENT)
        assert out["run"]["status"] == "failed"
        assert env["traces"] == []


# ---------------------------------------------------------------------------
# 共有（SB13）と教員の画面
# ---------------------------------------------------------------------------


class TestShare:
    def _run(self, env):
        pid = _seed_program(env)
        wid = _seed_wiring(env)
        return lab.create_simulation_lab_run(COURSE, body=_run_body(pid, wid), current_user=STUDENT)["run"]["id"]

    def test_share_sets_shared_at_and_appends_ledger_candidate(self, env):
        rid = self._run(env)
        out = lab.share_simulation_lab_run(rid, body=None, current_user=STUDENT)
        assert out["run"]["shared"] is True
        assert env["store"].runs[rid]["shared_at"]
        assert [c["target"] for c in env["store"].ledger] == [("claim", "c1")]
        assert env["store"].ledger[0]["created_by"] == STUDENT["id"]
        assert env["store"].ledger[0]["status"] == "candidate"
        assert env["store"].audits == []  # 本人の共有は監査記帳しない
        assert schema.FACT_SHARED_LEDGER in out["facts"]
        assert env["shared_marks"] == [(STUDENT["id"], rid)]
        assert "created_by" not in _walk_keys(out)

    def test_share_is_idempotent(self, env):
        rid = self._run(env)
        lab.share_simulation_lab_run(rid, body=None, current_user=STUDENT)
        lab.share_simulation_lab_run(rid, body=None, current_user=STUDENT)
        assert len(env["store"].ledger) == 1
        assert len(env["shared_marks"]) == 1

    def test_teacher_overview_lists_shared_run_without_student_id(self, env):
        rid = self._run(env)
        unshared_pid = _seed_program(env)
        lab.create_simulation_lab_run(COURSE, body=_run_body(unshared_pid), current_user=STUDENT)
        out = sbr.get_simulation_overview(DOC, current_user=TEACHER)
        assert out["runs"] == []
        assert out["programs"] == []
        lab.share_simulation_lab_run(rid, body=None, current_user=STUDENT)
        out = sbr.get_simulation_overview(DOC, current_user=TEACHER)
        assert [r["id"] for r in out["runs"]] == [rid]
        run = out["runs"][0]
        assert run["shared"] is True
        assert run["shared_by_label"] == "学生A"
        assert STUDENT["id"] not in repr(out)
        assert out["programs"] == []
        detail = sbr.get_simulation_run(rid, current_user=TEACHER)
        assert detail["run"]["shared_by_label"] == "学生A"

    def test_teacher_cannot_open_unshared_learner_run(self, env):
        rid = self._run(env)
        with pytest.raises(HTTPException) as exc:
            sbr.get_simulation_run(rid, current_user=TEACHER)
        assert exc.value.status_code == 404


class TestProposeRevision:
    def test_creates_owned_llm_candidate_and_uses_learner_feature(self, env, monkeypatch):
        pid = _seed_program(env)
        wid = _seed_wiring(env)
        rid = lab.create_simulation_lab_run(COURSE, body=_run_body(pid, wid), current_user=STUDENT)["run"]["id"]
        monkeypatch.setattr(lab, "generate_text_with_structured_output", _structured(
            {"changes": [{"op": "set_param", "slot": "noise", "param": "sigma", "value": 0.05, "reason": "下げる"}]}))
        seen = {}
        real_usage = lab.usage_context
        monkeypatch.setattr(lab, "usage_context", lambda **kw: seen.update(kw) or real_usage(**kw))
        out = lab.propose_simulation_lab_revision(rid, current_user=STUDENT)
        assert seen["feature"] == "learning:simulation_revision"
        assert len(env["gate_calls"]) == 1
        wiring = out["wiring"]
        assert wiring["origin"] == "llm_candidate"
        assert wiring["review_status"] == "candidate"
        assert env["store"].wirings[wiring["id"]]["owner_user_id"] == STUDENT["id"]
        assert all(isinstance(p["params"], list) for p in wiring["parts"])


class TestWirings:
    def test_learner_wiring_is_confirmed_and_params_are_lists(self, env):
        body = lab.LearnerWiringRequest(
            name="増幅", parts=[{"slot": "amp", "entry_id": "amp", "version_no": 1,
                                 "params": [{"name": "gain", "value": 3}]}],
        )
        out = lab.create_simulation_lab_wiring(COURSE, body=body, current_user=STUDENT)
        wiring = out["wiring"]
        assert wiring["origin"] == "learner"
        assert wiring["review_status"] == "confirmed"
        assert wiring["parts"][0]["params"] == [{"name": "gain", "value": 3.0}]
        assert env["store"].wirings[wiring["id"]]["parts"][0]["params"] == {"gain": 3.0}

    def test_invalid_wiring_is_422(self, env):
        body = lab.LearnerWiringRequest(name="x", parts=[{"slot": "a", "entry_id": "ghost"}])
        with pytest.raises(HTTPException) as exc:
            lab.create_simulation_lab_wiring(COURSE, body=body, current_user=STUDENT)
        assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# 表示投影（LearnerDisplayRoute）を通ってもキーを失わない
# ---------------------------------------------------------------------------


class TestLearnerProjection:
    def test_dto_survives_project_for_learner_with_k_count_n(self, env):
        pid = _seed_program(env)
        wid = _seed_wiring(env)
        lab.create_simulation_lab_run(
            COURSE, body=_run_body(pid, wid, prediction=[{"symbol": "k", "value": 2.5}],
                                   reference=[{"symbol": "k", "value": 2.0, "unit": "", "source_note": "表 1"}]),
            current_user=STUDENT,
        )
        out = lab.get_simulation_lab_overview(COURSE, document_id=DOC, topic_id="", current_user=STUDENT)
        assert project_for_learner(out) == out

    def test_source_code_is_not_rewritten_by_the_projection(self):
        code = "def simulate(inputs, params):\n    step_1 = 1.0\n    sym_2 = 2.0\n    return {'k': step_1 + sym_2}\n"
        assert project_for_learner({"program": {"source_code": code}}) == {"program": {"source_code": code}}

    def test_overview_through_the_app(self, env):
        from fastapi.testclient import TestClient

        from api.main import app
        from dependencies import _get_current_user

        pid = _seed_program(env)
        lab.create_simulation_lab_run(COURSE, body=_run_body(pid), current_user=STUDENT)
        app.dependency_overrides[_get_current_user] = lambda: STUDENT
        try:
            client = TestClient(app)
            response = client.get(f"/api/learning/courses/{COURSE}/simulation/overview?document_id={DOC}")
        finally:
            app.dependency_overrides.pop(_get_current_user, None)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {
            "course_id", "document_id", "document_title", "documents", "available", "facts", "sandbox_configured",
            "modules", "programs", "wirings", "runs", "observation_targets", "parts_catalog",
        }
        run = body["runs"][0]
        assert {o["symbol"] for o in run["result"]["theory_outputs"]} >= {"k", "count", "n"}
        assert body["parts_catalog"][0]["params"][0]["name"] == "gain"
        assert "owner_user_id" not in response.text


class TestVectorValues:
    def test_value_pairs_accepts_short_numeric_vectors_and_rejects_bad(self):
        from fastapi import HTTPException

        from routes import simulation_lab as sl

        out = sl._value_pairs([{"symbol": "k1", "value": [0.1, 0.0, 0.0]}, {"symbol": "F3", "value": 0.5}], label="入力の値")
        assert out == {"k1": [0.1, 0.0, 0.0], "F3": 0.5}
        import pytest

        with pytest.raises(HTTPException):
            sl._value_pairs([{"symbol": "k1", "value": ["a", 1]}], label="入力の値")
        with pytest.raises(HTTPException):
            sl._value_pairs([{"symbol": "k1", "value": []}], label="入力の値")



# ---------------------------------------------------------------------------
# 第 21 周の是正（R13 採用の意味 / R16 観測系の主張 / 未測定の共有）
# ---------------------------------------------------------------------------


class TestRound21:
    def test_adopt_says_it_is_not_teacher_approval(self, env):
        pid = _seed_program(env, status="candidate")
        out = lab.adopt_simulation_lab_program(pid, current_user=STUDENT)
        assert out["facts"] == [schema.FACT_ADOPTED]
        assert schema.FACT_ADOPTED in out["program"]["facts"]
        assert schema.FACT_ADOPTED in out["program"]["provenance"]["facts"]

    def test_candidate_program_has_no_adopted_fact(self, env):
        pid = _seed_program(env, status="candidate")
        overview = lab.get_simulation_lab_overview(COURSE, document_id=DOC, current_user=STUDENT)
        program = next(p for p in overview["programs"] if p["id"] == pid)
        assert schema.FACT_ADOPTED not in program["facts"]

    def test_unreadable_targets_are_dropped_and_fact_added(self, env, monkeypatch):
        rows = [
            {"claim_id": "c1", "label": "（本文を特定できない要素）"},
            {"claim_id": "c2", "label": "claim:tex_b19:span_002"},
        ]
        for module in (lab, sbr):
            monkeypatch.setattr(module, "observation_claim_targets", lambda s, **k: list(rows))
        out = lab.get_simulation_lab_overview(COURSE, document_id=DOC, current_user=STUDENT)
        assert out["observation_targets"] == []
        assert schema.FACT_NO_OBSERVATION_TARGETS in out["facts"]
        teacher = sbr.get_simulation_overview(DOC, current_user=TEACHER)
        assert teacher["observation_targets"] == []
        assert schema.FACT_NO_OBSERVATION_TARGETS in teacher["facts"]

    def test_readable_targets_keep_no_fact(self, env):
        out = lab.get_simulation_lab_overview(COURSE, document_id=DOC, current_user=STUDENT)
        assert [t["claim_id"] for t in out["observation_targets"]] == ["c1"]
        assert schema.FACT_NO_OBSERVATION_TARGETS not in out["facts"]

    def test_share_of_unmeasured_run_does_not_write_ledger(self, env):
        pid = _seed_program(env)
        rid = lab.create_simulation_lab_run(COURSE, body=_run_body(pid), current_user=STUDENT)["run"]["id"]
        assert env["store"].runs[rid]["status"] == "succeeded"
        out = lab.share_simulation_lab_run(rid, body=None, current_user=STUDENT)
        assert out["run"]["shared"] is True
        assert env["store"].ledger == []
        assert schema.FACT_SHARED_UNMEASURED in out["facts"]
        assert schema.FACT_SHARED_LEDGER not in out["facts"]

    def test_no_wiring_run_shows_observed_as_theory(self, env):
        pid = _seed_program(env)
        run = lab.create_simulation_lab_run(COURSE, body=_run_body(pid), current_user=STUDENT)["run"]
        observed = {o["symbol"]: o for o in run["result"]["observed"]}
        assert observed["k"] == {"symbol": "k", "mean": 2.0, "std": None}
