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
    monkeypatch.setattr(
        learning_routes, "_ordered_source_document_ids", lambda _s, _m, **_k: ["doc-1"]
    )
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
        topics, info = learning_routes._bind_topic_units(_FakeSession(), data, user_id="u1")
        assert [u["stable_key"] for u in topics[0]["units"]] == ["k1:aaa", "k1:bbb"]
        assert topics[0]["units"][0]["source"] == "teacher_selected"
        assert "units" not in topics[1] or topics[1]["units"] == []
        assert info["presented"] == ["k1:aaa", "k1:bbb"]
        assert info["applied"] == ["k1:aaa", "k1:bbb"]
        assert info["unresolved"] is True  # U9 は候補に無い

    def test_does_not_mutate_input_topics(self, patched):
        original = {"id": "t0", "title": "A", "units": ["U1"]}
        data = {"sources": [{"material_id": "m1"}], "topics": [original]}
        learning_routes._bind_topic_units(_FakeSession(), data, user_id="u1")
        assert original["units"] == ["U1"]

    def test_no_candidates_means_nothing_presented(self, monkeypatch):
        monkeypatch.setattr(
            learning_routes, "_ordered_source_document_ids", lambda _s, _m, **_k: []
        )
        monkeypatch.setattr(learning_routes, "list_unit_candidates", lambda _s, _d: [])
        data = {"sources": [], "topics": [{"id": "t0", "title": "A", "units": ["U1"]}]}
        topics, info = learning_routes._bind_topic_units(_FakeSession(), data, user_id="u1")
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


class TestDecisionContextBasisCallSite:
    """P2-R9: ``BASIS_COURSE_REGISTER_UNITS`` が**この経路からしか**使われないことを逐語で固定する。

    決定文脈の basis は「どの操作の一括確定か」を後から再構成するための名前なので、
    別の経路が同じ basis で記帳し始めると記録の意味が壊れる（DC1/DC2）。
    """

    def test_basis_is_used_only_by_the_course_registration_recorder(self):
        src = inspect.getsource(learning_routes)
        occurrences = src.count("BASIS_COURSE_REGISTER_UNITS")
        assert occurrences == 1, f"想定外の call site がある: {occurrences}"
        recorder = inspect.getsource(learning_routes._record_course_registration)
        assert "basis=decision_context.BASIS_COURSE_REGISTER_UNITS," in recorder

    def test_no_other_backend_module_records_with_this_basis(self):
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for path in sorted(root.rglob("*.py")):
            if ".venv" in path.parts or path.parts[-2:][0] == "tests":
                continue
            text = path.read_text(encoding="utf-8")
            if "BASIS_COURSE_REGISTER_UNITS" not in text:
                continue
            # 定義元（core/decision_context.py）と、記帳する当の経路だけが許される。
            if path.name == "decision_context.py" or path.name == "learning.py":
                continue
            offenders.append(str(path.relative_to(root)))
        assert offenders == [], offenders


class TestPreserveTopicUnits:
    """P2-R4: GET 射影で参照キーが落ちた units を素通しさせない。"""

    _EXISTING = [{
        "id": "t0",
        "units": [{"kind": "section_block", "stable_key": "k1:aaa", "label": "章1",
                   "source": "teacher_selected"}],
    }]

    def test_projected_units_do_not_erase_the_stored_reference(self):
        incoming = [{"id": "t0", "title": "A", "units": [{"kind": "section_block", "label": "章1"}]}]
        out = learning_routes._preserve_topic_units(incoming, self._EXISTING)
        assert out[0]["units"] == self._EXISTING[0]["units"]

    def test_empty_units_do_not_erase_the_stored_reference(self):
        out = learning_routes._preserve_topic_units([{"id": "t0", "title": "A"}], self._EXISTING)
        assert out[0]["units"] == self._EXISTING[0]["units"]

    def test_incoming_units_with_reference_keys_win(self):
        incoming = [{"id": "t0", "units": [{"kind": "figure", "stable_key": "k1:zzz"}]}]
        out = learning_routes._preserve_topic_units(incoming, self._EXISTING)
        assert [u["stable_key"] for u in out[0]["units"]] == ["k1:zzz"]

    def test_new_topics_are_left_alone(self):
        out = learning_routes._preserve_topic_units([{"id": "t9", "units": []}], self._EXISTING)
        assert out[0]["units"] == []

    def test_inputs_are_not_mutated(self):
        incoming = [{"id": "t0", "title": "A"}]
        learning_routes._preserve_topic_units(incoming, self._EXISTING)
        assert "units" not in incoming[0]

    def test_update_course_goes_through_the_preserver(self):
        src = inspect.getsource(learning_routes.update_course)
        assert "_preserve_topic_units(" in src
        # P3-R8: 概念マップの記号除去も登録時と同じ弁を通す。
        assert "_split_symbol_concepts(" in src


class TestSourceDocumentVisibilityGate:
    """P2-R5: sources に material_id が書いてあるだけでは読んでよい根拠にならない。"""

    def test_invisible_documents_are_dropped(self, monkeypatch):
        class _Session:
            def execute(self, *_a, **_k):
                class _R:
                    @staticmethod
                    def fetchall():
                        return [("m1", "doc-visible"), ("m2", "doc-hidden")]
                return _R()

        monkeypatch.setattr(learning_routes, "list_visible_document_ids", lambda _u: ["doc-visible"])
        out = learning_routes._ordered_source_document_ids(
            _Session(), ["m1", "m2"], user_id="u1"
        )
        assert out == ["doc-visible"]

    def test_no_visible_documents_issues_no_sql(self, monkeypatch):
        monkeypatch.setattr(learning_routes, "list_visible_document_ids", lambda _u: [])
        out = learning_routes._ordered_source_document_ids(
            _FakeSession(), ["m1"], user_id="u1"
        )
        assert out == []

    def test_missing_user_is_fail_closed(self):
        assert learning_routes._ordered_source_document_ids(_FakeSession(), ["m1"], user_id="") == []
