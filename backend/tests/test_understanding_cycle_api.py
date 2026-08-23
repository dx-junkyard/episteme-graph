"""理解サイクル（Understanding Cycle, UCサイクル）Phase 1 — API/services のテスト。

対象仕様: docs/features/understanding_cycle_design.md §5（Phase 1 最小閉ループ）。
test_personal_graph_map_ops.py と同型の手法（ルート関数を直接呼ぶ・
``_pg_session`` をフェイクセッションに差し替える）で、DB/FastAPI TestClient なしに
分岐ロジックを検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))


# ===========================================================================
# フェイクセッション（test_personal_graph_map_ops.py と同型）
# ===========================================================================


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row or []


class _FakeSession:
    def __init__(self, row):
        self._row = row
        self.calls: list[tuple[str, dict]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        return _FakeResult(self._row)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _BoomSession(_FakeSession):
    def execute(self, *_a, **_kw):
        raise RuntimeError("boom")


# ===========================================================================
# services.record_cycle_intention
# ===========================================================================


class TestRecordCycleIntentionValidation:
    def test_invalid_role_returns_none(self):
        from api import services

        assert services.record_cycle_intention("u1", "c1", "not_a_role", "text") is None

    def test_empty_text_returns_none(self):
        from api import services

        assert services.record_cycle_intention("u1", "c1", "opening_motive", "   ") is None

    def test_empty_text_with_prediction_is_allowed_for_opening_motive(self, monkeypatch):
        """「予想してから開く」（§5.3）: 動機空 + prediction.text ありは記録できる。"""
        from api import services

        captured = {}

        def _fake_record(*a, **kw):
            captured["call"] = (a, kw)
            return "trace-p"

        monkeypatch.setattr(services, "record_interest_trace", _fake_record)

        result = services.record_cycle_intention(
            "u1", "c1", "opening_motive", "", prediction={"text": "銀河回転曲線の話だと思う"},
        )
        assert result == {"trace_id": "trace-p"}
        _args, kwargs = captured["call"]
        assert kwargs["extra_payload"]["prediction"] == {"text": "銀河回転曲線の話だと思う"}

    def test_empty_text_with_prediction_is_rejected_for_other_roles(self):
        """text 空の許容は opening_motive 限定（carryover/revisit は従来どおり None）。"""
        from api import services

        assert services.record_cycle_intention(
            "u1", "c1", "carryover_question", "", prediction={"text": "x"},
        ) is None

    def test_revisit_answer_without_source_trace_id_returns_none(self):
        from api import services

        result = services.record_cycle_intention(
            "u1", "c1", "revisit_answer", "answer text", source_trace_id=None,
        )
        assert result is None


class TestRecordCycleIntentionBehavior:
    def test_opening_motive_records_without_supersede(self, monkeypatch):
        from api import services

        captured = {}

        def _fake_record(*a, **kw):
            captured["call"] = (a, kw)
            return "trace-1"

        monkeypatch.setattr(services, "record_interest_trace", _fake_record)
        supersede_calls = []
        monkeypatch.setattr(
            services, "_supersede_active_carryover",
            lambda *a, **kw: supersede_calls.append((a, kw)),
        )

        result = services.record_cycle_intention("u1", "c1", "opening_motive", "なぜ開いたか")

        assert result == {"trace_id": "trace-1"}
        assert not supersede_calls
        args, kwargs = captured["call"]
        assert kwargs["kind"] == "intention"
        assert kwargs["extra_payload"]["role"] == "opening_motive"
        assert kwargs["status"] == "open"

    def test_carryover_question_calls_supersede_before_insert(self, monkeypatch):
        from api import services

        order = []
        monkeypatch.setattr(
            services, "_supersede_active_carryover",
            lambda uid, cid: order.append(("supersede", uid, cid)),
        )
        monkeypatch.setattr(
            services, "record_interest_trace",
            lambda *a, **kw: order.append(("insert",)) or "trace-2",
        )

        result = services.record_cycle_intention("u1", "c1", "carryover_question", "次はこれ")

        assert result == {"trace_id": "trace-2"}
        assert order[0] == ("supersede", "u1", "c1")
        assert order[1] == ("insert",)

    def test_revisit_answer_does_not_call_supersede(self, monkeypatch):
        from api import services

        supersede_calls = []
        monkeypatch.setattr(
            services, "_supersede_active_carryover",
            lambda *a, **kw: supersede_calls.append(a),
        )
        monkeypatch.setattr(services, "record_interest_trace", lambda *a, **kw: "trace-3")

        result = services.record_cycle_intention(
            "u1", "c1", "revisit_answer", "いまはこう考える", source_trace_id="carry-1",
        )

        assert result == {"trace_id": "trace-3"}
        assert not supersede_calls

    def test_prediction_strips_numeric_keys(self, monkeypatch):
        from api import services

        captured = {}

        def _fake_record(*a, **kw):
            captured["kw"] = kw
            return "trace-4"

        monkeypatch.setattr(services, "record_interest_trace", _fake_record)

        services.record_cycle_intention(
            "u1", "c1", "opening_motive", "予想文",
            prediction={"text": "こう思う", "confidence": 0.9, "load_score": 5, "score": 1},
        )

        prediction = captured["kw"]["extra_payload"].get("prediction")
        assert prediction == {"text": "こう思う"}
        assert "confidence" not in prediction
        assert "load_score" not in prediction
        assert "score" not in prediction

    def test_returns_none_when_insert_fails(self, monkeypatch):
        from api import services

        monkeypatch.setattr(services, "record_interest_trace", lambda *a, **kw: None)

        result = services.record_cycle_intention("u1", "c1", "opening_motive", "text")

        assert result is None


# ===========================================================================
# services.dismiss_cycle_intention
# ===========================================================================


class TestDismissCycleIntention:
    def test_success_returns_true(self, monkeypatch):
        from api import services

        fake = _FakeSession(("trace-1",))
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        assert services.dismiss_cycle_intention("u1", "trace-1") is True
        assert fake.committed and not fake.rolled_back

    def test_not_found_returns_false(self, monkeypatch):
        from api import services

        fake = _FakeSession(None)
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        assert services.dismiss_cycle_intention("u1", "trace-missing") is False

    def test_db_error_returns_false(self, monkeypatch):
        from api import services

        fake = _BoomSession(None)
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        assert services.dismiss_cycle_intention("u1", "trace-1") is False
        assert fake.rolled_back and fake.closed

    def test_scoped_to_intention_kind_and_owner(self, monkeypatch):
        from api import services

        fake = _FakeSession(("trace-1",))
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        services.dismiss_cycle_intention("u1", "trace-1")

        _, params = fake.calls[0]
        assert params["kind"] == "intention"
        assert params["uid"] == "u1"

    def test_does_not_delete_row(self):
        """UC6: 行削除しない（status 遷移のみ）。"""
        from tests.guardrail_helpers import assert_source_forbids, extract_function_source

        src = (ROOT / "backend" / "api" / "services.py").read_text(encoding="utf-8")
        body = extract_function_source(src, "dismiss_cycle_intention")
        assert_source_forbids(body, ["DELETE"], context="dismiss_cycle_intention")
        assert "SET status = 'dismissed'" in body


# ===========================================================================
# services.record_cycle_anchor_mark
# ===========================================================================


class TestRecordCycleAnchorMark:
    def test_invalid_quick_label_returns_none(self):
        from api import services

        result = services.record_cycle_anchor_mark(
            "u1", "c1", None, "not_a_label", {"anchor_type": "segment"}, "text",
        )
        assert result is None

    def test_valid_label_records_with_expected_payload(self, monkeypatch):
        from api import services

        captured = {}

        def _fake_record(*a, **kw):
            captured["kw"] = kw
            return "trace-5"

        monkeypatch.setattr(services, "record_interest_trace", _fake_record)

        anchor_payload = {"anchor_type": "segment", "anchor_id": "", "doubt_type": "unclassified"}
        result = services.record_cycle_anchor_mark(
            "u1", "c1", "topic-1", "return_later", anchor_payload, "あとで戻る",
        )

        assert result == "trace-5"
        kw = captured["kw"]
        assert kw["kind"] == "anchor_mark"
        assert kw["extra_payload"]["quick_label"] == "return_later"
        assert kw["extra_payload"]["revisit"] is True
        assert kw["extra_payload"]["structure_anchor"] == anchor_payload

    def test_non_revisit_label_sets_revisit_false(self, monkeypatch):
        from api import services

        captured = {}

        def _fake_record(*a, **kw):
            captured["kw"] = kw
            return "trace-6"

        monkeypatch.setattr(services, "record_interest_trace", _fake_record)

        services.record_cycle_anchor_mark(
            "u1", "c1", None, "curious", {"anchor_type": "segment"}, "気になる",
        )

        assert captured["kw"]["extra_payload"]["revisit"] is False


# ===========================================================================
# routes/cycle.py — POST .../cycle/intention
# ===========================================================================


class TestIntentionRoute:
    def test_invalid_role_is_422(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        body = cycle.CycleIntentionRequest(role="bogus", text="hello")

        with pytest.raises(HTTPException) as exc_info:
            cycle.record_cycle_intention_route("course-1", body, current_user={"id": "u1"})
        assert exc_info.value.status_code == 422

    def test_empty_text_is_422(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        body = cycle.CycleIntentionRequest(role="opening_motive", text="   ")

        with pytest.raises(HTTPException) as exc_info:
            cycle.record_cycle_intention_route("course-1", body, current_user={"id": "u1"})
        assert exc_info.value.status_code == 422

    def test_empty_text_with_prediction_is_accepted_for_opening_motive(self, monkeypatch):
        """「予想してから開く」（§5.3）: 動機空 + prediction.text は 422 にしない。"""
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(
            cycle, "record_cycle_intention", lambda *a, **kw: {"trace_id": "trace-p"}
        )
        body = cycle.CycleIntentionRequest(
            role="opening_motive", text="", prediction={"text": "何を示す論文かの予想"}
        )

        result = cycle.record_cycle_intention_route(
            "course-1", body, current_user={"id": "u1"}
        )
        assert result["ok"] is True
        assert result["trace_id"] == "trace-p"

    def test_revisit_answer_without_source_trace_id_is_422(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        body = cycle.CycleIntentionRequest(role="revisit_answer", text="いまはこう思う")

        with pytest.raises(HTTPException) as exc_info:
            cycle.record_cycle_intention_route("course-1", body, current_user={"id": "u1"})
        assert exc_info.value.status_code == 422

    def test_course_not_found_is_404(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: None)
        body = cycle.CycleIntentionRequest(role="opening_motive", text="hello")

        with pytest.raises(HTTPException) as exc_info:
            cycle.record_cycle_intention_route("course-missing", body, current_user={"id": "u1"})
        assert exc_info.value.status_code == 404

    def test_insert_failure_is_500(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(cycle, "record_cycle_intention", lambda *a, **kw: None)
        body = cycle.CycleIntentionRequest(role="opening_motive", text="hello")

        with pytest.raises(HTTPException) as exc_info:
            cycle.record_cycle_intention_route("course-1", body, current_user={"id": "u1"})
        assert exc_info.value.status_code == 500

    def test_non_revisit_role_has_no_facts(self, monkeypatch):
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(cycle, "record_cycle_intention", lambda *a, **kw: {"trace_id": "t1"})
        body = cycle.CycleIntentionRequest(role="opening_motive", text="hello")

        result = cycle.record_cycle_intention_route("course-1", body, current_user={"id": "u1"})

        assert result == {"ok": True, "trace_id": "t1", "facts": []}

    def test_revisit_answer_includes_facts(self, monkeypatch):
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(cycle, "record_cycle_intention", lambda *a, **kw: {"trace_id": "t2"})
        monkeypatch.setattr(
            cycle, "fetch_active_carryover",
            lambda uid, cid: {"id": "carry-1", "text": "前回の問い", "created_at": "2026-08-12T00:00:00+00:00"},
        )
        monkeypatch.setattr(cycle, "fetch_recent_traces_since", lambda uid, cid, since: [{"fake": "row"}])
        # 帰り道の景色（Phase 2, §6）: DB を叩く導出はフェイクに差し替え、
        # build_revisit_facts の map_diff_facts kwarg 合流だけを検証する。
        monkeypatch.setattr(cycle, "build_network_as_of", lambda uid, cid, since: "before-network")
        monkeypatch.setattr(cycle, "derive_personal_network", lambda uid, cid: "after-network")
        monkeypatch.setattr(cycle, "build_map_diff_facts", lambda before, after: [])
        monkeypatch.setattr(
            cycle, "build_revisit_facts",
            lambda carryover, rows, map_diff_facts=None: ["事実文A"],
        )
        body = cycle.CycleIntentionRequest(
            role="revisit_answer", text="いまはこう思う", source_trace_id="carry-1",
        )

        result = cycle.record_cycle_intention_route("course-1", body, current_user={"id": "u1"})

        assert result == {"ok": True, "trace_id": "t2", "facts": ["事実文A"]}

    def test_revisit_answer_facts_failure_is_fail_open(self, monkeypatch):
        """事実文の導出に失敗しても記録自体は成功させる（骨格は非LLM・同期のまま完結, UC8）。"""
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(cycle, "record_cycle_intention", lambda *a, **kw: {"trace_id": "t3"})

        def _boom(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(cycle, "fetch_active_carryover", _boom)
        body = cycle.CycleIntentionRequest(
            role="revisit_answer", text="いまはこう思う", source_trace_id="carry-1",
        )

        result = cycle.record_cycle_intention_route("course-1", body, current_user={"id": "u1"})

        assert result == {"ok": True, "trace_id": "t3", "facts": []}


# ===========================================================================
# routes/cycle.py — POST .../cycle/intention/{trace_id}/dismiss
# ===========================================================================


class TestDismissIntentionRoute:
    def test_success(self, monkeypatch):
        from api.routes import cycle

        monkeypatch.setattr(cycle, "dismiss_cycle_intention", lambda uid, tid: True)

        result = cycle.dismiss_cycle_intention_route("trace-1", current_user={"id": "u1"})

        assert result == {"ok": True}

    def test_not_found_is_404(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import cycle

        monkeypatch.setattr(cycle, "dismiss_cycle_intention", lambda uid, tid: False)

        with pytest.raises(HTTPException) as exc_info:
            cycle.dismiss_cycle_intention_route("trace-missing", current_user={"id": "u1"})
        assert exc_info.value.status_code == 404


# ===========================================================================
# routes/cycle.py — POST .../cycle/anchor
# ===========================================================================


class TestAnchorRoute:
    def test_invalid_quick_label_is_422(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        body = cycle.CycleAnchorRequest(quick_label="bogus")

        with pytest.raises(HTTPException) as exc_info:
            cycle.record_cycle_anchor_route("course-1", body, current_user={"id": "u1"})
        assert exc_info.value.status_code == 422

    def test_course_not_found_is_404(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: None)
        body = cycle.CycleAnchorRequest(quick_label="curious")

        with pytest.raises(HTTPException) as exc_info:
            cycle.record_cycle_anchor_route("course-missing", body, current_user={"id": "u1"})
        assert exc_info.value.status_code == 404

    def test_insert_failure_is_500(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(cycle, "record_cycle_anchor_mark", lambda *a, **kw: None)
        body = cycle.CycleAnchorRequest(quick_label="curious")

        with pytest.raises(HTTPException) as exc_info:
            cycle.record_cycle_anchor_route("course-1", body, current_user={"id": "u1"})
        assert exc_info.value.status_code == 500

    def test_success_returns_trace_id(self, monkeypatch):
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        captured = {}

        def _fake_anchor_mark(*a, **kw):
            captured["args"] = a
            return "trace-9"

        monkeypatch.setattr(cycle, "record_cycle_anchor_mark", _fake_anchor_mark)
        body = cycle.CycleAnchorRequest(quick_label="not_yet", selection_text="この式が分からない")

        result = cycle.record_cycle_anchor_route("course-1", body, current_user={"id": "u1"})

        assert result == {"ok": True, "trace_id": "trace-9"}
        # (user_id, course_id, topic_id, key, anchor_payload, text)
        assert captured["args"][0] == "u1"
        assert captured["args"][3] == "not_yet"

    def test_uses_build_anchor_payload_for_element_tap(self, monkeypatch):
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        captured = {}
        monkeypatch.setattr(
            cycle, "record_cycle_anchor_mark",
            lambda uid, cid, tid, key, anchor_payload, text: captured.update(
                anchor_payload=anchor_payload, text=text,
            )
            or "trace-10",
        )
        body = cycle.CycleAnchorRequest(
            quick_label="connects", element_id="claim-1", element_type="concept",
            element_label="ラベル",
        )

        cycle.record_cycle_anchor_route("course-1", body, current_user={"id": "u1"})

        anchor_payload = captured["anchor_payload"]
        assert anchor_payload["doubt_type"] == "connection"
        assert anchor_payload["attribution_source"] == "learner_selected"
        assert anchor_payload["anchor_id"] == "claim-1"

    def test_fallback_text_uses_quick_label_when_no_selection_or_element(self, monkeypatch):
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        captured = {}
        monkeypatch.setattr(
            cycle, "record_cycle_anchor_mark",
            lambda uid, cid, tid, key, anchor_payload, text: captured.update(text=text) or "trace-11",
        )
        body = cycle.CycleAnchorRequest(quick_label="curious")

        cycle.record_cycle_anchor_route("course-1", body, current_user={"id": "u1"})

        assert captured["text"] == "気になる"


# ===========================================================================
# routes/cycle.py — GET .../cycle/landing-candidates
# ===========================================================================


class TestLandingCandidatesRoute:
    def test_course_not_found_is_404(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: None)

        with pytest.raises(HTTPException) as exc_info:
            cycle.get_cycle_landing_candidates_route("course-missing", current_user={"id": "u1"})
        assert exc_info.value.status_code == 404

    def test_returns_derived_candidates(self, monkeypatch):
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(cycle, "fetch_landing_candidates", lambda uid, cid: [{"fake": "row"}])
        monkeypatch.setattr(
            cycle, "build_landing_candidates",
            lambda rows: [{"trace_id": "t1", "kind": "tension", "label": "l", "revisit": False}],
        )

        result = cycle.get_cycle_landing_candidates_route("course-1", current_user={"id": "u1"})

        assert result == {
            "candidates": [{"trace_id": "t1", "kind": "tension", "label": "l", "revisit": False}]
        }

    def test_fail_open_returns_empty_candidates(self, monkeypatch):
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})

        def _boom(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(cycle, "fetch_landing_candidates", _boom)

        result = cycle.get_cycle_landing_candidates_route("course-1", current_user={"id": "u1"})

        assert result == {"candidates": []}


# ===========================================================================
# discuss opening — intention 同梱（fail-open・既存キー不変）
# ===========================================================================


class TestDiscussOpeningIntentionMerge:
    def test_opening_includes_intention_when_available(self, monkeypatch):
        from api.routes import learning

        monkeypatch.setattr(learning, "get_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(learning, "list_course_source_document_ids", lambda data: [])
        monkeypatch.setattr(learning, "course_focus", lambda data: "")
        monkeypatch.setattr(
            learning, "build_discussion_opening",
            lambda cid, doc_ids, course_focus=None: {"documents": []},
        )
        monkeypatch.setattr(
            learning, "fetch_active_carryover",
            lambda uid, cid: {"id": "carry-1", "text": "問い", "created_at": "2026-08-12T00:00:00+00:00"},
        )
        monkeypatch.setattr(learning, "fetch_intentions", lambda uid, cid: [{"id": "i1", "role": "opening_motive"}])

        result = learning.get_discussion_opening("course-1", current_user={"id": "u1"})

        assert result["documents"] == []
        assert result["intention"]["has_motive"] is True
        assert result["intention"]["carryover"]["trace_id"] == "carry-1"

    def test_opening_is_fail_open_when_cycle_lookup_fails(self, monkeypatch):
        from api.routes import learning

        monkeypatch.setattr(learning, "get_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(learning, "list_course_source_document_ids", lambda data: [])
        monkeypatch.setattr(learning, "course_focus", lambda data: "")
        monkeypatch.setattr(
            learning, "build_discussion_opening",
            lambda cid, doc_ids, course_focus=None: {"documents": []},
        )

        def _boom(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(learning, "fetch_active_carryover", _boom)

        result = learning.get_discussion_opening("course-1", current_user={"id": "u1"})

        assert result == {"documents": []}

    def test_opening_404_when_course_not_found(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import learning

        monkeypatch.setattr(learning, "get_course_data", lambda uid, cid: None)

        with pytest.raises(HTTPException) as exc_info:
            learning.get_discussion_opening("course-missing", current_user={"id": "u1"})
        assert exc_info.value.status_code == 404
