"""分野マップ（Field Atlas）ドメインライフサイクル（migration 057）のユニットテスト。

正本: docs/features/atlas_binding_lifecycle_design.md。

対象:
  - ``core/atlas_store.py`` の lifecycle 関数群
    (``domain_lifecycle`` / ``retired_domain_keys`` / ``retire_domain`` /
    ``restore_domain`` / ``list_domains`` の ``lifecycle`` キー)。
  - ``core/course_data.py::course_atlas_binding_pending`` アクセサ。
  - ``core/atlas_lifecycle.py`` (``compute_freeze_impact`` / ``notify_atlas_event``)。

DB は ``tests/fixtures/atlas_skeletons_fake.py::AtlasSkeletonTableFake``
（既存 ``tests/core/test_atlas_store.py`` と同じ方式）を使う。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import atlas  # noqa: E402
from core import atlas_lifecycle  # noqa: E402
from core import atlas_store  # noqa: E402
from core.course_data import course_atlas_binding_pending  # noqa: E402
from tests.fixtures.atlas_skeletons_fake import AtlasSkeletonTableFake  # noqa: E402


def _skeleton(
    status: str = "frozen",
    version: str = "2026.1",
    concept_ids: tuple[str, ...] = ("c1", "c2"),
    region_ids: tuple[str, ...] = ("r1",),
) -> atlas.AtlasSkeleton:
    """テスト用の最小骨格。全 concept は最初の region にぶら下げる
    (concept_ids() / region_ids() の差分計算だけが検証対象のため、region 分布は問わない)。
    """
    changelog = (
        (atlas.ChangelogEntry(version=version, note="t"),) if status == "frozen" else ()
    )
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
        cartridge="dom",
        status=status,
        version=version if status == "frozen" else "",
        generated_by="model:test",
        reviewed_by=("faculty:t",) if status == "frozen" else (),
        changelog=changelog,
        regions=tuple(regions),
        edges=(),
        concept_bindings=(),
    )


@pytest.fixture
def db():
    return AtlasSkeletonTableFake()


# ===========================================================================
# atlas_store: domain_lifecycle / retire_domain / restore_domain / retired_domain_keys
# ===========================================================================


class TestDomainLifecycleDefault:
    def test_unknown_domain_is_active(self, db):
        assert atlas_store.domain_lifecycle(db, "unknown_dom") == "active"

    def test_none_session_is_active(self):
        assert atlas_store.domain_lifecycle(None, "dom") == "active"

    def test_empty_domain_key_is_active(self, db):
        assert atlas_store.domain_lifecycle(db, "") == "active"


class TestRetireRestore:
    def test_retire_creates_meta_row_when_absent(self, db):
        atlas_store.retire_domain(db, "dom", user_id="u1", note="実験的骨格のため")
        assert atlas_store.domain_lifecycle(db, "dom") == "retired"
        meta = db.domain_meta["dom"]
        assert meta["retired_by"] == "u1"
        assert meta["retire_note"] == "実験的骨格のため"

    def test_retire_upserts_existing_meta_row_preserving_name(self, db):
        db.domain_meta["dom"] = {"name": "既存分野名", "lifecycle": "active"}
        atlas_store.retire_domain(db, "dom", user_id="u2", note="")
        assert db.domain_meta["dom"]["name"] == "既存分野名"
        assert atlas_store.domain_lifecycle(db, "dom") == "retired"

    def test_restore_sets_active_and_clears_retired_fields(self, db):
        atlas_store.retire_domain(db, "dom", user_id="u1", note="n")
        atlas_store.restore_domain(db, "dom", user_id="u1")
        assert atlas_store.domain_lifecycle(db, "dom") == "active"
        meta = db.domain_meta["dom"]
        assert meta["retired_at"] is None
        assert meta["retired_by"] is None

    def test_restore_preserves_retire_note_as_history(self, db):
        atlas_store.retire_domain(db, "dom", user_id="u1", note="履歴として残る")
        atlas_store.restore_domain(db, "dom")
        assert db.domain_meta["dom"]["retire_note"] == "履歴として残る"

    def test_restore_on_nonexistent_domain_is_noop(self, db):
        # meta 行が無いドメインへの restore は何も起きない (行が作られない)。
        atlas_store.restore_domain(db, "never_existed")
        assert "never_existed" not in db.domain_meta


class TestRetiredDomainKeys:
    def test_empty_by_default(self, db):
        assert atlas_store.retired_domain_keys(db) == set()

    def test_returns_only_retired(self, db):
        atlas_store.retire_domain(db, "dom_a", note="")
        atlas_store.save_domain_meta(db, "dom_b", {"name": "b"})
        assert atlas_store.retired_domain_keys(db) == {"dom_a"}

    def test_none_session_returns_empty(self):
        assert atlas_store.retired_domain_keys(None) == set()


class TestListDomainsLifecycleKey:
    def test_db_domain_defaults_to_active(self, db):
        atlas_store.insert_frozen(db, "dom_db", _skeleton("frozen", "2026.1"))
        domains = {d["domain_key"]: d for d in atlas_store.list_domains(db)}
        assert domains["dom_db"]["lifecycle"] == "active"

    def test_db_domain_reflects_retired_meta(self, db):
        atlas_store.insert_frozen(db, "dom_db", _skeleton("frozen", "2026.1"))
        atlas_store.retire_domain(db, "dom_db", note="廃止")
        domains = {d["domain_key"]: d for d in atlas_store.list_domains(db)}
        assert domains["dom_db"]["lifecycle"] == "retired"

    def test_bundled_only_domain_can_be_retired_via_meta(self, db, monkeypatch):
        """DB に atlas_skeletons 行が無い同梱カートリッジのみの domain でも、
        atlas_domain_meta に retired 行があれば lifecycle='retired' を返す
        (cartridge_directory 経由で _ensure_domain_exists が通るため、
        こういう domain も retire API の対象になりうる)。"""

        class _Summary:
            cartridge_id = "bundled_only"

        import core.cartridges as cartridges_module

        monkeypatch.setattr(cartridges_module, "list_cartridges", lambda: [_Summary()])
        monkeypatch.setattr(
            atlas_store, "_bundled_skeleton", lambda key: _skeleton("frozen", "2020.1")
        )
        atlas_store.retire_domain(db, "bundled_only", note="実験終了")
        domains = {d["domain_key"]: d for d in atlas_store.list_domains(db)}
        assert domains["bundled_only"]["source"] == "bundled"
        assert domains["bundled_only"]["lifecycle"] == "retired"


# ===========================================================================
# course_data.course_atlas_binding_pending
# ===========================================================================


class TestCourseAtlasBindingPendingAccessor:
    def test_missing_key_returns_empty(self):
        assert course_atlas_binding_pending({}) == ""

    def test_none_data_returns_empty(self):
        assert course_atlas_binding_pending(None) == ""

    def test_non_dict_data_returns_empty(self):
        assert course_atlas_binding_pending("not-a-dict") == ""

    def test_returns_trimmed_value(self):
        assert course_atlas_binding_pending({"atlas_binding_pending": "  modified_gravity  "}) == "modified_gravity"

    def test_none_value_returns_empty(self):
        assert course_atlas_binding_pending({"atlas_binding_pending": None}) == ""


# ===========================================================================
# atlas_lifecycle.compute_freeze_impact
# ===========================================================================


class _CourseRowsSession:
    """learning_courses の `SELECT id, title, data WHERE data->>'cartridge_id' = ...`
    のみを模倣する最小フェイク (compute_freeze_impact 専用)。"""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def execute(self, stmt, params=None):
        return self

    def mappings(self):
        return self

    def fetchall(self):
        return self._rows


class TestComputeFreezeImpactBasics:
    def test_no_draft_returns_all_empty(self):
        impact = atlas_lifecycle.compute_freeze_impact(None, "dom", None, _skeleton())
        assert impact["removed_node_ids"] == []
        assert impact["added_node_ids"] == []
        assert impact["affected_courses"] == []
        assert impact["frozen_version"] == "2026.1"
        assert impact["cartridge_id"] == "dom"

    def test_first_freeze_has_no_removed_but_reports_added(self):
        draft = _skeleton(status="draft", concept_ids=("c1", "c2"), region_ids=("r1",))
        impact = atlas_lifecycle.compute_freeze_impact(None, "dom", draft, None)
        assert impact["removed_node_ids"] == []
        assert set(impact["added_node_ids"]) == {"c1", "c2", "r1"}
        assert impact["frozen_version"] == ""
        assert impact["affected_courses"] == []

    def test_diff_is_deterministic_and_sorted(self):
        old = _skeleton(status="frozen", version="2026.1", concept_ids=("c1", "c2"), region_ids=("r1",))
        new_draft = _skeleton(status="draft", concept_ids=("c2", "c3"), region_ids=("r1",))
        impact = atlas_lifecycle.compute_freeze_impact(None, "dom", new_draft, old)
        assert impact["removed_node_ids"] == ["c1"]
        assert impact["added_node_ids"] == ["c3"]
        assert impact["frozen_version"] == "2026.1"


class TestComputeFreezeImpactAffectedCourses:
    def test_matches_only_courses_with_removed_node_bound(self):
        old = _skeleton(status="frozen", version="2026.1", concept_ids=("c1", "c2"), region_ids=("r1",))
        new_draft = _skeleton(status="draft", concept_ids=("c2",), region_ids=("r1",))
        session = _CourseRowsSession([
            {
                "id": "course_hit",
                "title": "対応が外れるコース",
                "data": {
                    "cartridge_id": "dom",
                    "topics": [
                        {"id": "t1", "title": "topic1", "atlas_node_id": "c1"},
                        {"id": "t2", "title": "topic2", "atlas_node_id": "c2"},
                    ],
                },
            },
            {
                "id": "course_miss",
                "title": "無関係コース",
                "data": {
                    "cartridge_id": "dom",
                    "topics": [{"id": "t3", "title": "topic3", "atlas_node_id": "c2"}],
                },
            },
        ])
        impact = atlas_lifecycle.compute_freeze_impact(session, "dom", new_draft, old)
        assert impact["removed_node_ids"] == ["c1"]
        assert len(impact["affected_courses"]) == 1
        affected = impact["affected_courses"][0]
        assert affected["course_id"] == "course_hit"
        assert affected["topics"] == [{"topic_id": "t1", "title": "topic1", "atlas_node_id": "c1"}]

    def test_no_query_when_nothing_removed(self):
        """removed が空なら DB へ問い合わせない (短絡・無駄なクエリを発行しない)。"""

        class _BoomSession:
            def execute(self, *a, **k):
                raise AssertionError("should not query when removed_node_ids is empty")

        draft = _skeleton(status="draft", concept_ids=("c1", "c2"), region_ids=("r1",))
        impact = atlas_lifecycle.compute_freeze_impact(_BoomSession(), "dom", draft, draft)
        assert impact["removed_node_ids"] == []
        assert impact["affected_courses"] == []


# ===========================================================================
# atlas_lifecycle.notify_atlas_event
# ===========================================================================


class TestNotifyAtlasEvent:
    def test_unions_recipients_and_excludes_actor(self, monkeypatch):
        monkeypatch.setattr(
            atlas_lifecycle, "atlas_bound_course_owner_ids", lambda session, dk: ["u1", "u2"]
        )
        monkeypatch.setattr(
            atlas_lifecycle, "atlas_skeleton_editor_ids", lambda session, dk: ["u2", "u3"]
        )
        delivered_to: list[tuple] = []

        def _fake_notify_user(recipient_id, kind, entity_type, entity_id, payload):
            delivered_to.append((recipient_id, kind, entity_type, entity_id))
            return True

        monkeypatch.setattr(atlas_lifecycle.cross_layer_notify, "notify_user", _fake_notify_user)

        delivered = atlas_lifecycle.notify_atlas_event(
            object(), "dom", "atlas_skeleton_frozen", {"k": "v"}, actor_id="u1"
        )

        assert delivered == 2
        recipients = {d[0] for d in delivered_to}
        assert recipients == {"u2", "u3"}  # u1 (actor) excluded
        for _, kind, entity_type, entity_id in delivered_to:
            assert kind == "atlas_skeleton_frozen"
            assert entity_type == "atlas_skeleton"
            assert entity_id == "dom"

    def test_recipient_resolution_failure_is_swallowed(self, monkeypatch):
        def _boom(session, dk):
            raise RuntimeError("db down")

        monkeypatch.setattr(atlas_lifecycle, "atlas_bound_course_owner_ids", _boom)
        monkeypatch.setattr(atlas_lifecycle, "atlas_skeleton_editor_ids", lambda s, dk: [])

        delivered = atlas_lifecycle.notify_atlas_event(object(), "dom", "atlas_domain_retired", {})
        assert delivered == 0

    def test_single_delivery_failure_does_not_block_others(self, monkeypatch):
        monkeypatch.setattr(atlas_lifecycle, "atlas_bound_course_owner_ids", lambda s, dk: ["u1", "u2"])
        monkeypatch.setattr(atlas_lifecycle, "atlas_skeleton_editor_ids", lambda s, dk: [])

        def _flaky_notify_user(recipient_id, kind, entity_type, entity_id, payload):
            if recipient_id == "u1":
                raise RuntimeError("insert failed")
            return True

        monkeypatch.setattr(atlas_lifecycle.cross_layer_notify, "notify_user", _flaky_notify_user)

        delivered = atlas_lifecycle.notify_atlas_event(object(), "dom", "atlas_skeleton_frozen", {})
        assert delivered == 1

    def test_no_recipients_returns_zero(self, monkeypatch):
        monkeypatch.setattr(atlas_lifecycle, "atlas_bound_course_owner_ids", lambda s, dk: [])
        monkeypatch.setattr(atlas_lifecycle, "atlas_skeleton_editor_ids", lambda s, dk: [])
        assert atlas_lifecycle.notify_atlas_event(object(), "dom", "atlas_domain_retired", {}) == 0
