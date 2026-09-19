"""分野マップのノード版間対応 — API 層（``routes/atlas.py`` の freeze-impact / freeze）。

正本: ``docs/features/atlas_node_correspondence_design.md`` §5（NC1 / NC3 / NC7）。

検証するもの:

1. ``GET .../atlas/freeze-impact`` の ``correspondence``（既存キーは不変）
2. ``POST .../atlas/skeleton/freeze`` の ``id_migrations``（``{from,to}`` alias・版の付与・
   draft 由来分の保持・骨格外 id と from 重複の 422 事実文）
3. 確定文脈（``decision_context``）— 候補ありのときだけ別行で記帳し、候補ゼロなら記帳しない
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import atlas  # noqa: E402
from core import decision_context  # noqa: E402
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

TEACHER_ID = "11111111-1111-1111-1111-111111111111"
DOMAIN = "domain_a"
_BASE = f"/api/admin/cartridges/{DOMAIN}/atlas"


@pytest.fixture
def client():
    if not _HAS_FASTAPI:
        pytest.skip("FastAPI not installed")
    backend_dir = str(Path(__file__).resolve().parent.parent)
    api_dir = str(Path(__file__).resolve().parent.parent / "api")
    src_dir = str(Path(__file__).resolve().parent.parent.parent / "src")
    for path in (backend_dir, api_dir, src_dir):
        if path not in sys.path:
            sys.path.insert(0, path)
    from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


def _headers(role: str = "TEACHER", sub: str = TEACHER_ID):
    import jwt

    token = jwt.encode(
        {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"},
        "test-secret-key",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def teacher_headers():
    return _headers()


@pytest.fixture(autouse=True)
def skeleton_db(monkeypatch):
    import core.postgres as postgres_module

    fake = AtlasSkeletonTableFake()
    monkeypatch.setattr(postgres_module, "get_session", make_session_factory(fake))
    return fake


@pytest.fixture(autouse=True)
def _no_bundled_cartridges(monkeypatch):
    import core.atlas_store as atlas_store_module
    import core.cartridges as cartridges_module

    monkeypatch.setattr(cartridges_module, "list_cartridges", lambda: [])
    monkeypatch.setattr(atlas_store_module, "_bundled_domain_keys", lambda: [])
    cartridges_module.clear_cache()
    yield
    cartridges_module.clear_cache()


@pytest.fixture(autouse=True)
def _no_side_channels(monkeypatch):
    """別名・レジストリ・通知・埋め込みの外部照会を切る（本テストの関心外）。"""
    from core.atlas_vectors import store as vector_store
    from core.library import registry as library_registry
    from core import atlas_lifecycle

    monkeypatch.setattr(
        vector_store, "confirmed_aliases_by_node", lambda _s, _k: {}
    )
    monkeypatch.setattr(
        library_registry, "list_node_links", lambda **kwargs: []
    )
    monkeypatch.setattr(atlas_lifecycle, "notify_atlas_event", lambda *a, **k: 0)


def _skeleton(
    *,
    status: str = "frozen",
    version: str = "2026.1",
    concepts=(("c_dm", "暗黒物質"),),
    id_migrations=(),
) -> atlas.AtlasSkeleton:
    return atlas.AtlasSkeleton(
        cartridge=DOMAIN,
        status=status,
        version=version if status == "frozen" else "",
        generated_by="model:test",
        reviewed_by=("faculty:t",) if status == "frozen" else (),
        changelog=(
            (atlas.ChangelogEntry(version=version, note="t"),) if status == "frozen" else ()
        ),
        regions=(
            atlas.SkeletonRegion(
                id="r_cosmo",
                label="宇宙論",
                layout=atlas.RegionLayout(x=0.1, y=0.1, w=0.4, h=0.4),
                concepts=tuple(
                    atlas.SkeletonConcept(
                        id=cid, label=label, layout=atlas.ConceptLayout(x=0.5, y=0.5)
                    )
                    for cid, label in concepts
                ),
            ),
        ),
        id_migrations=tuple(
            atlas.IdMigration(from_id=f, to_id=t, version=v) for f, t, v in id_migrations
        ),
    )


def _seed_frozen(db, **kwargs):
    from core import atlas_store

    atlas_store.insert_frozen(db, DOMAIN, _skeleton(status="frozen", **kwargs))


def _seed_draft(db, **kwargs):
    from core import atlas_store

    atlas_store.save_draft(
        db, DOMAIN, _skeleton(status="draft", **kwargs), expected_revision=None
    )


def _renamed_draft(db, **kwargs):
    """旧版と同じラベル・違う id の draft（候補が1件立つ状態）。"""
    _seed_draft(db, concepts=(("dark_matter", "暗黒物質"),), **kwargs)


@_skip_no_fastapi
class TestFreezeImpactCorrespondence:
    def test_existing_keys_are_unchanged_and_correspondence_is_added(
        self, client, teacher_headers, skeleton_db
    ):
        _seed_frozen(skeleton_db)
        _renamed_draft(skeleton_db)

        body = client.get(f"{_BASE}/freeze-impact", headers=teacher_headers).json()

        assert {
            "cartridge_id", "frozen_version", "removed_node_ids", "added_node_ids",
            "affected_courses", "facts",
        } <= set(body)
        assert body["removed_node_ids"] == ["c_dm"]
        assert body["added_node_ids"] == ["dark_matter"]

        correspondence = body["correspondence"]
        assert set(correspondence) == {
            "candidates", "alternatives", "unmatched_removed", "already_declared",
            "added_nodes", "facts",
        }
        assert [(c["from_id"], c["to_id"]) for c in correspondence["candidates"]] == [
            ("c_dm", "dark_matter")
        ]
        assert correspondence["candidates"][0]["via"] == "label"
        assert correspondence["candidates"][0]["justification"] == "lexical_match"

    def test_unmatched_removed_carries_labels(self, client, teacher_headers, skeleton_db):
        _seed_frozen(skeleton_db, concepts=(("c_dm", "暗黒物質"),))
        _seed_draft(skeleton_db, concepts=(("c_other", "まったく別の主題"),))

        correspondence = client.get(
            f"{_BASE}/freeze-impact", headers=teacher_headers
        ).json()["correspondence"]

        assert correspondence["candidates"] == []
        assert correspondence["unmatched_removed"] == [
            {"node_id": "c_dm", "label": "暗黒物質", "kind": "concept"}
        ]

    def test_first_freeze_has_an_empty_correspondence(
        self, client, teacher_headers, skeleton_db
    ):
        _seed_draft(skeleton_db)
        correspondence = client.get(
            f"{_BASE}/freeze-impact", headers=teacher_headers
        ).json()["correspondence"]
        assert correspondence["candidates"] == []
        assert correspondence["facts"] == []

    def test_alias_lookup_failure_degrades_to_label_route(
        self, client, teacher_headers, skeleton_db, monkeypatch
    ):
        """別名の照会が落ちても候補導出は続く（fail-soft）。"""
        from core.atlas_vectors import store as vector_store

        def _boom(*_a, **_k):
            raise RuntimeError("alias store down")

        monkeypatch.setattr(vector_store, "confirmed_aliases_by_node", _boom)
        _seed_frozen(skeleton_db)
        _renamed_draft(skeleton_db)

        response = client.get(f"{_BASE}/freeze-impact", headers=teacher_headers)
        assert response.status_code == 200
        assert response.json()["correspondence"]["candidates"][0]["to_id"] == "dark_matter"

    def test_requires_teacher(self, client, skeleton_db):
        _seed_draft(skeleton_db)
        response = client.get(f"{_BASE}/freeze-impact", headers=_headers("STUDENT"))
        assert response.status_code == 403


@_skip_no_fastapi
class TestFreezeAcceptsCorrespondence:
    def test_declared_pair_lands_in_the_new_versions_id_migrations(
        self, client, teacher_headers, skeleton_db
    ):
        _seed_frozen(skeleton_db)
        _renamed_draft(skeleton_db)

        response = client.post(
            f"{_BASE}/skeleton/freeze",
            headers=teacher_headers,
            json={
                "version": "2026.2",
                "id_migrations": [{"from": "c_dm", "to": "dark_matter"}],
            },
        )
        assert response.status_code == 200, response.text
        migrations = response.json()["frozen"]["skeleton"]["id_migrations"]
        assert migrations == [
            {"from": "c_dm", "to": "dark_matter", "version": "2026.2"}
        ]

    def test_field_names_are_accepted_too(self, client, teacher_headers, skeleton_db):
        _seed_frozen(skeleton_db)
        _renamed_draft(skeleton_db)
        response = client.post(
            f"{_BASE}/skeleton/freeze",
            headers=teacher_headers,
            json={
                "version": "2026.2",
                "id_migrations": [{"from_id": "c_dm", "to_id": "dark_matter"}],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["frozen"]["skeleton"]["id_migrations"][0]["from"] == "c_dm"

    def test_draft_declared_migrations_are_kept(self, client, teacher_headers, skeleton_db):
        """draft 由来の対応は保持され、新しい対応は **追加** される（NC4）。"""
        _seed_frozen(skeleton_db, concepts=(("c_dm", "暗黒物質"), ("c_gone", "消える")))
        _seed_draft(
            skeleton_db,
            concepts=(("dark_matter", "暗黒物質"), ("kept", "残る")),
            id_migrations=(("c_gone", "kept", "2026.2"),),
        )

        response = client.post(
            f"{_BASE}/skeleton/freeze",
            headers=teacher_headers,
            json={
                "version": "2026.2",
                "id_migrations": [{"from": "c_dm", "to": "dark_matter"}],
            },
        )
        assert response.status_code == 200, response.text
        pairs = {
            (m["from"], m["to"]) for m in response.json()["frozen"]["skeleton"]["id_migrations"]
        }
        assert pairs == {("c_gone", "kept"), ("c_dm", "dark_matter")}

    def test_without_id_migrations_the_behaviour_is_unchanged(
        self, client, teacher_headers, skeleton_db
    ):
        _seed_frozen(skeleton_db)
        _renamed_draft(skeleton_db)
        response = client.post(
            f"{_BASE}/skeleton/freeze", headers=teacher_headers, json={"version": "2026.2"}
        )
        assert response.status_code == 200, response.text
        assert "id_migrations" not in response.json()["frozen"]["skeleton"]
        assert response.json()["impact"]["correspondence"]["applied"] == []

    def test_applied_is_reported_with_labels(self, client, teacher_headers, skeleton_db):
        _seed_frozen(skeleton_db)
        _renamed_draft(skeleton_db)
        response = client.post(
            f"{_BASE}/skeleton/freeze",
            headers=teacher_headers,
            json={
                "version": "2026.2",
                "id_migrations": [{"from": "c_dm", "to": "dark_matter"}],
            },
        )
        applied = response.json()["impact"]["correspondence"]["applied"]
        assert applied == [
            {
                "from_id": "c_dm",
                "from_label": "暗黒物質",
                "to_id": "dark_matter",
                "to_label": "暗黒物質",
            }
        ]

    def test_unknown_from_is_422_with_a_plain_string_detail(
        self, client, teacher_headers, skeleton_db
    ):
        _seed_frozen(skeleton_db)
        _renamed_draft(skeleton_db)
        response = client.post(
            f"{_BASE}/skeleton/freeze",
            headers=teacher_headers,
            json={
                "version": "2026.2",
                "id_migrations": [{"from": "ghost", "to": "dark_matter"}],
            },
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert isinstance(detail, str) and "ghost" in detail

    def test_unknown_to_is_422(self, client, teacher_headers, skeleton_db):
        _seed_frozen(skeleton_db)
        _renamed_draft(skeleton_db)
        response = client.post(
            f"{_BASE}/skeleton/freeze",
            headers=teacher_headers,
            json={
                "version": "2026.2",
                "id_migrations": [{"from": "c_dm", "to": "ghost"}],
            },
        )
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], str)

    def test_duplicate_from_is_422(self, client, teacher_headers, skeleton_db):
        _seed_frozen(skeleton_db)
        _seed_draft(skeleton_db, concepts=(("a", "A"), ("b", "B")))
        response = client.post(
            f"{_BASE}/skeleton/freeze",
            headers=teacher_headers,
            json={
                "version": "2026.2",
                "id_migrations": [
                    {"from": "c_dm", "to": "a"},
                    {"from": "c_dm", "to": "b"},
                ],
            },
        )
        assert response.status_code == 422

    def test_rejected_body_does_not_freeze_anything(
        self, client, teacher_headers, skeleton_db
    ):
        """422 のとき版は増えず draft も残る（検証は凍結の前に済ませる）。"""
        _seed_frozen(skeleton_db)
        _renamed_draft(skeleton_db)
        before = len(skeleton_db.skeleton_rows)

        client.post(
            f"{_BASE}/skeleton/freeze",
            headers=teacher_headers,
            json={
                "version": "2026.2",
                "id_migrations": [{"from": "ghost", "to": "dark_matter"}],
            },
        )

        assert len(skeleton_db.skeleton_rows) == before
        from core import atlas_store

        assert atlas_store.load_draft(skeleton_db, DOMAIN) is not None


@_skip_no_fastapi
class TestFreezeCorrespondenceAudit:
    def _events(self, skeleton_db, action: str) -> list[dict]:
        import json

        out = []
        for event in skeleton_db.review_events:
            metadata = event.get("metadata")
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            if isinstance(metadata, dict) and metadata.get("action") == action:
                out.append(metadata)
        return out

    def test_correspondence_is_recorded_in_its_own_row(
        self, client, teacher_headers, skeleton_db
    ):
        _seed_frozen(skeleton_db)
        _renamed_draft(skeleton_db)

        client.post(
            f"{_BASE}/skeleton/freeze",
            headers=teacher_headers,
            json={
                "version": "2026.2",
                "id_migrations": [{"from": "c_dm", "to": "dark_matter"}],
            },
        )

        rows = self._events(skeleton_db, "node_correspondence")
        assert len(rows) == 1
        ctx = rows[0][decision_context.DECISION_CONTEXT_KEY]
        assert ctx["basis"] == decision_context.BASIS_ATLAS_NODE_CORRESPONDENCE
        assert ctx["presented"]["ids"] == ["c_dm|dark_matter"]
        assert ctx["applied"]["ids"] == ["c_dm|dark_matter"]
        assert ctx["presented_matches_applied"] is True
        assert ctx["alternatives_available"]
        # 凍結そのものの記帳は別行のまま（混ぜない）。
        assert len(self._events(skeleton_db, "freeze")) == 1

    def test_skipping_the_candidate_is_recorded_as_a_mismatch(
        self, client, teacher_headers, skeleton_db
    ):
        _seed_frozen(skeleton_db)
        _renamed_draft(skeleton_db)

        client.post(
            f"{_BASE}/skeleton/freeze", headers=teacher_headers, json={"version": "2026.2"}
        )

        ctx = self._events(skeleton_db, "node_correspondence")[0][
            decision_context.DECISION_CONTEXT_KEY
        ]
        assert ctx["presented"]["ids"] == ["c_dm|dark_matter"]
        assert ctx["applied"]["ids"] == []
        assert ctx["presented_matches_applied"] is False

    def test_no_candidates_means_no_decision_context_row(
        self, client, teacher_headers, skeleton_db
    ):
        """候補ゼロ = 判断の機会が無い → 記帳しない（NC3 / DC3）。"""
        _seed_frozen(skeleton_db)
        _seed_draft(skeleton_db, concepts=(("c_dm", "暗黒物質"),))

        client.post(
            f"{_BASE}/skeleton/freeze", headers=teacher_headers, json={"version": "2026.2"}
        )

        assert self._events(skeleton_db, "node_correspondence") == []
        assert len(self._events(skeleton_db, "freeze")) == 1

    def test_unmatched_removed_is_kept_in_the_metadata(
        self, client, teacher_headers, skeleton_db
    ):
        _seed_frozen(skeleton_db, concepts=(("c_dm", "暗黒物質"), ("c_gone", "消える")))
        _renamed_draft(skeleton_db)

        client.post(
            f"{_BASE}/skeleton/freeze",
            headers=teacher_headers,
            json={
                "version": "2026.2",
                "id_migrations": [{"from": "c_dm", "to": "dark_matter"}],
            },
        )

        metadata = self._events(skeleton_db, "node_correspondence")[0]
        assert metadata["unmatched_removed_node_ids"] == ["c_gone"]


@_skip_no_fastapi
class TestFrozenHistoryReadBack:
    def test_the_declared_pair_resolves_after_freeze(
        self, client, teacher_headers, skeleton_db
    ):
        """凍結した対応が、そのまま読み手の読み替え（NodeResolver）に効く。"""
        from core import atlas_correspondence, atlas_store

        _seed_frozen(skeleton_db)
        _renamed_draft(skeleton_db)
        client.post(
            f"{_BASE}/skeleton/freeze",
            headers=teacher_headers,
            json={
                "version": "2026.2",
                "id_migrations": [{"from": "c_dm", "to": "dark_matter"}],
            },
        )

        history = atlas_store.load_frozen_history(skeleton_db, DOMAIN)
        assert [s.version for s in history] == ["2026.1", "2026.2"]
        resolved = atlas_correspondence.build_node_resolver(history).resolve("c_dm")
        assert resolved["status"] == "migrated"
        assert resolved["current_node_id"] == "dark_matter"
        assert resolved["last_version"] == "2026.1"
