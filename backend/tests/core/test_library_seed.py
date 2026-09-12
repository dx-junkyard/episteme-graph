"""L層ライブラリ — 同梱シード取込の初版凍結（P0-7 / K-4）。

`core/library/seed.py::import_bundled_library` が
①取り込んだ draft の初版をその場で凍結すること
②凍結導入より前に取り込まれた「版ゼロ」行を冪等にバックフィル凍結すること
③凍結が失敗しても draft と取込件数は保たれること（fail-soft）
を、`store.create_entry` / `store.freeze_entry` / `store.list_entries` /
`get_session` をモックして検証する。

凍結が無いと retrieval（`search_frozen_entries` は `library_entry_versions` を
引く）から同梱ライブラリが構造的に不可視になり、同一性リンク・標準化判定など
下流機能がまとめて起動しない — それが K-4 の実測（entries 3 / versions 0）。

`store.py` は非改変。ここでは seed 側の呼び出し規則だけを固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.library import seed as library_seed  # noqa: E402


class _Summary:
    def __init__(self, cartridge_id: str) -> None:
        self.cartridge_id = cartridge_id


class _NullSession:
    """seed が自前で開くセッション。重複チェック（SELECT 1）は常に「未存在」。"""

    def __init__(self) -> None:
        self.existing: set[tuple[str, str, str]] = set()

    def execute(self, _stmt, params=None):
        params = dict(params or {})
        key = (
            str(params.get("domain_key") or ""),
            str(params.get("name") or ""),
            str(params.get("entry_type") or ""),
        )

        class _Result:
            def __init__(self, row):
                self._row = row

            def fetchone(self):
                return self._row

        return _Result((1,) if key in self.existing else None)

    def close(self) -> None:
        pass


@pytest.fixture
def bundled(tmp_path, monkeypatch):
    """カートリッジ1つ + ライブラリ JSON 1件を用意し、seed の外部依存を差し替える。"""
    library_dir = tmp_path / "particle_physics" / "library"
    library_dir.mkdir(parents=True)
    (library_dir / "vacuum_chamber.json").write_text(
        '{"entry_type": "apparatus", "name": "Vacuum chamber", "summary": "A sealed enclosure."}',
        encoding="utf-8",
    )

    import core.cartridges as cartridges_module

    monkeypatch.setattr(cartridges_module, "list_cartridges", lambda: [_Summary("particle_physics")])
    monkeypatch.setattr(cartridges_module, "cartridge_directory", lambda cid: tmp_path / cid)

    session = _NullSession()
    monkeypatch.setattr(library_seed, "get_session", lambda: session)
    return session


def _patch_store(monkeypatch, *, create=None, freeze=None, list_entries=None):
    calls: dict[str, list] = {"create": [], "freeze": [], "list": []}

    def _create(**kwargs):
        calls["create"].append(kwargs)
        if create is not None:
            return create(**kwargs)
        return {"id": "entry-1"}

    def _freeze(entry_id, **kwargs):
        calls["freeze"].append((entry_id, kwargs))
        if freeze is not None:
            return freeze(entry_id, **kwargs)
        return {"version_no": 1}

    def _list_entries(**kwargs):
        calls["list"].append(kwargs)
        if list_entries is not None:
            return list_entries(**kwargs)
        return []

    monkeypatch.setattr(library_seed.store, "create_entry", _create)
    monkeypatch.setattr(library_seed.store, "freeze_entry", _freeze)
    monkeypatch.setattr(library_seed.store, "list_entries", _list_entries)
    return calls


class TestSeedFreezesInitialVersion:
    def test_import_freezes_created_entry(self, bundled, monkeypatch):
        calls = _patch_store(monkeypatch)

        result = library_seed.import_bundled_library()

        assert result == {"imported": 1, "skipped": 0, "frozen": 1, "freeze_failed": 0}
        assert len(calls["freeze"]) == 1
        entry_id, kwargs = calls["freeze"][0]
        assert entry_id == "entry-1"
        assert kwargs["published_by"] == "bundled_import"
        assert kwargs["note"] == library_seed.SEED_FREEZE_NOTE

    def test_created_by_marks_bundled_import(self, bundled, monkeypatch):
        calls = _patch_store(monkeypatch)
        library_seed.import_bundled_library()
        assert calls["create"][0]["created_by"] == "bundled_import"

    def test_freeze_failure_keeps_import_count_and_draft(self, bundled, monkeypatch):
        def _boom(entry_id, **kwargs):
            raise RuntimeError("db down")

        calls = _patch_store(monkeypatch, freeze=_boom)

        result = library_seed.import_bundled_library()

        # draft は作られている（imported は減らない）。凍結だけが失敗として残る。
        assert len(calls["create"]) == 1
        assert result == {"imported": 1, "skipped": 0, "frozen": 0, "freeze_failed": 1}

    def test_missing_entry_id_counts_as_freeze_failure(self, bundled, monkeypatch):
        calls = _patch_store(monkeypatch, create=lambda **kwargs: {})

        result = library_seed.import_bundled_library()

        assert calls["freeze"] == []
        assert result["freeze_failed"] == 1
        assert result["frozen"] == 0


class TestUnfrozenSeedBackfill:
    def _stale(self, **overrides) -> dict:
        row = {
            "id": "stale-1",
            "domain_key": "particle_physics",
            "name": "Legacy chamber",
            "created_by": "bundled_import",
            "latest_version_no": 0,
            "status": "active",
        }
        row.update(overrides)
        return row

    def test_backfill_freezes_unfrozen_seed_entry(self, bundled, monkeypatch):
        calls = _patch_store(monkeypatch, list_entries=lambda **kw: [self._stale()])

        result = library_seed.import_bundled_library()

        # 新規取込 1 件 + バックフィル 1 件。
        assert result == {"imported": 1, "skipped": 0, "frozen": 2, "freeze_failed": 0}
        assert [entry_id for entry_id, _ in calls["freeze"]] == ["entry-1", "stale-1"]

    @pytest.mark.parametrize(
        "overrides",
        [
            {"created_by": "teacher-1"},      # 教員が作った行は対象外
            {"latest_version_no": 1},          # 既に凍結済み（通常運用の状態）
            {"status": "retired"},             # retired は読み取り専用（N29）
        ],
    )
    def test_backfill_targets_only_unfrozen_active_seed_rows(self, bundled, monkeypatch, overrides):
        calls = _patch_store(
            monkeypatch, list_entries=lambda **kw: [self._stale(**overrides)]
        )

        result = library_seed.import_bundled_library()

        # 新規取込ぶんの 1 件だけが凍結される（バックフィル対象ゼロ）。
        assert [entry_id for entry_id, _ in calls["freeze"]] == ["entry-1"]
        assert result["frozen"] == 1

    def test_second_startup_freezes_nothing(self, bundled, monkeypatch):
        # 2 回目の起動: 既存行はスキップされ、版ゼロの行も無い。
        bundled.existing.add(("particle_physics", "Vacuum chamber", "apparatus"))
        calls = _patch_store(monkeypatch)

        result = library_seed.import_bundled_library()

        assert calls["create"] == []
        assert calls["freeze"] == []
        assert result == {"imported": 0, "skipped": 1, "frozen": 0, "freeze_failed": 0}

    def test_backfill_skips_entries_already_attempted_in_this_run(self, bundled, monkeypatch):
        # この起動で凍結に失敗した行を、同じ起動のバックフィルで二重に試さない。
        def _boom(entry_id, **kwargs):
            raise RuntimeError("db down")

        stale = self._stale(id="entry-1")  # 直前に作成した行と同一 id
        calls = _patch_store(monkeypatch, freeze=_boom, list_entries=lambda **kw: [stale])

        result = library_seed.import_bundled_library()

        assert len(calls["freeze"]) == 1
        assert result["freeze_failed"] == 1

    def test_list_entries_failure_does_not_break_import(self, bundled, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("db down")

        _patch_store(monkeypatch, list_entries=_boom)

        result = library_seed.import_bundled_library()

        assert result == {"imported": 1, "skipped": 0, "frozen": 1, "freeze_failed": 0}


class TestErrorResultShape:
    def test_cartridge_listing_failure_returns_all_counters(self, monkeypatch):
        import core.cartridges as cartridges_module

        def _boom():
            raise RuntimeError("no cartridges")

        monkeypatch.setattr(cartridges_module, "list_cartridges", _boom)

        result = library_seed.import_bundled_library()

        assert result["imported"] == 0
        assert result["skipped"] == 0
        assert result["frozen"] == 0
        assert result["freeze_failed"] == 0
        assert "no cartridges" in result["error"]
