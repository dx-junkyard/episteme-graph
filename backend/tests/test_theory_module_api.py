"""理論モジュール層 — API（GET /documents/{id}/theory-modules）のテスト。

正本: ``docs/features/theory_module_layer_design.md`` §8.1（API・DTO）/ TM8（fail-soft）/ TM9（権限）。
DB への実接続は行わず、route 関数を直接呼んで monkeypatch で分離する
（``test_graph_paper_layer_api.py`` と同じ流儀）。
"""

from __future__ import annotations

import inspect
import json
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
from core.theory_modules.schema import FACT_BUILD_FAILED, FACT_NO_GRAPH, RULE_VERSION  # noqa: E402

_TEACHER = {"id": "22222222-2222-2222-2222-222222222222", "role": "TEACHER"}
_DOC = "11111111-1111-1111-1111-111111111111"
FIXTURES = BACKEND / "tests" / "fixtures" / "theory_modules"


def _install(monkeypatch, *, artifacts=None, stored=None, normalized=None, builder=None):
    """theory-modules route の依存を monkeypatch し、builder の受領引数を返す。"""
    captured: dict = {}
    monkeypatch.setattr(tc, "_ensure_document_viewable", lambda doc, user: None)
    monkeypatch.setattr(tc, "_components_for_document", lambda doc: [])
    stored_graph = {"nodes": [{"component_id": "n1"}]} if stored is None else stored
    monkeypatch.setattr(tc, "_stored_component_graph", lambda doc: stored_graph)

    def _normalize(doc, graph, components):
        captured["normalize_input"] = graph
        return dict(graph) if normalized is None else normalized

    monkeypatch.setattr(tc, "_normalize_stored_component_graph", _normalize)
    monkeypatch.setattr(
        tc, "_build_component_graph_payload",
        lambda doc, components: pytest.fail("理論モジュールは component 一覧からの組み立てに落とさない"),
    )
    monkeypatch.setattr(
        tc, "_build_graph_reference_index",
        lambda doc, payload: pytest.fail("理論モジュールは reference index を組まない"),
    )
    if isinstance(artifacts, Exception):
        def _artifacts(_doc):
            raise artifacts
    else:
        def _artifacts(_doc):
            return {"derivation_chain": {"chains": []}} if artifacts is None else artifacts
    monkeypatch.setattr(tc, "document_run_artifacts", _artifacts)

    def _default_builder(document_id, artifacts_arg, graph_arg, equation_stable_keys=None):
        captured["document_id"] = document_id
        captured["artifacts"] = artifacts_arg
        captured["graph"] = graph_arg
        captured["equation_stable_keys"] = equation_stable_keys
        return {"document_id": document_id, "available": True, "facts": [], "modules": []}

    monkeypatch.setattr(tc, "_build_theory_modules_payload", builder or _default_builder)
    return captured


# ---------------------------------------------------------------------------
# TM9: 権限ゲート（fail-closed）
# ---------------------------------------------------------------------------


class TestPermissionGate:
    @pytest.mark.parametrize("status", [403, 404])
    def test_gate_error_propagates_before_any_read(self, monkeypatch, status):
        def _deny(_doc, _user):
            raise HTTPException(status_code=status, detail="denied")

        monkeypatch.setattr(tc, "_ensure_document_viewable", _deny)
        for name in ("_components_for_document", "_stored_component_graph", "document_run_artifacts"):
            monkeypatch.setattr(tc, name, lambda *a, _n=name: pytest.fail(f"ゲート前に {_n} を呼んではならない"))
        with pytest.raises(HTTPException) as exc:
            tc.get_document_theory_modules(_DOC, current_user=_TEACHER)
        assert exc.value.status_code == status

    def test_route_requires_teacher(self):
        signature = inspect.signature(tc.get_document_theory_modules)
        dependency = signature.parameters["current_user"].default
        assert getattr(dependency, "dependency", None) is tc._require_teacher

    def test_route_registered_without_response_model(self):
        routes = [
            r for r in tc.router.routes
            if getattr(r, "path", "") == "/documents/{document_id}/theory-modules"
        ]
        assert len(routes) == 1
        assert routes[0].response_model is None
        assert routes[0].methods == {"GET"}


# ---------------------------------------------------------------------------
# builder への受け渡しと fail-soft（TM8）
# ---------------------------------------------------------------------------


