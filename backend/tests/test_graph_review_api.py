"""グラフ対話レビュー — API（承認遷移・claim レビュー・グラフ全体対話）のテスト。

正本: ``docs/features/graph_dialogue_review_design.md`` §4/§5。
DB / LLM への実接続は行わず、route 関数を直接呼んで monkeypatch で分離する
（``test_component_graph_reference_index.py`` と同じ流儀）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import routes.theory_components as tc  # noqa: E402
import routes.deliberation as delib_routes  # noqa: E402
from core.deliberation import graph_dialogue as gd  # noqa: E402
from schemas import TheoryComponentOut, TheoryIOItem, TheorySourceRef  # noqa: E402

_TEACHER = {"id": "22222222-2222-2222-2222-222222222222", "role": "TEACHER"}
_DOC = "11111111-1111-1111-1111-111111111111"
_COMPONENT = "33333333-3333-3333-3333-333333333333"
_CLAIM = "44444444-4444-4444-4444-444444444444"
_SESSION = "55555555-5555-5555-5555-555555555555"


def _sourced_item(label):
    return TheoryIOItem(label=label, source_refs=[TheorySourceRef(chunk_id="c1", quote="q")])


def _component(**overrides):
    data = {
        "id": _COMPONENT,
        "course_id": "",
        "name": "テスト理論",
        "inputs": [_sourced_item("入力A")],
        "outputs": [_sourced_item("出力B")],
        "source_scope": {"document_id": _DOC},
    }
    data.update(overrides)
    return TheoryComponentOut(**data)


# ---------------------------------------------------------------------------
# component 承認（POST /theory-components/{id}/approve）
# ---------------------------------------------------------------------------


class TestApproveComponent:
    def test_404_when_component_missing(self, monkeypatch):
        monkeypatch.setattr(tc, "_get_component", lambda _id: None)
        with pytest.raises(HTTPException) as exc:
            tc.approve_theory_component(_COMPONENT, current_user=_TEACHER)
        assert exc.value.status_code == 404

    def test_422_when_inputs_missing(self, monkeypatch):
        monkeypatch.setattr(tc, "_get_component", lambda _id: _component(inputs=[]))
        monkeypatch.setattr(tc, "_ensure_component_editable", lambda component, user: None)
        with pytest.raises(HTTPException) as exc:
            tc.approve_theory_component(_COMPONENT, current_user=_TEACHER)
        assert exc.value.status_code == 422
        assert "入力" in str(exc.value.detail)

    def test_422_when_item_has_no_source(self, monkeypatch):
        unsourced = TheoryIOItem(label="出典なし")
        monkeypatch.setattr(tc, "_get_component", lambda _id: _component(outputs=[unsourced]))
        monkeypatch.setattr(tc, "_ensure_component_editable", lambda component, user: None)
        with pytest.raises(HTTPException) as exc:
            tc.approve_theory_component(_COMPONENT, current_user=_TEACHER)
        assert exc.value.status_code == 422
        assert "出典" in str(exc.value.detail)

    def test_approve_transitions_status_without_touching_content(self, monkeypatch):
        existing = _component(summary="元の要約")
        captured = {}
        monkeypatch.setattr(tc, "_get_component", lambda _id: existing)
        monkeypatch.setattr(tc, "_ensure_component_editable", lambda component, user: None)

        def _fake_update(component_id, payload):
            captured["component_id"] = component_id
            captured["payload"] = payload
            return existing

        monkeypatch.setattr(tc, "_update_component", _fake_update)
        tc.approve_theory_component(_COMPONENT, current_user=_TEACHER)
        assert captured["component_id"] == _COMPONENT
        assert captured["payload"]["status"] == "teacher_reviewed"
        assert captured["payload"]["review_status"] == "teacher_approved"
        # 内容フィールドはサーバの現在値そのまま（画面の古いコピーで巻き戻さない）
        assert captured["payload"]["summary"] == "元の要約"

    def test_permission_gate_is_called(self, monkeypatch):
        called = {}
        monkeypatch.setattr(tc, "_get_component", lambda _id: _component())
        monkeypatch.setattr(
            tc, "_ensure_component_editable",
            lambda component, user: called.setdefault("user", user),
        )
        monkeypatch.setattr(tc, "_update_component", lambda _id, payload: _component())
        tc.approve_theory_component(_COMPONENT, current_user=_TEACHER)
        assert called["user"] is _TEACHER


class TestEnsureComponentEditable:
    def test_document_scoped_uses_document_gate(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            tc, "_ensure_document_editable",
            lambda document_id, user: seen.setdefault("document_id", document_id),
        )
        tc._ensure_component_editable(_component(), _TEACHER)
        assert seen["document_id"] == _DOC

    def test_falls_back_to_course_gate(self, monkeypatch):
        component = _component(course_id="course-1", source_scope={})
        monkeypatch.setattr(tc, "_component_document_id", lambda c: "")
        seen = {}
        monkeypatch.setattr(
            tc, "_ensure_editable",
            lambda course_id, user: seen.setdefault("course_id", course_id),
        )
        tc._ensure_component_editable(component, _TEACHER)
        assert seen["course_id"] == "course-1"

    def test_404_when_neither_document_nor_course(self, monkeypatch):
        component = _component(course_id="", source_scope={})
        monkeypatch.setattr(tc, "_component_document_id", lambda c: "")
        with pytest.raises(HTTPException) as exc:
            tc._ensure_component_editable(component, _TEACHER)
        assert exc.value.status_code == 404

    def test_reject_uses_component_gate(self):
        # 既存 /reject が course 限定ゲートに戻っていないこと（document-scoped の
        # パイプライン component が常に 404 になる旧バグの再発防止）。
        from tests.guardrail_helpers import extract_function_source

        src = extract_function_source(
            (BACKEND / "api" / "routes" / "theory_components.py").read_text(encoding="utf-8"),
            "reject_theory_component",
        )
        assert "_ensure_component_editable(existing, current_user)" in src


# ---------------------------------------------------------------------------
# claim レビュー遷移（POST /claims/{id}/review）
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, row):
        self._row = row
        self.executed = []

    def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        return self

    def fetchone(self):
        return self._row

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class TestReviewClaim:
    def test_invalid_vocab_is_422(self):
        with pytest.raises(HTTPException) as exc:
            tc.review_claim(_CLAIM, tc.ClaimReviewRequest(review_status="approved!"), current_user=_TEACHER)
        assert exc.value.status_code == 422

    def test_404_when_claim_missing(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeSession(None))
        with pytest.raises(HTTPException) as exc:
            tc.review_claim(_CLAIM, tc.ClaimReviewRequest(review_status="teacher_approved"), current_user=_TEACHER)
        assert exc.value.status_code == 404

    def test_vocab_is_closed_set(self):
        assert set(tc._CLAIM_REVIEW_STATUSES) == {
            "teacher_approved", "rejected", "needs_revision", "teacher_review_required",
        }


class TestClaimReviewSideEffects:
    def _updated(self, review_status):
        return tc.ClaimOut(
            claim_id=_CLAIM,
            document_id=_DOC,
            claim_type="diagnostic_claim",
            text="t",
            review_status=review_status,
        )

    def test_noop_when_status_unchanged(self, monkeypatch):
        events = []
        monkeypatch.setattr(tc, "_record_review_event", lambda *a, **k: events.append(a))
        tc._apply_claim_review_side_effects(_CLAIM, "teacher_approved", self._updated("teacher_approved"), "u1")
        assert events == []

    def test_rejected_triggers_propagation(self, monkeypatch):
        monkeypatch.setattr(tc, "_record_review_event", lambda *a, **k: None)
        propagated = []
        monkeypatch.setattr(tc, "_propagate_rejected_claim", lambda claim_id: propagated.append(claim_id))
        tc._apply_claim_review_side_effects(_CLAIM, "teacher_review_required", self._updated("rejected"), "u1")
        assert propagated == [_CLAIM]

    def test_approved_schedules_reconstruction_authoring(self, monkeypatch):
        monkeypatch.setattr(tc, "_record_review_event", lambda *a, **k: None)
        import core.reconstruction.worker as recon_worker

        scheduled = []
        monkeypatch.setattr(recon_worker, "maybe_schedule_item_authoring", lambda doc: scheduled.append(doc))
        tc._apply_claim_review_side_effects(_CLAIM, "teacher_review_required", self._updated("teacher_approved"), "u1")
        assert scheduled == [_DOC]

    def test_audit_recorded_on_transition(self, monkeypatch):
        events = []
        monkeypatch.setattr(
            tc, "_record_review_event",
            lambda entity, entity_id, old, new, user_id: events.append((entity, entity_id, old, new)),
        )
        monkeypatch.setattr(tc, "_propagate_rejected_claim", lambda claim_id: None)
        tc._apply_claim_review_side_effects(_CLAIM, "teacher_review_required", self._updated("rejected"), "u1")
        assert events == [(tc.AUDIT_ENTITY_CLAIM, _CLAIM, "teacher_review_required", "rejected")]


# ---------------------------------------------------------------------------
# グラフ全体対話（graph-sessions）
# ---------------------------------------------------------------------------


class _Access:
    def __init__(self, found=True, can_view=True, document_id=_DOC):
        self.found = found
        self.can_view = can_view
        self.document_id = document_id


def _graph_session(element_type="document_graph", element_id=_DOC, created_by=None):
    return {
        "id": _SESSION,
        "scope": "document",
        "element_type": element_type,
        "element_id": element_id,
        "document_id": _DOC,
        "domain_key": None,
        "title": "",
        "messages": [],
        "created_by": created_by or _TEACHER["id"],
        "created_at": "",
        "updated_at": "",
    }


class TestOpenGraphSession:
    def test_404_when_not_viewable(self, monkeypatch):
        monkeypatch.setattr(delib_routes, "resolve_document_access", lambda uid, ref: _Access(can_view=False))
        with pytest.raises(HTTPException) as exc:
            delib_routes.open_graph_dialogue_session(_DOC, current_user=_TEACHER)
        assert exc.value.status_code == 404

    def test_422_when_graph_missing(self, monkeypatch):
        monkeypatch.setattr(delib_routes, "resolve_document_access", lambda uid, ref: _Access())
        monkeypatch.setattr(gd, "load_latest_graph", lambda doc: {})
        with pytest.raises(HTTPException) as exc:
            delib_routes.open_graph_dialogue_session(_DOC, current_user=_TEACHER)
        assert exc.value.status_code == 422

    def test_get_or_create_reuses_existing(self, monkeypatch):
        monkeypatch.setattr(delib_routes, "resolve_document_access", lambda uid, ref: _Access())
        monkeypatch.setattr(gd, "load_latest_graph", lambda doc: {"nodes": [{"component_id": "m1"}]})
        monkeypatch.setattr(gd, "find_latest_graph_session", lambda doc, user: _graph_session())
        monkeypatch.setattr(gd, "create_graph_session", lambda *a, **k: pytest.fail("must not create"))
        result = delib_routes.open_graph_dialogue_session(_DOC, current_user=_TEACHER)
        assert result["created"] is False
        assert result["session"]["id"] == _SESSION

    def test_creates_and_audits_when_absent(self, monkeypatch):
        monkeypatch.setattr(delib_routes, "resolve_document_access", lambda uid, ref: _Access())
        monkeypatch.setattr(gd, "load_latest_graph", lambda doc: {"nodes": [{"component_id": "m1"}]})
        monkeypatch.setattr(gd, "find_latest_graph_session", lambda doc, user: None)
        monkeypatch.setattr(gd, "create_graph_session", lambda doc, **kw: _graph_session())
        events = []
        monkeypatch.setattr(delib_routes, "record_review_event", lambda *a, **k: events.append(a))
        result = delib_routes.open_graph_dialogue_session(_DOC, current_user=_TEACHER)
        assert result["created"] is True
        assert events, "セッション作成は監査記帳される（GR4）"


class TestPostGraphMessage:
    def _setup(self, monkeypatch, session=None):
        monkeypatch.setattr(delib_routes, "resolve_document_access", lambda uid, ref: _Access())
        monkeypatch.setattr(
            delib_routes.delib_store, "get_session_by_id",
            lambda sid: session if session is not None else _graph_session(),
        )
        monkeypatch.setattr(gd, "load_latest_graph", lambda doc: {"nodes": [{"component_id": "m1"}]})

    def test_404_when_session_element_type_mismatch(self, monkeypatch):
        self._setup(monkeypatch, session=_graph_session(element_type="theory_component"))
        with pytest.raises(HTTPException) as exc:
            delib_routes.post_graph_dialogue_message(
                _DOC, _SESSION, delib_routes.GraphMessageCreateRequest(content="q"), current_user=_TEACHER,
            )
        assert exc.value.status_code == 404

    def test_404_when_session_owned_by_other(self, monkeypatch):
        self._setup(monkeypatch, session=_graph_session(created_by="99999999-9999-9999-9999-999999999999"))
        with pytest.raises(HTTPException) as exc:
            delib_routes.post_graph_dialogue_message(
                _DOC, _SESSION, delib_routes.GraphMessageCreateRequest(content="q"), current_user=_TEACHER,
            )
        assert exc.value.status_code == 404

    def test_422_when_content_empty(self, monkeypatch):
        self._setup(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            delib_routes.post_graph_dialogue_message(
                _DOC, _SESSION, delib_routes.GraphMessageCreateRequest(content="  "), current_user=_TEACHER,
            )
        assert exc.value.status_code == 422

    def test_429_when_cost_gate_exhausted(self, monkeypatch):
        self._setup(monkeypatch)
        monkeypatch.setattr(delib_routes.dialogue, "check_and_count_llm_call", lambda sid, uid: False)
        with pytest.raises(HTTPException) as exc:
            delib_routes.post_graph_dialogue_message(
                _DOC, _SESSION, delib_routes.GraphMessageCreateRequest(content="q"), current_user=_TEACHER,
            )
        assert exc.value.status_code == 429
        assert not any(ch.isdigit() for ch in str(exc.value.detail))  # 残数の数値を返さない

    def test_happy_path_appends_and_returns_reply(self, monkeypatch):
        self._setup(monkeypatch)
        monkeypatch.setattr(delib_routes.dialogue, "check_and_count_llm_call", lambda sid, uid: True)
        monkeypatch.setattr(
            gd, "run_graph_turn",
            lambda doc, **kw: gd.GraphTurnResult(reply="仮説です", degraded=False),
        )
        appended = []
        monkeypatch.setattr(
            delib_routes.delib_store, "append_messages",
            lambda sid, msgs: appended.append((sid, msgs)),
        )
        result = delib_routes.post_graph_dialogue_message(
            _DOC, _SESSION, delib_routes.GraphMessageCreateRequest(content="どこが弱い？"), current_user=_TEACHER,
        )
        assert result["reply"] == "仮説です"
        assert "annotations" not in result  # グラフ全体対話は候補注釈を返さない
        assert appended and appended[0][0] == _SESSION
        roles = [m["role"] for m in appended[0][1]]
        assert roles == ["user", "assistant"]
