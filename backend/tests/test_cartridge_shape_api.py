"""``GET /api/admin/cartridges/{cartridge_id}/fit`` のゲート
（概念レジストリ P3-7 / ``concept_registry_design.md`` §8）。

固定するのは:

1. 未知の分野は 404、教材未指定は 422（事実文・数値なし）。
2. 権限は TEACHER 以上 + ``_ensure_document_viewable``（KR10 fail-closed）。
3. **読み取り専用**（DELETE / POST を生やさない・監査記帳しない・LLM を呼ばない）。
4. main.py に ``prefix="/api/admin"`` で登録されている。
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for path in (str(BACKEND), os.path.join(str(BACKEND), "api"), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)


def _user():
    return {"id": "u1", "role": "TEACHER"}


class TestFitGating:
    @patch("api.routes.cartridge_shape._known_cartridge_ids", return_value={"particle_physics"})
    def test_unknown_cartridge_is_404(self, _mock_ids):
        from fastapi import HTTPException

        from api.routes.cartridge_shape import get_cartridge_fit

        with pytest.raises(HTTPException) as exc:
            get_cartridge_fit("no_such_domain", document_id="d1", current_user=_user())
        assert exc.value.status_code == 404
        assert not any(ch.isdigit() for ch in exc.value.detail)

    @patch("api.routes.cartridge_shape._known_cartridge_ids", return_value={"particle_physics"})
    def test_missing_document_is_422(self, _mock_ids):
        from fastapi import HTTPException

        from api.routes.cartridge_shape import get_cartridge_fit

        with pytest.raises(HTTPException) as exc:
            get_cartridge_fit("particle_physics", document_id="  ", current_user=_user())
        assert exc.value.status_code == 422

    @patch("api.routes.cartridge_shape._ensure_document_viewable")
    @patch("api.routes.cartridge_shape._known_cartridge_ids", return_value={"particle_physics"})
    def test_document_gate_is_always_applied(self, _mock_ids, mock_gate):
        """閲覧できない教材は既存の正本ゲートが 404 を上げる（fail-closed）。"""
        from fastapi import HTTPException

        from api.routes.cartridge_shape import get_cartridge_fit

        mock_gate.side_effect = HTTPException(status_code=404, detail="Document not found")
        with pytest.raises(HTTPException) as exc:
            get_cartridge_fit("particle_physics", document_id="d1", current_user=_user())
        assert exc.value.status_code == 404
        mock_gate.assert_called_once()

    @patch("api.routes.cartridge_shape.fit_facts", return_value={"available": True, "facts": ["x"]})
    @patch("api.routes.cartridge_shape.document_run_artifacts", return_value={"paper_skeleton": {}})
    @patch("api.routes.cartridge_shape.get_session")
    @patch("api.routes.cartridge_shape.resolve_document_access")
    @patch("api.routes.cartridge_shape._ensure_document_viewable")
    @patch("api.routes.cartridge_shape._known_cartridge_ids", return_value={"particle_physics"})
    def test_material_id_is_resolved_to_the_canonical_uuid(
        self, _ids, _gate, mock_access, mock_session, mock_artifacts, mock_fit
    ):
        """material_id 形の参照でも知識表を引く前に UUID へ解決する（KO9）。"""
        from api.routes.cartridge_shape import get_cartridge_fit

        mock_access.return_value = MagicMock(document_id="11111111-1111-4111-8111-111111111111")
        mock_session.return_value = MagicMock()
        get_cartridge_fit("particle_physics", document_id="mat-1", current_user=_user())
        assert mock_artifacts.call_args.args[0] == "11111111-1111-4111-8111-111111111111"
        assert mock_fit.call_args.kwargs["document_id"] == "11111111-1111-4111-8111-111111111111"

    @patch("api.routes.cartridge_shape.fit_facts", return_value={"available": False, "facts": []})
    @patch("api.routes.cartridge_shape.document_run_artifacts", return_value={})
    @patch("api.routes.cartridge_shape.get_session")
    @patch("api.routes.cartridge_shape.resolve_document_access")
    @patch("api.routes.cartridge_shape._ensure_document_viewable")
    @patch("api.routes.cartridge_shape._known_cartridge_ids", return_value={"particle_physics"})
    def test_adopted_run_policy_is_used(
        self, _ids, _gate, mock_access, mock_session, mock_artifacts, _fit
    ):
        """成果物の run 選択は adopted（P0-8: 表示は常に採用 run）。"""
        from api.routes.cartridge_shape import get_cartridge_fit

        mock_access.return_value = MagicMock(document_id="d1")
        mock_session.return_value = MagicMock()
        get_cartridge_fit("particle_physics", document_id="d1", current_user=_user())
        assert mock_artifacts.call_args.kwargs["policy"] == "adopted"

    @patch("api.routes.cartridge_shape.fit_facts", return_value={"available": False, "facts": []})
    @patch("api.routes.cartridge_shape.document_run_artifacts", return_value={})
    @patch("api.routes.cartridge_shape.get_session")
    @patch("api.routes.cartridge_shape.resolve_document_access")
    @patch("api.routes.cartridge_shape._ensure_document_viewable")
    @patch("api.routes.cartridge_shape._known_cartridge_ids", return_value={"particle_physics"})
    def test_session_is_always_closed(
        self, _ids, _gate, mock_access, mock_session, _artifacts, _fit
    ):
        from api.routes.cartridge_shape import get_cartridge_fit

        mock_access.return_value = MagicMock(document_id="d1")
        session = MagicMock()
        mock_session.return_value = session
        get_cartridge_fit("particle_physics", document_id="d1", current_user=_user())
        session.close.assert_called_once()


class TestRouteGuardrails:
    def _source(self) -> str:
        return (BACKEND / "api" / "routes" / "cartridge_shape.py").read_text(encoding="utf-8")

    def test_requires_teacher(self):
        source = self._source()
        assert "_require_teacher" in source
        assert "_get_current_user" not in source

    def test_read_only(self):
        """書き込み・監査記帳・LLM 呼び出しを持たない（§8 / KR5）。"""
        source = self._source()
        for forbidden in (
            "@router.post",
            "@router.patch",
            "@router.delete",
            "record_review_event",
            "generate_text",
            "CostGate",
        ):
            assert forbidden not in source

    def test_only_one_get_route(self):
        from api.routes import cartridge_shape as route_module

        paths = [
            (route.path, sorted(route.methods))
            for route in route_module.router.routes
            if hasattr(route, "path")
        ]
        assert paths == [("/cartridges/{cartridge_id}/fit", ["GET"])]

    def test_registered_in_main_with_the_admin_prefix(self):
        main_source = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")
        assert (
            'app.include_router(cartridge_shape_routes.router, prefix="/api/admin")' in main_source
        )
        assert "from routes import cartridge_shape as cartridge_shape_routes" in main_source

    def test_lifespan_validates_shapes_fail_open(self):
        """起動時 validator は fail-open（宣言の不備で起動を止めない）。"""
        main_source = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")
        assert "from core.cartridge_shape import validate_all_shapes" in main_source
        block = main_source[main_source.index("validate_all_shapes"):]
        block = block[: block.index("ヘルプKB Phase 3")]
        assert "except Exception" in block

    def test_module_docstring_states_the_invariants(self):
        from api.routes import cartridge_shape as route_module

        doc = inspect.getdoc(route_module) or ""
        assert "読み取り専用" in doc
        assert "スコア" in doc  # 数値を出さないことの明記
