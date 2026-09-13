"""コース登録（create_course）の配線 — 学ぶ単位の一級化 Phase 2（learning_units_design.md §6.2 / §6.4 / §7）。

- unit handle は候補表で解決し、候補に無い handle は捨てる（LU3）
- 候補が提示されていたときだけ decision_context 付きで記帳する（LU7 / DC3）
- 学習者向け DTO では units を kind / label だけに射影する（KO10）
- 前提は同コース topic の ID 参照に解決される（P2-4）
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

_API_DIR = str(Path(__file__).resolve().parents[1] / "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from core import decision_context  # noqa: E402
from core.course_units import UnitCandidate  # noqa: E402
import routes.learning as learning_routes  # noqa: E402


def _candidate(handle: str, key: str, label: str = "単位") -> UnitCandidate:
    return UnitCandidate(
        handle=handle,
        stable_key=key,
        unit_id=f"id-{handle}",
        unit_kind="section_block",
        label=label,
        summary="",
        document_id="doc-1",
    )


class _FakeSession:
    def execute(self, *_args, **_kwargs):  # pragma: no cover - 呼ばれない経路の保険
        raise AssertionError("SQL must not be issued in this test")

    def close(self) -> None:
        pass


@pytest.fixture
def patched(monkeypatch):
    candidates = [_candidate("U1", "k1:aaa", "章1"), _candidate("U2", "k1:bbb", "章2")]
    monkeypatch.setattr(learning_routes, "_ordered_source_document_ids", lambda _s, _m: ["doc-1"])
    monkeypatch.setattr(learning_routes, "list_unit_candidates", lambda _s, _d: list(candidates))
    return candidates


class TestBindTopicUnits:
    def test_resolves_handles_and_drops_unknown(self, patched):
        data = {
            "sources": [{"material_id": "m1"}],
            "topics": [
                {"id": "t0", "title": "A", "units": ["U1", "U9", "u2"]},
                {"id": "t1", "title": "B"},
            ],
        }
        topics, info = learning_routes._bind_topic_units(_FakeSession(), data)
        assert [u["stable_key"] for u in topics[0]["units"]] == ["k1:aaa", "k1:bbb"]
        assert topics[0]["units"][0]["source"] == "teacher_selected"
        assert "units" not in topics[1] or topics[1]["units"] == []
        assert info["presented"] == ["k1:aaa", "k1:bbb"]
        assert info["applied"] == ["k1:aaa", "k1:bbb"]
        assert info["unresolved"] is True  # U9 は候補に無い

    def test_does_not_mutate_input_topics(self, patched):
        original = {"id": "t0", "title": "A", "units": ["U1"]}
        data = {"sources": [{"material_id": "m1"}], "topics": [original]}
        learning_routes._bind_topic_units(_FakeSession(), data)
        assert original["units"] == ["U1"]

    def test_no_candidates_means_nothing_presented(self, monkeypatch):
        monkeypatch.setattr(learning_routes, "_ordered_source_document_ids", lambda _s, _m: [])
        monkeypatch.setattr(learning_routes, "list_unit_candidates", lambda _s, _d: [])
        data = {"sources": [], "topics": [{"id": "t0", "title": "A", "units": ["U1"]}]}
        topics, info = learning_routes._bind_topic_units(_FakeSession(), data)
        assert topics[0]["units"] == []
        assert info["presented"] == [] and info["applied"] == []


class TestRecordCourseRegistration:
    def test_records_decision_context_with_course_basis(self, monkeypatch):
        recorded: list[tuple] = []
        monkeypatch.setattr(
            learning_routes, "record_review_event", lambda *a, **k: recorded.append(a)
        )
        learning_routes._record_course_registration(
            "c1", "u1", {"presented": ["k1:a", "k1:b"], "applied": ["k1:a"], "unresolved": False},
        )
        assert len(recorded) == 1
        entity_type, entity_id, old, new, user_id, metadata = recorded[0]
        assert entity_type == "course_topic" and entity_id == "c1" and user_id == "u1"
        assert (old, new) == ("draft", "registered")
        ctx = metadata["decision_context"]
        assert ctx["basis"] == decision_context.BASIS_COURSE_REGISTER_UNITS
        assert ctx["presented_matches_applied"] is False
        assert set(ctx["alternatives_available"]) == {decision_context.ALT_EDIT, decision_context.ALT_DESELECT}
        assert ctx["reopen"]["statuses"] == ["candidate"]
        assert ctx["evidence_shown"] is None
        assert metadata["unit_handles_unresolved"] is False

    def test_failure_does_not_raise(self, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("db down")

        monkeypatch.setattr(learning_routes, "record_review_event", _boom)
        learning_routes._record_course_registration("c1", "u1", {"presented": ["k"], "applied": []})

    def test_create_course_records_only_when_candidates_presented(self):
        src = inspect.getsource(learning_routes.create_course)
        assert 'units_info.get("presented")' in src
        assert "_record_course_registration(" in src


class TestLearnerProjection:
    def test_units_projected_to_kind_and_label_only(self):
        data = {
            "topics": [{"id": "t0", "units": [
                {"kind": "section_block", "stable_key": "k1:x", "unit_id": "u", "label": "章1", "source": "teacher_selected"},
                {"kind": "figure", "stable_key": "k1:y", "unit_id": "v", "label": "", "source": "title_match"},
            ]}],
            "chapters": [{"title": "c", "topics": [{"id": "t0", "units": [{"kind": "figure", "stable_key": "k1:z", "label": "図1"}]}]}],
        }
        out = learning_routes._project_topics_for_learner(data)
        assert out["topics"][0]["units"] == [{"kind": "section_block", "label": "章1"}]
        assert out["chapters"][0]["topics"][0]["units"] == [{"kind": "figure", "label": "図1"}]
        # 保存データは不変
        assert data["topics"][0]["units"][0]["stable_key"] == "k1:x"

    def test_get_course_goes_through_projection(self):
        src = inspect.getsource(learning_routes.get_course)
        assert "_project_topics_for_learner(" in src


class TestPrerequisiteWiring:
    def test_create_course_resolves_prerequisite_topic_ids_before_save(self):
        src = inspect.getsource(learning_routes.create_course)
        assert src.index("resolve_prerequisite_topic_ids(") < src.index("save_course_data(")
        assert src.index("_bind_topic_units(") < src.index("save_course_data(")
