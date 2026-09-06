"""グラフの論文層 — API（GET /documents/{id}/paper-layer）のテスト。

正本: ``docs/features/graph_paper_layer_design.md`` §3（DTO）/ §4（実装配置）/ PL6・PL8。
DB / core builder への実接続は行わず、route 関数を直接呼んで monkeypatch で分離する
（``test_graph_review_api.py`` / ``test_component_graph_reference_index.py`` と同じ流儀）。
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

_TEACHER = {"id": "22222222-2222-2222-2222-222222222222", "role": "TEACHER"}
_DOC = "11111111-1111-1111-1111-111111111111"


class _Row:
    """SQLAlchemy Row の属性アクセスだけを模擬する。"""

    def __init__(self, **fields):
        for key, value in fields.items():
            setattr(self, key, value)


class _FakeSession:
    """1クエリ = 1 結果セットを返す最小セッション。"""

    def __init__(self, rows_by_call):
        self._rows_by_call = list(rows_by_call)
        self.statements: list[str] = []
        self.closed = 0

    def execute(self, stmt, params=None):
        self.statements.append(str(stmt))
        self._current = self._rows_by_call.pop(0) if self._rows_by_call else []
        return self

    def fetchall(self):
        return self._current

    def close(self):
        self.closed += 1


class _RaisingSession:
    def __init__(self):
        self.closed = 0

    def execute(self, _stmt, _params=None):
        raise RuntimeError("db unavailable")

    def close(self):
        self.closed += 1


_FIGURE_ROW = _Row(
    id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    figure_key="fig_3",
    figure_label="Figure 3",
    page=5,
    caption_text="装置の模式図",
)
_EXPLANATION_ROW = _Row(element_id="comp_0001", body="この論文では…", status="approved")


def _install_common(monkeypatch, *, artifacts=None, sessions=None, graph=None, builder=None):
    """paper-layer route の依存を全て monkeypatch し、builder の受領引数を返す。"""
    captured: dict = {}

    monkeypatch.setattr(tc, "_ensure_document_viewable", lambda doc, user: None)
    monkeypatch.setattr(tc, "_components_for_document", lambda doc: [])
    monkeypatch.setattr(tc, "_stored_component_graph", lambda doc: {"nodes": [{"component_id": "n1"}]})
    monkeypatch.setattr(
        tc,
        "_normalize_stored_component_graph",
        lambda doc, stored, components: dict(graph if graph is not None else stored),
    )
    monkeypatch.setattr(
        tc, "_build_component_graph_payload",
        lambda doc, components: pytest.fail("stored graph があるときは build しない"),
    )
    monkeypatch.setattr(
        tc, "_build_graph_reference_index", lambda doc, payload: {"claims": {"claim_a": {"text": "t"}}}
    )

    if artifacts is None:
        artifacts = {"equation_semantics": {"equations": []}}
    if isinstance(artifacts, Exception):
        def _artifacts(_doc):
            raise artifacts
    else:
        def _artifacts(_doc):
            return artifacts
    monkeypatch.setattr(tc, "document_run_artifacts", _artifacts)

    session_list = list(sessions) if sessions is not None else [
        _FakeSession([[_FIGURE_ROW]]),
        _FakeSession([[_EXPLANATION_ROW]]),
    ]
    captured["sessions"] = session_list
    monkeypatch.setattr(tc, "_pg_session", lambda: session_list.pop(0))

    def _default_builder(graph_arg, artifacts_arg, *, figure_rows, explanation_rows):
        captured["graph"] = graph_arg
        captured["artifacts"] = artifacts_arg
        captured["figure_rows"] = figure_rows
        captured["explanation_rows"] = explanation_rows
        return {"document_id": _DOC, "available": True, "facts": [], "nodes": {}}

    def _wrapped(graph_arg, artifacts_arg, *, figure_rows, explanation_rows):
        captured["graph"] = graph_arg
        captured["artifacts"] = artifacts_arg
        captured["figure_rows"] = figure_rows
        captured["explanation_rows"] = explanation_rows
        return (builder or _default_builder)(
            graph_arg, artifacts_arg, figure_rows=figure_rows, explanation_rows=explanation_rows
        )

    monkeypatch.setattr(tc, "_build_paper_layer_payload", _wrapped)
    return captured


# ---------------------------------------------------------------------------
# PL6: 権限ゲート
# ---------------------------------------------------------------------------


class TestPermissionGate:
    def test_viewable_gate_403_propagates(self, monkeypatch):
        def _deny(_doc, _user):
            raise HTTPException(status_code=403, detail="閲覧権限がありません")

        monkeypatch.setattr(tc, "_ensure_document_viewable", _deny)
        monkeypatch.setattr(
            tc, "_components_for_document",
            lambda doc: pytest.fail("ゲート前に DB を触ってはならない"),
        )
        with pytest.raises(HTTPException) as exc:
            tc.get_document_paper_layer(_DOC, current_user=_TEACHER)
        assert exc.value.status_code == 403

    def test_viewable_gate_404_propagates(self, monkeypatch):
        def _missing(_doc, _user):
            raise HTTPException(status_code=404, detail="not found")

        monkeypatch.setattr(tc, "_ensure_document_viewable", _missing)
        with pytest.raises(HTTPException) as exc:
            tc.get_document_paper_layer(_DOC, current_user=_TEACHER)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# builder への受け渡し
# ---------------------------------------------------------------------------


class TestBuilderInputs:
    def test_builder_receives_normalized_graph_with_reference_index(self, monkeypatch):
        captured = _install_common(monkeypatch)
        result = tc.get_document_paper_layer(_DOC, current_user=_TEACHER)
        assert result["available"] is True
        graph = captured["graph"]
        assert graph["nodes"] == [{"component_id": "n1"}]
        assert graph["reference_index"] == {"claims": {"claim_a": {"text": "t"}}}

    def test_builder_receives_artifacts_and_rows(self, monkeypatch):
        captured = _install_common(monkeypatch, artifacts={"paper_skeleton": {"paper_goal": {}}})
        tc.get_document_paper_layer(_DOC, current_user=_TEACHER)
        assert captured["artifacts"] == {"paper_skeleton": {"paper_goal": {}}}
        assert captured["figure_rows"] == [
            {
                "id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
                "figure_key": "fig_3",
                "figure_label": "Figure 3",
                "page": 5,
                "caption_text": "装置の模式図",
            }
        ]
        assert captured["explanation_rows"] == [
            {"element_id": "comp_0001", "body": "この論文では…", "status": "approved"}
        ]

    def test_artifacts_loaded_once(self, monkeypatch):
        calls = []
        captured = _install_common(monkeypatch)
        monkeypatch.setattr(
            tc, "document_run_artifacts", lambda doc: calls.append(doc) or {"x": 1}
        )
        tc.get_document_paper_layer(_DOC, current_user=_TEACHER)
        # reference_index は monkeypatch 済みなので、route 自身の読みは1回だけ。
        assert calls == [_DOC]
        assert captured["artifacts"] == {"x": 1}

    def test_falls_back_to_built_graph_when_no_stored_graph(self, monkeypatch):
        captured = _install_common(monkeypatch)
        monkeypatch.setattr(tc, "_normalize_stored_component_graph", lambda doc, stored, comps: {})
        monkeypatch.setattr(
            tc, "_build_component_graph_payload", lambda doc, comps: {"nodes": [], "edges": []}
        )
        tc.get_document_paper_layer(_DOC, current_user=_TEACHER)
        assert captured["graph"]["nodes"] == []
        assert "reference_index" in captured["graph"]

    def test_sessions_are_closed(self, monkeypatch):
        captured = _install_common(monkeypatch)
        sessions = list(captured["sessions"])
        tc.get_document_paper_layer(_DOC, current_user=_TEACHER)
        # _install_common は pop で消費するため、生成した実体を先に控えておく。
        assert all(s.closed >= 1 for s in sessions if isinstance(s, _FakeSession))


# ---------------------------------------------------------------------------
# PL8: fail-soft
# ---------------------------------------------------------------------------


class TestFailSoft:
    def test_artifacts_failure_yields_empty_dict(self, monkeypatch):
        captured = _install_common(monkeypatch, artifacts=RuntimeError("stage_outputs unavailable"))
        result = tc.get_document_paper_layer(_DOC, current_user=_TEACHER)
        assert captured["artifacts"] == {}
        assert result["available"] is True

    def test_both_sql_failures_yield_empty_lists(self, monkeypatch):
        captured = _install_common(
            monkeypatch, sessions=[_RaisingSession(), _RaisingSession()]
        )
        result = tc.get_document_paper_layer(_DOC, current_user=_TEACHER)
        assert captured["figure_rows"] == []
        assert captured["explanation_rows"] == []
        assert result["available"] is True

    def test_non_uuid_document_skips_explanation_query(self, monkeypatch):
        # セッションは figure 用の1本だけ（explanation 用を引くと IndexError で落ちる）。
        captured = _install_common(
            monkeypatch, sessions=[_FakeSession([[_FIGURE_ROW]])]
        )
        result = tc.get_document_paper_layer("some/source_path.pdf", current_user=_TEACHER)
        assert captured["explanation_rows"] == []
        assert len(captured["figure_rows"]) == 1
        assert result["available"] is True

    def test_builder_failure_returns_available_false_fallback(self, monkeypatch):
        def _boom(graph, artifacts, *, figure_rows, explanation_rows):
            raise RuntimeError("core module exploded")

        _install_common(monkeypatch, builder=_boom)
        result = tc.get_document_paper_layer(_DOC, current_user=_TEACHER)
        assert result["available"] is False
        assert result["document_id"] == _DOC
        assert result["facts"] == ["論文層の導出に失敗したため表示できません。"]
        assert result["paper"] is None
        assert result["nodes"] == {}
        assert result["edges"] == {}
        assert result["coverage"] == {}
        assert result["narrative"] == {}


# ---------------------------------------------------------------------------
# 既存 component-graph の非改変（設計書 §7）
# ---------------------------------------------------------------------------


class TestExistingGraphEndpointUntouched:
    def test_component_graph_route_still_present_with_response_model(self):
        from schemas import ComponentGraphResponse

        assert callable(tc.get_component_graph)
        routes = [
            r for r in tc.router.routes
            if getattr(r, "path", "") == "/documents/{document_id}/component-graph"
        ]
        assert len(routes) == 1
        assert routes[0].response_model is ComponentGraphResponse

    def test_paper_layer_route_registered_without_response_model(self):
        routes = [
            r for r in tc.router.routes
            if getattr(r, "path", "") == "/documents/{document_id}/paper-layer"
        ]
        assert len(routes) == 1
        assert routes[0].response_model is None
        assert "GET" in routes[0].methods
