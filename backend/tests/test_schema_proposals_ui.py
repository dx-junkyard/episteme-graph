"""Issue #48: スキーマ提案(Shadow Testing) UI繋ぎ込みのテスト。

admin.html の DOM 要素と admin.js の配線が正しく連携し、
スキーマ提案APIエンドポイントが期待通りに動作することを検証する。
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Frontend DOM / JS tests (静的HTML/JS解析)
# ---------------------------------------------------------------------------

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public"
ADMIN_HTML = FRONTEND_DIR / "admin.html"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"


@pytest.fixture(autouse=True)
def _no_backend_settings():
    """フロントエンドテストではバックエンド設定のオーバーライドは不要。
    conftest.py の _override_settings を上書きして無効化する。"""
    yield


class TestAdminHTMLSchemaProposals:
    """admin.html にスキーマ提案に必要な DOM 要素が存在することを検証する。"""

    @pytest.fixture(autouse=True)
    def _load_html(self):
        self.html = ADMIN_HTML.read_text(encoding="utf-8")

    def test_tab_button_exists(self):
        """サイドバー(タブバー)に「スキーマ提案」タブボタンが存在すること。"""
        assert 'data-tab="schema-proposals"' in self.html

    def test_tab_panel_exists(self):
        """スキーマ提案パネル (id=tab-schema-proposals) が存在すること。"""
        assert 'id="tab-schema-proposals"' in self.html

    def test_proposals_list_container(self):
        """提案一覧コンテナ (id=sp-proposals-list) が存在すること。"""
        assert 'id="sp-proposals-list"' in self.html

    def test_simulation_area(self):
        """シミュレーション結果エリア (id=sp-simulation-area) が存在すること。"""
        assert 'id="sp-simulation-area"' in self.html

    def test_simulation_summary(self):
        """シミュレーションサマリー (id=sp-simulation-summary) が存在すること。"""
        assert 'id="sp-simulation-summary"' in self.html

    def test_simulation_details(self):
        """シミュレーション詳細 (id=sp-simulation-details) が存在すること。"""
        assert 'id="sp-simulation-details"' in self.html

    def test_approve_actions(self):
        """承認アクションエリア (id=sp-approve-actions) が存在すること。"""
        assert 'id="sp-approve-actions"' in self.html

    def test_approve_full_button(self):
        """システム全体適用ボタン (id=sp-approve-full) が存在すること。"""
        assert 'id="sp-approve-full"' in self.html

    def test_approve_canary_button(self):
        """カナリアリリースボタン (id=sp-approve-canary) が存在すること。"""
        assert 'id="sp-approve-canary"' in self.html

    def test_canary_options(self):
        """カナリアオプション (id=sp-canary-options) が存在すること。"""
        assert 'id="sp-canary-options"' in self.html

    def test_canary_course_select(self):
        """カナリアコース選択 (id=sp-canary-course-select) が存在すること。"""
        assert 'id="sp-canary-course-select"' in self.html

    def test_canary_confirm_button(self):
        """カナリア確認ボタン (id=sp-canary-confirm) が存在すること。"""
        assert 'id="sp-canary-confirm"' in self.html

    def test_approve_msg(self):
        """承認メッセージ (id=sp-approve-msg) が存在すること。"""
        assert 'id="sp-approve-msg"' in self.html

    def test_panel_has_admin_panel_class(self):
        """パネルが admin-panel クラスを持つこと (タブ切り替えに必要)。"""
        pattern = r'class="admin-panel"[^>]*id="tab-schema-proposals"'
        assert re.search(pattern, self.html), \
            "tab-schema-proposals should have class 'admin-panel'"

    def test_tab_inside_admin_tabs_container(self):
        """タブボタンが adminTabs コンテナ内にあること。"""
        tabs_match = re.search(
            r'id="adminTabs"[^>]*>(.*?)</div>',
            self.html,
            re.DOTALL,
        )
        assert tabs_match, "adminTabs container not found"
        assert 'data-tab="schema-proposals"' in tabs_match.group(1)


class TestAdminJSSchemaProposals:
    """admin.js にスキーマ提案の配線ロジックが存在することを検証する。"""

    @pytest.fixture(autouse=True)
    def _load_js(self):
        self.js = ADMIN_JS.read_text(encoding="utf-8")

    def test_init_schema_proposals_exists(self):
        """initSchemaProposals 関数が定義されていること。"""
        assert "function initSchemaProposals" in self.js

    def test_init_schema_proposals_called_in_init_app(self):
        """initApp() 内で initSchemaProposals() が呼ばれていること。"""
        assert "initSchemaProposals()" in self.js

    def test_load_schema_proposals_list_exists(self):
        """loadSchemaProposalsList 関数が定義されていること。"""
        assert "function loadSchemaProposalsList" in self.js

    def test_init_approve_actions_exists(self):
        """initApproveActions 関数が定義されていること。"""
        assert "function initApproveActions" in self.js

    def test_run_simulation_exists(self):
        """runSimulation 関数が定義されていること。"""
        assert "function runSimulation" in self.js

    def test_render_simulation_result_exists(self):
        """renderSimulationResult 関数が定義されていること。"""
        assert "function renderSimulationResult" in self.js

    def test_tab_activate_callback_mechanism(self):
        """タブ切り替えコールバック機構 (onTabActivate) が存在すること。"""
        assert "onTabActivate" in self.js

    def test_schema_proposals_tab_activate_wired(self):
        """スキーマ提案タブにonTabActivateコールバックが登録されていること。"""
        assert 'onTabActivate("schema-proposals"' in self.js

    def test_tab_switching_fires_callbacks(self):
        """initTabs内でタブ切り替え時にコールバックが発火されること。"""
        assert "_tabActivateCallbacks" in self.js

    def test_api_endpoint_schema_proposals(self):
        """API呼び出しが /admin/schema-proposals エンドポイントを使用すること。"""
        assert '"/admin/schema-proposals"' in self.js

    def test_api_endpoint_simulate(self):
        """シミュレーションAPIが /simulate エンドポイントを使用すること。"""
        assert "/simulate" in self.js

    def test_api_endpoint_approve_with_scope(self):
        """承認APIが approve-with-scope エンドポイントを使用すること。"""
        assert "approve-with-scope" in self.js

    def test_sp_dom_ids_referenced(self):
        """JS内でsp-プレフィックスのDOM IDが参照されていること。"""
        expected_ids = [
            "sp-proposals-list",
            "sp-simulation-area",
            "sp-simulation-summary",
            "sp-simulation-details",
            "sp-approve-actions",
            "sp-approve-full",
            "sp-approve-canary",
            "sp-canary-options",
            "sp-canary-course-select",
            "sp-canary-confirm",
            "sp-approve-msg",
        ]
        for dom_id in expected_ids:
            assert dom_id in self.js, f"DOM ID '{dom_id}' not referenced in admin.js"


# ---------------------------------------------------------------------------
# Backend API endpoint tests (Docker環境でのみ実行)
# ---------------------------------------------------------------------------

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient
except ImportError:
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI,
    reason="FastAPI not installed (run in Docker for full API tests)",
)


@pytest.fixture
def client():
    """FastAPI TestClient を生成する。"""
    if not _HAS_FASTAPI:
        pytest.skip("FastAPI not installed")
    import sys

    backend_dir = str(Path(__file__).resolve().parents[1])
    api_dir = str(Path(__file__).resolve().parents[1] / "api")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)

    from api.main import app

    return TestClient(app)


@pytest.fixture
def teacher_token():
    """テスト用の教員JWTトークンを生成する。"""
    import jwt

    payload = {"sub": "test-teacher-id", "role": "TEACHER", "username": "teacher1", "email": "teacher1@test.com"}
    return jwt.encode(payload, "test-secret-key", algorithm="HS256")


@pytest.fixture
def auth_headers(teacher_token):
    return {"Authorization": f"Bearer {teacher_token}"}


@_skip_no_fastapi
class TestSchemaProposalsAPIEndpoints:
    """スキーマ提案APIエンドポイントの結合テスト。"""

    @patch("routes.admin.get_proposals")
    def test_list_schema_proposals_empty(self, mock_get, client, auth_headers):
        """提案が0件の場合、空リストが返ること。"""
        mock_get.return_value = []
        resp = client.get("/api/admin/schema-proposals", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    @patch("routes.admin.get_proposals")
    def test_list_schema_proposals_with_data(self, mock_get, client, auth_headers):
        """提案がある場合、SchemaProposalOut形式で返ること。"""
        mock_get.return_value = [
            {
                "proposal_id": "p-001",
                "status": "pending",
                "summary": "新しい概念カテゴリの追加",
                "reasoning": "テスト理由",
                "source_query_count": 5,
                "items": [],
                "created_at": "2026-03-30T00:00:00",
                "reviewed_at": "",
            }
        ]
        resp = client.get("/api/admin/schema-proposals", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["proposal_id"] == "p-001"
        assert data[0]["status"] == "pending"

    @patch("routes.admin.get_proposals")
    def test_list_schema_proposals_filter_by_status(self, mock_get, client, auth_headers):
        """status パラメータでフィルタリングできること。"""
        mock_get.return_value = []
        resp = client.get(
            "/api/admin/schema-proposals?status=pending",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        mock_get.assert_called_once_with(status="pending")

    @patch("core.simulator.run_simulation")
    def test_simulate_schema_proposal_success(self, mock_sim, client, auth_headers):
        """シミュレーションが成功した場合、SimulationResponse形式で返ること。"""
        mock_sim.return_value = {
            "proposal_id": "p-001",
            "proposal_summary": "テスト提案",
            "proposal_items": [],
            "results": {"target": [], "similar": [], "control": []},
            "stats": {
                "target_doc_count": 3,
                "similar_doc_count": 5,
                "control_doc_count": 2,
                "total_added_concepts": 10,
                "total_removed_concepts": 0,
                "total_reclassified_nodes": 1,
            },
        }
        resp = client.post(
            "/api/admin/schema-proposals/p-001/simulate",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["proposal_id"] == "p-001"
        assert data["stats"]["target_doc_count"] == 3

    @patch("core.simulator.run_simulation")
    def test_simulate_schema_proposal_not_found(self, mock_sim, client, auth_headers):
        """存在しない提案IDの場合、404が返ること。"""
        mock_sim.return_value = None
        resp = client.post(
            "/api/admin/schema-proposals/nonexistent/simulate",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @patch("core.simulator.approve_with_scope")
    def test_approve_with_scope_full(self, mock_approve, client, auth_headers):
        """システム全体への適用が成功すること。"""
        mock_approve.return_value = {
            "proposal_id": "p-001",
            "status": "approved",
            "scope": "full",
        }
        resp = client.put(
            "/api/admin/schema-proposals/p-001/approve-with-scope",
            headers=auth_headers,
            json={"scope": "full", "course_ids": []},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "full"

    @patch("core.simulator.approve_with_scope")
    def test_approve_with_scope_canary(self, mock_approve, client, auth_headers):
        """カナリアリリースが成功すること。"""
        mock_approve.return_value = {
            "proposal_id": "p-001",
            "status": "approved",
            "scope": "canary",
            "course_ids": ["course-1"],
        }
        resp = client.put(
            "/api/admin/schema-proposals/p-001/approve-with-scope",
            headers=auth_headers,
            json={"scope": "canary", "course_ids": ["course-1"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "canary"

    @patch("core.simulator.approve_with_scope")
    def test_approve_with_scope_not_found(self, mock_approve, client, auth_headers):
        """存在しない提案IDの承認で404が返ること。"""
        mock_approve.return_value = None
        resp = client.put(
            "/api/admin/schema-proposals/nonexistent/approve-with-scope",
            headers=auth_headers,
            json={"scope": "full", "course_ids": []},
        )
        assert resp.status_code == 404

    def test_schema_proposals_requires_auth(self, client):
        """認証なしで403/401が返ること。"""
        resp = client.get("/api/admin/schema-proposals")
        assert resp.status_code in (401, 403)

    def test_simulate_requires_auth(self, client):
        """認証なしでシミュレーションAPIが拒否されること。"""
        resp = client.post("/api/admin/schema-proposals/p-001/simulate")
        assert resp.status_code in (401, 403)
