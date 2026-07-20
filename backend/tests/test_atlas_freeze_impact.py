"""凍結時の影響プレビュー (freeze-impact, §4.4) のルートレベル結合テスト。

対象: `backend/api/routes/atlas.py`
  - `GET /api/admin/cartridges/{cartridge_id}/atlas/freeze-impact` (新設)
  - `POST /api/admin/cartridges/{cartridge_id}/atlas/skeleton/freeze` のレスポンスへの
    `impact` 同梱 + 凍結時通知 (§3.4, best-effort)

正本: docs/features/atlas_binding_lifecycle_design.md。

core 層 (`core/atlas_lifecycle.py::compute_freeze_impact` / `notify_atlas_event`) は
実装済み・変更不可。ここではルート層の配線 (呼び出し・レスポンス整形・通知の
best-effort 性) のみを検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import atlas  # noqa: E402
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
    backend_dir = str(Path(__file__).resolve().parent.parent)
    api_dir = str(Path(__file__).resolve().parent.parent / "api")
    src_dir = str(Path(__file__).resolve().parent.parent.parent / "src")
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


@pytest.fixture(autouse=True)
def skeleton_db(monkeypatch):
    """migration 027: atlas_skeletons のインメモリフェイクを DB として注入する。"""
    import core.postgres as postgres_module

    fake = AtlasSkeletonTableFake()
    monkeypatch.setattr(postgres_module, "get_session", make_session_factory(fake))
    return fake


@pytest.fixture(autouse=True)
def _no_bundled_cartridges(monkeypatch):
    """実カートリッジ (particle_physics 等) の同梱骨格をテストの対象外にする。"""
    import core.cartridges as cartridges_module

    monkeypatch.setattr(cartridges_module, "list_cartridges", lambda: [])
    cartridges_module.clear_cache()
    yield
    cartridges_module.clear_cache()


TEACHER_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TEACHER_ID = "33333333-3333-3333-3333-333333333333"


def _skeleton(
    domain_key: str = "domain_a",
    status: str = "frozen",
    version: str = "2026.1",
    concept_ids: tuple[str, ...] = ("c1", "c2"),
    region_ids: tuple[str, ...] = ("r1",),
) -> atlas.AtlasSkeleton:
    concepts = tuple(
        atlas.SkeletonConcept(id=cid, label=cid, layout=atlas.ConceptLayout(x=0.5, y=0.5))
        for cid in concept_ids
    )
    regions = []
    for i, rid in enumerate(region_ids):
        regions.append(
            atlas.SkeletonRegion(
                id=rid,
                label=rid,
                layout=atlas.RegionLayout(x=0.1, y=0.1, w=0.4, h=0.4),
                concepts=concepts if i == 0 else (),
            )
        )
    return atlas.AtlasSkeleton(
        cartridge=domain_key,
        status=status,
        version=version if status == "frozen" else "",
        generated_by="model:test",
        reviewed_by=("faculty:t",) if status == "frozen" else (),
        changelog=(atlas.ChangelogEntry(version=version, note="t"),) if status == "frozen" else (),
        regions=tuple(regions),
        edges=(),
        concept_bindings=(),
    )


def _seed_frozen(skeleton_db, domain_key="domain_a", **kwargs) -> None:
    from core import atlas_store

    atlas_store.insert_frozen(skeleton_db, domain_key, _skeleton(domain_key, status="frozen", **kwargs))


def _seed_draft(skeleton_db, domain_key="domain_a", **kwargs) -> None:
    from core import atlas_store

    atlas_store.save_draft(
        skeleton_db,
        domain_key,
        _skeleton(domain_key, status="draft", **kwargs),
        expected_revision=None,
    )


def _seed_course(skeleton_db, course_id="c1", owner=TEACHER_ID, data=None) -> None:
    skeleton_db.courses[course_id] = {
        "user_id": owner,
        "data": data if data is not None else {"title": "テストコース", "topics": []},
    }


@_skip_no_fastapi
class TestFreezeImpactEndpoint:
    def test_requires_teacher(self, client, student_headers, skeleton_db):
        _seed_draft(skeleton_db)
        resp = client.get(
            "/api/admin/cartridges/domain_a/atlas/freeze-impact", headers=student_headers
        )
        assert resp.status_code == 403

    def test_no_draft_is_404(self, client, teacher_headers, skeleton_db):
        resp = client.get(
            "/api/admin/cartridges/domain_a/atlas/freeze-impact", headers=teacher_headers
        )
        assert resp.status_code == 404

    def test_first_freeze_has_no_removed(self, client, teacher_headers, skeleton_db):
        """現行凍結版が無ければ (初回凍結) removed は必ず空。"""
        _seed_draft(skeleton_db, concept_ids=("c1", "c2"))
        resp = client.get(
            "/api/admin/cartridges/domain_a/atlas/freeze-impact", headers=teacher_headers
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["cartridge_id"] == "domain_a"
        assert data["frozen_version"] == ""
        assert data["removed_node_ids"] == []
        assert set(data["added_node_ids"]) == {"c1", "c2", "r1"}
        assert data["affected_courses"] == []

    def test_removed_and_affected_courses_match(self, client, teacher_headers, skeleton_db):
        """現行凍結版から draft で消える node_id と、それにバインド中のコースが突合する。"""
        _seed_frozen(skeleton_db, concept_ids=("c1", "c2"), region_ids=("r1",))
        _seed_draft(skeleton_db, concept_ids=("c2",), region_ids=("r1",))
        _seed_course(
            skeleton_db,
            "course_hit",
            data={
                "title": "対応が外れるコース",
                "cartridge_id": "domain_a",
                "topics": [
                    {"id": "t1", "title": "topic1", "atlas_node_id": "c1"},
                    {"id": "t2", "title": "topic2", "atlas_node_id": "c2"},
                ],
            },
        )
        _seed_course(
            skeleton_db,
            "course_miss",
            data={
                "title": "無関係コース",
                "cartridge_id": "domain_a",
                "topics": [{"id": "t3", "title": "topic3", "atlas_node_id": "c2"}],
            },
        )
        resp = client.get(
            "/api/admin/cartridges/domain_a/atlas/freeze-impact", headers=teacher_headers
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["frozen_version"] == "2026.1"
        assert data["removed_node_ids"] == ["c1"]
        assert len(data["affected_courses"]) == 1
        affected = data["affected_courses"][0]
        assert affected["course_id"] == "course_hit"
        assert affected["topics"] == [{"topic_id": "t1", "title": "topic1", "atlas_node_id": "c1"}]

    def test_no_removed_when_draft_matches_frozen(self, client, teacher_headers, skeleton_db):
        _seed_frozen(skeleton_db, concept_ids=("c1", "c2"), region_ids=("r1",))
        _seed_draft(skeleton_db, concept_ids=("c1", "c2"), region_ids=("r1",))
        resp = client.get(
            "/api/admin/cartridges/domain_a/atlas/freeze-impact", headers=teacher_headers
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["removed_node_ids"] == []
        assert data["affected_courses"] == []


@_skip_no_fastapi
class TestFreezeResponseIncludesImpact:
    def test_first_freeze_response_has_empty_impact(self, client, teacher_headers, skeleton_db):
        _seed_draft(skeleton_db, concept_ids=("c1", "c2"))
        resp = client.post(
            "/api/admin/cartridges/domain_a/atlas/skeleton/freeze",
            headers=teacher_headers,
            json={"version": "2026.1", "note": "初回凍結"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "impact" in body
        assert body["impact"]["removed_node_ids"] == []
        assert body["frozen"]["skeleton"]["status"] == "frozen"

    def test_refreeze_response_reports_removed_and_affected(
        self, client, teacher_headers, skeleton_db
    ):
        _seed_frozen(skeleton_db, concept_ids=("c1", "c2"), region_ids=("r1",))
        _seed_course(
            skeleton_db,
            "course_hit",
            data={
                "title": "対応が外れるコース",
                "cartridge_id": "domain_a",
                "topics": [{"id": "t1", "title": "topic1", "atlas_node_id": "c1"}],
            },
        )
        _seed_draft(skeleton_db, concept_ids=("c2",), region_ids=("r1",))
        resp = client.post(
            "/api/admin/cartridges/domain_a/atlas/skeleton/freeze",
            headers=teacher_headers,
            json={"version": "2026.2", "note": "改版"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["impact"]["removed_node_ids"] == ["c1"]
        assert body["impact"]["frozen_version"] == "2026.1"
        assert len(body["impact"]["affected_courses"]) == 1
        assert body["impact"]["affected_courses"][0]["course_id"] == "course_hit"


@_skip_no_fastapi
class TestFreezeNotification:
    def test_freeze_notifies_editor_excluding_actor(
        self, client, teacher_headers, skeleton_db, monkeypatch
    ):
        """凍結時に関係教員 (骨格編集履歴のある教員) へ通知され、actor は除外される (§3.4)。

        `cross_layer_notify.notify_user` は現在 ``core.postgres.get_session()``
        (モジュール属性経由) でセッションを取るため monkeypatch が効くが、
        本テストの関心は recipient 解決と route の配線なので、配送本体
        (`notify_user`) を直接 monkeypatch して検証する。
        """
        from core import atlas_store
        from core.status import cross_layer_notify as cross_layer_notify_module

        delivered_to: list[str] = []

        def _fake_notify_user(recipient_id, kind, entity_type, entity_id, payload=None):
            delivered_to.append(recipient_id)
            return True

        monkeypatch.setattr(cross_layer_notify_module, "notify_user", _fake_notify_user)

        atlas_store.insert_frozen(
            skeleton_db, "domain_a", _skeleton("domain_a"), user_id=OTHER_TEACHER_ID
        )
        _seed_draft(skeleton_db, concept_ids=("c1", "c2"))
        resp = client.post(
            "/api/admin/cartridges/domain_a/atlas/skeleton/freeze",
            headers=teacher_headers,  # actor = TEACHER_ID
            json={"version": "2026.2", "note": ""},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["notified"] == 1
        assert delivered_to == [OTHER_TEACHER_ID]

    def test_notify_call_receives_expected_kind_and_payload(
        self, client, teacher_headers, skeleton_db, monkeypatch
    ):
        """notify_atlas_event を monkeypatch し、kind・payload・actor_id を検証する。"""
        import routes.atlas as route_mod

        calls: list[dict] = []

        def _fake_notify(session, domain_key, kind, payload=None, *, actor_id=None):
            calls.append(
                {
                    "domain_key": domain_key,
                    "kind": kind,
                    "payload": payload,
                    "actor_id": actor_id,
                }
            )
            return 0

        monkeypatch.setattr(route_mod.atlas_lifecycle, "notify_atlas_event", _fake_notify)

        _seed_frozen(skeleton_db, concept_ids=("c1", "c2"), region_ids=("r1",))
        _seed_draft(skeleton_db, concept_ids=("c2",), region_ids=("r1",))
        resp = client.post(
            "/api/admin/cartridges/domain_a/atlas/skeleton/freeze",
            headers=teacher_headers,
            json={"version": "2026.2", "note": ""},
        )
        assert resp.status_code == 200, resp.text
        assert len(calls) == 1
        call = calls[0]
        assert call["domain_key"] == "domain_a"
        assert call["kind"] == "atlas_skeleton_frozen"
        assert call["payload"]["domain_key"] == "domain_a"
        assert call["payload"]["version"] == "2026.2"
        assert call["payload"]["removed_node_count"] == 1
        assert call["payload"]["affected_course_count"] == 0
        assert call["actor_id"] == TEACHER_ID

    def test_notify_failure_does_not_fail_freeze(
        self, client, teacher_headers, skeleton_db, monkeypatch
    ):
        """通知が例外を投げても凍結自体は成功する (非致命, §3.4)。"""
        import routes.atlas as route_mod

        def _boom(*a, **k):
            raise RuntimeError("notification backend down")

        monkeypatch.setattr(route_mod.atlas_lifecycle, "notify_atlas_event", _boom)

        _seed_draft(skeleton_db, concept_ids=("c1", "c2"))
        resp = client.post(
            "/api/admin/cartridges/domain_a/atlas/skeleton/freeze",
            headers=teacher_headers,
            json={"version": "2026.1", "note": ""},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["notified"] == 0
        # 凍結自体は成功しているため DB に反映されている
        frozen_rows = [r for r in skeleton_db.skeleton_rows if r["status"] == "frozen"]
        assert [r["version"] for r in frozen_rows] == ["2026.1"]
