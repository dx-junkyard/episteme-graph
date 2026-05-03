"""Issue #63: 教材処理パイプラインの非同期化・ポーリングUIのテスト。

バックグラウンドタスクのDB状態管理、ポーリングAPI、フロントエンドの
ポーリングロジックが期待通りに動作することを検証する。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure api/ is importable (schemas, services live there)
_backend_dir = str(Path(__file__).resolve().parents[1])
_api_dir = str(Path(__file__).resolve().parents[1] / "api")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if _api_dir not in sys.path:
    sys.path.insert(0, _api_dir)

# ---------------------------------------------------------------------------
# Frontend DOM / JS tests (静的HTML/JS解析)
# ---------------------------------------------------------------------------

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public"
ADMIN_HTML = FRONTEND_DIR / "admin.html"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"
ADMIN_ROUTE = Path(__file__).resolve().parents[1] / "api" / "routes" / "admin.py"


@pytest.fixture(autouse=True)
def _no_backend_settings():
    """フロントエンドテストではバックエンド設定のオーバーライドは不要。"""
    yield


class TestAdminJSPolling:
    """admin.js にポーリング関連のロジックが正しく実装されていることを検証する。"""

    @pytest.fixture(autouse=True)
    def _load_js(self):
        self.js = ADMIN_JS.read_text(encoding="utf-8")

    def test_start_task_polling_function_exists(self):
        """startTaskPolling 関数が定義されていること。"""
        assert "function startTaskPolling" in self.js

    def test_stop_task_polling_function_exists(self):
        """stopTaskPolling 関数が定義されていること。"""
        assert "function stopTaskPolling" in self.js

    def test_polling_calls_tasks_endpoint(self):
        """/admin/tasks/ エンドポイントをポーリングで呼び出していること。"""
        assert "/admin/tasks/" in self.js

    def test_polling_handles_completed_status(self):
        """completed ステータスでポーリングを停止し、UIを更新すること。"""
        assert 'task.status === "completed"' in self.js

    def test_polling_handles_failed_status(self):
        """failed ステータスでポーリングを停止し、エラーを表示すること。"""
        assert 'task.status === "failed"' in self.js

    def test_polling_uses_set_interval(self):
        """setInterval を使って定期ポーリングを行っていること。"""
        assert "setInterval(poll" in self.js

    def test_polling_clears_interval_on_stop(self):
        """ポーリング停止時に clearInterval を呼んでいること。"""
        assert "clearInterval(" in self.js

    def test_polling_has_retry_logic(self):
        """ネットワークエラー時のリトライカウントが実装されていること。"""
        assert "retryCount" in self.js

    def test_upload_disables_ui_during_processing(self):
        """アップロード中にUIを無効化する disableUploadUI 関数が存在すること。"""
        assert "function disableUploadUI" in self.js

    def test_upload_returns_task_id(self):
        """アップロード後に data.task_id を使ってポーリングを開始すること。"""
        assert "data.task_id" in self.js

    def test_polling_shows_error_message_from_task(self):
        """タスク失敗時にerror_messageを表示すること。"""
        assert "task.error_message" in self.js

    def test_pdf_reupload_button_shows_failed_state(self):
        """教材処理失敗またはチャンク0件時にPDF再登録ボタンを失敗表示にすること。"""
        assert "pdfRegistrationFailed" in self.js
        assert "admin-pdf-reupload-btn-failed" in self.js
        assert "失敗 (PDF再登録)" in self.js


class TestAdminPdfReupload:
    """PDF再登録時の補助処理が実装されていることを静的に検証する。"""

    @pytest.fixture(autouse=True)
    def _load_admin_route(self):
        self.route = ADMIN_ROUTE.read_text(encoding="utf-8")

    def test_reupload_backfills_missing_chunk_page_info(self):
        """ページ情報の無いチャンクだけを再登録PDFから補完すること。"""
        assert "_backfill_missing_chunk_pages_from_pdf" in self.route
        assert "extract_pdf_pages(pdf_bytes)" in self.route
        assert "page_start IS NULL OR page_end IS NULL" in self.route
        assert "UPDATE chunks" in self.route

    def test_reupload_returns_page_info_updated_count(self):
        """補完件数をレスポンスとログに含めること。"""
        assert "page_info_updated" in self.route
        assert '"page_info_updated": page_info_updated' in self.route


# ---------------------------------------------------------------------------
# Backend unit tests (services layer)
# ---------------------------------------------------------------------------


class TestBackgroundTaskSchema:
    """BackgroundTaskOut Pydantic スキーマのテスト。"""

    def test_schema_fields(self):
        """BackgroundTaskOut に必要なフィールドが存在すること。"""
        from schemas import BackgroundTaskOut

        task = BackgroundTaskOut(
            task_id="test-123",
            task_type="material_processing",
            status="pending",
        )
        assert task.task_id == "test-123"
        assert task.status == "pending"
        assert task.result_data is None
        assert task.error_message is None

    def test_schema_with_result_data(self):
        """result_data が設定可能であること。"""
        from schemas import BackgroundTaskOut

        task = BackgroundTaskOut(
            task_id="test-456",
            status="completed",
            result_data={"material_id": "abc", "chunk_count": 10},
        )
        assert task.result_data["chunk_count"] == 10

    def test_schema_with_error_message(self):
        """error_message が設定可能であること。"""
        from schemas import BackgroundTaskOut

        task = BackgroundTaskOut(
            task_id="test-789",
            status="failed",
            error_message="PDF parse error",
        )
        assert task.error_message == "PDF parse error"


class TestBackgroundTaskORM:
    """BackgroundTask ORM モデルのテスト。"""

    def test_model_has_required_columns(self):
        """BackgroundTask モデルに必要なカラムが定義されていること。"""
        from core.models import BackgroundTask

        columns = {c.name for c in BackgroundTask.__table__.columns}
        expected = {"id", "task_type", "status", "created_by", "result_data",
                    "error_message", "created_at", "updated_at"}
        assert expected.issubset(columns)

    def test_model_table_name(self):
        """テーブル名が background_tasks であること。"""
        from core.models import BackgroundTask
        assert BackgroundTask.__tablename__ == "background_tasks"


class TestProcessMaterialBackgroundSignature:
    """process_material_background に task_id パラメータが追加されたことを検証。"""

    def test_accepts_task_id_parameter(self):
        """task_id パラメータが受け入れられること。"""
        import inspect
        from services import process_material_background

        sig = inspect.signature(process_material_background)
        assert "task_id" in sig.parameters

    def test_task_id_is_optional(self):
        """task_id がオプショナル（デフォルト None）であること。"""
        import inspect
        from services import process_material_background

        sig = inspect.signature(process_material_background)
        param = sig.parameters["task_id"]
        assert param.default is None


class TestProcessMaterialBackgroundFailures:
    """教材処理でチャンクが作成されない / 保存されないケースを失敗扱いにすることを検証。

    issue #226 で PDF アップロード処理は `run_document_pipeline` へ委譲されたため、
    失敗ハンドリングは orchestrator 側で `PipelineStageError` として表現される。
    """

    ORCHESTRATOR_FILE = (
        Path(__file__).resolve().parents[1] / "core" / "document_pipeline" / "orchestrator.py"
    )

    @pytest.fixture(autouse=True)
    def _load_source(self):
        self.source = self.ORCHESTRATOR_FILE.read_text(encoding="utf-8")

    def test_zero_chunks_raises_failure(self):
        # source_chunking stage で chunk が 0 件なら PipelineStageError を上げる
        assert "if not source_chunks:" in self.source
        assert 'PipelineStageError(\n                "source_chunking"' in self.source

    def test_zero_embedded_chunks_raises_failure(self):
        # source_embedding stage で persist が失敗 / 0 件なら PipelineStageError を上げる
        assert '"source_embedding"' in self.source
        assert "PipelineStageError" in self.source


class TestServiceHelpers:
    """services.py のバックグラウンドタスクヘルパー関数のテスト。"""

    def test_create_background_task_exists(self):
        """create_background_task 関数がエクスポートされていること。"""
        from services import create_background_task
        assert callable(create_background_task)

    def test_update_background_task_exists(self):
        """update_background_task 関数がエクスポートされていること。"""
        from services import update_background_task
        assert callable(update_background_task)

    def test_get_background_task_exists(self):
        """get_background_task 関数がエクスポートされていること。"""
        from services import get_background_task
        assert callable(get_background_task)


# ---------------------------------------------------------------------------
# Migration SQL tests
# ---------------------------------------------------------------------------


class TestMigrationSQL:
    """マイグレーションSQLファイルのテスト。"""

    MIGRATION_FILE = Path(__file__).resolve().parents[1] / "db" / "005_background_tasks.sql"

    def test_migration_file_exists(self):
        """005_background_tasks.sql が存在すること。"""
        assert self.MIGRATION_FILE.exists()

    def test_migration_creates_table(self):
        """CREATE TABLE background_tasks が含まれていること。"""
        sql = self.MIGRATION_FILE.read_text(encoding="utf-8")
        assert "CREATE TABLE" in sql
        assert "background_tasks" in sql

    def test_migration_has_status_check(self):
        """status カラムに CHECK 制約があること。"""
        sql = self.MIGRATION_FILE.read_text(encoding="utf-8")
        assert "pending" in sql
        assert "processing" in sql
        assert "completed" in sql
        assert "failed" in sql

    def test_migration_has_indexes(self):
        """必要なインデックスが定義されていること。"""
        sql = self.MIGRATION_FILE.read_text(encoding="utf-8")
        assert "idx_bg_tasks_status" in sql
        assert "idx_bg_tasks_created_by" in sql


# ---------------------------------------------------------------------------
# Admin routes tests (endpoint registration)
# ---------------------------------------------------------------------------


class TestAdminRoutesRegistration:
    """admin.py のルーター定義にポーリングエンドポイントが含まれることを検証。"""

    ADMIN_ROUTES = Path(__file__).resolve().parents[1] / "api" / "routes" / "admin.py"

    @pytest.fixture(autouse=True)
    def _load_source(self):
        self.source = self.ADMIN_ROUTES.read_text(encoding="utf-8")

    def test_tasks_endpoint_defined(self):
        """/tasks/{task_id} エンドポイントが定義されていること。"""
        assert "/tasks/{task_id}" in self.source

    def test_tasks_endpoint_is_get(self):
        """ポーリングエンドポイントが GET メソッドであること。"""
        assert '@router.get("/tasks/{task_id}"' in self.source

    def test_upload_returns_task_id(self):
        """upload_material が task_id を生成していること。"""
        # Check task_id creation in upload endpoint
        assert "task_id = str(uuid.uuid4())" in self.source

    def test_upload_status_code_202(self):
        """upload エンドポイントが 202 を返すこと。"""
        assert "status_code=202" in self.source

    def test_upload_creates_background_task(self):
        """upload が create_background_task を呼び出すこと。"""
        assert "create_background_task(task_id" in self.source
