"""分野の地図 — 骨格レビュー/凍結 API (Issue A-2 / A-3) の結合テスト。"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient  # noqa: F401
except ImportError:
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)


@pytest.fixture
def client():
    if not _HAS_FASTAPI:
        pytest.skip("FastAPI not installed")
    backend_dir = str(Path(__file__).resolve().parents[2])
    api_dir = str(Path(__file__).resolve().parents[2] / "api")
    # api.main → document_pipeline が episteme_graph (src/) を参照するため
    src_dir = str(Path(__file__).resolve().parents[3] / "src")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


def _headers(role: str, sub: str = "11111111-1111-1111-1111-111111111111"):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    token = jwt.encode(payload, "test-secret-key", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def teacher_headers():
    return _headers("TEACHER")


@pytest.fixture
def student_headers():
    return _headers("STUDENT")


@pytest.fixture
def sandbox_cartridges(tmp_path, monkeypatch):
    """particle_physics を tmp にコピーしてカートリッジルートを差し替える。

    draft の書き込み・凍結でリポジトリ内の実カートリッジを汚さないため。
    """
    from core import cartridges as cartridges_module

    src = cartridges_module._cartridges_root() / "particle_physics"
    dst = tmp_path / "particle_physics"
    shutil.copytree(src, dst)
    monkeypatch.setattr(cartridges_module, "_cartridges_root", lambda: tmp_path)
    cartridges_module.clear_cache()
    yield tmp_path
    cartridges_module.clear_cache()


def _sample_draft_dict(cartridge_id: str = "particle_physics") -> dict:
    return {
        "atlas_skeleton": {
            "version": "",
            "cartridge": cartridge_id,
            "status": "draft",
            "generated_by": "model:test batch:api",
            "reviewed_by": [],
            "regions": [
                {
                    "id": "region_a",
                    "label": "領域A",
                    "layout": {"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4},
                    "concepts": [
                        {
                            "id": "concept_a",
                            "label": "概念A",
                            "layout": {"x": 0.5, "y": 0.5},
                            "seed_status": {"value": "verified", "reviewed": True},
                        }
                    ],
                }
            ],
            "edges": [],
            "concept_bindings": [],
        }
    }


@_skip_no_fastapi
class TestAtlasAdminFlow:
    def test_admin_endpoints_require_teacher(self, client, student_headers, sandbox_cartridges):
        resp = client.get(
            "/api/admin/cartridges/particle_physics/atlas/skeleton", headers=student_headers
        )
        assert resp.status_code == 403

    def test_get_state_returns_frozen_bundle(self, client, teacher_headers, sandbox_cartridges):
        resp = client.get(
            "/api/admin/cartridges/particle_physics/atlas/skeleton", headers=teacher_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["draft"] is None
        assert data["frozen"]["skeleton"]["status"] == "frozen"
        assert data["frozen"]["validation"]["errors"] == []

    def test_review_edit_and_freeze_flow(self, client, teacher_headers, sandbox_cartridges):
        # 1) レビュー修正 (draft 保存)
        resp = client.put(
            "/api/admin/cartridges/particle_physics/atlas/skeleton/draft",
            headers=teacher_headers,
            json={"skeleton": _sample_draft_dict()},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["draft"]["skeleton"]["status"] == "draft"

        # 2) 凍結 (既に 2026.1 が同梱済みなので次版を付与)
        resp = client.post(
            "/api/admin/cartridges/particle_physics/atlas/skeleton/freeze",
            headers=teacher_headers,
            json={"version": "2027.1", "note": "テスト改版"},
        )
        assert resp.status_code == 200, resp.text
        frozen = resp.json()["frozen"]["skeleton"]
        assert frozen["status"] == "frozen"
        assert frozen["version"] == "2027.1"
        # 承認で reviewed_by に帰属が記録される (受け入れ条件2)
        assert frozen["reviewed_by"] == ["11111111-1111-1111-1111-111111111111"]
        assert any(e["version"] == "2027.1" for e in frozen["changelog"])

        # 3) 凍結後は draft が消えている
        resp = client.get(
            "/api/admin/cartridges/particle_physics/atlas/skeleton", headers=teacher_headers
        )
        assert resp.json()["draft"] is None

    def test_invalid_draft_is_rejected(self, client, teacher_headers, sandbox_cartridges):
        bad = _sample_draft_dict()
        bad["atlas_skeleton"]["regions"][0]["layout"]["x"] = 5.0
        resp = client.put(
            "/api/admin/cartridges/particle_physics/atlas/skeleton/draft",
            headers=teacher_headers,
            json={"skeleton": bad},
        )
        assert resp.status_code == 422

    def test_freeze_without_draft_is_404(self, client, teacher_headers, sandbox_cartridges):
        resp = client.post(
            "/api/admin/cartridges/particle_physics/atlas/skeleton/freeze",
            headers=teacher_headers,
            json={"version": "2027.9"},
        )
        assert resp.status_code == 404

    def test_generate_conflicts_with_existing_draft(
        self, client, teacher_headers, sandbox_cartridges, monkeypatch
    ):
        # 既に draft がある状態で force なし生成 → 409 (一度だけ実行の担保)
        client.put(
            "/api/admin/cartridges/particle_physics/atlas/skeleton/draft",
            headers=teacher_headers,
            json={"skeleton": _sample_draft_dict()},
        )
        resp = client.post(
            "/api/admin/cartridges/particle_physics/atlas/skeleton/generate",
            headers=teacher_headers,
            json={"force": False},
        )
        assert resp.status_code == 409


@_skip_no_fastapi
class TestLearnerVisibility:
    def test_learner_gets_frozen_skeleton(self, client, student_headers, sandbox_cartridges):
        resp = client.get("/api/learning/atlas/particle_physics/skeleton", headers=student_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["skeleton_version"]
        assert data["regions"]
        # 学習者向けビューには status / reviewed フラグの生値は出ない
        assert "status" not in data

    def test_draft_only_cartridge_is_hidden_from_learner(
        self, client, student_headers, teacher_headers, sandbox_cartridges
    ):
        """draft しかないカートリッジは学習者から見えない (受け入れ条件3)。"""
        # 同梱凍結版を除去し、draft だけを置く
        frozen_path = sandbox_cartridges / "particle_physics" / "atlas" / "skeleton.yaml"
        frozen_path.unlink()
        from core import cartridges as cartridges_module

        cartridges_module.clear_cache()
        resp = client.put(
            "/api/admin/cartridges/particle_physics/atlas/skeleton/draft",
            headers=teacher_headers,
            json={"skeleton": _sample_draft_dict()},
        )
        assert resp.status_code == 200

        resp = client.get("/api/learning/atlas/particle_physics/skeleton", headers=student_headers)
        assert resp.status_code == 404
        # 管理側からは draft が見える (レビュー導線)
        resp = client.get(
            "/api/admin/cartridges/particle_physics/atlas/skeleton", headers=teacher_headers
        )
        assert resp.json()["draft"] is not None

    def test_unauthenticated_request_is_rejected(self, client, sandbox_cartridges):
        resp = client.get("/api/learning/atlas/particle_physics/skeleton")
        assert resp.status_code in (401, 403)
