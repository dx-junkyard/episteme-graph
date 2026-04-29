"""カートリッジAPI (Issue #176) の結合テスト。"""

from __future__ import annotations

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
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


@pytest.fixture
def teacher_headers():
    import jwt

    payload = {
        "sub": "test-teacher-id",
        "role": "TEACHER",
        "username": "teacher1",
        "email": "teacher1@test.com",
    }
    token = jwt.encode(payload, "test-secret-key", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@_skip_no_fastapi
class TestCartridgesAPI:
    def test_list_cartridges_returns_particle_physics(self, client, teacher_headers):
        resp = client.get("/api/admin/cartridges", headers=teacher_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        ids = [item["cartridge_id"] for item in data]
        assert "particle_physics" in ids

    def test_get_particle_physics_full(self, client, teacher_headers):
        resp = client.get("/api/admin/cartridges/particle_physics", headers=teacher_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["cartridge_id"] == "particle_physics"
        assert "ontology" in data
        assert "component_types" in data
        assert "maturity_levels" in data
        assert any(c["id"] == "Theory" for c in data["ontology"]["concept_types"])

    def test_get_ontology_endpoint(self, client, teacher_headers):
        resp = client.get(
            "/api/admin/cartridges/particle_physics/ontology", headers=teacher_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["concept_types"], list)
        ids = [c["id"] for c in data["concept_types"]]
        assert "Theory" in ids
        assert "Observable" in ids

    def test_get_component_types(self, client, teacher_headers):
        resp = client.get(
            "/api/admin/cartridges/particle_physics/component-types", headers=teacher_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        ids = [c["id"] for c in data["component_types"]]
        assert "PaperClaimComponent" in ids
        assert "DomainTheoryComponent" in ids

    def test_get_maturity_levels(self, client, teacher_headers):
        resp = client.get(
            "/api/admin/cartridges/particle_physics/maturity-levels", headers=teacher_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        level_ids = [m["id"] for m in data["maturity_levels"]]
        source_ids = [m["id"] for m in data["maturity_sources"]]
        assert "paper_claim" in level_ids
        assert "canonical" in level_ids
        assert "llm_proposed" in source_ids
        assert "teacher_reviewed" in source_ids

    def test_get_support_statuses(self, client, teacher_headers):
        resp = client.get(
            "/api/admin/cartridges/particle_physics/support-statuses", headers=teacher_headers
        )
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()["support_statuses"]]
        assert "source_backed" in ids
        assert "domain_inferred" in ids

    def test_get_relation_types(self, client, teacher_headers):
        resp = client.get(
            "/api/admin/cartridges/particle_physics/relation-types", headers=teacher_headers
        )
        assert resp.status_code == 200
        ids = [r["id"] for r in resp.json()["relation_types"]]
        assert "REQUIRES" in ids

    def test_unknown_cartridge_returns_404(self, client, teacher_headers):
        resp = client.get("/api/admin/cartridges/no_such", headers=teacher_headers)
        assert resp.status_code == 404

    def test_unauthorized_without_token(self, client):
        resp = client.get("/api/admin/cartridges")
        assert resp.status_code in (401, 403)
