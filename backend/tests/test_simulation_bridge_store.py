"""検証シミュレーション層（SB層）— ``core/simulation_bridge/store.py`` の単体テスト。

正本: ``docs/features/simulation_bridge_design.md`` §2（SB1 / SB3 / SB5 / SB6 / SB7）/ §4。

DB には触らない。SQL 文字列で振る舞いを切り替える小さな in-memory の fake session で、
状態遷移と台帳候補の規則を固定する:

- 再生成は旧版を DELETE せず superseded にする（SB7）。
- ``origin='llm_candidate'`` の配線は confirmed で作れない（SB5）。
- 判断は candidate からのみ・判断済みへの再判断は事実文のエラー（CandidateFlow 経由）。
- 台帳候補の確定は ``untested`` / ``unknown`` からだけ ``indirectly_supported`` に上げ、
  それ以上には上げない（SB1）。
- 候補 dict に confidence が無い（SB6）。
"""

from __future__ import annotations

import inspect
import json
import re
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.schema import AUDIT_ENTITY_SIMULATION  # noqa: E402
from core.simulation_bridge import schema  # noqa: E402
from core.simulation_bridge import store  # noqa: E402

PROGRAM_COLS = store._PROGRAM_COLUMNS
WIRING_COLS = store._WIRING_COLUMNS
RUN_COLS = store._RUN_COLUMNS


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _tuple(record: dict, columns) -> tuple:
    return tuple(record.get(c) for c in columns)


