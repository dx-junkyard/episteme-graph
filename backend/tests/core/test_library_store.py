"""L層ライブラリ — DB ストア (core/library/store.py, migration 042) のユニットテスト。

`tests/core/test_atlas_store.py` の流儀を踏襲し、`LibraryEntryTableFake`
(`tests/fixtures/library_entries_fake.py`) で `core.library.store.get_session`
(および `core.library.seed.get_session`) を差し替えて検証する。

- draft の楽観ロック（revision インクリメント成功 / stale revision で
  LibraryConflictError / 存在しない id で LibraryNotFoundError）
- freeze で versions 追記 + latest_version_no 更新
- retire → restore の status 遷移
- シード取込（`core.library.seed.import_bundled_library`）の冪等性
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.library import seed as library_seed  # noqa: E402
from core.library import store  # noqa: E402
from core.library.store import (  # noqa: E402
    LibraryConflictError,
    LibraryNotFoundError,
    LibraryRetiredError,
)
from tests.fixtures.library_entries_fake import (  # noqa: E402
    LibraryEntryTableFake,
    make_session_factory,
)


@pytest.fixture
def fake():
    return LibraryEntryTableFake()


@pytest.fixture(autouse=True)
def _patch_store_session(fake, monkeypatch):
    monkeypatch.setattr(store, "get_session", make_session_factory(fake))


def _make_entry(**overrides) -> dict:
    defaults = dict(
        domain_key="particle_physics",
        entry_type="apparatus",
        name="Vacuum chamber",
        summary="A sealed enclosure.",
        body={"typical_parts": ["flange"]},
        created_by="teacher-1",
    )
    defaults.update(overrides)
    return store.create_entry(**defaults)


class TestCreateAndGetEntry:
    def test_create_entry_starts_at_revision_1_active(self, fake):
        entry = _make_entry()
        assert entry["revision"] == 1
        assert entry["status"] == "active"
        assert entry["latest_version_no"] == 0
        assert entry["name"] == "Vacuum chamber"

    def test_get_entry_roundtrip(self, fake):
        entry = _make_entry()
        fetched = store.get_entry(entry["id"])
        assert fetched == entry

    def test_get_entry_missing_returns_none(self, fake):
        assert store.get_entry("00000000-0000-0000-0000-000000000000") is None


class TestUpdateEntryOptimisticLock:
    def test_update_success_increments_revision(self, fake):
        entry = _make_entry()
        updated = store.update_entry(entry["id"], expected_revision=1, name="Renamed chamber")
        assert updated["revision"] == 2
        assert updated["name"] == "Renamed chamber"

    def test_stale_revision_raises_conflict_with_current_revision(self, fake):
        entry = _make_entry()
        store.update_entry(entry["id"], expected_revision=1, name="First rename")  # -> revision 2
        with pytest.raises(LibraryConflictError) as exc:
            store.update_entry(entry["id"], expected_revision=1, name="Stale rename")
        assert exc.value.current_revision == 2

    def test_missing_entry_raises_not_found(self, fake):
        with pytest.raises(LibraryNotFoundError):
            store.update_entry("00000000-0000-0000-0000-000000000000", expected_revision=1, name="x")

    def test_unknown_field_raises_value_error(self, fake):
        entry = _make_entry()
        with pytest.raises(ValueError):
            store.update_entry(entry["id"], expected_revision=1, status="retired")

    def test_empty_name_rejected(self, fake):
        entry = _make_entry()
        with pytest.raises(ValueError):
            store.update_entry(entry["id"], expected_revision=1, name="")

    def test_json_field_update_persists(self, fake):
        entry = _make_entry()
        updated = store.update_entry(entry["id"], expected_revision=1, aliases=["chamber", "bell jar"])
        assert updated["aliases"] == ["chamber", "bell jar"]


class TestFreezeEntry:
    def test_freeze_appends_version_and_bumps_latest_version_no(self, fake):
        entry = _make_entry()
        version = store.freeze_entry(entry["id"], published_by="teacher-1", embed_fn=lambda text: [0.1, 0.2])
        assert version["version_no"] == 1
        assert version["embedding_status"] == "ok"

        refreshed = store.get_entry(entry["id"])
        assert refreshed["latest_version_no"] == 1

        versions = store.list_versions(entry["id"])
        assert len(versions) == 1
        assert versions[0]["version_no"] == 1
        assert "embedding" not in versions[0]

    def test_freeze_twice_appends_second_version(self, fake):
        entry = _make_entry()
        store.freeze_entry(entry["id"], embed_fn=lambda text: [0.1])
        store.update_entry(entry["id"], expected_revision=1, summary="Updated summary")
        store.freeze_entry(entry["id"], embed_fn=lambda text: [0.2])

        versions = store.list_versions(entry["id"])
        assert [v["version_no"] for v in versions] == [2, 1]  # 新しい順
        assert store.get_entry(entry["id"])["latest_version_no"] == 2

    def test_get_version_returns_single_version(self, fake):
        entry = _make_entry()
        store.freeze_entry(entry["id"], embed_fn=lambda text: [0.1])
        version = store.get_version(entry["id"], 1)
        assert version is not None
        assert version["version_no"] == 1

    def test_freeze_missing_entry_raises_not_found(self, fake):
        with pytest.raises(LibraryNotFoundError):
            store.freeze_entry("00000000-0000-0000-0000-000000000000")

    def test_freeze_without_embed_fn_uses_llm_module_and_survives_failure(self, fake, monkeypatch):
        """embed_fn 未指定時は core.llm.generate_embeddings を遅延 import するが、
        失敗しても凍結自体は止めない (embedding_status='failed')。"""
        import core.llm as llm_module

        def _boom(_texts):
            raise RuntimeError("no api key")

        monkeypatch.setattr(llm_module, "generate_embeddings", _boom)
        entry = _make_entry()
        version = store.freeze_entry(entry["id"])
        assert version["embedding_status"] == "failed"


class TestRetireRestore:
    def test_retire_sets_status_retired(self, fake):
        entry = _make_entry()
        retired, previous_status = store.retire_entry(entry["id"], updated_by="teacher-1")
        assert retired["status"] == "retired"
        assert previous_status == "active"

    def test_restore_sets_status_active(self, fake):
        entry = _make_entry()
        store.retire_entry(entry["id"])
        restored, previous_status = store.restore_entry(entry["id"])
        assert restored["status"] == "active"
        assert previous_status == "retired"

    def test_idempotent_transition_reports_actual_previous_status(self, fake):
        """既に目的の status だった冪等呼び出しでも、遷移前 status は事実
        （ハードコードの active/retired ではなく実際の現在値）を返す。"""
        entry = _make_entry()
        store.retire_entry(entry["id"])
        retired_again, previous_status = store.retire_entry(entry["id"])
        assert retired_again["status"] == "retired"
        assert previous_status == "retired"

        store.restore_entry(entry["id"])
        restored_again, previous_status = store.restore_entry(entry["id"])
        assert restored_again["status"] == "active"
        assert previous_status == "active"

    def test_retire_missing_entry_raises_not_found(self, fake):
        with pytest.raises(LibraryNotFoundError):
            store.retire_entry("00000000-0000-0000-0000-000000000000")

    def test_list_entries_excludes_retired_by_default(self, fake):
        entry = _make_entry()
        store.retire_entry(entry["id"])
        assert store.list_entries(domain_key="particle_physics") == []
        assert len(store.list_entries(domain_key="particle_physics", include_retired=True)) == 1


class TestRetiredIsReadOnly:
    """N29: retired エントリは読み取り専用。draft 更新・凍結は LibraryRetiredError
    （routes 側で 409 にマップ）。復元（restore）してからなら従来どおり編集できる。"""

    def test_update_retired_entry_raises_retired_error(self, fake):
        entry = _make_entry()
        store.retire_entry(entry["id"])
        with pytest.raises(LibraryRetiredError):
            store.update_entry(entry["id"], expected_revision=entry["revision"], name="Renamed")
        # 行は変更されていない（P4: retired の内容は保全される）。
        current = store.get_entry(entry["id"])
        assert current["name"] == "Vacuum chamber"
        assert current["revision"] == entry["revision"]

    def test_freeze_retired_entry_raises_retired_error(self, fake):
        entry = _make_entry()
        store.retire_entry(entry["id"])
        with pytest.raises(LibraryRetiredError):
            store.freeze_entry(entry["id"], embed_fn=lambda text: [0.1])
        # 版は追加されていない。
        assert store.list_versions(entry["id"]) == []

    def test_restore_then_update_and_freeze_work(self, fake):
        entry = _make_entry()
        store.retire_entry(entry["id"])
        store.restore_entry(entry["id"])
        updated = store.update_entry(entry["id"], expected_revision=entry["revision"], name="Renamed")
        assert updated["name"] == "Renamed"
        version = store.freeze_entry(entry["id"], embed_fn=lambda text: [0.1])
        assert version["version_no"] == 1

    def test_retired_error_message_is_factual(self, fake):
        entry = _make_entry()
        store.retire_entry(entry["id"])
        with pytest.raises(LibraryRetiredError) as exc:
            store.update_entry(entry["id"], expected_revision=entry["revision"], name="x")
        assert "復元" in str(exc.value)

    def test_update_missing_entry_still_raises_not_found(self, fake):
        """retired 事前チェックの追加で not-found の挙動が変わっていないこと。"""
        with pytest.raises(LibraryNotFoundError):
            store.update_entry("00000000-0000-0000-0000-000000000000", expected_revision=1, name="x")


class TestSeedImportIdempotency:
    def test_import_bundled_library_is_idempotent(self, fake, monkeypatch, tmp_path):
        monkeypatch.setattr(library_seed, "get_session", make_session_factory(fake))

        class _Summary:
            cartridge_id = "particle_physics"

        library_dir = tmp_path / "particle_physics" / "library"
        library_dir.mkdir(parents=True)
        (library_dir / "vacuum_chamber.json").write_text(
            '{"entry_type": "apparatus", "name": "Vacuum chamber", "summary": "A sealed enclosure."}',
            encoding="utf-8",
        )

        import core.cartridges as cartridges_module

        monkeypatch.setattr(cartridges_module, "list_cartridges", lambda: [_Summary()])
        monkeypatch.setattr(
            cartridges_module, "cartridge_directory", lambda cid: tmp_path / cid
        )

        first = library_seed.import_bundled_library()
        assert first == {"imported": 1, "skipped": 0}
        assert len(fake.entries) == 1

        second = library_seed.import_bundled_library()
        assert second == {"imported": 0, "skipped": 1}
        assert len(fake.entries) == 1

    def test_import_bundled_library_skips_invalid_entry_type(self, fake, monkeypatch, tmp_path):
        monkeypatch.setattr(library_seed, "get_session", make_session_factory(fake))

        class _Summary:
            cartridge_id = "particle_physics"

        library_dir = tmp_path / "particle_physics" / "library"
        library_dir.mkdir(parents=True)
        (library_dir / "bad.json").write_text(
            '{"entry_type": "not_a_real_type", "name": "Bogus"}', encoding="utf-8"
        )

        import core.cartridges as cartridges_module

        monkeypatch.setattr(cartridges_module, "list_cartridges", lambda: [_Summary()])
        monkeypatch.setattr(
            cartridges_module, "cartridge_directory", lambda cid: tmp_path / cid
        )

        result = library_seed.import_bundled_library()
        assert result == {"imported": 0, "skipped": 1}
        assert len(fake.entries) == 0

    def test_import_bundled_library_no_cartridge_directory_no_op(self, fake, monkeypatch, tmp_path):
        monkeypatch.setattr(library_seed, "get_session", make_session_factory(fake))

        class _Summary:
            cartridge_id = "no_library_dir"

        import core.cartridges as cartridges_module

        monkeypatch.setattr(cartridges_module, "list_cartridges", lambda: [_Summary()])

        def _raise_not_found(cid):
            raise FileNotFoundError(cid)

        monkeypatch.setattr(cartridges_module, "cartridge_directory", _raise_not_found)

        result = library_seed.import_bundled_library()
        assert result == {"imported": 0, "skipped": 0}
