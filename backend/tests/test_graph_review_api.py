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
        "source_chunks": [TheorySourceRef(chunk_id="c1", quote="q")],
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

    def test_approve_uses_status_only_transition(self, monkeypatch):
        """approve はフルオブジェクト往復ではなく遷移専用ヘルパーを使う（§11）。

        往復方式には ①_row_to_out が component_type に自由語彙を投影するため CHECK
        制約違反で 500 ②TheorySourceScope が extra を落とすため source_scope の
        legacy_ids / figure_id を破壊、の2欠陥があった（2026-08-29 レビュー）。
        """
        existing = _component(summary="元の要約")
        captured = {}
        monkeypatch.setattr(tc, "_get_component", lambda _id: existing)
        monkeypatch.setattr(tc, "_ensure_component_editable", lambda component, user: None)

        def _fake_transition(component_id, passed_existing, **kwargs):
            captured["component_id"] = component_id
            captured["existing"] = passed_existing
            captured.update(kwargs)
            return existing

        monkeypatch.setattr(tc, "_transition_component_review", _fake_transition)
        monkeypatch.setattr(
            tc, "_update_component",
            lambda *a, **k: pytest.fail("approve はフル UPDATE を使ってはならない"),
        )
        tc.approve_theory_component(_COMPONENT, current_user=_TEACHER)
        assert captured["component_id"] == _COMPONENT
        assert captured["existing"] is existing
        assert captured["status"] == "teacher_reviewed"
        assert captured["review_status"] == "teacher_approved"
        assert captured["approve"] is True
        assert captured["user_id"] == _TEACHER["id"]

    def test_transition_sql_touches_only_status_columns(self, monkeypatch):
        """遷移 UPDATE が内容フィールド（source_scope / component_type 等）を含まない。"""
        existing = _component(review_status="teacher_review_required")
        fake = _FakeSession(None)
        monkeypatch.setattr(tc, "_pg_session", lambda: fake)
        monkeypatch.setattr(tc, "_get_component", lambda _id: _component(review_status="teacher_approved"))
        events = []
        monkeypatch.setattr(
            tc, "_record_review_event",
            lambda entity, entity_id, old, new, user_id: events.append((old, new, user_id)),
        )
        tc._transition_component_review(
            _COMPONENT, existing,
            status="teacher_reviewed", review_status="teacher_approved",
            user_id=_TEACHER["id"], approve=True,
        )
        sql = fake.executed[0][0]
        assert "source_scope" not in sql
        assert "component_type" not in sql
        assert "inputs" not in sql
        assert "maturity_source = 'teacher_reviewed'" in sql
        # 監査は実行者付き（_update_component 内の記帳は changed_by=NULL だった）
        assert events == [("teacher_review_required", "teacher_approved", _TEACHER["id"])]

    def test_reject_uses_status_only_transition(self, monkeypatch):
        existing = _component()
        captured = {}
        monkeypatch.setattr(tc, "_get_component", lambda _id: existing)
        monkeypatch.setattr(tc, "_ensure_component_editable", lambda component, user: None)
        monkeypatch.setattr(
            tc, "_transition_component_review",
            lambda component_id, passed, **kw: captured.update(kw) or existing,
        )
        tc.reject_theory_component(_COMPONENT, current_user=_TEACHER)
        assert captured["status"] == "rejected"
        assert captured["review_status"] == "rejected"
        assert captured["user_id"] == _TEACHER["id"]

    def test_non_uuid_component_id_is_404_not_500(self):
        """main 層集約ノード（theory_op_0001 等）は DB 行を持たない — CAST の
        DataError で 500 にせず、_get_component が None → 404 に落ちる。"""
        with pytest.raises(HTTPException) as exc:
            tc.approve_theory_component("theory_op_0001", current_user=_TEACHER)
        assert exc.value.status_code == 404

    def test_permission_gate_is_called(self, monkeypatch):
        called = {}
        monkeypatch.setattr(tc, "_get_component", lambda _id: _component())
        monkeypatch.setattr(
            tc, "_ensure_component_editable",
            lambda component, user: called.setdefault("user", user),
        )
        monkeypatch.setattr(
            tc, "_transition_component_review",
            lambda component_id, existing, **kw: _component(),
        )
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