class FakeSession:
    """SQL の先頭語と対象表で振る舞いを切り替える in-memory fake。"""

    def __init__(self):
        self.programs: dict[str, dict] = {}
        self.wirings: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.ledger: dict[tuple[str, str], dict] = {}
        self.statements: list[str] = []

    # -- dispatcher -------------------------------------------------------
    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = dict(params or {})
        self.statements.append(sql)
        assert not re.search(r"\bDELETE\b", sql, re.IGNORECASE), "store は DELETE を発行しない（SB7）"
        if sql.startswith("INSERT INTO simulation_programs"):
            return self._insert_program(params)
        if sql.startswith("UPDATE simulation_programs SET review_status = :superseded"):
            return self._supersede_programs(params)
        if sql.startswith("UPDATE simulation_programs"):
            return self._update_status(self.programs, params, PROGRAM_COLS)
        if "FROM simulation_programs WHERE id" in sql:
            row = self.programs.get(params["id"])
            return _Result([_tuple(row, PROGRAM_COLS)] if row else [])
        if "FROM simulation_programs" in sql:
            rows = [
                r for r in self.programs.values()
                if r["document_id"] == params["document_id"]
                and (params["include_superseded"] or r["review_status"] != "superseded")
            ]
            return _Result([_tuple(r, PROGRAM_COLS) for r in rows])
        if sql.startswith("INSERT INTO instrument_wirings"):
            return self._insert_wiring(params)
        if sql.startswith("UPDATE instrument_wirings"):
            return self._update_status(self.wirings, params, WIRING_COLS)
        if "FROM instrument_wirings WHERE id" in sql:
            row = self.wirings.get(params["id"])
            return _Result([_tuple(row, WIRING_COLS)] if row else [])
        if "FROM instrument_wirings" in sql:
            rows = [
                r for r in self.wirings.values()
                if (params["document_id"] and r["document_id"] == params["document_id"])
                or (params["include_generic"] and r["document_id"] is None)
            ]
            return _Result([_tuple(r, WIRING_COLS) for r in rows])
        if sql.startswith("INSERT INTO simulation_runs"):
            return self._insert_run(params)
        if sql.startswith("UPDATE simulation_runs SET shared_at"):
            row = self.runs.get(params["id"])
            if row is None or row.get("owner_user_id") != params["owner_user_id"] or row.get("shared_at"):
                return _Result([])
            row["shared_at"] = "now"
            return _Result([_tuple(row, RUN_COLS)])
        if sql.startswith("UPDATE simulation_runs"):
            row = self.runs.get(params["id"])
            if row:
                row["ledger_candidate_id"] = params["candidate_id"]
            return _Result([_tuple(row, RUN_COLS)] if row else [])
        if "FROM simulation_runs WHERE id" in sql:
            row = self.runs.get(params["id"])
            return _Result([_tuple(row, RUN_COLS)] if row else [])
        if sql.startswith("INSERT INTO epistemic_ledger"):
            self.ledger.setdefault(
                (params["ttype"], params["tid"]),
                {"verification_status": "unknown", "verification_scopes": [], "simulation_candidates": []},
            )
            return _Result([])
        if sql.startswith("SELECT verification_status"):
            row = self.ledger.get((params["ttype"], params["tid"]))
            if row is None:
                return _Result([])
            return _Result([(row["verification_status"], row["verification_scopes"], row["simulation_candidates"])])
        if sql.startswith("UPDATE epistemic_ledger"):
            row = self.ledger[(params["ttype"], params["tid"])]
            if "simulation_candidates || CAST(:new AS jsonb)" in sql:
                row["simulation_candidates"] = row["simulation_candidates"] + json.loads(params["new"])
            else:
                row["simulation_candidates"] = json.loads(params["candidates"])
            if "verification_scopes || CAST(:scope AS jsonb)" in sql:
                row["verification_scopes"] = row["verification_scopes"] + json.loads(params["scope"])
            if "verification_status = :verification_status" in sql:
                row["verification_status"] = params["verification_status"]
            return _Result([])
        raise AssertionError(f"unexpected SQL: {sql}")

    # -- handlers ---------------------------------------------------------
    def _insert_program(self, p):
        rid = str(uuid.uuid4())
        row = {
            "id": rid, "document_id": p["document_id"], "module_stable_key": p["module_stable_key"],
            "module_key": p["module_key"], "language": p["language"], "source_code": p["source_code"],
            "io_contract": json.loads(p["io_contract"]), "provenance": json.loads(p["provenance"]),
            "review_status": p["review_status"], "review_note": "", "decided_by": None,
            "decided_at": None, "created_by": p["created_by"], "created_at": "t", "updated_at": "t",
            "superseded_at": None, "superseded_by_id": None,
            "owner_user_id": p["owner_user_id"], "course_id": p["course_id"],
        }
        self.programs[rid] = row
        return _Result([_tuple(row, PROGRAM_COLS)])

    def _supersede_programs(self, p):
        out = []
        for row in self.programs.values():
            if (
                row["document_id"] == p["document_id"]
                and row["module_key"] == p["module_key"]
                and row["review_status"] in (p["candidate"], p["confirmed"])
                and row["id"] != p["new_id"]
                and (
                    row.get("owner_user_id") == p["owner_user_id"]
                    or (p["supersede_unowned"] and not row.get("owner_user_id"))
                )
            ):
                row["review_status"] = p["superseded"]
                row["superseded_at"] = "now"
                row["superseded_by_id"] = p["new_id"]
                out.append((row["id"], row["review_status"]))
        return _Result(out)

    def _update_status(self, table, p, cols):
        row = table.get(p["id"])
        if row is None or row["review_status"] != p["old_status"]:
            return _Result([])
        row["review_status"] = p["new_status"]
        if p["review_note"]:
            row["review_note"] = p["review_note"]
        row["decided_by"] = p["actor_id"]
        row["decided_at"] = "now"
        return _Result([_tuple(row, cols)])

    def _insert_wiring(self, p):
        rid = str(uuid.uuid4())
        confirmed = p["review_status"] == p["confirmed"]
        row = {
            "id": rid, "document_id": p["document_id"] or None, "name": p["name"],
            "parts": json.loads(p["parts"]), "edges": json.loads(p["edges"]), "origin": p["origin"],
            "parent_wiring_id": p["parent_wiring_id"] or None, "revision_note": p["revision_note"],
            "review_status": p["review_status"], "review_note": "",
            "decided_by": p["created_by"] if confirmed else None,
            "decided_at": "now" if confirmed else None, "created_by": p["created_by"],
            "created_at": "t", "updated_at": "t",
        }
        self.wirings[rid] = row
        return _Result([_tuple(row, WIRING_COLS)])

    def _insert_run(self, p):
        rid = str(uuid.uuid4())
        row = {
            "id": rid, "document_id": p["document_id"], "program_id": p["program_id"],
            "wiring_id": p["wiring_id"] or None,
            "observation_claim_ids": json.loads(p["observation_claim_ids"]),
            "inputs": json.loads(p["inputs"]), "result": json.loads(p["result"]),
            "runner": json.loads(p["runner"]), "status": p["status"], "ledger_candidate_id": "",
            "created_by": p["created_by"], "created_at": "t",
            "owner_user_id": p["owner_user_id"], "course_id": p["course_id"], "shared_at": None,
            "prediction": json.loads(p["prediction"]),
        }
        self.runs[rid] = row
        return _Result([_tuple(row, RUN_COLS)])