class TestBuilderInputs:
    def test_builder_receives_normalized_graph_and_artifacts(self, monkeypatch):
        captured = _install(monkeypatch, artifacts={"derivation_chain": {"chains": [{"x": 1}]}})
        result = tc.get_document_theory_modules(_DOC, current_user=_TEACHER)
        assert result["available"] is True
        assert captured["document_id"] == _DOC
        assert captured["artifacts"] == {"derivation_chain": {"chains": [{"x": 1}]}}
        assert captured["graph"] == {"nodes": [{"component_id": "n1"}]}

    def test_builder_receives_equation_stable_keys_from_the_shared_map(self, monkeypatch):
        """§13.3: route は persistence.equation_stable_key_map（ステージと同じ関数）で写像を作る。"""
        from core.document_pipeline.persistence import equation_stable_key_map

        equations = {"equations": [{"equation_id": "eq_1", "label": "1",
                                    "source_extraction": {"latex": "a=b", "source_location": {"block_id": "b1"}}}]}
        captured = _install(monkeypatch, artifacts={"derivation_chain": {"chains": []}, "equation_semantics": equations})
        tc.get_document_theory_modules(_DOC, current_user=_TEACHER)
        assert captured["equation_stable_keys"] == equation_stable_key_map(_DOC, equations)
        assert captured["equation_stable_keys"]["eq_1"].startswith("k1:")

    def test_equation_key_failure_passes_empty_map(self, monkeypatch):
        import core.document_pipeline.persistence as persistence

        captured = _install(monkeypatch)
        monkeypatch.setattr(persistence, "equation_stable_key_map", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
        result = tc.get_document_theory_modules(_DOC, current_user=_TEACHER)
        assert result["available"] is True
        assert captured["equation_stable_keys"] == {}

    def test_artifacts_failure_passes_empty_dict(self, monkeypatch):
        captured = _install(monkeypatch, artifacts=RuntimeError("db down"))
        tc.get_document_theory_modules(_DOC, current_user=_TEACHER)
        assert captured["artifacts"] == {}

    def test_missing_stored_graph_passes_empty_graph(self, monkeypatch):
        captured = _install(monkeypatch, stored={}, normalized={})
        tc.get_document_theory_modules(_DOC, current_user=_TEACHER)
        assert captured["graph"] == {}

    def test_graph_read_failure_passes_empty_graph(self, monkeypatch):
        captured = _install(monkeypatch)

        def _boom(_doc):
            raise RuntimeError("db down")

        monkeypatch.setattr(tc, "_stored_component_graph", _boom)
        tc.get_document_theory_modules(_DOC, current_user=_TEACHER)
        assert captured["graph"] == {}

    def test_builder_exception_returns_unavailable_200(self, monkeypatch):
        def _raise(*_args, **_kwargs):
            raise ValueError("boom")

        _install(monkeypatch, builder=_raise)
        result = tc.get_document_theory_modules(_DOC, current_user=_TEACHER)
        assert result["available"] is False
        assert result["facts"] == [FACT_BUILD_FAILED]
        assert result["rule_version"] == RULE_VERSION
        assert result["modules"] == [] and result["claim_sequence_node_ids"] == []


class TestEndToEndWithRealBuilder:
    """route → 実 builder を fixture でつないで、DTO が §8.1 の形で返ることを確かめる。"""

    def _run(self, monkeypatch, fixture_name, *, graph_available=True):
        fixture = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
        monkeypatch.setattr(tc, "_ensure_document_viewable", lambda doc, user: None)
        monkeypatch.setattr(tc, "_components_for_document", lambda doc: [])
        monkeypatch.setattr(
            tc, "_stored_component_graph", lambda doc: fixture["graph_json"] if graph_available else {}
        )
        monkeypatch.setattr(tc, "_normalize_stored_component_graph", lambda doc, graph, components: graph)
        monkeypatch.setattr(tc, "document_run_artifacts", lambda doc: fixture["artifacts"])
        return tc.get_document_theory_modules(fixture["document_id"], current_user=_TEACHER)

    def test_tex_2407_outer_modules(self, monkeypatch):
        result = self._run(monkeypatch, "arxiv_2407_01221v2_tex.json")
        assert result["available"] is True
        assert len([m for m in result["modules"] if m["level"] == "outer"]) == 8

    def test_pdf_is_unavailable_but_marks_claim_sequence(self, monkeypatch):
        result = self._run(monkeypatch, "arxiv_2609_15375v1_pdf.json")
        assert result["available"] is False
        assert len(result["claim_sequence_node_ids"]) == 470

    def test_without_stored_graph_is_fact_not_error(self, monkeypatch):
        result = self._run(monkeypatch, "arxiv_2407_01221v2_tex.json", graph_available=False)
        assert result["available"] is True
        assert FACT_NO_GRAPH in result["facts"]


# ---------------------------------------------------------------------------
# 既存 component-graph の非改変（設計書 §8.1）
# ---------------------------------------------------------------------------


class TestExistingGraphEndpointUntouched:
    def test_component_graph_route_still_present_with_response_model(self):
        from schemas import ComponentGraphResponse

        routes = [
            r for r in tc.router.routes
            if getattr(r, "path", "") == "/documents/{document_id}/component-graph"
        ]
        assert len(routes) == 1
        assert routes[0].response_model is ComponentGraphResponse

    def test_component_graph_response_has_no_module_fields(self):
        from schemas import ComponentGraphResponse

        fields = set(ComponentGraphResponse.model_fields)
        assert not fields & {"modules", "theory_modules", "claim_sequence_node_ids", "sinks", "foundations"}

    def test_component_graph_handler_does_not_call_theory_modules(self):
        src = inspect.getsource(tc.get_component_graph)
        assert "theory_modules" not in src and "build_theory_modules" not in src