class TestStoredGraphLiveReviewStatus:
    """stored graph の焼き込み review_status より教員判断（live 値）を優先する
    （2026-08-29 レビュー是正 — これが無いと承認してもレビューループが閉じない）。"""

    def _stored_node(self, review_status="teacher_review_required"):
        return {
            "component_id": _COMPONENT,
            "label": "Theory basis",
            "graph_layer": "main",
            "review_status": review_status,
            "source_backing_status": "source_backed",
        }

    def test_human_decision_overrides_stored_value(self):
        component = _component(review_status="teacher_approved")
        graph = tc._normalize_stored_component_graph(
            _DOC, {"nodes": [self._stored_node()], "edges": []}, [component],
        )
        assert graph["nodes"][0]["review_status"] == "teacher_approved"

    def test_derived_stored_value_is_kept_when_no_decision(self):
        component = _component(review_status="teacher_review_required")
        graph = tc._normalize_stored_component_graph(
            _DOC, {"nodes": [self._stored_node(review_status="source_backed")], "edges": []}, [component],
        )
        assert graph["nodes"][0]["review_status"] == "source_backed"


class TestStoredGraphReviewReasonProjection:
    """review_reasons は構築時の焼き込み値なので、レビューを待っていないノードで
    「要確認の理由」として出すと確定済みの構造まで欠陥に見える。読み時射影だけで
    是正し（graph_json は書き換えない）、理由自体は破棄しない。"""

    def _stored_node(self, **overrides):
        node = {
            "component_id": _COMPONENT,
            "label": "Theory basis",
            "graph_layer": "main",
            "review_status": "teacher_review_required",
            "source_backing_status": "partially_source_backed",
            "review_reasons": ["missing_atomic_claim"],
        }
        node.update(overrides)
        return node

    def _node(self, stored_node, component):
        graph = tc._normalize_stored_component_graph(
            _DOC, {"nodes": [stored_node], "edges": []}, [component],
        )
        return graph["nodes"][0]

    def test_approved_node_moves_reasons_out_of_the_review_field(self):
        node = self._node(self._stored_node(), _component(review_status="teacher_approved"))
        assert node["review_reasons"] == []
        assert node["review_reasons_at_analysis"] == ["missing_atomic_claim"]
        assert node["review_reasons_advisory"] is True

    def test_unreviewed_node_keeps_its_reasons_as_review_requests(self):
        node = self._node(self._stored_node(), _component(review_status="teacher_review_required"))
        assert node["review_reasons"] == ["missing_atomic_claim"]
        assert node["review_reasons_at_analysis"] == []
        assert node["review_reasons_advisory"] is False

    def test_source_backed_node_keeps_reasons_but_marks_them_advisory(self):
        # #306: 式・evidence で裏付いていても最小命題の claim が無いことは warning
        # として残す。表示側が「要確認」と読ませないための宣言。
        node = self._node(
            self._stored_node(review_status="source_backed", source_backing_status="source_backed"),
            _component(review_status="teacher_review_required"),
        )
        assert node["review_reasons"] == ["missing_atomic_claim"]
        assert node["review_reasons_advisory"] is True

    def test_rejected_node_still_shows_why(self):
        node = self._node(self._stored_node(), _component(review_status="rejected"))
        assert node["review_reasons"] == ["missing_atomic_claim"]
        assert node["review_reasons_advisory"] is False

    def test_approved_status_vocabulary_is_shared_with_core(self):
        # routes 側で語彙表を作り直していないこと（test_issue_319 の exec スタブが
        # 参照している値もこれ）。
        assert tc.APPROVED_REVIEW_STATUSES == gd.APPROVED_REVIEW_STATUSES
        assert tc.APPROVED_REVIEW_STATUSES == ("teacher_approved", "teacher_reviewed", "endorsed")

    def test_graph_updated_at_is_passed_through(self):
        graph = tc._normalize_stored_component_graph(
            _DOC,
            {"nodes": [self._stored_node()], "edges": [], "graph_updated_at": "2026-08-29T00:00:00+00:00"},
            [_component()],
        )
        assert graph["graph_updated_at"] == "2026-08-29T00:00:00+00:00"

    def test_graph_updated_at_is_empty_when_unknown(self):
        graph = tc._normalize_stored_component_graph(
            _DOC, {"nodes": [self._stored_node()], "edges": []}, [_component()],
        )
        assert graph["graph_updated_at"] == ""


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
        result = delib_routes.open_graph_dialogue_session(_DOC, force_new=False, current_user=_TEACHER)
        assert result["created"] is False
        assert result["session"]["id"] == _SESSION

    def test_creates_and_audits_when_absent(self, monkeypatch):
        monkeypatch.setattr(delib_routes, "resolve_document_access", lambda uid, ref: _Access())
        monkeypatch.setattr(gd, "load_latest_graph", lambda doc: {"nodes": [{"component_id": "m1"}]})
        monkeypatch.setattr(gd, "find_latest_graph_session", lambda doc, user: None)
        monkeypatch.setattr(gd, "create_graph_session", lambda doc, **kw: _graph_session())
        events = []
        monkeypatch.setattr(delib_routes, "record_review_event", lambda *a, **k: events.append(a))
        result = delib_routes.open_graph_dialogue_session(_DOC, force_new=False, current_user=_TEACHER)
        assert result["created"] is True
        assert events, "セッション作成は監査記帳される（GR4）"

    def test_force_new_skips_reuse(self, monkeypatch):
        """セッション上限到達後の再開手段（2026-08-29 レビュー是正）: force_new=true は
        既存セッションを再開せず常に新規作成する。"""
        monkeypatch.setattr(delib_routes, "resolve_document_access", lambda uid, ref: _Access())
        monkeypatch.setattr(gd, "load_latest_graph", lambda doc: {"nodes": [{"component_id": "m1"}]})
        monkeypatch.setattr(
            gd, "find_latest_graph_session",
            lambda doc, user: pytest.fail("force_new では既存を探さない"),
        )
        monkeypatch.setattr(gd, "create_graph_session", lambda doc, **kw: _graph_session())
        monkeypatch.setattr(delib_routes, "record_review_event", lambda *a, **k: None)
        result = delib_routes.open_graph_dialogue_session(_DOC, force_new=True, current_user=_TEACHER)
        assert result["created"] is True

    def test_system_admin_can_open_without_view_grant(self, monkeypatch):
        """SYSTEM_ADMIN は存在する document なら閲覧ゲートを通れる（編集ゲートと対称）。"""
        monkeypatch.setattr(
            delib_routes, "resolve_document_access", lambda uid, ref: _Access(can_view=False),
        )
        monkeypatch.setattr(gd, "load_latest_graph", lambda doc: {"nodes": [{"component_id": "m1"}]})
        monkeypatch.setattr(gd, "find_latest_graph_session", lambda doc, user: _graph_session(
            created_by="99999999-9999-9999-9999-999999999999"))
        admin = {"id": "99999999-9999-9999-9999-999999999999", "role": "SYSTEM_ADMIN"}
        result = delib_routes.open_graph_dialogue_session(_DOC, force_new=False, current_user=admin)
        assert result["session"]["id"] == _SESSION


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

    def test_invalid_model_422_does_not_consume_cost_gate(self, monkeypatch):
        """CostGate の消費は全 422 経路の後（2026-08-29 レビュー是正 — 失敗する
        リクエストで上限を焼かない）。"""
        self._setup(monkeypatch)
        monkeypatch.setattr(
            delib_routes.dialogue, "check_and_count_llm_call",
            lambda sid, uid: pytest.fail("422 経路で CostGate を消費してはならない"),
        )
        monkeypatch.setattr(
            delib_routes.llm_policy, "validate_model_for_scene",
            lambda scene, model: "そのモデルは選択できません",
        )
        with pytest.raises(HTTPException) as exc:
            delib_routes.post_graph_dialogue_message(
                _DOC, _SESSION,
                delib_routes.GraphMessageCreateRequest(content="q", model="bad-model"),
                current_user=_TEACHER,
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


# ---------------------------------------------------------------------------
# 「深く検討」の要素解決（2026-09 是正、設計書 §11）
#
# 理論操作グラフの main / equation_detail ノードは graph-native ID
# （theory_op_0001 / eq_op_0001）で theory_components の行を持たない。レビュー画面は
# 代わりに集約元の代表要素（representative_component_id = component_assembly の
# agent 側 ID）を渡すため、overview / annotations / sessions が agent 側 ID を
# document_id スコープで解決できることを固定する（fail-closed のまま）。
# ---------------------------------------------------------------------------

_AGENT_COMPONENT_ID = "comp_003"
_GRAPH_NATIVE_NODE_ID = "theory_op_0001"
_RESOLVED_COMPONENT_UUID = "66666666-6666-6666-6666-666666666666"


class _RefsResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _RefsSession:
    """``refs._resolve_by_legacy_id`` の SQL を document スコープごと再現する fake。"""

    def __init__(self, rows):
        self.rows = rows
        self.statements = []
        self.params = []
        self.closed = False

    def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(dict(params or {}))
        key = (str((params or {}).get("doc_id")), str((params or {}).get("raw_id")))
        return _RefsResult(self.rows.get(key))

    def close(self):
        self.closed = True


def _patch_legacy_refs(monkeypatch, rows):
    session = _RefsSession(rows)
    monkeypatch.setattr(delib_routes.refs, "get_session", lambda: session)
    return session


def _forbid_refs_db(monkeypatch):
    def boom():
        raise AssertionError("document スコープなしで DB を触ってはならない")

    monkeypatch.setattr(delib_routes.refs, "get_session", boom)


def _patch_overview_faces(monkeypatch):
    monkeypatch.setattr(delib_routes, "_ensure_document_viewable", lambda *a, **k: None)
    monkeypatch.setattr(
        delib_routes.decomposition, "build",
        lambda ref: {"element_type": ref.element_type, "label": "L", "fields": {}, "notes": []},
    )
    monkeypatch.setattr(delib_routes.positioning, "build", lambda ref: {})
    monkeypatch.setattr(delib_routes.context_lens, "build", lambda ref: None)
    monkeypatch.setattr(delib_routes.decomposition, "explanations_for_element", lambda ref: [])


class TestOverviewAcceptsAgentSideComponentId:
    def test_agent_id_resolves_to_db_uuid_within_document_scope(self, monkeypatch):
        session = _patch_legacy_refs(
            monkeypatch, {(_DOC, _AGENT_COMPONENT_ID): (_RESOLVED_COMPONENT_UUID,)}
        )
        _patch_overview_faces(monkeypatch)

        result = delib_routes.get_element_overview(
            "theory_component", _AGENT_COMPONENT_ID, document_id=_DOC, current_user=_TEACHER,
        )
        assert result["ref"]["element_id"] == _RESOLVED_COMPONENT_UUID
        assert result["ref"]["document_id"] == _DOC
        # SQL は document スコープで絞る（別論文の同名要素に一致しない・fail-closed）。
        assert session.params[0] == {"doc_id": _DOC, "raw_id": _AGENT_COMPONENT_ID}
        assert "document_id = :doc_id" in session.statements[0]

    def test_agent_id_in_other_document_is_404(self, monkeypatch):
        _patch_legacy_refs(monkeypatch, {("other-doc", _AGENT_COMPONENT_ID): (_RESOLVED_COMPONENT_UUID,)})
        _patch_overview_faces(monkeypatch)

        with pytest.raises(HTTPException) as exc:
            delib_routes.get_element_overview(
                "theory_component", _AGENT_COMPONENT_ID, document_id=_DOC, current_user=_TEACHER,
            )
        assert exc.value.status_code == 404

    def test_agent_id_without_document_scope_is_404_before_touching_db(self, monkeypatch):
        _forbid_refs_db(monkeypatch)
        _patch_overview_faces(monkeypatch)

        with pytest.raises(HTTPException) as exc:
            delib_routes.get_element_overview(
                "theory_component", _AGENT_COMPONENT_ID, document_id=None, current_user=_TEACHER,
            )
        assert exc.value.status_code == 404

    def test_graph_native_node_id_is_not_resolved(self, monkeypatch):
        """集約 main ノードの ID そのものは要素として解決しない（実体が無い）。"""
        _patch_legacy_refs(monkeypatch, {})
        _patch_overview_faces(monkeypatch)

        with pytest.raises(HTTPException) as exc:
            delib_routes.get_element_overview(
                "theory_component", _GRAPH_NATIVE_NODE_ID, document_id=_DOC, current_user=_TEACHER,
            )
        assert exc.value.status_code == 404


class TestResolutionErrorDetailIsFactual:
    """422/404 の detail は事実文のみ。原因と無関係の固定文言（equation の
    document_id 案内）や内部 ID・英語の例外メッセージを教員 UI に出さない。"""

    def test_not_found_detail_has_no_internal_id_or_equation_hint(self, monkeypatch):
        _patch_legacy_refs(monkeypatch, {})
        _patch_overview_faces(monkeypatch)

        with pytest.raises(HTTPException) as exc:
            delib_routes.get_element_overview(
                "theory_component", _GRAPH_NATIVE_NODE_ID, document_id=_DOC, current_user=_TEACHER,
            )
        detail = str(exc.value.detail)
        assert _GRAPH_NATIVE_NODE_ID not in detail
        assert "equation" not in detail
        assert "theory_component" not in detail
        assert detail == "この要素は見つかりませんでした。"

    def test_invalid_element_type_detail_is_factual(self, monkeypatch):
        _forbid_refs_db(monkeypatch)
        _patch_overview_faces(monkeypatch)

        with pytest.raises(HTTPException) as exc:
            delib_routes.get_element_overview(
                "nonsense", "x", document_id=_DOC, current_user=_TEACHER,
            )
        assert exc.value.status_code == 422
        detail = str(exc.value.detail)
        assert "nonsense" not in detail
        assert "equation" not in detail


class TestNodeSessionAcceptsAgentSideComponentId:
    """ノード対話（W層 sessions）も同じ解決規約。永続化するのは常に DB UUID。"""

    def test_session_stores_canonical_db_uuid(self, monkeypatch):
        _patch_legacy_refs(monkeypatch, {(_DOC, _AGENT_COMPONENT_ID): (_RESOLVED_COMPONENT_UUID,)})
        monkeypatch.setattr(delib_routes, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(delib_routes, "record_review_event", lambda *a, **k: None)
        created = {}

        def _create_session(ref, *, title, created_by):
            created["element_id"] = ref.element_id
            created["document_id"] = ref.document_id
            return {
                "id": _SESSION, "scope": ref.scope, "element_type": ref.element_type,
                "element_id": ref.element_id, "document_id": ref.document_id,
                "domain_key": ref.domain_key, "title": title, "messages": [],
                "created_by": created_by, "created_at": "",
            }

        monkeypatch.setattr(delib_routes.delib_store, "create_session", _create_session)

        result = delib_routes.create_deliberation_session(
            delib_routes.SessionCreateRequest(
                scope="document",
                element_type="theory_component",
                element_id=_AGENT_COMPONENT_ID,
                document_id=_DOC,
                title="t",
            ),
            current_user=_TEACHER,
        )
        assert created["element_id"] == _RESOLVED_COMPONENT_UUID
        assert created["document_id"] == _DOC
        assert result["session"]["element_id"] == _RESOLVED_COMPONENT_UUID


class TestAnnotationsListAcceptsAgentSideComponentId:
    def test_annotations_are_looked_up_by_canonical_id(self, monkeypatch):
        _patch_legacy_refs(monkeypatch, {(_DOC, _AGENT_COMPONENT_ID): (_RESOLVED_COMPONENT_UUID,)})
        monkeypatch.setattr(delib_routes, "_ensure_document_viewable", lambda *a, **k: None)
        seen = {}

        def _list(element_type, element_id, *, document_id=None, domain_key=None):
            seen["element_id"] = element_id
            return []

        monkeypatch.setattr(delib_routes.delib_store, "list_annotations_for_element", _list)

        result = delib_routes.list_element_annotations(
            "theory_component", _AGENT_COMPONENT_ID, document_id=_DOC, current_user=_TEACHER,
        )
        assert result["annotations"] == []
        assert seen["element_id"] == _RESOLVED_COMPONENT_UUID