class AuditLog:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)

    @property
    def actions(self):
        return [c["action"] for c in self.calls]


DOC = "11111111-1111-1111-1111-111111111111"


def _program(session, **kw):
    params = dict(
        document_id=DOC, module_key="mod-a", module_stable_key="k1:abc", source_code="def simulate(i, p):\n    return {}",
        io_contract={"inputs": [], "outputs": []}, provenance={"reconstructed_inputs": False},
        created_by="teacher-1",
    )
    params.update(kw)
    return store.create_program(session, **params)


# ===========================================================================
# programs
# ===========================================================================


class TestProgramSupersede:
    def test_create_supersedes_previous_candidate_and_confirmed(self):
        session = FakeSession()
        first = _program(session)
        audit = AuditLog()
        store.decide_program(session, program_id=first["id"], action="confirm", actor_id="t1", record_audit=audit)
        second = _program(session)
        assert second["superseded_ids"] == [first["id"]]
        old = session.programs[first["id"]]
        assert old["review_status"] == "superseded"
        assert old["superseded_by_id"] == second["id"]
        assert session.programs[second["id"]]["review_status"] == "candidate"
        # 旧行は残っている（DELETE しない）
        assert len(session.programs) == 2

    def test_other_module_and_dismissed_are_untouched(self):
        session = FakeSession()
        a = _program(session)
        audit = AuditLog()
        store.decide_program(session, program_id=a["id"], action="dismiss", actor_id="t1", record_audit=audit)
        other = _program(session, module_key="mod-b")
        again = _program(session)
        assert again["superseded_ids"] == []
        assert session.programs[a["id"]]["review_status"] == "dismissed"
        assert session.programs[other["id"]]["review_status"] == "candidate"

    def test_list_hides_superseded_by_default(self):
        session = FakeSession()
        _program(session)
        _program(session)
        assert len(store.list_programs(session, document_id=DOC)) == 1
        assert len(store.list_programs(session, document_id=DOC, include_superseded=True)) == 2

    def test_cannot_create_superseded_or_dismissed(self):
        with pytest.raises(store.SimulationStateError):
            _program(FakeSession(), review_status="superseded")

    def test_create_audits_generated_and_superseded(self):
        session = FakeSession()
        audit = AuditLog()
        _program(session, record_audit=audit)
        _program(session, record_audit=audit)
        assert audit.actions == ["program_generated", "program_generated", "program_superseded"]
        assert {c["entity_type"] for c in audit.calls} == {AUDIT_ENTITY_SIMULATION}


class TestProgramDecide:
    def test_confirm_goes_through_candidate_flow_and_audits(self):
        session = FakeSession()
        prog = _program(session)
        audit = AuditLog()
        out = store.decide_program(session, program_id=prog["id"], action="confirm", actor_id="t1", record_audit=audit)
        assert out["review_status"] == "confirmed"
        assert out["decided_by"] == "t1"
        assert audit.actions == ["program_confirmed"]
        assert audit.calls[0]["entity_type"] == AUDIT_ENTITY_SIMULATION

    def test_dismiss_needs_no_reason(self):
        session = FakeSession()
        prog = _program(session)
        audit = AuditLog()
        out = store.decide_program(session, program_id=prog["id"], action="dismiss", actor_id="t1", record_audit=audit)
        assert out["review_status"] == "dismissed"
        assert audit.actions == ["program_dismissed"]

    def test_already_decided_is_a_fact_error(self):
        session = FakeSession()
        prog = _program(session)
        audit = AuditLog()
        store.decide_program(session, program_id=prog["id"], action="confirm", actor_id="t1", record_audit=audit)
        with pytest.raises(store.SimulationStateError, match="既に判断済み"):
            store.decide_program(session, program_id=prog["id"], action="dismiss", actor_id="t1", record_audit=audit)
        assert audit.actions == ["program_confirmed"]

    def test_missing_program_is_not_found(self):
        with pytest.raises(store.SimulationNotFoundError):
            store.decide_program(
                FakeSession(), program_id=str(uuid.uuid4()), action="confirm", actor_id="t1", record_audit=AuditLog()
            )

    def test_unknown_action_and_missing_actor(self):
        session = FakeSession()
        prog = _program(session)
        with pytest.raises(store.SimulationStateError):
            store.decide_program(session, program_id=prog["id"], action="restore", actor_id="t1", record_audit=AuditLog())
        with pytest.raises(store.SimulationStateError):
            store.decide_program(session, program_id=prog["id"], action="confirm", actor_id="", record_audit=AuditLog())

    def test_store_uses_candidate_flow(self):
        source = inspect.getsource(store)
        assert "CandidateFlow(" in source
        assert "require_dismiss_reason=False" in source


