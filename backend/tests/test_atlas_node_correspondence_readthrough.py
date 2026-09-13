"""分野マップのノード版間対応 — 読み手の読み替え（設計書 §6・NC5）。

対象:

- ``core/landscape/projection.py``（教員 / 学習者の配置 DTO）
- ``routes/landscape.py``（``_node_resolve`` の配線・overview の集約）
- ``core/corpus_view.py``（論文の海）
- ``core/library/registry.py::annotate_node_links``（Phase 3 の node リンク）

**``landscape_placements.node_id`` は書き換えない**（読み替えるだけ）ことが主題。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import atlas  # noqa: E402
from core import atlas_correspondence as ac  # noqa: E402
from core import corpus_view  # noqa: E402
from core.landscape import projection  # noqa: E402
from core.library import registry  # noqa: E402

_DOMAIN = "astrophysics"
_DOC = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# フェイク骨格・行
# ---------------------------------------------------------------------------


def _skeleton(version: str, concepts, *, id_migrations=()) -> atlas.AtlasSkeleton:
    return atlas.AtlasSkeleton(
        cartridge=_DOMAIN,
        status=atlas.STATUS_FROZEN,
        version=version,
        reviewed_by=("faculty:t",),
        regions=(
            atlas.SkeletonRegion(
                id="r_cosmo",
                label="宇宙論",
                concepts=tuple(
                    atlas.SkeletonConcept(id=cid, label=label) for cid, label in concepts
                ),
            ),
        ),
        id_migrations=tuple(
            atlas.IdMigration(from_id=f, to_id=t, version=v) for f, t, v in id_migrations
        ),
    )


_V1 = _skeleton("2026.1", (("c_dm", "暗黒物質"),))
_V2 = _skeleton(
    "2026.2", (("dark_matter", "暗黒物質"),), id_migrations=(("c_dm", "dark_matter", "2026.2"),)
)


def _resolver_resolve(history):
    resolver = ac.build_node_resolver(history)

    def _resolve(domain_key: str, node_id: str) -> dict:
        out = resolver.resolve(node_id)
        return {
            "current_node_id": out.get("current_node_id") or "",
            "node_status": out.get("status") or "",
            "via": list(out.get("via") or []),
        }

    return _resolve


def _row(**overrides) -> dict:
    row = {
        "id": "55555555-5555-5555-5555-555555555555",
        "document_id": _DOC,
        "domain_key": _DOMAIN,
        "skeleton_version": "2026.1",
        "node_id": "c_dm",
        "node_kind": "concept",
        "perspective": "subject",
        "weight": 0.8,
        "reason": "",
        "evidence": [],
        "status": "confirmed",
        "provenance": "teacher",
        "created_at": "2026-08-04T09:00:00+00:00",
    }
    row.update(overrides)
    return row


def _index(skeleton) -> dict:
    return projection.skeleton_node_index({_DOMAIN: skeleton})


# ---------------------------------------------------------------------------
# 1. 教員 DTO
# ---------------------------------------------------------------------------


class TestAdminPlacementDto:
    def test_migrated_row_keeps_its_node_id_and_gains_the_current_one(self):
        row = _row()
        dto = projection.admin_placement_dto(
            row, _index(_V2), resolve=_resolver_resolve([_V1, _V2])
        )

        assert dto["node_id"] == "c_dm"                 # 行は書き換えない（NC5）
        assert dto["current_node_id"] == "dark_matter"
        assert dto["node_status"] == ac.NODE_STATUS_MIGRATED
        assert dto["node_label"] == "暗黒物質"           # ラベルは現行版のもの
        assert dto["region_id"] == "r_cosmo"
        # 入力行は不変。
        assert row["node_id"] == "c_dm"

    def test_current_row_is_marked_current(self):
        dto = projection.admin_placement_dto(
            _row(node_id="dark_matter", skeleton_version="2026.2"),
            _index(_V2),
            resolve=_resolver_resolve([_V1, _V2]),
        )
        assert dto["node_status"] == ac.NODE_STATUS_CURRENT
        assert dto["current_node_id"] == "dark_matter"

    def test_unmapped_row_is_kept_with_the_raw_id(self):
        """対応が付かない配置も教員側は行を残す（P4）。"""
        dto = projection.admin_placement_dto(
            _row(node_id="ghost"), _index(_V2), resolve=_resolver_resolve([_V1, _V2])
        )
        assert dto["node_status"] == ac.NODE_STATUS_UNMAPPED
        assert dto["node_id"] == "ghost"
        assert dto["node_label"] == "ghost"

    def test_without_a_resolver_the_behaviour_is_unchanged(self):
        dto = projection.admin_placement_dto(_row(node_id="dark_matter"), _index(_V2))
        assert dto["node_status"] == ac.NODE_STATUS_CURRENT
        assert dto["current_node_id"] == "dark_matter"
        dto2 = projection.admin_placement_dto(_row(node_id="c_dm"), _index(_V2))
        assert dto2["node_status"] == ac.NODE_STATUS_UNMAPPED

    def test_resolver_failure_degrades_to_the_raw_id(self):
        def _boom(_domain, _node):
            raise RuntimeError("resolver down")

        dto = projection.admin_placement_dto(
            _row(node_id="dark_matter"), _index(_V2), resolve=_boom
        )
        assert dto["node_status"] == ac.NODE_STATUS_CURRENT


# ---------------------------------------------------------------------------
# 2. 学習者 DTO
# ---------------------------------------------------------------------------


def _learner(rows, *, resolve=None, skeleton=_V2):
    return projection.learner_landscape_dto(
        rows,
        _index(skeleton),
        [
            {
                "domain_key": _DOMAIN,
                "domain_name": "宇宙物理",
                "frozen_version": getattr(skeleton, "version", ""),
            }
        ],
        _DOMAIN,
        document_titles={_DOC: "重力波観測"},
        resolve=resolve,
    )


class TestLearnerLandscapeDto:
    def test_migrated_placement_is_shown_at_the_current_node(self):
        dto = _learner([_row()], resolve=_resolver_resolve([_V1, _V2]))

        placements = dto["documents"][0]["placements"]
        assert len(placements) == 1
        assert placements[0]["node_id"] == "dark_matter"
        assert placements[0]["current_node_id"] == "dark_matter"
        assert placements[0]["node_status"] == ac.NODE_STATUS_MIGRATED
        assert placements[0]["node_label"] == "暗黒物質"
        assert dto["domains"][0]["facts"] == []

    def test_learner_dto_never_exposes_via_or_the_old_node_id(self):
        dto = _learner([_row()], resolve=_resolver_resolve([_V1, _V2]))
        keys = _keys_recursive(dto)
        assert "via" not in keys
        values = _strings_recursive(dto)
        assert "c_dm" not in values

    def test_unmapped_placement_is_not_placed_and_becomes_a_fact(self):
        dto = _learner([_row(node_id="ghost")], resolve=_resolver_resolve([_V1, _V2]))

        assert dto["documents"] == []
        assert dto["domains"][0]["facts"] == [
            ac.unmapped_placement_fact("2026.1", "2026.2")
        ]

    def test_the_fact_names_both_versions_and_no_counts(self):
        dto = _learner([_row(node_id="ghost")], resolve=_resolver_resolve([_V1, _V2]))
        fact = dto["domains"][0]["facts"][0]
        assert "版 2026.1" in fact and "版 2026.2" in fact
        assert "件" not in fact

    def test_a_domain_with_only_unmapped_placements_still_reports_the_fact(self):
        dto = projection.learner_landscape_dto(
            [_row(node_id="ghost", domain_key=_DOMAIN)],
            _index(_V2),
            [{"domain_key": _DOMAIN, "domain_name": "宇宙物理", "frozen_version": "2026.2"}],
            None,  # コースはこのドメインにバインドされていない
            document_titles={_DOC: "重力波観測"},
            resolve=_resolver_resolve([_V1, _V2]),
        )
        assert [d["domain_key"] for d in dto["domains"]] == [_DOMAIN]
        assert dto["domains"][0]["facts"]

    def test_without_a_resolver_the_old_id_is_simply_dropped(self):
        dto = _learner([_row()])
        assert dto["documents"] == []
        # 読み替え器が無くても「消えた」事実は出す（黙って欠けさせない）。
        assert dto["domains"][0]["facts"]


def _keys_recursive(node) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(str(key))
            found |= _keys_recursive(value)
    elif isinstance(node, list):
        for item in node:
            found |= _keys_recursive(item)
    return found


def _strings_recursive(node) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for value in node.values():
            found |= _strings_recursive(value)
    elif isinstance(node, list):
        for item in node:
            found |= _strings_recursive(item)
    elif isinstance(node, str):
        found.add(node)
    return found


# ---------------------------------------------------------------------------
# 3. 論文の海（corpus_view）
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _CorpusSession:
    """corpus_view が発行する SQL のうち、本テストが関心を持つ2本だけ答える。"""

    def __init__(self, placements, history):
        self.placements = placements
        self.history = history

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        if "FROM landscape_placements p" in sql and "JOIN documents" in sql:
            return _Result(self.placements)
        return _Result([])

    def close(self):
        pass


class TestCorpusView:
    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch):
        monkeypatch.setattr(
            corpus_view.atlas_store,
            "load_learner_skeleton",
            lambda key, session=None: _V2 if key == _DOMAIN else None,
        )
        monkeypatch.setattr(
            corpus_view.atlas_store, "load_frozen_history", lambda _s, _k: [_V1, _V2]
        )

    def test_migrated_placement_lands_on_the_current_node(self):
        session = _CorpusSession(
            [(_DOC, "重力波観測", "c_dm", "subject", "confirmed", "2026.1")], [_V1, _V2]
        )
        out = corpus_view.build_corpus_landscape(session, _DOMAIN, {_DOC})

        assert [p["anchor_node_id"] for p in out["placements"]] == ["dark_matter"]
        assert out["placements"][0]["node_label"] == "暗黒物質"
        assert out["facts"] == []

    def test_unmapped_placement_is_dropped_with_a_fact(self):
        session = _CorpusSession(
            [(_DOC, "重力波観測", "ghost", "subject", "confirmed", "2026.1")], [_V1, _V2]
        )
        out = corpus_view.build_corpus_landscape(session, _DOMAIN, {_DOC})

        assert out["placements"] == []
        assert out["facts"] == [ac.unmapped_placement_fact("2026.1", "2026.2")]

    def test_history_failure_degrades_to_the_raw_id(self, monkeypatch):
        def _boom(_s, _k):
            raise RuntimeError("db down")

        monkeypatch.setattr(corpus_view.atlas_store, "load_frozen_history", _boom)
        session = _CorpusSession(
            [(_DOC, "重力波観測", "dark_matter", "subject", "confirmed", "2026.2")], []
        )
        out = corpus_view.build_corpus_landscape(session, _DOMAIN, {_DOC})
        assert [p["anchor_node_id"] for p in out["placements"]] == ["dark_matter"]


# ---------------------------------------------------------------------------
# 4. 概念レジストリの node リンク（Phase 3・KR9）
# ---------------------------------------------------------------------------


class TestAnnotateNodeLinks:
    def _resolve(self):
        return ac.build_node_resolver([_V1, _V2]).resolve

    def test_old_link_is_read_through_to_the_current_node(self):
        links = registry.annotate_node_links(
            [{"node_id": "c_dm"}], _V2, resolve=self._resolve()
        )
        assert links[0]["node_in_current_version"] is True
        assert links[0]["current_node_id"] == "dark_matter"
        assert links[0]["node_label"] == "暗黒物質"
        # リンク行の node_id は書き換えない（NC5）。
        assert links[0]["node_id"] == "c_dm"

    def test_unmapped_link_stays_false(self):
        links = registry.annotate_node_links(
            [{"node_id": "ghost"}], _V2, resolve=self._resolve()
        )
        assert links[0]["node_in_current_version"] is False
        assert links[0]["current_node_id"] == ""

    def test_without_a_resolver_the_behaviour_is_unchanged(self):
        links = registry.annotate_node_links([{"node_id": "c_dm"}], _V2)
        assert links[0]["node_in_current_version"] is False

    def test_unknown_skeleton_is_still_none(self):
        links = registry.annotate_node_links(
            [{"node_id": "c_dm"}], None, resolve=self._resolve()
        )
        assert links[0]["node_in_current_version"] is None
        assert links[0]["current_node_id"] == ""

    def test_input_rows_are_not_mutated(self):
        rows = [{"node_id": "c_dm"}]
        registry.annotate_node_links(rows, _V2, resolve=self._resolve())
        assert rows == [{"node_id": "c_dm"}]
