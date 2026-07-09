"""分野の地図 — 骨格 DB ストア (core/atlas_store.py, migration 027) のユニットテスト。

- draft の楽観ロック (revision 照合・DraftRevisionConflict)
- 凍結版の履歴保持と現行版の選択
- DB 優先・同梱ファイルへのフォールバック (load_learner_skeleton)
- 同梱骨格の一度きり取り込み (import_bundled_skeletons の冪等性)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import atlas  # noqa: E402
from core import atlas_store  # noqa: E402
from tests.fixtures.atlas_skeletons_fake import AtlasSkeletonTableFake  # noqa: E402


def _skeleton(status: str = "draft", version: str = "", cartridge: str = "dom") -> atlas.AtlasSkeleton:
    changelog = (
        (atlas.ChangelogEntry(version=version, note="t"),) if status == "frozen" else ()
    )
    return atlas.AtlasSkeleton(
        cartridge=cartridge,
        status=status,
        version=version,
        generated_by="model:test",
        reviewed_by=("faculty:t",) if status == "frozen" else (),
        changelog=changelog,
        regions=(
            atlas.SkeletonRegion(
                id="r1",
                label="領域",
                layout=atlas.RegionLayout(x=0.1, y=0.1, w=0.4, h=0.4),
                concepts=(
                    atlas.SkeletonConcept(
                        id="c1", label="概念", layout=atlas.ConceptLayout(x=0.5, y=0.5)
                    ),
                ),
            ),
        ),
        edges=(),
        concept_bindings=(),
    )


@pytest.fixture
def db():
    return AtlasSkeletonTableFake()


class TestDraftOptimisticLock:
    def test_create_and_update(self, db):
        rev = atlas_store.save_draft(db, "dom", _skeleton(), expected_revision=None)
        assert rev == 1
        rev = atlas_store.save_draft(db, "dom", _skeleton(), expected_revision=1)
        assert rev == 2
        row = atlas_store.load_draft(db, "dom")
        assert row["revision"] == 2
        assert row["skeleton"].status == "draft"

    def test_create_conflicts_when_draft_exists(self, db):
        atlas_store.save_draft(db, "dom", _skeleton(), expected_revision=None)
        with pytest.raises(atlas_store.DraftRevisionConflict) as exc:
            atlas_store.save_draft(db, "dom", _skeleton(), expected_revision=None)
        assert exc.value.current_revision == 1

    def test_stale_revision_conflicts(self, db):
        atlas_store.save_draft(db, "dom", _skeleton(), expected_revision=None)  # rev1
        atlas_store.save_draft(db, "dom", _skeleton(), expected_revision=1)     # rev2
        with pytest.raises(atlas_store.DraftRevisionConflict) as exc:
            atlas_store.save_draft(db, "dom", _skeleton(), expected_revision=1)
        assert exc.value.current_revision == 2

    def test_delete_draft(self, db):
        atlas_store.save_draft(db, "dom", _skeleton(), expected_revision=None)
        atlas_store.delete_draft(db, "dom")
        assert atlas_store.load_draft(db, "dom") is None


class TestFrozenVersions:
    def test_insert_requires_frozen(self, db):
        with pytest.raises(ValueError):
            atlas_store.insert_frozen(db, "dom", _skeleton(status="draft"))

    def test_latest_frozen_wins(self, db):
        atlas_store.insert_frozen(db, "dom", _skeleton("frozen", "2026.1"))
        atlas_store.insert_frozen(db, "dom", _skeleton("frozen", "2026.2"))
        skeleton = atlas_store.load_frozen_skeleton(db, "dom")
        assert skeleton.version == "2026.2"
        # 履歴は残る
        assert len([r for r in db.skeleton_rows if r["status"] == "frozen"]) == 2

    def test_duplicate_version_rejected(self, db):
        atlas_store.insert_frozen(db, "dom", _skeleton("frozen", "2026.1"))
        with pytest.raises(Exception):
            atlas_store.insert_frozen(db, "dom", _skeleton("frozen", "2026.1"))


class TestLearnerSkeletonFallback:
    def test_db_first(self, db, monkeypatch):
        atlas_store.insert_frozen(db, "dom", _skeleton("frozen", "2026.5"))
        monkeypatch.setattr(
            atlas_store, "_bundled_skeleton", lambda key: _skeleton("frozen", "2020.1")
        )
        skeleton = atlas_store.load_learner_skeleton("dom", db)
        assert skeleton.version == "2026.5"

    def test_fallback_to_bundled(self, db, monkeypatch):
        monkeypatch.setattr(
            atlas_store, "_bundled_skeleton", lambda key: _skeleton("frozen", "2020.1")
        )
        skeleton = atlas_store.load_learner_skeleton("dom", db)
        assert skeleton.version == "2020.1"

    def test_none_when_nowhere(self, db, monkeypatch):
        monkeypatch.setattr(atlas_store, "_bundled_skeleton", lambda key: None)
        assert atlas_store.load_learner_skeleton("dom", db) is None


class TestListDomains:
    def test_merges_db_and_bundled(self, db, monkeypatch):
        atlas_store.insert_frozen(db, "dom_db", _skeleton("frozen", "2026.1"))
        atlas_store.save_draft(db, "dom_draft", _skeleton(), expected_revision=None)

        class _Summary:
            cartridge_id = "bundled_dom"

        import core.cartridges as cartridges_module

        monkeypatch.setattr(cartridges_module, "list_cartridges", lambda: [_Summary()])
        monkeypatch.setattr(
            atlas_store, "_bundled_skeleton", lambda key: _skeleton("frozen", "2020.1")
        )
        domains = {d["domain_key"]: d for d in atlas_store.list_domains(db)}
        assert domains["dom_db"]["frozen_version"] == "2026.1"
        assert domains["dom_db"]["source"] == "db"
        assert domains["dom_draft"]["has_draft"] is True
        assert domains["bundled_dom"]["source"] == "bundled"


class TestImportBundled:
    def test_idempotent_import(self, db, monkeypatch):
        class _Summary:
            cartridge_id = "dom"

        import core.cartridges as cartridges_module

        monkeypatch.setattr(cartridges_module, "list_cartridges", lambda: [_Summary()])
        monkeypatch.setattr(
            atlas_store, "_bundled_skeleton", lambda key: _skeleton("frozen", "2026.1")
        )
        assert atlas_store.import_bundled_skeletons(db) == 1
        # 2回目は取り込まない (冪等)
        assert atlas_store.import_bundled_skeletons(db) == 0
        assert len(db.skeleton_rows) == 1