# ===========================================================================
# wirings
# ===========================================================================


class TestWirings:
    def test_teacher_wiring_starts_confirmed(self):
        session = FakeSession()
        w = store.create_wiring(
            session, document_id=DOC, name="配線A", parts=[{"slot": "amp"}], edges=[],
            origin="teacher", created_by="t1",
        )
        assert w["review_status"] == "confirmed"
        assert w["decided_by"] == "t1"

    def test_llm_candidate_starts_candidate(self):
        session = FakeSession()
        w = store.create_wiring(
            session, document_id=DOC, name="改訂案", parts=[], edges=[], origin="llm_candidate",
            created_by="t1", parent_wiring_id=str(uuid.uuid4()),
        )
        assert w["review_status"] == "candidate"
        assert w["decided_by"] is None

    def test_llm_candidate_cannot_be_created_confirmed(self):
        with pytest.raises(store.SimulationStateError):
            store.create_wiring(
                FakeSession(), document_id=DOC, name="x", parts=[], edges=[], origin="llm_candidate",
                created_by="t1", review_status="confirmed",
            )

    def test_unknown_origin_rejected(self):
        with pytest.raises(store.SimulationStateError):
            store.create_wiring(FakeSession(), document_id=DOC, name="x", parts=[], edges=[], origin="ai", created_by="t1")

    def test_decide_wiring_and_already_decided(self):
        session = FakeSession()
        w = store.create_wiring(
            session, document_id=DOC, name="案", parts=[], edges=[], origin="llm_candidate", created_by="t1"
        )
        audit = AuditLog()
        out = store.decide_wiring(session, wiring_id=w["id"], action="confirm", actor_id="t2", record_audit=audit)
        assert out["review_status"] == "confirmed"
        assert audit.actions == ["wiring_confirmed"]
        with pytest.raises(store.SimulationStateError, match="既に判断済み"):
            store.decide_wiring(session, wiring_id=w["id"], action="dismiss", actor_id="t2", record_audit=audit)

    def test_teacher_wiring_is_not_decidable_again(self):
        session = FakeSession()
        w = store.create_wiring(session, document_id=DOC, name="A", parts=[], edges=[], origin="teacher", created_by="t1")
        with pytest.raises(store.SimulationStateError):
            store.decide_wiring(session, wiring_id=w["id"], action="dismiss", actor_id="t1", record_audit=AuditLog())

    def test_list_includes_generic(self):
        session = FakeSession()
        store.create_wiring(session, document_id=DOC, name="A", parts=[], edges=[], origin="teacher", created_by="t1")
        store.create_wiring(session, document_id=None, name="汎用", parts=[], edges=[], origin="teacher", created_by="t1")
        assert len(store.list_wirings(session, document_id=DOC)) == 2
        assert len(store.list_wirings(session, document_id=DOC, include_generic=False)) == 1


# ===========================================================================
# runs
# ===========================================================================


class TestRuns:
    def test_record_run_and_stamp_candidate(self):
        session = FakeSession()
        prog = _program(session)
        audit = AuditLog()
        run = store.record_run(
            session, document_id=DOC, program_id=prog["id"], wiring_id=None, observation_claim_ids=["c1"],
            inputs={"values": {"x": 1}}, result={}, runner={}, status="failed", created_by="t1", record_audit=audit,
        )
        assert run["status"] == "failed"
        assert run["wiring_id"] is None
        assert audit.actions == ["run_recorded"]
        stamped = store.set_run_ledger_candidate(session, run_id=run["id"], candidate_id="cand-1")
        assert stamped["ledger_candidate_id"] == "cand-1"

    def test_unknown_run_status_rejected(self):
        session = FakeSession()
        prog = _program(session)
        with pytest.raises(store.SimulationStateError):
            store.record_run(
                session, document_id=DOC, program_id=prog["id"], wiring_id=None, observation_claim_ids=[],
                inputs={}, result={}, runner={}, status="ok", created_by="t1",
            )


