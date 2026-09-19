"""P4-5（D層・C層の表現語彙）の routes スコープのテスト。

正本: docs/features/knowledge_transfer_design.md §8。対象は
backend/api/routes/doubt.py（challenge_mode / target_element_ref / 根拠の線）と
backend/api/routes/theory_components.py::cite_explanation（引用の意図）。

DB / FastAPI TestClient は使わず、ルート関数を直接呼び、`_pg_session` をフェイク
セッションに差し替えて分岐ロジックを検証する（test_stakes_ledger_api.py と同型）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

from fastapi import HTTPException  # noqa: E402

from api.routes import doubt  # noqa: E402
from api.routes import theory_components  # noqa: E402
from core import label_vocab  # noqa: E402


# ===========================================================================
# フェイクセッション（test_stakes_ledger_api.py と同じ方式）
# ===========================================================================


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows) if rows is not None else []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeSession:
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

    def sql_containing(self, needle: str) -> list[tuple[str, dict]]:
        return [(sql, params) for sql, params in self.calls if needle in sql]


def _no_session():
    raise AssertionError("this test expects validation to fail before a DB session opens")


_LEDGER_COLUMNS = (
    "id", "target_id", "target_type", "document_id", "course_id",
    "verification_status", "verification_scopes", "scope_candidates",
    "consensus_explicit", "consensus_behavioral", "load_score",
    "created_at", "updated_at", "falsification_conditions", "falsification_candidates",
    "evidence_lines",
)


def _ledger_row(**overrides) -> tuple:
    defaults = {
        "id": "led-1", "target_id": "target-1", "target_type": "claim",
        "document_id": "doc-1", "course_id": "course-1",
        "verification_status": "untested", "verification_scopes": [],
        "scope_candidates": [], "consensus_explicit": {}, "consensus_behavioral": 0,
        "load_score": None, "created_at": "2024-01-01", "updated_at": "2024-01-01",
        "falsification_conditions": [], "falsification_candidates": [],
        "evidence_lines": [],
    }
    defaults.update(overrides)
    return tuple(defaults[c] for c in _LEDGER_COLUMNS)


def _is_ledger_select(sql: str) -> bool:
    return "SELECT id::text, target_id, target_type" in sql and "FROM epistemic_ledger" in sql


def _capture_audit(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(doubt, "_record_doubt_event", lambda *a, **kw: calls.append((a, kw)))
    return calls


def _evidence_line(**overrides) -> dict:
    line = {
        "line_id": "line-1",
        "line_kind": "observation",
        "evidence_ids": ["ev-1"],
        "claim_ids": [],
        "equation_ids": [],
        "recorded_by": "teacher-1",
        "reason": "r",
        "recorded_at": "2024-01-01",
    }
    line.update(overrides)
    return line


# ===========================================================================
# 1. 疑義の向き（challenge_mode）と対象要素（target_element_ref）
# ===========================================================================


class TestChallengeMode:
    def _patch_notify(self, monkeypatch):
        monkeypatch.setattr(doubt, "_challenge_target_recorder", lambda *a, **kw: "")

    def test_defaults_to_direct_when_body_omits_it(self, monkeypatch):
        """既存クライアント（challenge_type / reason / course_id のみ）は不変で動く。"""
        body = doubt.ChallengeCreateRequest(challenge_type="definitional", reason="r")
        assert body.challenge_mode == "direct"
        assert body.target_element_ref == {}

        session = _FakeSession([(lambda sql: "INSERT INTO challenges" in sql, [("ch-1",)])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        self._patch_notify(monkeypatch)
        _capture_audit(monkeypatch)

        result = doubt.create_challenge("claim", "c1", body, current_user={"id": "u1"})
        assert result["challenge_mode"] == "direct"
        assert result["challenge_mode_label"] == "主張そのものへ"
        inserts = session.sql_containing("INSERT INTO challenges")
        assert inserts and inserts[0][1]["cmode"] == "direct"

    def test_undercut_is_accepted_and_labelled(self, monkeypatch):
        session = _FakeSession([(lambda sql: "INSERT INTO challenges" in sql, [("ch-1",)])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        self._patch_notify(monkeypatch)
        audit = _capture_audit(monkeypatch)

        body = doubt.ChallengeCreateRequest(
            challenge_type="hidden_lemma", reason="r", challenge_mode="undercut",
        )
        result = doubt.create_challenge("claim", "c1", body, current_user={"id": "u1"})
        assert result["challenge_mode"] == "undercut"
        assert result["challenge_mode_label"] == "主張と根拠のつながりへ"
        # 監査 metadata に向きが残る（新 entity_type は作らない）
        assert audit and audit[0][0][5]["challenge_mode"] == "undercut"

    def test_invalid_mode_is_422_without_opening_a_session(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        body = doubt.ChallengeCreateRequest(
            challenge_type="definitional", reason="r", challenge_mode="bogus",
        )
        with pytest.raises(HTTPException) as exc:
            doubt.create_challenge("claim", "c1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_target_element_ref_keeps_only_the_three_allowed_string_keys(self, monkeypatch):
        session = _FakeSession([(lambda sql: "INSERT INTO challenges" in sql, [("ch-1",)])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        self._patch_notify(monkeypatch)
        _capture_audit(monkeypatch)

        body = doubt.ChallengeCreateRequest(
            challenge_type="definitional", reason="r", challenge_mode="undercut",
            target_element_ref={
                "element_type": "theory_component", "element_id": "comp-1",
                "document_id": "doc-1",
                # 捨てられるべきキー（数値・自由記述・非文字列）
                "confidence": 0.9, "note": "", "weight": 1, "extra": {"a": 1},
            },
        )
        result = doubt.create_challenge("claim", "c1", body, current_user={"id": "u1"})
        assert result["target_element_ref"] == {
            "element_type": "theory_component",
            "element_id": "comp-1",
            "document_id": "doc-1",
        }
        params = session.sql_containing("INSERT INTO challenges")[0][1]
        stored = json.loads(params["element_ref"])
        assert "confidence" not in stored and "weight" not in stored

    def test_non_dict_target_element_ref_normalizes_to_empty(self):
        assert doubt._normalize_target_element_ref(None) == {}
        assert doubt._normalize_target_element_ref("x") == {}
        assert doubt._normalize_target_element_ref({"element_id": "  "}) == {}

    def test_list_challenges_projects_mode_and_ref(self, monkeypatch):
        rows = [(
            "ch-1", "definitional", "r", "open", "先生", "2024-01-01",
            "undercut", {"element_type": "equation", "element_id": "eq-1", "bogus": 1},
        )]
        session = _FakeSession([(lambda sql: "FROM challenges c" in sql, rows)])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        out = doubt.list_challenges("claim", "c1", current_user={"id": "u1"})
        item = out["items"][0]
        assert item["challenge_mode"] == "undercut"
        assert item["challenge_mode_label"] == "主張と根拠のつながりへ"
        assert item["target_element_ref"] == {"element_type": "equation", "element_id": "eq-1"}

    def test_legacy_null_mode_reads_back_as_direct(self, monkeypatch):
        rows = [("ch-1", "definitional", "r", "open", "先生", "2024-01-01", None, {})]
        session = _FakeSession([(lambda sql: "FROM challenges c" in sql, rows)])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        out = doubt.list_challenges("claim", "c1", current_user={"id": "u1"})
        assert out["items"][0]["challenge_mode"] == "direct"


# ===========================================================================
# 2. 根拠の線（POST / PATCH）
# ===========================================================================


def _allow_ledger_target(monkeypatch):
    """P4-R8 の編集権限ゲートを通す（本テストは語彙・検証の単体で、権限は
    ``test_stakes_ledger_api.py`` が検査する）。"""
    monkeypatch.setattr(
        doubt, "_require_editable_ledger_target",
        lambda target_type, target_id, current_user: "doc-1",
    )


class TestAddEvidenceLine:
    def test_invalid_kind_is_422_without_opening_a_session(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        body = doubt.EvidenceLineCreateRequest(
            line_kind="bogus", reason="r", evidence_ids=["ev-1"],
        )
        with pytest.raises(HTTPException) as exc:
            doubt.add_evidence_line("claim", "c1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_empty_reason_is_422(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        body = doubt.EvidenceLineCreateRequest(
            line_kind="observation", reason="   ", evidence_ids=["ev-1"],
        )
        with pytest.raises(HTTPException) as exc:
            doubt.add_evidence_line("claim", "c1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_no_supporting_ids_at_all_is_422(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        body = doubt.EvidenceLineCreateRequest(line_kind="derivation", reason="r")
        with pytest.raises(HTTPException) as exc:
            doubt.add_evidence_line("claim", "c1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    @pytest.mark.parametrize(
        "field", ["evidence_ids", "claim_ids", "equation_ids"],
    )
    def test_any_one_of_the_three_id_families_is_enough(self, monkeypatch, field):
        session = _FakeSession([(_is_ledger_select, [_ledger_row()])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        _allow_ledger_target(monkeypatch)
        _capture_audit(monkeypatch)
        body = doubt.EvidenceLineCreateRequest(
            line_kind="consistency", reason="r", **{field: ["x-1"]},
        )
        result = doubt.add_evidence_line("claim", "c1", body, current_user={"id": "u1"})
        assert result["ok"] is True
        assert result["evidence_line"][field] == ["x-1"]

    def test_invalid_target_type_is_422(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", _no_session)
        body = doubt.EvidenceLineCreateRequest(
            line_kind="observation", reason="r", evidence_ids=["ev-1"],
        )
        with pytest.raises(HTTPException) as exc:
            doubt.add_evidence_line("bogus", "c1", body, current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_attribution_comes_from_the_authenticated_user(self, monkeypatch):
        session = _FakeSession([(_is_ledger_select, [_ledger_row()])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        _allow_ledger_target(monkeypatch)
        _capture_audit(monkeypatch)
        body = doubt.EvidenceLineCreateRequest(
            line_kind="observation", reason="r", evidence_ids=["ev-1"],
        )
        result = doubt.add_evidence_line("claim", "c1", body, current_user={"id": "teacher-9"})
        assert result["evidence_line"]["recorded_by"] == "teacher-9"
        # body に recorded_by を申告する口が無い（帰属の偽装ができない）
        assert "recorded_by" not in doubt.EvidenceLineCreateRequest.model_fields

    def test_appends_only_and_never_replaces_the_column(self, monkeypatch):
        session = _FakeSession([(_is_ledger_select, [_ledger_row()])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        _allow_ledger_target(monkeypatch)
        _capture_audit(monkeypatch)
        body = doubt.EvidenceLineCreateRequest(
            line_kind="derivation", reason="r", claim_ids=["cl-1"],
        )
        doubt.add_evidence_line("claim", "c1", body, current_user={"id": "u1"})
        updates = session.sql_containing("UPDATE epistemic_ledger")
        assert updates
        assert "evidence_lines = evidence_lines ||" in updates[0][0]
        stored = json.loads(updates[0][1]["line"])
        assert len(stored) == 1 and stored[0]["line_kind"] == "derivation"
        assert session.committed == 1

    def test_response_carries_the_kind_label(self, monkeypatch):
        session = _FakeSession([(_is_ledger_select, [_ledger_row()])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        _allow_ledger_target(monkeypatch)
        _capture_audit(monkeypatch)
        body = doubt.EvidenceLineCreateRequest(
            line_kind="external_reference", reason="r", evidence_ids=["ev-1"],
        )
        result = doubt.add_evidence_line("claim", "c1", body, current_user={"id": "u1"})
        assert result["evidence_line"]["line_kind_label"] == "外部文献"

    def test_audit_uses_the_existing_ledger_entity_type(self, monkeypatch):
        session = _FakeSession([(_is_ledger_select, [_ledger_row()])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        _allow_ledger_target(monkeypatch)
        audit = _capture_audit(monkeypatch)
        body = doubt.EvidenceLineCreateRequest(
            line_kind="observation", reason="r", evidence_ids=["ev-1"],
        )
        doubt.add_evidence_line("claim", "c1", body, current_user={"id": "u1"})
        assert audit
        args = audit[0][0]
        assert args[0] == doubt.AUDIT_ENTITY_LEDGER
        assert args[5]["action"] == "evidence_line_add"


class TestPatchEvidenceLine:
    def test_missing_ledger_row_is_404(self, monkeypatch):
        session = _FakeSession([(_is_ledger_select, [])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        _allow_ledger_target(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            doubt.patch_evidence_line(
                "claim", "c1", "line-1",
                doubt.EvidenceLinePatchRequest(reason="r2"),
                current_user={"id": "u1"},
            )
        assert exc.value.status_code == 404

    def test_unknown_line_id_is_404(self, monkeypatch):
        row = _ledger_row(evidence_lines=[_evidence_line()])
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        _allow_ledger_target(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            doubt.patch_evidence_line(
                "claim", "c1", "nope",
                doubt.EvidenceLinePatchRequest(reason="r2"),
                current_user={"id": "u1"},
            )
        assert exc.value.status_code == 404

    def test_patch_rewrites_the_whole_array_in_place(self, monkeypatch):
        row = _ledger_row(evidence_lines=[
            _evidence_line(line_id="line-1"),
            _evidence_line(line_id="line-2", line_kind="derivation", claim_ids=["cl-1"],
                           evidence_ids=[]),
        ])
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        _allow_ledger_target(monkeypatch)
        _capture_audit(monkeypatch)
        result = doubt.patch_evidence_line(
            "claim", "c1", "line-2",
            doubt.EvidenceLinePatchRequest(reason="訂正後", line_kind="consistency"),
            current_user={"id": "u1"},
        )
        assert result["evidence_line"]["reason"] == "訂正後"
        assert result["evidence_line"]["line_kind"] == "consistency"
        updates = session.sql_containing("UPDATE epistemic_ledger")
        stored = json.loads(updates[0][1]["lines"])
        # 他の線は保持される（行削除しない, KT5）
        assert [line["line_id"] for line in stored] == ["line-1", "line-2"]

    def test_patch_that_would_break_the_required_fields_is_422(self, monkeypatch):
        row = _ledger_row(evidence_lines=[_evidence_line()])
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        _allow_ledger_target(monkeypatch)
        _capture_audit(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            doubt.patch_evidence_line(
                "claim", "c1", "line-1",
                doubt.EvidenceLinePatchRequest(evidence_ids=[]),
                current_user={"id": "u1"},
            )
        assert exc.value.status_code == 422
        assert session.sql_containing("UPDATE epistemic_ledger") == []

    def test_patch_cannot_change_attribution(self):
        assert "recorded_by" not in doubt.EvidenceLinePatchRequest.model_fields


# ===========================================================================
# 3. 台帳 GET（教員 / 学習者）
# ===========================================================================


class TestLedgerProjection:
    def test_admin_get_returns_evidence_lines_with_attribution(self, monkeypatch):
        row = _ledger_row(evidence_lines=[_evidence_line()])
        session = _FakeSession([(_is_ledger_select, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        monkeypatch.setattr(doubt, "compute_support_lines", lambda *a, **kw: None)
        monkeypatch.setattr(doubt, "target_label", lambda *a, **kw: "label")
        monkeypatch.setattr(doubt, "has_naive_signal", lambda *a, **kw: False)
        monkeypatch.setattr(doubt, "_load_level_for_row", lambda *a, **kw: "low")

        result = doubt.get_ledger_entry("claim", "target-1", current_user={"id": "u1"})
        assert result["evidence_lines"] == [{
            "line_id": "line-1",
            "line_kind": "observation",
            "line_kind_label": "観測",
            "evidence_ids": ["ev-1"],
            "claim_ids": [],
            "equation_ids": [],
            "recorded_by": "teacher-1",
            "reason": "r",
            "recorded_at": "2024-01-01",
        }]

    def test_learner_projection_is_one_fact_line_without_attribution(self, monkeypatch):
        row = _ledger_row(evidence_lines=[
            _evidence_line(line_kind="observation"),
            _evidence_line(line_id="line-2", line_kind="derivation"),
            # 同じ種別は重複させない
            _evidence_line(line_id="line-3", line_kind="observation"),
        ])
        session = _FakeSession([(_is_ledger_select, [row])])
        line = doubt.learner_ledger_line(
            session, "claim", "target-1", include_support_lines=False,
        )
        assert line["evidence_lines_fact"] == "根拠の線が記帳されています（種類: 観測・導出）。"
        assert "evidence_lines" not in line
        serialized = json.dumps(line, ensure_ascii=False)
        assert "teacher-1" not in serialized
        assert "ev-1" not in serialized
        assert "recorded_by" not in serialized

    def test_learner_projection_omits_the_key_when_nothing_is_recorded(self, monkeypatch):
        session = _FakeSession([(_is_ledger_select, [_ledger_row()])])
        line = doubt.learner_ledger_line(
            session, "claim", "target-1", include_support_lines=False,
        )
        assert "evidence_lines_fact" not in line

    def test_learner_fact_line_ignores_unknown_kinds(self):
        assert doubt._learner_evidence_lines_fact([{"line_kind": "bogus"}]) == ""
        assert doubt._learner_evidence_lines_fact(["not a dict"]) == ""


# ===========================================================================
# 4. 引用の意図（C層）
# ===========================================================================


class TestCitationIntent:
    def _patch_cite(self, monkeypatch, session):
        monkeypatch.setattr(
            theory_components, "_explanation_context",
            lambda eid: ("comp-1", "course-1", "author-1", True, "teacher_approved"),
        )
        monkeypatch.setattr(theory_components, "_ensure_editable", lambda *a, **kw: None)
        monkeypatch.setattr(theory_components, "_pg_session", lambda: session)
        monkeypatch.setattr(
            theory_components, "_stamp_citation_source_version", lambda *a, **kw: None,
        )
        calls: list[tuple] = []
        monkeypatch.setattr(
            theory_components, "_record_review_event",
            lambda *a, **kw: calls.append((a, kw)),
        )
        return calls

    def _session(self):
        return _FakeSession([(lambda sql: "INSERT INTO component_citations" in sql, [("cit-1",)])])

    def test_intent_is_an_optional_body_field(self):
        """既存クライアントの body（citing_course_id のみ）が 422 にならないこと。

        直接呼び出しでは FastAPI の依存解決を通らないので、ここでは**宣言**
        （``Body(default=None, embed=True)``）を検査する。
        """
        import inspect

        param = inspect.signature(theory_components.cite_explanation).parameters[
            "citation_intent"
        ]
        assert param.default.default is None
        assert param.default.embed is True
        # 必須のまま（``...``）の citing_course_id と対比
        assert theory_components.cite_explanation.__annotations__["citation_intent"] == (
            "str | None"
        )

    def test_explicitly_null_intent_stays_null(self, monkeypatch):
        session = self._session()
        audit = self._patch_cite(monkeypatch, session)
        result = theory_components.cite_explanation(
            "exp-1", citing_course_id="course-1", citation_intent=None,
            current_user={"id": "u1", "role": "teacher"},
        )
        assert result["citation_intent"] is None
        assert result["citation_intent_label"] == ""
        assert result["citation_id"] == "cit-1"
        params = session.sql_containing("INSERT INTO component_citations")[0][1]
        assert params["intent"] is None
        # 監査 metadata にも空のキーを足さない
        assert "citation_intent" not in audit[0][0][5]

    def test_valid_intent_is_stored_and_labelled(self, monkeypatch):
        session = self._session()
        audit = self._patch_cite(monkeypatch, session)
        result = theory_components.cite_explanation(
            "exp-1", citing_course_id="course-1", citation_intent="qualifies",
            current_user={"id": "u1", "role": "teacher"},
        )
        assert result["citation_intent"] == "qualifies"
        assert result["citation_intent_label"] == "条件を付ける"
        params = session.sql_containing("INSERT INTO component_citations")[0][1]
        assert params["intent"] == "qualifies"
        assert audit[0][0][5]["citation_intent"] == "qualifies"

    def test_invalid_intent_is_422_before_any_lookup(self, monkeypatch):
        monkeypatch.setattr(
            theory_components, "_explanation_context",
            lambda eid: (_ for _ in ()).throw(AssertionError("must not be reached")),
        )
        with pytest.raises(HTTPException) as exc:
            theory_components.cite_explanation(
                "exp-1", citing_course_id="course-1", citation_intent="bogus",
                current_user={"id": "u1", "role": "teacher"},
            )
        assert exc.value.status_code == 422

    def test_blank_intent_is_treated_as_not_recorded(self, monkeypatch):
        session = self._session()
        self._patch_cite(monkeypatch, session)
        result = theory_components.cite_explanation(
            "exp-1", citing_course_id="course-1", citation_intent="   ",
            current_user={"id": "u1", "role": "teacher"},
        )
        assert result["citation_intent"] is None

    @pytest.mark.parametrize("intent", sorted(label_vocab.CITATION_INTENT_LABELS))
    def test_every_catalog_intent_is_accepted(self, monkeypatch, intent):
        session = self._session()
        self._patch_cite(monkeypatch, session)
        result = theory_components.cite_explanation(
            "exp-1", citing_course_id="course-1", citation_intent=intent,
            current_user={"id": "u1", "role": "teacher"},
        )
        assert result["citation_intent"] == intent
        assert result["citation_intent_label"] == label_vocab.CITATION_INTENT_LABELS[intent]
