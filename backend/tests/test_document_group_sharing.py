"""機能1: パイプライン成果のグループ共有（migration 035）。

対象:
  - `backend/db/035_document_group_permissions.sql` と main.py のマイグレーション登録
  - `backend/api/schemas.py` の DocumentGroupPermission{Out,UpsertRequest}
  - `backend/api/services.py` の権限ヘルパー・監査ヘルパー
  - `backend/api/routes/admin.py` の共有エンドポイント + list_materials/get_material 拡張
  - `backend/api/routes/theory_components.py` の成果ゲート拡張
  - `frontend/public/js/admin.js` の共有ボタン・共有モーダル

すべて静的解析。外部 API/DB は呼び出さない（DB は docker 内でのみ）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend" / "public"

MAIN_PY = BACKEND_DIR / "api" / "main.py"
SCHEMAS_PY = BACKEND_DIR / "api" / "schemas.py"
SERVICES_PY = BACKEND_DIR / "api" / "services.py"
ADMIN_PY = BACKEND_DIR / "api" / "routes" / "admin.py"
THEORY_PY = BACKEND_DIR / "api" / "routes" / "theory_components.py"
MIGRATION_SQL = BACKEND_DIR / "db" / "035_document_group_permissions.sql"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------


def test_migration_sql_exists_and_defines_table():
    assert MIGRATION_SQL.exists(), "035_document_group_permissions.sql が無い"
    sql = MIGRATION_SQL.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS document_group_permissions" in sql
    assert "PRIMARY KEY (document_id, group_id)" in sql
    assert "REFERENCES documents(id) ON DELETE CASCADE" in sql
    assert "CHECK (permission IN ('viewer', 'editor'))" in sql


def test_main_registers_migration_035():
    src = MAIN_PY.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS document_group_permissions" in src
    assert "idx_dgp_group_permission" in src
    # migration 035 は登録済み（適用完了ログの範囲は後続 migration 追加で末尾が伸びる）
    assert "Migrations (002-036) applied successfully." in src


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------


def test_schemas_define_document_group_permission():
    src = SCHEMAS_PY.read_text(encoding="utf-8")
    assert "class DocumentGroupPermissionOut(BaseModel):" in src
    assert "class DocumentGroupPermissionUpsertRequest(BaseModel):" in src


# ---------------------------------------------------------------------------
# services
# ---------------------------------------------------------------------------


class TestServices:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = SERVICES_PY.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "fn",
        [
            "def _resolve_document",
            "def _has_document_group_permission",
            "def user_owns_document",
            "def user_can_view_document",
            "def user_can_edit_document",
            "def get_document_group_permissions",
            "def record_review_event",
        ],
    )
    def test_defines(self, fn):
        assert fn in self.src, f"{fn} が無い"

    def test_view_logic_covers_all_paths(self):
        body = self.src.split("def user_can_view_document")[1].split("\ndef ")[0]
        assert '"uploaded_by"' in body            # 所有者
        assert '== "public"' in body              # public
        assert 'user_can_access_group' in body     # group 単一共有
        assert '("viewer", "editor")' in body      # document_group_permissions

    def test_edit_logic_requires_editor(self):
        body = self.src.split("def user_can_edit_document")[1].split("\ndef ")[0]
        assert '("editor",)' in body

    def test_resolve_document_accepts_uuid_or_source_path(self):
        body = self.src.split("def _resolve_document")[1].split("\ndef ")[0]
        assert "id::text = :ref" in body
        assert "source_path = :ref" in body


# ---------------------------------------------------------------------------
# admin routes
# ---------------------------------------------------------------------------


class TestAdminRoutes:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = ADMIN_PY.read_text(encoding="utf-8")

    def test_endpoints_defined(self):
        assert "def list_document_group_permissions" in self.src
        assert "def upsert_document_group_permission" in self.src
        assert "def delete_document_group_permission" in self.src
        assert '"/documents/{document_id}/groups"' in self.src
        assert '"/documents/{document_id}/groups/{group_id}"' in self.src

    def test_upsert_owner_only_and_audited(self):
        body = self.src.split("def upsert_document_group_permission")[1].split("\n@router")[0]
        assert "user_owns_document" in body
        assert "document_group_permissions" in body
        assert "ON CONFLICT (document_id, group_id)" in body
        assert '"document_share"' in body  # 監査 entity_type

    def test_delete_owner_only(self):
        body = self.src.split("def delete_document_group_permission")[1].split("\n@router")[0]
        assert "user_owns_document" in body
        assert "DELETE FROM document_group_permissions" in body

    def test_list_uses_view_gate(self):
        body = self.src.split("def list_document_group_permissions")[1].split("\n@router")[0]
        assert "user_can_view_document" in body

    def test_list_materials_includes_doc_perm_clause(self):
        body = self.src.split("def list_materials")[1].split("return materials")[0]
        assert "doc_perm_clause" in body
        assert "document_group_permissions" in body

    def test_get_material_includes_doc_perm_clause(self):
        body = self.src.split("def get_material")[1].split("return MaterialOut")[0]
        assert "doc_perm_clause" in body


# ---------------------------------------------------------------------------
# theory_components gate
# ---------------------------------------------------------------------------


class TestTheoryGate:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = THEORY_PY.read_text(encoding="utf-8")

    def test_imports_document_helpers(self):
        assert "user_can_view_document" in self.src
        assert "user_can_edit_document" in self.src

    def test_viewable_honors_document_sharing(self):
        body = self.src.split("def _ensure_document_viewable")[1].split("\ndef ")[0]
        assert "user_can_view_document" in body

    def test_editable_honors_document_sharing(self):
        body = self.src.split("def _ensure_document_editable")[1].split("\ndef ")[0]
        assert "user_can_edit_document" in body


# ---------------------------------------------------------------------------
# frontend
# ---------------------------------------------------------------------------


class TestAdminJs:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = ADMIN_JS.read_text(encoding="utf-8")

    def test_share_button_rendered(self):
        assert "admin-share-doc-btn" in self.src
        assert "data-document-id" in self.src

    @pytest.mark.parametrize(
        "fn",
        [
            "function openDocumentShareModal",
            "function loadDocumentShareModal",
            "function addDocumentShare",
            "function removeDocumentShare",
        ],
    )
    def test_modal_functions(self, fn):
        assert fn in self.src

    def test_modal_calls_document_groups_api(self):
        assert "/admin/documents/" in self.src
        assert "/groups" in self.src