class TestLearnerOwnership:
    """v1.1（§14・SB12 / SB13 / SB14）: 所有者ごとの置き換え・共有の刻印・予想の保存。"""

    def test_supersede_is_scoped_to_the_owner(self):
        session = FakeSession()
        a = _program(session, owner_user_id="u-a")
        b = _program(session, owner_user_id="u-b")
        a2 = _program(session, owner_user_id="u-a")
        assert a2["superseded_ids"] == [a["id"]]
        assert session.programs[b["id"]]["review_status"] == "candidate"

    def test_teacher_regeneration_can_supersede_unowned_v1_rows(self):
        session = FakeSession()
        legacy = _program(session)  # owner なし = v1 の旧行
        learner = _program(session, owner_user_id="u-a")
        teacher = _program(session, owner_user_id="t-1", supersede_unowned=True)
        assert teacher["superseded_ids"] == [legacy["id"]]
        assert session.programs[learner["id"]]["review_status"] == "candidate"

    def test_learner_wiring_starts_confirmed(self):
        session = FakeSession()
        wiring = store.create_wiring(
            session, document_id=DOC, name="A", parts=[], edges=[], origin="learner", created_by="u-a",
            owner_user_id="u-a",
        )
        assert wiring["review_status"] == "confirmed"

    def test_record_run_keeps_prediction_and_owner(self):
        session = FakeSession()
        prog = _program(session, owner_user_id="u-a")
        run = store.record_run(
            session, document_id=DOC, program_id=prog["id"], wiring_id=None, observation_claim_ids=[],
            inputs={}, result={}, runner={}, status="succeeded", created_by="u-a", owner_user_id="u-a",
            course_id="c1", prediction=[{"symbol": "k", "value": 2.0}],
        )
        assert run["prediction"] == [{"symbol": "k", "value": 2.0}]
        assert run["owner_user_id"] == "u-a"
        assert run["shared_at"] is None

    def test_set_run_shared_is_owner_only_and_idempotent(self):
        session = FakeSession()
        prog = _program(session, owner_user_id="u-a")
        run = store.record_run(
            session, document_id=DOC, program_id=prog["id"], wiring_id=None, observation_claim_ids=[],
            inputs={}, result={}, runner={}, status="succeeded", created_by="u-a", owner_user_id="u-a",
        )
        with pytest.raises(store.SimulationStateError):
            store.set_run_shared(session, run_id=run["id"], owner_user_id="u-b")
        shared = store.set_run_shared(session, run_id=run["id"], owner_user_id="u-a")
        assert shared["shared_at"] == "now"
        again = store.set_run_shared(session, run_id=run["id"], owner_user_id="u-a")
        assert again["shared_at"] == "now"
        with pytest.raises(store.SimulationNotFoundError):
            store.set_run_shared(session, run_id=str(uuid.uuid4()), owner_user_id="u-a")

    def test_is_owner(self):
        assert store.is_owner({"owner_user_id": "u-a"}, "u-a")
        assert not store.is_owner({"owner_user_id": "u-a"}, "u-b")
        assert not store.is_owner({"owner_user_id": None}, "")
        assert not store.is_owner(None, "u-a")


# ===========================================================================
# ledger candidates
# ===========================================================================


def _run_dict():
    return {
        "id": "run-1", "program_id": "prog-1", "wiring_id": "wir-1",
        "inputs": {"values": {"a": 2.0}, "params": {"gain": 10}, "seed": 7},
    }


def _candidate(**kw):
    params = dict(
        run=_run_dict(), statement="このモデルと配線のもとでは、一致しています。", level="match",
        facts=[schema.FACT_NOT_VERIFICATION], reconstructed_inputs=False, created_by="t1",
        wiring_name="配線A",
    )
    params.update(kw)
    return store.build_ledger_candidate(**params)


def _walk_keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk_keys(v)


