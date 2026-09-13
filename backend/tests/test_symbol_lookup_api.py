"""``GET /api/learning/courses/{course_id}/symbols/lookup`` のゲート
（概念レジストリ P3-5 / ``concept_registry_design.md`` §7）。

``test_component_context_api.py`` と同じ方針で、境界
（``get_accessible_course_data`` / ``list_course_source_document_ids`` /
``_pg_session`` / ``lookup_symbol_definition``）だけをモックし、ルート関数自体の
fail-closed 判定と引数の受け渡しを検証する。

固定するのは:

1. 受講できないコースは 404（コースの存在を漏らさない）。
2. 記号が空なら 422（何を引くのか決まっていない照会は受けない）。
3. **コース sources 以外の document を core へ渡さない**（全域可視集合へ広げない）。
4. LLM を呼ばない・quota を消費しない（ルート本体に該当コードが無い）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BACKEND = Path(__file__).resolve().parents[1]
_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


def _course_data():
    return {"topics": [], "sources": [{"material_id": "mat-1"}]}


class TestSymbolLookupGating:
    @patch("api.routes.learning.get_accessible_course_data", return_value=None)
    def test_inaccessible_course_returns_404(self, _mock_course):
        from fastapi import HTTPException

        from api.routes.learning import get_symbol_lookup_route

        with pytest.raises(HTTPException) as exc:
            get_symbol_lookup_route("c1", symbol="F", current_user={"id": "u1"})
        assert exc.value.status_code == 404

    @patch("api.routes.learning.get_accessible_course_data")
    def test_empty_symbol_is_422(self, mock_course):
        from fastapi import HTTPException

        from api.routes.learning import get_symbol_lookup_route

        mock_course.return_value = _course_data()
        with pytest.raises(HTTPException) as exc:
            get_symbol_lookup_route("c1", symbol="   ", current_user={"id": "u1"})
        assert exc.value.status_code == 422
        # 受講ゲートより手前で弾くので、コースの存在も漏らさない。
        assert mock_course.call_count == 0

    @patch("api.routes.learning.lookup_symbol_definition", return_value={"available": False})
    @patch("api.routes.learning._pg_session")
    @patch("api.routes.learning.list_course_source_document_ids", return_value={"doc-b", "doc-a"})
    @patch("api.routes.learning.get_accessible_course_data")
    def test_scope_is_the_course_sources_only(
        self, mock_course, mock_sources, mock_session, mock_lookup
    ):
        """core へ渡る document 集合はコースの sources だけ（DM1 / P0 と同じ規律）。"""
        from api.routes.learning import get_symbol_lookup_route

        mock_course.return_value = _course_data()
        mock_session.return_value = MagicMock()
        get_symbol_lookup_route(
            "c1", symbol="F", equation_id="eq_1", chunk_id="ch-1", current_user={"id": "u1"}
        )
        mock_sources.assert_called_once()
        kwargs = mock_lookup.call_args.kwargs
        assert kwargs["document_ids"] == ["doc-a", "doc-b"]
        assert kwargs["symbol"] == "F"
        assert kwargs["equation_id"] == "eq_1"
        assert kwargs["chunk_id"] == "ch-1"

    @patch("api.routes.learning.lookup_symbol_definition", return_value={"available": False})
    @patch("api.routes.learning._pg_session")
    @patch("api.routes.learning.list_course_source_document_ids", return_value=set())
    @patch("api.routes.learning.get_accessible_course_data")
    def test_empty_sources_still_returns_200_unavailable(
        self, mock_course, _mock_sources, mock_session, _mock_lookup
    ):
        """sources が空でも 500 にせず ``available=false`` を返す（core が SQL を出さない）。"""
        from api.routes.learning import get_symbol_lookup_route

        mock_course.return_value = _course_data()
        mock_session.return_value = MagicMock()
        result = get_symbol_lookup_route("c1", symbol="F", current_user={"id": "u1"})
        assert result["available"] is False

    @patch("api.routes.learning.lookup_symbol_definition", return_value={"available": True})
    @patch("api.routes.learning._pg_session")
    @patch("api.routes.learning.list_course_source_document_ids", return_value={"doc-a"})
    @patch("api.routes.learning.get_accessible_course_data")
    def test_session_is_always_closed(self, mock_course, _mock_sources, mock_session, _mock_lookup):
        from api.routes.learning import get_symbol_lookup_route

        mock_course.return_value = _course_data()
        session = MagicMock()
        mock_session.return_value = session
        get_symbol_lookup_route("c1", symbol="F", current_user={"id": "u1"})
        session.close.assert_called_once()


class TestSymbolLookupRouteGuardrails:
    def _route_source(self) -> str:
        import inspect

        from api.routes.learning import get_symbol_lookup_route

        return inspect.getsource(get_symbol_lookup_route)

    def test_route_does_not_consume_quota_or_call_an_llm(self):
        """LLM 0 回・quota 非消費（§7）。同期パスに生成を混ぜない。"""
        source = self._route_source()
        for forbidden in ("_consume_quota", "generate_text", "CostGate", "usage_context"):
            assert forbidden not in source

    def test_route_does_not_widen_to_all_visible_documents(self):
        """``list_visible_document_ids``（本人の全域可視集合）へ広げない。"""
        source = self._route_source()
        assert "list_visible_document_ids" not in source
        assert "list_course_source_document_ids" in source

    def test_route_is_registered_on_the_learning_router(self):
        from api.routes import learning as learning_routes

        paths = {
            route.path
            for route in learning_routes.router.routes
            if hasattr(route, "path")
        }
        assert "/api/learning/courses/{course_id}/symbols/lookup" in paths

    def test_route_has_no_write_methods(self):
        """記号の照会は読み取り専用（POST / PATCH / DELETE を生やさない）。"""
        from api.routes import learning as learning_routes

        for route in learning_routes.router.routes:
            if getattr(route, "path", "") == "/api/learning/courses/{course_id}/symbols/lookup":
                assert set(route.methods) == {"GET"}
