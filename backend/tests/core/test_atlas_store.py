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
        # 骨格専用バンドルドメイン (atlas_domains/) は本ケースの対象外にする
        # (実ディレクトリの astrophysics を数に混ぜない)。
        monkeypatch.setattr(atlas_store, "_bundled_domain_keys", lambda: [])
        monkeypatch.setattr(
            atlas_store, "_bundled_skeleton", lambda key: _skeleton("frozen", "2026.1")
        )
        assert atlas_store.import_bundled_skeletons(db) == 1
        # 2回目は取り込まない (冪等)
        assert atlas_store.import_bundled_skeletons(db) == 0
        assert len(db.skeleton_rows) == 1


class TestBundledDomainDirectory:
    """骨格専用バンドルドメイン (`backend/atlas_domains/<key>/`) のシード経路。

    正本: docs/features/knowledge_landscape_design.md §6.1。カートリッジ一式を持たない
    分野の骨格をファイルから取り込めること・`domain.json` は **行が無いときだけ**
    meta へ入ること (既存の表示名を上書きしない) を検証する。
    """

    @staticmethod
    def _write_domain(root: Path, key: str, *, version: str, meta: dict | None) -> None:
        import json as _json

        d = root / key
        d.mkdir(parents=True, exist_ok=True)
        atlas.write_skeleton(
            atlas.AtlasSkeleton(
                cartridge=key,
                status="frozen",
                version=version,
                generated_by="reference_map:test",
                reviewed_by=("faculty:t",),
                changelog=(atlas.ChangelogEntry(version=version, note="t"),),
                regions=(
                    atlas.SkeletonRegion(
                        id="r1",
                        label="領域",
                        layout=atlas.RegionLayout(x=0.1, y=0.1, w=0.4, h=0.4),
                        concepts=(),
                    ),
                ),
            ),
            d / "skeleton.yaml",
        )
        if meta is not None:
            (d / "domain.json").write_text(
                _json.dumps(meta, ensure_ascii=False), encoding="utf-8"
            )

    @pytest.fixture
    def domains_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(atlas_store, "ATLAS_BUNDLED_DOMAINS_DIR", tmp_path)
        import core.cartridges as cartridges_module

        monkeypatch.setattr(cartridges_module, "list_cartridges", lambda: [])
        return tmp_path

    def test_skeleton_is_read_from_domain_directory(self, domains_dir):
        self._write_domain(domains_dir, "astro_test", version="2026.9", meta=None)
        skeleton = atlas_store._bundled_skeleton("astro_test")
        assert skeleton is not None and skeleton.version == "2026.9"
        assert atlas_store._bundled_domain_keys() == ["astro_test"]

    def test_missing_and_invalid_files_are_fail_soft(self, domains_dir):
        assert atlas_store._bundled_skeleton("nope") is None
        # YAML/スキーマが壊れている → None (例外を投げず起動を止めない)
        (domains_dir / "broken").mkdir()
        (domains_dir / "broken" / "skeleton.yaml").write_text(
            "atlas_skeleton: {regions: [{id: r1, layout: {x: nope}}]}", encoding="utf-8"
        )
        assert atlas_store._bundled_skeleton("broken") is None
        # draft (未凍結・レビュー帰属なし) は読まない (LS6)
        self._write_domain(domains_dir, "unfrozen", version="2026.1", meta=None)
        (domains_dir / "unfrozen" / "skeleton.yaml").write_text(
            (domains_dir / "unfrozen" / "skeleton.yaml")
            .read_text(encoding="utf-8")
            .replace("status: frozen", "status: draft"),
            encoding="utf-8",
        )
        assert atlas_store._bundled_skeleton("unfrozen") is None

    def test_import_seeds_skeleton_and_domain_meta(self, db, domains_dir):
        self._write_domain(
            domains_dir, "astro_test", version="2026.9", meta={"name": "宇宙物理", "description": "地図"}
        )
        assert atlas_store.import_bundled_skeletons(db) == 1
        assert atlas_store.load_frozen_skeleton(db, "astro_test").version == "2026.9"
        assert db.domain_meta["astro_test"]["name"] == "宇宙物理"
        # 冪等: 2回目は骨格も meta も増やさない
        assert atlas_store.import_bundled_skeletons(db) == 0
        assert len(db.skeleton_rows) == 1

    def test_import_never_overwrites_existing_domain_meta(self, db, domains_dir):
        self._write_domain(
            domains_dir, "astro_test", version="2026.9", meta={"name": "宇宙物理", "description": "地図"}
        )
        atlas_store.save_domain_meta(db, "astro_test", {"name": "教員が付けた名前"})
        atlas_store.import_bundled_skeletons(db)
        assert db.domain_meta["astro_test"]["name"] == "教員が付けた名前"

    def test_list_domains_includes_bundled_domain_directories(self, db, domains_dir):
        self._write_domain(domains_dir, "astro_test", version="2026.9", meta=None)
        domains = {d["domain_key"]: d for d in atlas_store.list_domains(db)}
        assert domains["astro_test"]["source"] == "bundled"
        assert domains["astro_test"]["frozen_version"] == "2026.9"
        assert domains["astro_test"]["lifecycle"] == "active"