class TestLedgerCandidate:
    def test_candidate_shape_has_no_confidence(self):
        cand = _candidate()
        assert "confidence" not in set(_walk_keys(cand))
        assert not (set(_walk_keys(cand)) & schema.FORBIDDEN_DTO_KEYS)
        for key in (
            "candidate_id", "run_id", "program_id", "wiring_id", "statement", "level", "facts",
            "reconstructed_inputs", "status", "detector_version", "created_at", "created_by",
        ):
            assert key in cand
        assert cand["status"] == "candidate"
        assert cand["detector_version"] == schema.DETECTOR_VERSION

    def test_append_strips_forbidden_keys(self):
        session = FakeSession()
        cand = dict(_candidate(), confidence=0.9)
        stored = store.append_ledger_candidate(session, target_type="claim", target_id="c1", candidate=cand, document_id="dddddddd-dddd-dddd-dddd-dddddddddddd")
        assert "confidence" not in stored
        assert "confidence" not in session.ledger[("claim", "c1")]["simulation_candidates"][0]

    @pytest.mark.parametrize("before", ["untested", "unknown"])
    def test_confirm_raises_only_to_indirectly_supported(self, before):
        session = FakeSession()
        cand = _candidate()
        store.append_ledger_candidate(session, target_type="claim", target_id="c1", candidate=cand, document_id="dddddddd-dddd-dddd-dddd-dddddddddddd")
        session.ledger[("claim", "c1")]["verification_status"] = before
        audit = AuditLog()
        out = store.decide_ledger_candidate(
            session, target_type="claim", target_id="c1", candidate_id=cand["candidate_id"],
            action="confirm", actor_id="t2", record_audit=audit,
        )
        assert out["verification_status"] == "indirectly_supported"
        row = session.ledger[("claim", "c1")]
        assert row["verification_status"] == "indirectly_supported"
        assert len(row["verification_scopes"]) == 1
        scope = row["verification_scopes"][0]
        assert scope["system"] == "配線A"
        assert "a=2.0" in scope["condition"]
        assert scope["evidence_ids"] == []
        assert scope["recorded_by"] == "t2"
        assert "run-1" in scope["reason"]
        assert row["simulation_candidates"][0]["status"] == "confirmed"
        assert row["simulation_candidates"][0]["confirmed_by"] == "t2"
        assert audit.actions == ["ledger_candidate_confirmed"]

    @pytest.mark.parametrize("before", ["directly_verified", "refuted", "indirectly_supported"])
    def test_confirm_does_not_touch_other_statuses(self, before):
        session = FakeSession()
        cand = _candidate()
        store.append_ledger_candidate(session, target_type="claim", target_id="c1", candidate=cand, document_id="dddddddd-dddd-dddd-dddd-dddddddddddd")
        session.ledger[("claim", "c1")]["verification_status"] = before
        store.decide_ledger_candidate(
            session, target_type="claim", target_id="c1", candidate_id=cand["candidate_id"],
            action="confirm", actor_id="t2",
        )
        assert session.ledger[("claim", "c1")]["verification_status"] == before

    def test_never_raises_to_directly_verified(self):
        """store のコード（docstring を除く文字列定数）に directly_verified が現れない（SB1）。"""
        import ast

        assert schema.LEDGER_MAX_STATUS == "indirectly_supported"
        tree = ast.parse((BACKEND / "core" / "simulation_bridge" / "store.py").read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node):
                docstrings.add(id(node.body[0].value))
        literals = [
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
        ]
        assert not any("directly_verified" in v for v in literals)

    def test_no_wiring_label(self):
        session = FakeSession()
        cand = _candidate(wiring_name="")
        store.append_ledger_candidate(session, target_type="claim", target_id="c1", candidate=cand, document_id="dddddddd-dddd-dddd-dddd-dddddddddddd")
        store.decide_ledger_candidate(
            session, target_type="claim", target_id="c1", candidate_id=cand["candidate_id"],
            action="confirm", actor_id="t2",
        )
        assert session.ledger[("claim", "c1")]["verification_scopes"][0]["system"] == "配線なし"

    def test_dismiss_keeps_status_and_element(self):
        session = FakeSession()
        cand = _candidate()
        store.append_ledger_candidate(session, target_type="claim", target_id="c1", candidate=cand, document_id="dddddddd-dddd-dddd-dddd-dddddddddddd")
        session.ledger[("claim", "c1")]["verification_status"] = "untested"
        out = store.decide_ledger_candidate(
            session, target_type="claim", target_id="c1", candidate_id=cand["candidate_id"],
            action="dismiss", actor_id="t2",
        )
        row = session.ledger[("claim", "c1")]
        assert out["scope"] is None
        assert row["verification_status"] == "untested"
        assert row["verification_scopes"] == []
        assert row["simulation_candidates"][0]["status"] == "dismissed"
        assert row["simulation_candidates"][0]["dismissed_by"] == "t2"

    def test_already_decided_and_missing(self):
        session = FakeSession()
        cand = _candidate()
        store.append_ledger_candidate(session, target_type="claim", target_id="c1", candidate=cand, document_id="dddddddd-dddd-dddd-dddd-dddddddddddd")
        store.decide_ledger_candidate(
            session, target_type="claim", target_id="c1", candidate_id=cand["candidate_id"],
            action="dismiss", actor_id="t2",
        )
        with pytest.raises(store.SimulationStateError, match="既に処理済み"):
            store.decide_ledger_candidate(
                session, target_type="claim", target_id="c1", candidate_id=cand["candidate_id"],
                action="confirm", actor_id="t2",
            )
        with pytest.raises(store.SimulationNotFoundError):
            store.decide_ledger_candidate(
                session, target_type="claim", target_id="c1", candidate_id="nope",
                action="confirm", actor_id="t2",
            )
        with pytest.raises(store.SimulationNotFoundError):
            store.decide_ledger_candidate(
                session, target_type="claim", target_id="other", candidate_id="nope",
                action="confirm", actor_id="t2",
            )

    def test_invalid_target_type(self):
        with pytest.raises(store.SimulationStateError):
            store.append_ledger_candidate(FakeSession(), target_type="figure", target_id="x", candidate=_candidate())


