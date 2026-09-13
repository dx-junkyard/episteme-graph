"""``GET /api/admin/documents/{document_id}/reference-health`` のゲート
（知識の転用層 P4-3 / ``knowledge_transfer_design.md`` §6）。

固定するのは:

1. 教材未指定は 422、閲覧できない / 存在しない教材は 404（fail-closed = KT6）。
2. material_id 形の参照は知識表を引く前に canonical な UUID へ解決する（KO9）。
3. 200 の形（``document_id`` + core の結果。件数バッジを作らない = T-3）。
4. **読み取り専用**（DB を書かない・監査記帳しない・run を作らない）。
5. main.py に ``prefix="/api/admin"`` で登録され、``MaterialOut`` に投影が載る。
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


class TestGating:
    def test_blank_document_is_422(self):
        from fastapi import HTTPException

        from api.routes.reference_health import get_reference_health

        with pytest.raises(HTTPException) as exc:
            get_reference_health("   ", current_user=_user())
        assert exc.value.status_code == 422
        assert not any(ch.isdigit() for ch in exc.value.detail)

    @patch("api.routes.reference_health._ensure_document_viewable")
    def test_document_gate_is_always_applied(self, mock_gate):
        """閲覧できない教材は既存の正本ゲートが 404 を上げる（不在と同一）。"""
        from fastapi import HTTPException

        from api.routes.reference_health import get_reference_health

        mock_gate.side_effect = HTTPException(status_code=404, detail="Document not found")
        with pytest.raises(HTTPException) as exc:
            get_reference_health("d1", current_user=_user())
        assert exc.value.status_code == 404
        mock_gate.assert_called_once()

    def test_route_depends_on_require_teacher(self):
        from api.routes.reference_health import get_reference_health

        sig = inspect.signature(get_reference_health)
        default = sig.parameters["current_user"].default
        assert getattr(default, "dependency", None).__name__ == "_require_teacher"


class TestSuccessPath:
    @patch("api.routes.reference_health.check_document_references")
    @patch("api.routes.reference_health.get_session")
    @patch("api.routes.reference_health.resolve_document_access")
    @patch("api.routes.reference_health._ensure_document_viewable")
    def test_material_id_is_resolved_to_the_canonical_uuid(
        self, _gate, mock_access, mock_session, mock_check
    ):
        from api.routes.reference_health import get_reference_health

        mock_access.return_value = MagicMock(document_id="doc-uuid", can_view=True)
        session = MagicMock()
        mock_session.return_value = session
        mock_check.return_value = {"status": "ok", "checked_at": "t", "facts": ["x"], "details": {}}

        result = get_reference_health("material-123", current_user=_user())

        assert mock_check.call_args[0][1] == "doc-uuid"
        assert result["document_id"] == "doc-uuid"
        assert result["status"] == "ok"
        assert result["details"] == {}
        session.close.assert_called_once()

    @patch("api.routes.reference_health.check_document_references")
    @patch("api.routes.reference_health.get_session")
    @patch("api.routes.reference_health.resolve_document_access", side_effect=RuntimeError("boom"))
    @patch("api.routes.reference_health._ensure_document_viewable")
    def test_resolution_failure_keeps_the_original_reference(
        self, _gate, _access, mock_session, mock_check
    ):
        from api.routes.reference_health import get_reference_health

        mock_session.return_value = MagicMock()
        mock_check.return_value = {"status": "unchecked", "checked_at": "t", "facts": [], "details": {}}

        result = get_reference_health("d1", current_user=_user())
        assert mock_check.call_args[0][1] == "d1"
        assert result["status"] == "unchecked"


class TestReadOnly:
    def test_route_module_has_no_write_verbs(self):
        src = (BACKEND / "api" / "routes" / "reference_health.py").read_text(encoding="utf-8")
        for term in ("@router.post", "@router.put", "@router.patch", "@router.delete"):
            assert term not in src
        for term in ("record_review_event", "INSERT INTO", "UPDATE ", "DELETE FROM"):
            assert term not in src


class TestRegistration:
    def test_router_is_registered_under_api_admin(self):
        from tests.guardrail_helpers import collect_route_pairs

        from api.main import app

        pairs = collect_route_pairs(app)
        assert ("/api/admin/documents/{document_id}/reference-health", "GET") in pairs

    def test_main_registers_with_the_admin_prefix(self):
        src = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")
        assert 'app.include_router(reference_health_routes.router, prefix="/api/admin")' in src


class TestMaterialProjection:
    def test_material_out_has_reference_health(self):
        from api.schemas import MaterialOut

        assert "reference_health" in MaterialOut.model_fields
        assert MaterialOut(material_id="m", filename="f", title="t", status="uploaded",
                           uploaded_at="").reference_health is None

    def test_projection_keeps_only_status_and_checked_at(self):
        from api.routes.admin import _material_reference_health

        projected = _material_reference_health({
            "reference_health": {
                "status": "broken",
                "checked_at": "2026-09-13T00:00:00+00:00",
                "facts": ["…"],
                "details": {"component_claim": [{"ref": "x", "from_label": "y"}]},
            }
        })
        assert projected == {"status": "broken", "checked_at": "2026-09-13T00:00:00+00:00"}

    def test_missing_or_malformed_snapshot_is_unchecked(self):
        from api.routes.admin import _material_reference_health

        assert _material_reference_health(None) == {"status": "unchecked"}
        assert _material_reference_health({}) == {"status": "unchecked"}
        assert _material_reference_health({"reference_health": "nope"}) == {"status": "unchecked"}
        assert _material_reference_health(
            {"reference_health": {"status": "weird"}}
        ) == {"status": "unchecked"}

    def test_list_materials_sets_the_projection(self):
        from tests.guardrail_helpers import extract_function_source

        src = (BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
        body = extract_function_source(src, "list_materials")
        assert "_material_reference_health(stage_outputs)" in body
        assert "reference_health=reference_health," in body
