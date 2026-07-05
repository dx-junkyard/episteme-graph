"""分野の地図 — 骨格レビュー/凍結 API (Issue A-2 / A-3, migration 027 で DB 管理) の結合テスト。"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.fixtures.atlas_skeletons_fake import (  # noqa: E402
    AtlasSkeletonTableFake,
    make_session_factory,
)

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


@pytest.fixture(autouse=True)
def skeleton_db(monkeypatch):
    """migration 027: atlas_skeletons のインメモリフェイクを DB として注入する。"""
    import core.postgres as postgres_module

    fake = AtlasSkeletonTableFake()
    monkeypatch.setattr(postgres_module, "get_session", make_session_factory(fake))
    return fake


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


@_skip_no_fastapi
class TestOptimisticLock:
    """migration 027: draft の同時編集は楽観ロック (revision 照合) で保護される。"""

    def _put(self, client, headers, revision):
        return client.put(
            "/api/admin/cartridges/particle_physics/atlas/skeleton/draft",
            headers=headers,
            json={"skeleton": _sample_draft_dict(), "revision": revision},
        )

    def test_save_returns_revision_and_bumps(self, client, teacher_headers, sandbox_cartridges):
        resp = self._put(client, teacher_headers, None)  # 新規作成
        assert resp.status_code == 200, resp.text
        assert resp.json()["draft"]["revision"] == 1
        resp = self._put(client, teacher_headers, 1)
        assert resp.status_code == 200
        assert resp.json()["draft"]["revision"] == 2

    def test_stale_revision_is_409(self, client, teacher_headers, sandbox_cartridges):
        self._put(client, teacher_headers, None)   # rev 1
        self._put(client, teacher_headers, 1)      # rev 2
        resp = self._put(client, teacher_headers, 1)  # 他の教員が rev1 のまま保存
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["current_revision"] == 2

    def test_missing_revision_with_existing_draft_is_409(
        self, client, teacher_headers, sandbox_cartridges
    ):
        self._put(client, teacher_headers, None)
        resp = self._put(client, teacher_headers, None)
        assert resp.status_code == 409

    def test_frozen_survives_in_db(self, client, teacher_headers, sandbox_cartridges, skeleton_db):
        """凍結版が DB に永続される (ファイル焼き込み・再ビルド消失問題の解消)。"""
        self._put(client, teacher_headers, None)
        resp = client.post(
            "/api/admin/cartridges/particle_physics/atlas/skeleton/freeze",
            headers=teacher_headers,
            json={"version": "2027.1", "note": "db"},
        )
        assert resp.status_code == 200, resp.text
        frozen_rows = [r for r in skeleton_db.skeleton_rows if r["status"] == "frozen"]
        assert [r["version"] for r in frozen_rows] == ["2027.1"]
        # 学習者向けは DB の凍結版が返る
        resp = client.get(
            "/api/learning/atlas/particle_physics/skeleton", headers=_headers("STUDENT")
        )
        assert resp.status_code == 200
        assert resp.json()["skeleton_version"] == "2027.1"


@_skip_no_fastapi
class TestDomainsAndNewDomain:
    """migration 027: カートリッジファイルの無い新分野を DB 骨格だけで成立させる。"""

    def test_domains_lists_bundled(self, client, teacher_headers, sandbox_cartridges):
        resp = client.get("/api/admin/atlas/domains", headers=teacher_headers)
        assert resp.status_code == 200
        domains = {d["domain_key"]: d for d in resp.json()["domains"]}
        assert "particle_physics" in domains
        assert domains["particle_physics"]["source"] == "bundled"

    def test_new_domain_draft_and_freeze_without_cartridge_files(
        self, client, teacher_headers, sandbox_cartridges
    ):
        # カートリッジディレクトリが存在しない domain に draft を保存して凍結する
        resp = client.put(
            "/api/admin/cartridges/modified_gravity/atlas/skeleton/draft",
            headers=teacher_headers,
            json={"skeleton": _sample_draft_dict("modified_gravity")},
        )
        assert resp.status_code == 200, resp.text
        resp = client.post(
            "/api/admin/cartridges/modified_gravity/atlas/skeleton/freeze",
            headers=teacher_headers,
            json={"version": "2026.1", "note": "新分野"},
        )
        assert resp.status_code == 200, resp.text
        # 学習者にも見える (ファイルデプロイ不要)
        resp = client.get(
            "/api/learning/atlas/modified_gravity/skeleton", headers=_headers("STUDENT")
        )
        assert resp.status_code == 200
        # domains 一覧にも DB 由来として現れる
        resp = client.get("/api/admin/atlas/domains", headers=teacher_headers)
        domains = {d["domain_key"]: d for d in resp.json()["domains"]}
        assert domains["modified_gravity"]["source"] == "db"
        assert domains["modified_gravity"]["frozen_version"] == "2026.1"

    def test_generate_for_unknown_domain_without_meta_is_404(
        self, client, teacher_headers, sandbox_cartridges
    ):
        resp = client.post(
            "/api/admin/cartridges/no_such_domain/atlas/skeleton/generate",
            headers=teacher_headers,
            json={"force": False},
        )
        assert resp.status_code == 404


TEACHER_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TEACHER_ID = "33333333-3333-3333-3333-333333333333"


@_skip_no_fastapi
class TestCourseAtlasBinding:
    """S2: コース ⇄ 地図バインディング (提案は決定論・教員が承認して保存)。"""

    def _seed_course(self, skeleton_db, owner=TEACHER_ID):
        skeleton_db.courses["c1"] = {
            "user_id": owner,
            "data": {
                "title": "テストコース",
                "topics": [
                    {"id": "t0", "title": "演算子積展開"},
                    {"id": "t1", "title": "全く無関係な題目zzz"},
                ],
            },
        }

    def test_propose_requires_owner_or_admin(
        self, client, teacher_headers, sandbox_cartridges, skeleton_db
    ):
        self._seed_course(skeleton_db, owner=OTHER_TEACHER_ID)
        resp = client.post(
            "/api/admin/courses/c1/atlas-binding/propose", headers=teacher_headers
        )
        assert resp.status_code == 403

    def test_propose_returns_deterministic_coverage(
        self, client, teacher_headers, sandbox_cartridges, skeleton_db
    ):
        self._seed_course(skeleton_db)
        resp = client.post(
            "/api/admin/courses/c1/atlas-binding/propose", headers=teacher_headers
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["recommended"] == "particle_physics"
        proposal = data["proposals"][0]
        assert proposal["domain_key"] == "particle_physics"
        assert proposal["matched"] == 1  # 演算子積展開 のみラベル一致
        by_topic = {b["topic_id"]: b for b in proposal["bindings"]}
        assert by_topic["t0"]["atlas_node_id"] == "ope"
        assert by_topic["t1"]["atlas_node_id"] == ""
        assert proposal["concepts"]  # UI の選択肢

    def test_save_binding_updates_course(
        self, client, teacher_headers, sandbox_cartridges, skeleton_db
    ):
        self._seed_course(skeleton_db)
        resp = client.put(
            "/api/admin/courses/c1/atlas-binding",
            headers=teacher_headers,
            json={
                "cartridge_id": "particle_physics",
                "topic_bindings": [
                    {"topic_id": "t0", "atlas_node_id": "ope"},
                    {"topic_id": "t1", "atlas_node_id": "no_such_node"},
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["bindings_applied"] == 1
        assert body["bindings_skipped"] == ["t1"]
        data = skeleton_db.courses["c1"]["data"]
        assert data["cartridge_id"] == "particle_physics"
        topics = {t["id"]: t for t in data["topics"]}
        assert topics["t0"]["atlas_node_id"] == "ope"
        assert "atlas_node_id" not in topics["t1"]
        # 監査記録 (theory_review_events) が残る
        assert any(
            e.get("entity_type") == "atlas_binding" for e in skeleton_db.review_events
        )

    def test_save_unknown_domain_is_422(
        self, client, teacher_headers, sandbox_cartridges, skeleton_db
    ):
        self._seed_course(skeleton_db)
        resp = client.put(
            "/api/admin/courses/c1/atlas-binding",
            headers=teacher_headers,
            json={"cartridge_id": "no_such_domain", "topic_bindings": []},
        )
        assert resp.status_code == 422

    def test_unbind_clears_cartridge(
        self, client, teacher_headers, sandbox_cartridges, skeleton_db
    ):
        self._seed_course(skeleton_db)
        skeleton_db.courses["c1"]["data"]["cartridge_id"] = "particle_physics"
        resp = client.put(
            "/api/admin/courses/c1/atlas-binding",
            headers=teacher_headers,
            json={"cartridge_id": "", "topic_bindings": [{"topic_id": "t0", "atlas_node_id": ""}]},
        )
        assert resp.status_code == 200
        assert "cartridge_id" not in skeleton_db.courses["c1"]["data"]