class TestStoreModuleDiscipline:
    def test_no_delete_and_no_fastapi(self):
        source = (BACKEND / "core" / "simulation_bridge" / "store.py").read_text(encoding="utf-8")
        assert not re.search(r"\bDELETE\s+FROM\b", source, re.IGNORECASE)
        assert "fastapi" not in source
        assert "AUDIT_ENTITY_SIMULATION" in source
        assert 'entity_type="simulation"' not in source


class TestLedgerDecisionsForRuns:
    class _Rows:
        def __init__(self, rows=None, fail=False):
            self.rows = rows or []
            self.fail = fail
            self.rollbacks = 0
            self.params = None
            self.sql = ""

        def execute(self, statement, params=None):
            if self.fail:
                raise RuntimeError("column missing")
            self.sql = str(statement)
            self.params = params

            rows = self.rows

            class _R:
                def fetchall(self_inner):
                    return rows

            return _R()

        def rollback(self):
            self.rollbacks += 1

    def test_groups_by_run_and_aggregates(self):
        session = self._Rows([("r1", "dismissed"), ("r1", "confirmed"), ("r2", "candidate"), ("r3", "dismissed")])
        out = store.ledger_decisions_for_runs(session, ["r1", "r2", "r3", "r4"])
        assert out == {"r1": "confirmed", "r2": "candidate", "r3": "dismissed"}
        assert session.params == {"run_ids": ["r1", "r2", "r3", "r4"]}
        assert "SELECT" in session.sql and "UPDATE" not in session.sql and "INSERT" not in session.sql

    def test_single_run_default_none(self):
        assert store.ledger_decisions_for_run(self._Rows([]), "r9") == schema.LEDGER_DECISION_NONE

    def test_empty_ids_issue_no_sql(self):
        session = self._Rows(fail=True)
        assert store.ledger_decisions_for_runs(session, []) == {}

    def test_failure_is_fail_soft_and_rolls_back(self):
        session = self._Rows(fail=True)
        assert store.ledger_decisions_for_runs(session, ["r1"]) == {}
        assert session.rollbacks == 1


class TestDisplayMetrics:
    def test_wired_is_unchanged(self):
        metrics = {"relative_difference": 0.1, "by_symbol": {"k": {"relative_difference": 0.1}}}
        assert store.display_metrics(metrics, wired=True) == metrics

    def test_unwired_drops_relative_and_empty_symbols(self):
        metrics = {
            "relative_difference": 0.0, "separation_ratio": None,
            "by_symbol": {
                "k": {"relative_difference": 0.0, "separation_ratio": None, "reference_relative_difference": None},
                "n": {"relative_difference": 0.0, "reference_relative_difference": 0.5},
            },
        }
        out = store.display_metrics(metrics, wired=False)
        assert out["relative_difference"] is None
        assert out["by_symbol"] == {"n": {"relative_difference": None, "separation_ratio": None,
                                          "reference_relative_difference": 0.5}}
        assert metrics["by_symbol"]["k"]["relative_difference"] == 0.0  # 入力は不変

    def test_none_metrics(self):
        assert store.display_metrics(None, wired=False)["by_symbol"] == {}
