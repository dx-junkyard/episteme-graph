"""SL層（賭け金の台帳, Stakes Ledger）— routes / 投影スコープ（第2波）のテスト。

対象: backend/api/routes/doubt.py（SL-1〜SL-4 の新規/拡張エンドポイント）/
backend/core/doubt/open_assumptions.py（SL-4 投影拡張）/
backend/core/discuss/opening.py（SL-1 結線）。

routes/doubt.py は `from dependencies import ...` の flat import を含むため、
`sys.path` に `backend/` と `backend/api/` の両方を通してから
`from api.routes import doubt` で直接 import する
（test_understanding_cycle_api.py / test_personal_graph_map_ops.py と同型の手法）。
DB / FastAPI TestClient は使わず、ルート関数を直接呼び、`_pg_session` を
フェイクセッションに差し替えて分岐ロジックを検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

from fastapi import HTTPException  # noqa: E402

from api.routes import doubt  # noqa: E402
from core.doubt import open_assumptions  # noqa: E402
from core.discuss import opening  # noqa: E402


# ===========================================================================
# フェイクセッション（SQL 部分文字列 → 応答 のディスパッチ方式）
# ===========================================================================


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows) if rows is not None else []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeSession:
    """(predicate(sql) -> bool, rows) の列に先勝ちでディスパッチする。

    マッチしない SQL（大半の UPDATE/INSERT）は空の結果を返す（route 側の
    コードはこれらの戻り値を使わないため無害）。
    """

    def __init__(self, handlers=None):
        self._handlers = list(handlers or [])
        self.calls: list[tuple[str, dict]] = []
        self.committed = 0
        self.rolled_back = 0
        self.closed = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = dict(params or {})
        self.calls.append((sql, params))
        for predicate, rows in self._handlers:
            if predicate(sql):
                return _FakeResult(rows)
        return _FakeResult([])

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


def _no_session():
    raise AssertionError("this test expects validation to fail before a DB session opens")


# ---------------------------------------------------------------------------
# epistemic_ledger 行のフェイク行ビルダ（_fetch_ledger_row の15列と対応）
# ---------------------------------------------------------------------------

_LEDGER_COLUMNS = (
    "id", "target_id", "target_type", "document_id", "course_id",
    "verification_status", "verification_scopes", "scope_candidates",
    "consensus_explicit", "consensus_behavioral", "load_score",
    "created_at", "updated_at", "falsification_conditions", "falsification_candidates",
)


def _ledger_row(**overrides) -> tuple:
    defaults = {
        "id": "led-1", "target_id": "target-1", "target_type": "claim",
        "document_id": "doc-1", "course_id": "course-1",
        "verification_status": "untested", "verification_scopes": [],
        "scope_candidates": [], "consensus_explicit": {}, "consensus_behavioral": 0,
        "load_score": None, "created_at": "2024-01-01", "updated_at": "2024-01-01",
        "falsification_conditions": [], "falsification_candidates": [],
    }
    defaults.update(overrides)
    return tuple(defaults[c] for c in _LEDGER_COLUMNS)


def _is_ledger_select(sql: str) -> bool:
    return "SELECT id::text, target_id, target_type" in sql and "FROM epistemic_ledger" in sql


def _capture_audit(monkeypatch):
    """`_record_doubt_event` をキャプチャして副作用の DB 書き込みを避ける。"""
    calls: list[tuple] = []
    monkeypatch.setattr(doubt, "_record_doubt_event", lambda *a, **kw: calls.append((a, kw)))
    return calls


# ===========================================================================
# SL-1: POST .../falsification-conditions（手動記帳）
# ===========================================================================


class TestAddFalsificationCondition:
    def test_empty_statement_is_422_without_opening_session(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        body = doubt.FalsificationConditionCreateRequest(
            statement="", kind="observation_value", reason="r", evidence_quote="q",
        )
        with pytest.raises(HTTPException) as exc:
            doubt.add_falsification_condition("claim", "c1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_invalid_kind_is_422(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        body = doubt.FalsificationConditionCreateRequest(
            statement="s", kind="bogus", reason="r", evidence_quote="q",
        )
        with pytest.raises(HTTPException) as exc:
            doubt.add_falsification_condition("claim", "c1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_empty_reason_is_422(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        body = doubt.FalsificationConditionCreateRequest(
            statement="s", kind="observation_value", reason="", evidence_quote="q",
        )
        with pytest.raises(HTTPException) as exc:
            doubt.add_falsification_condition("claim", "c1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_missing_evidence_for_non_not_formulable_kind_is_422(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        body = doubt.FalsificationConditionCreateRequest(
            statement="s", kind="observation_value", reason="r",
            evidence_ids=[], evidence_quote="",
        )
        with pytest.raises(HTTPException) as exc:
            doubt.add_falsification_condition("claim", "c1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_not_formulable_kind_does_not_require_evidence(self, monkeypatch):
        session = _FakeSession([(_is_ledger_select, [_ledger_row()])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        _capture_audit(monkeypatch)
        body = doubt.FalsificationConditionCreateRequest(
            statement="定式化できない", kind="not_formulable", reason="r",
            evidence_ids=[], evidence_quote="",
        )
        result = doubt.add_falsification_condition("claim", "c1", body, current_user={"id": "u1"})
        assert result["ok"] is True
        assert result["condition"]["kind"] == "not_formulable"

    def test_invalid_reachability_is_422(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        body = doubt.FalsificationConditionCreateRequest(
            statement="s", kind="observation_value", reason="r", evidence_quote="q",
            reachability="bogus",
        )
        with pytest.raises(HTTPException) as exc:
            doubt.add_falsification_condition("claim", "c1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_success_attributes_recorded_by_to_current_user(self, monkeypatch):
        session = _FakeSession([(_is_ledger_select, [_ledger_row()])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        audit_calls = _capture_audit(monkeypatch)
        body = doubt.FalsificationConditionCreateRequest(
            statement="測定値が閾値を超えれば覆る", kind="observation_value", reason="出典に明記",
            evidence_quote="原文の引用", reachability="reachable",
        )
        result = doubt.add_falsification_condition("claim", "c1", body, current_user={"id": "teacher-1"})
        assert result["ok"] is True
        condition = result["condition"]
        assert condition["statement"] == "測定値が閾値を超えれば覆る"
        assert condition["recorded_by"] == "teacher-1"
        assert condition["reachability"] == "reachable"
        assert condition["from_candidate_id"] == ""
        assert "confidence" not in condition
        assert len(audit_calls) == 1
        assert audit_calls[0][0][5]["action"] == "falsification_add"


# ===========================================================================
# SL-1: PATCH .../falsification-conditions/{condition_id}
# ===========================================================================


class TestPatchFalsificationCondition:
    def _existing_condition(self, **overrides):
        base = {
            "condition_id": "cond-1", "statement": "s", "kind": "observation_value",
            "reachability": "unassessed", "evidence_ids": [], "evidence_quote": "q",
            "recorded_by": "teacher-0", "reason": "r", "recorded_at": "2024-01-01",
            "from_candidate_id": "",
        }
        base.update(overrides)
        return base

    def test_ledger_not_found_is_404(self, monkeypatch):
        session = _FakeSession([(_is_ledger_select, [])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        body = doubt.FalsificationConditionPatchRequest(reachability="reachable")
        with pytest.raises(HTTPException) as exc:
            doubt.patch_falsification_condition("claim", "c1", "cond-1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 404

    def test_condition_not_found_is_404(self, monkeypatch):
        row = _ledger_row(falsification_conditions=[self._existing_condition(condition_id="other")])
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        body = doubt.FalsificationConditionPatchRequest(reachability="reachable")
        with pytest.raises(HTTPException) as exc:
            doubt.patch_falsification_condition("claim", "c1", "cond-1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 404

    def test_patch_clearing_evidence_on_non_not_formulable_kind_is_422(self, monkeypatch):
        row = _ledger_row(falsification_conditions=[self._existing_condition()])
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        body = doubt.FalsificationConditionPatchRequest(evidence_quote="")
        with pytest.raises(HTTPException) as exc:
            doubt.patch_falsification_condition("claim", "c1", "cond-1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_successful_reachability_patch(self, monkeypatch):
        row = _ledger_row(falsification_conditions=[self._existing_condition()])
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        audit_calls = _capture_audit(monkeypatch)
        body = doubt.FalsificationConditionPatchRequest(reachability="reachable")
        result = doubt.patch_falsification_condition("claim", "c1", "cond-1", body, current_user={"id": "u1"})
        assert result["condition"]["reachability"] == "reachable"
        assert audit_calls[0][0][5]["action"] == "falsification_patch"


# ===========================================================================
# SL-1: 候補の confirm / dismiss
# ===========================================================================


class TestConfirmFalsificationCandidate:
    def _candidate(self, **overrides):
        base = {
            "candidate_id": "cand-1", "statement": "測定値がずれれば覆る",
            "kind": "observation_value", "evidence_quote": "出典の原文",
            "reason": "出典に明記", "confidence": 0.8, "status": "candidate",
            "detector_version": "v1", "created_at": "2024-01-01",
        }
        base.update(overrides)
        return base

    def test_candidate_not_found_is_404(self, monkeypatch):
        session = _FakeSession([(_is_ledger_select, [_ledger_row()])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        with pytest.raises(HTTPException) as exc:
            doubt.confirm_falsification_candidate(
                "claim", "c1", "missing", body=None, current_user={"id": "u1"},
            )
        assert exc.value.status_code == 404

    def test_already_processed_candidate_is_422(self, monkeypatch):
        row = _ledger_row(falsification_candidates=[self._candidate(status="dismissed")])
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        with pytest.raises(HTTPException) as exc:
            doubt.confirm_falsification_candidate(
                "claim", "c1", "cand-1", body=None, current_user={"id": "u1"},
            )
        assert exc.value.status_code == 422

    def test_confirm_defaults_reachability_to_unassessed_and_keeps_candidate_row(self, monkeypatch):
        candidate = self._candidate()
        row = _ledger_row(falsification_candidates=[candidate])
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        audit_calls = _capture_audit(monkeypatch)
        result = doubt.confirm_falsification_candidate(
            "claim", "c1", "cand-1", body=None, current_user={"id": "teacher-9"},
        )
        condition = result["condition"]
        assert condition["reachability"] == "unassessed"
        assert condition["statement"] == candidate["statement"]
        assert condition["recorded_by"] == "teacher-9"
        assert condition["from_candidate_id"] == "cand-1"
        assert "confidence" not in condition
        # 候補行自体は昇格せず confirmed で保持される（in-place mutate。P4）
        assert candidate["status"] == "confirmed"
        assert candidate["confirmed_by"] == "teacher-9"
        assert audit_calls[0][0][5]["action"] == "falsification_candidate_confirm"

    def test_confirm_accepts_reachability_override(self, monkeypatch):
        candidate = self._candidate()
        row = _ledger_row(falsification_candidates=[candidate])
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        _capture_audit(monkeypatch)
        body = doubt.FalsificationCandidateConfirmRequest(reachability="reachable")
        result = doubt.confirm_falsification_candidate(
            "claim", "c1", "cand-1", body=body, current_user={"id": "u1"},
        )
        assert result["condition"]["reachability"] == "reachable"


class TestDismissFalsificationCandidate:
    def test_not_found_is_404(self, monkeypatch):
        session = _FakeSession([(_is_ledger_select, [_ledger_row()])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        with pytest.raises(HTTPException) as exc:
            doubt.dismiss_falsification_candidate("claim", "c1", "missing", current_user={"id": "u1"})
        assert exc.value.status_code == 404

    def test_dismiss_marks_status_and_preserves_row(self, monkeypatch):
        candidate = {"candidate_id": "cand-1", "statement": "s", "status": "candidate"}
        row = _ledger_row(falsification_candidates=[candidate])
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        audit_calls = _capture_audit(monkeypatch)
        result = doubt.dismiss_falsification_candidate("claim", "c1", "cand-1", current_user={"id": "u1"})
        assert result == {"ok": True, "candidate_id": "cand-1", "status": "dismissed"}
        assert candidate["status"] == "dismissed"
        assert audit_calls[0][0][5]["action"] == "falsification_candidate_dismiss"


class TestRefreshFalsificationCandidates:
    def test_schedules_and_reports_ok(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            doubt, "maybe_schedule_falsification_candidates",
            lambda **kw: calls.append(kw) or True,
        )
        result = doubt.refresh_falsification_candidates("course-1", current_user={"id": "u1"})
        assert result == {"ok": True, "scheduled": True}
        assert calls == [{"course_id": "course-1"}]


# ===========================================================================
# SL-2: 観測ターゲット一覧・反実仮想の拡張
# ===========================================================================


class TestObservationTargetsEndpoint:
    def test_delegates_to_core_function(self, monkeypatch):
        session = _FakeSession([])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        captured = {}

        def _fake_targets(_session, *, course_id="", document_id=""):
            captured["course_id"] = course_id
            return [{"claim_id": "c1", "label": "L", "identified_via": "dsl_measures"}]

        monkeypatch.setattr(doubt, "observation_claim_targets", _fake_targets)
        result = doubt.get_observation_targets("course-1", current_user={"id": "u1"})
        assert captured["course_id"] == "course-1"
        assert result == {"targets": [{"claim_id": "c1", "label": "L", "identified_via": "dsl_measures"}]}
        assert session.closed is True


class TestObservationClaimIdsHelper:
    def test_invalid_aspect_is_422(self):
        obs = [doubt.ToggledObservation(claim_id="c1", aspect="bogus")]
        with pytest.raises(HTTPException) as exc:
            doubt._observation_claim_ids(obs)
        assert exc.value.status_code == 422

    def test_valid_aspects_pass_through_claim_ids(self):
        obs = [
            doubt.ToggledObservation(claim_id="c1", aspect="value"),
            doubt.ToggledObservation(claim_id="c2", aspect="systematics"),
        ]
        assert doubt._observation_claim_ids(obs) == ["c1", "c2"]

    def test_blank_claim_id_is_dropped(self):
        obs = [doubt.ToggledObservation(claim_id="  ", aspect="value")]
        assert doubt._observation_claim_ids(obs) == []


class TestRequireAtLeastOneToggle:
    def test_both_empty_is_422(self):
        with pytest.raises(HTTPException) as exc:
            doubt._require_at_least_one_toggle([], [])
        assert exc.value.status_code == 422

    def test_assumption_only_is_allowed(self):
        doubt._require_at_least_one_toggle(["a1"], [])

    def test_observation_only_is_allowed(self):
        doubt._require_at_least_one_toggle([], [doubt.ToggledObservation(claim_id="c1")])


class TestComputeCounterfactualRoute:
    def test_combines_assumption_and_observation_ids(self, monkeypatch):
        session = _FakeSession([])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        captured = {}

        def _fake_compute(_session, *, toggled_assumption_ids, course_id="", document_id=""):
            captured["ids"] = list(toggled_assumption_ids)
            return {"toggled_assumption_ids": toggled_assumption_ids}

        monkeypatch.setattr(doubt, "compute_counterfactual", _fake_compute)
        body = doubt.CounterfactualComputeRequest(
            toggled_assumption_ids=["a1"],
            toggled_observations=[doubt.ToggledObservation(claim_id="c1", aspect="value")],
        )
        doubt.compute_counterfactual_route(body, current_user={"id": "u1"})
        assert captured["ids"] == ["a1", "c1"]

    def test_both_empty_is_422_before_compute_call(self, monkeypatch):
        called = []
        monkeypatch.setattr(doubt, "compute_counterfactual", lambda *a, **kw: called.append(1))
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        body = doubt.CounterfactualComputeRequest()
        with pytest.raises(HTTPException) as exc:
            doubt.compute_counterfactual_route(body, current_user={"id": "u1"})
        assert exc.value.status_code == 422
        assert called == []


class TestSaveCounterfactualSession:
    def test_persists_toggled_observations_column(self, monkeypatch):
        insert_calls = []

        def _is_cf_insert(sql):
            return "INSERT INTO counterfactual_sessions" in sql

        def _capture_insert(sql):
            return None

        session = _FakeSession([(_is_cf_insert, [("sess-1",)])])
        orig_execute = session.execute

        def _tracking_execute(stmt, params=None):
            sql = str(stmt)
            if "INSERT INTO counterfactual_sessions" in sql:
                insert_calls.append(dict(params or {}))
            return orig_execute(stmt, params)

        session.execute = _tracking_execute
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        monkeypatch.setattr(
            doubt, "compute_counterfactual",
            lambda *a, **kw: {
                "toggled_assumption_ids": ["a1", "c1"],
                "collapsed_node_ids": ["n1"],
                "surviving_node_ids": [],
                "indeterminate_node_ids": [],
                "node_labels": {"n1": "N1"},
                "warnings": [],
            },
        )
        _capture_audit(monkeypatch)
        body = doubt.CounterfactualSessionCreateRequest(
            toggled_assumption_ids=["a1"],
            toggled_observations=[doubt.ToggledObservation(claim_id="c1", aspect="systematics")],
        )
        result = doubt.save_counterfactual_session(body, current_user={"id": "u1"})
        assert result["session_id"] == "sess-1"
        assert len(insert_calls) == 1
        assert '"claim_id": "c1"' in insert_calls[0]["toggled_obs"]
        assert '"aspect": "systematics"' in insert_calls[0]["toggled_obs"]


# ===========================================================================
# SL-8: challenge → proposal 昇格 + proposal PATCH
# ===========================================================================


class TestCreateVerificationProposal:
    def test_missing_external_check_is_422(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        body = doubt.ProposalCreateRequest(proposal="実験Xで検証する", external_check="")
        with pytest.raises(HTTPException) as exc:
            doubt.create_verification_proposal("challenge-1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422
        assert "コーパス外" in exc.value.detail

    def test_missing_proposal_text_is_422(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        body = doubt.ProposalCreateRequest(proposal="", external_check="確認済み")
        with pytest.raises(HTTPException) as exc:
            doubt.create_verification_proposal("challenge-1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_invalid_reachability_is_422(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        body = doubt.ProposalCreateRequest(
            proposal="p", external_check="確認済み", reachability="bogus",
        )
        with pytest.raises(HTTPException) as exc:
            doubt.create_verification_proposal("challenge-1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_withdrawn_challenge_cannot_be_promoted(self, monkeypatch):
        def _is_challenge_select(sql):
            return "SELECT status, course_id FROM challenges" in sql

        session = _FakeSession([(_is_challenge_select, [("withdrawn", "course-1")])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        body = doubt.ProposalCreateRequest(proposal="p", external_check="確認済み")
        with pytest.raises(HTTPException) as exc:
            doubt.create_verification_proposal("challenge-1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_challenge_not_found_is_404(self, monkeypatch):
        def _is_challenge_select(sql):
            return "SELECT status, course_id FROM challenges" in sql

        session = _FakeSession([(_is_challenge_select, [])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        body = doubt.ProposalCreateRequest(proposal="p", external_check="確認済み")
        with pytest.raises(HTTPException) as exc:
            doubt.create_verification_proposal("challenge-1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 404

    def test_success_returns_proposal_id(self, monkeypatch):
        def _is_challenge_select(sql):
            return "SELECT status, course_id FROM challenges" in sql

        def _is_proposal_insert(sql):
            return "INSERT INTO verification_proposals" in sql

        session = _FakeSession([
            (_is_challenge_select, [("open", "course-1")]),
            (_is_proposal_insert, [("proposal-1",)]),
        ])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        _capture_audit(monkeypatch)
        body = doubt.ProposalCreateRequest(
            proposal="実験Xで検証する", external_check="外部文献Yを確認した", reachability="reachable",
        )
        result = doubt.create_verification_proposal("challenge-1", body, current_user={"id": "u1"})
        assert result == {
            "ok": True, "proposal_id": "proposal-1", "challenge_status": "led_to_verification",
        }


class TestPatchVerificationProposal:
    def _session_with_status(self, status: str) -> _FakeSession:
        def _is_proposal_select(sql):
            return "SELECT status FROM verification_proposals" in sql

        return _FakeSession([(_is_proposal_select, [(status,)])])

    def test_not_found_is_404(self, monkeypatch):
        def _is_proposal_select(sql):
            return "SELECT status FROM verification_proposals" in sql

        session = _FakeSession([(_is_proposal_select, [])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        body = doubt.ProposalPatchRequest(status="in_progress")
        with pytest.raises(HTTPException) as exc:
            doubt.patch_verification_proposal("proposal-1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 404

    def test_forward_transition_allowed(self, monkeypatch):
        session = self._session_with_status("proposed")
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        _capture_audit(monkeypatch)
        body = doubt.ProposalPatchRequest(status="in_progress")
        result = doubt.patch_verification_proposal("proposal-1", body, current_user={"id": "u1"})
        assert result == {"ok": True, "proposal_id": "proposal-1", "status": "in_progress"}

    def test_skip_ahead_transition_is_422(self, monkeypatch):
        session = self._session_with_status("proposed")
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        body = doubt.ProposalPatchRequest(status="completed")
        with pytest.raises(HTTPException) as exc:
            doubt.patch_verification_proposal("proposal-1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_withdraw_allowed_from_any_open_point(self, monkeypatch):
        session = self._session_with_status("in_progress")
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        _capture_audit(monkeypatch)
        body = doubt.ProposalPatchRequest(status="withdrawn")
        result = doubt.patch_verification_proposal("proposal-1", body, current_user={"id": "u1"})
        assert result["status"] == "withdrawn"

    def test_transition_from_withdrawn_is_422(self, monkeypatch):
        session = self._session_with_status("withdrawn")
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        body = doubt.ProposalPatchRequest(status="in_progress")
        with pytest.raises(HTTPException) as exc:
            doubt.patch_verification_proposal("proposal-1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_invalid_status_value_is_422(self, monkeypatch):
        session = self._session_with_status("proposed")
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        body = doubt.ProposalPatchRequest(status="bogus")
        with pytest.raises(HTTPException) as exc:
            doubt.patch_verification_proposal("proposal-1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_reachability_only_update_does_not_require_status(self, monkeypatch):
        session = self._session_with_status("proposed")
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        body = doubt.ProposalPatchRequest(reachability="reachable")
        result = doubt.patch_verification_proposal("proposal-1", body, current_user={"id": "u1"})
        assert result["status"] == "proposed"

    def test_invalid_reachability_is_422(self, monkeypatch):
        session = self._session_with_status("proposed")
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        body = doubt.ProposalPatchRequest(reachability="bogus")
        with pytest.raises(HTTPException) as exc:
            doubt.patch_verification_proposal("proposal-1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422


# ===========================================================================
# GET .../ledger/{t}/{id} の拡張（falsification_conditions / candidates / support_lines）
# ===========================================================================


class TestGetLedgerEntryExtension:
    def test_response_includes_projected_conditions_and_candidate_only_status(self, monkeypatch):
        condition = {
            "condition_id": "cond-1", "statement": "s", "kind": "observation_value",
            "reachability": "reachable", "evidence_ids": [], "evidence_quote": "q",
            "recorded_by": "teacher-1", "reason": "r", "recorded_at": "2024-01-01",
            "from_candidate_id": "",
        }
        candidate_pending = {
            "candidate_id": "cand-1", "statement": "s2", "kind": "observation_value",
            "evidence_quote": "q2", "reason": "r2", "confidence": 0.5, "status": "candidate",
        }
        candidate_confirmed = {
            "candidate_id": "cand-2", "statement": "s3", "kind": "observation_value",
            "evidence_quote": "q3", "reason": "r3", "confidence": 0.9, "status": "confirmed",
        }
        row = _ledger_row(
            falsification_conditions=[condition],
            falsification_candidates=[candidate_pending, candidate_confirmed],
        )
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        monkeypatch.setattr(doubt, "compute_support_lines", lambda *a, **kw: None)

        result = doubt.get_ledger_entry("claim", "target-1", current_user={"id": "u1"})

        assert len(result["falsification_conditions"]) == 1
        assert result["falsification_conditions"][0]["statement"] == "s"
        # candidate-only フィルタ（status=='candidate' のみ）+ confidence 非漏洩
        assert len(result["falsification_candidates"]) == 1
        assert result["falsification_candidates"][0]["candidate_id"] == "cand-1"
        assert "confidence" not in result["falsification_candidates"][0]
        # support_lines は None を返す実装なのでキー自体が付かない（fail-soft）
        assert "support_lines" not in result

    def test_support_lines_key_present_when_computable(self, monkeypatch):
        row = _ledger_row()
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        canned = {"level": "single", "fact_line": "f", "cut_members": [], "observation_roots": []}
        monkeypatch.setattr(doubt, "compute_support_lines", lambda *a, **kw: canned)

        result = doubt.get_ledger_entry("claim", "target-1", current_user={"id": "u1"})
        assert result["support_lines"] == canned

    def test_support_lines_exception_is_fail_soft(self, monkeypatch):
        row = _ledger_row()
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)

        def _boom(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(doubt, "compute_support_lines", _boom)
        result = doubt.get_ledger_entry("claim", "target-1", current_user={"id": "u1"})
        assert "support_lines" not in result


# ===========================================================================
# 学習者投影: GET /api/learning/courses/{course_id}/ledger/{t}/{id}
# ===========================================================================


class TestLearnerLedgerProjection:
    def test_falsification_conditions_drop_attribution_and_evidence(self, monkeypatch):
        condition = {
            "condition_id": "cond-1", "statement": "測定値がずれれば覆る",
            "kind": "observation_value", "reachability": "reachable",
            "evidence_ids": ["e1"], "evidence_quote": "quote", "recorded_by": "teacher-1",
            "reason": "r", "recorded_at": "2024-01-01", "from_candidate_id": "",
        }
        row = _ledger_row(falsification_conditions=[condition])
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        monkeypatch.setattr(doubt, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(doubt, "compute_support_lines", lambda *a, **kw: None)

        result = doubt.get_learner_ledger_line(
            "course-1", "claim", "target-1", current_user={"id": "student-1"},
        )
        items = result["falsification_conditions"]
        assert items == [{
            "statement": "測定値がずれれば覆る",
            "kind_label": doubt.FALSIFICATION_KIND_LABELS["observation_value"],
            "reachability_label": doubt.REACHABILITY_LABELS["reachable"],
            "source_label": "教員の記帳",
        }]
        assert "support_fact_line" not in result

    def test_support_fact_line_included_when_available(self, monkeypatch):
        row = _ledger_row()
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        monkeypatch.setattr(doubt, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(
            doubt, "compute_support_lines",
            lambda *a, **kw: {"level": "several", "fact_line": "複数の支持線", "cut_members": [], "observation_roots": []},
        )
        result = doubt.get_learner_ledger_line(
            "course-1", "claim", "target-1", current_user={"id": "student-1"},
        )
        assert result["support_fact_line"] == "複数の支持線"

    def test_course_not_accessible_is_404(self, monkeypatch):
        monkeypatch.setattr(doubt, "get_accessible_course_data", lambda uid, cid: None)
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        with pytest.raises(HTTPException) as exc:
            doubt.get_learner_ledger_line(
                "course-1", "claim", "target-1", current_user={"id": "student-1"},
            )
        assert exc.value.status_code == 404


# ===========================================================================
# core/doubt/open_assumptions.py — SL-4 投影拡張（3+1キー）
# ===========================================================================


class TestCompileOpenAssumptionsFalsificationKeys:
    def _rows(self, falsification_conditions):
        return [(
            "assumption-1", "assumption", "untested", [], 0, 95.0, falsification_conditions,
        )]

    def _session(self, rows):
        def _is_main_select(sql):
            return "FROM epistemic_ledger" in sql and "falsification_conditions" in sql

        def _is_challenge_select(sql):
            return "FROM challenges c" in sql

        def _is_proposal_count(sql):
            return "FROM verification_proposals vp" in sql

        return _FakeSession([
            (_is_main_select, rows),
            (_is_challenge_select, []),
            (_is_proposal_count, [(0,)]),
        ])

    def _patch_common(self, monkeypatch):
        monkeypatch.setattr(open_assumptions, "load_percentiles", lambda *a, **kw: (10.0, 50.0, 90.0))
        monkeypatch.setattr(open_assumptions, "build_support_context", lambda *a, **kw: None)

    def test_has_falsification_condition_true_when_non_not_formulable_present(self, monkeypatch):
        self._patch_common(monkeypatch)
        conditions = [{"kind": "observation_value", "reachability": "reachable"}]
        session = self._session(self._rows(conditions))
        items = open_assumptions.compile_open_assumptions(session, "course-1")
        assert len(items) == 1
        assert items[0]["has_falsification_condition"] is True
        assert items[0]["falsification_not_formulable"] is False
        assert items[0]["reachability_summary"] == "reachable"
        assert items[0]["support_line_level"] == ""

    def test_not_formulable_only_sets_flag_without_condition(self, monkeypatch):
        self._patch_common(monkeypatch)
        conditions = [{"kind": "not_formulable", "reachability": "unassessed"}]
        session = self._session(self._rows(conditions))
        items = open_assumptions.compile_open_assumptions(session, "course-1")
        assert items[0]["has_falsification_condition"] is False
        assert items[0]["falsification_not_formulable"] is True
        assert items[0]["reachability_summary"] == ""

    def test_no_conditions_at_all(self, monkeypatch):
        self._patch_common(monkeypatch)
        session = self._session(self._rows([]))
        items = open_assumptions.compile_open_assumptions(session, "course-1")
        assert items[0]["has_falsification_condition"] is False
        assert items[0]["falsification_not_formulable"] is False
        assert items[0]["reachability_summary"] == ""

    def test_support_line_level_reflects_context_result(self, monkeypatch):
        monkeypatch.setattr(open_assumptions, "load_percentiles", lambda *a, **kw: (10.0, 50.0, 90.0))
        monkeypatch.setattr(open_assumptions, "build_support_context", lambda *a, **kw: "ctx")
        monkeypatch.setattr(
            open_assumptions, "compute_support_lines_from_context",
            lambda ctx, ttype, tid: {"level": "single", "fact_line": "f", "cut_members": [], "observation_roots": []},
        )
        session = self._session(self._rows([]))
        items = open_assumptions.compile_open_assumptions(session, "course-1")
        assert items[0]["support_line_level"] == "single"

    def test_reachability_summary_picks_best_value(self):
        conditions = [
            {"reachability": "unreachable"},
            {"reachability": "reachable"},
            {"reachability": "next_generation"},
        ]
        assert open_assumptions._falsification_reachability_summary(conditions) == "reachable"

    def test_reachability_summary_empty_for_no_conditions(self):
        assert open_assumptions._falsification_reachability_summary([]) == ""


# ===========================================================================
# core/discuss/opening.py — SL-1 結線（_assumption_fact_line の3分岐 + 後方互換）
# ===========================================================================


class TestAssumptionFactLineFalsificationBranches:
    def test_has_falsification_condition_appends_fact(self):
        item = {
            "scope_count_is_zero": True,
            "has_falsification_condition": True,
            "falsification_not_formulable": False,
        }
        line = opening._assumption_fact_line(item)
        assert line == (
            "どの範囲で確かめたかが記録されていません。"
            "何が起これば覆るかが記帳されている前提です。"
        )

    def test_not_formulable_appends_fact(self):
        item = {
            "verification_status": "untested",
            "has_falsification_condition": False,
            "falsification_not_formulable": True,
        }
        line = opening._assumption_fact_line(item)
        assert line == (
            "検証の記録がない前提です。反証条件を定式化できないと記帳されている前提です。"
        )

    def test_no_condition_appends_undetermined_fact(self):
        item = {
            "verification_status": "refuted",
            "has_falsification_condition": False,
            "falsification_not_formulable": False,
        }
        line = opening._assumption_fact_line(item)
        assert line == "反証の記録がある前提です。覆る条件はまだ定式化されていません。"

    def test_legacy_item_shape_without_keys_is_unaffected(self):
        """SL 結線前の item 形状（キー無し）は従来の文だけを返す（後方互換）。"""
        item = {"scope_count_is_zero": True}
        assert opening._assumption_fact_line(item) == "どの範囲で確かめたかが記録されていません。"

    def test_join_helper_adds_period_when_missing(self):
        assert opening._join_fact_sentences("base without period", "X") == "base without period。X"

    def test_join_helper_avoids_duplicate_period(self):
        assert opening._join_fact_sentences("base。", "X") == "base。X"

    def test_join_helper_with_empty_base_returns_addition_only(self):
        assert opening._join_fact_sentences("", "X") == "X"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
