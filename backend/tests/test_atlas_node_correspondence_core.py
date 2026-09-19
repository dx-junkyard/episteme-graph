"""分野マップのノード版間対応 — core の純関数（``core/atlas_correspondence.py``）。

正本: ``docs/features/atlas_node_correspondence_design.md`` §4（NC2 / NC4 / NC6 / NC7 / NC8）。

検証するもの:

1. 候補導出（3経路・優先順位・merge 可・alternatives・already_declared・facts）
2. 版連鎖の解決（current / migrated / 多段 / 循環 / 欠落 / 履歴不在）
3. 凍結 body の検証（骨格外 id・from 重複・空・初回凍結）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import atlas  # noqa: E402
from core import atlas_correspondence as ac  # noqa: E402


def _skeleton(
    *,
    version: str = "2026.1",
    status: str = atlas.STATUS_FROZEN,
    regions=(("r_cosmo", "宇宙論", (("c_dm", "暗黒物質"),)),),
    id_migrations=(),
) -> atlas.AtlasSkeleton:
    return atlas.AtlasSkeleton(
        cartridge="domain_a",
        status=status,
        version=version,
        reviewed_by=("faculty:t",),
        regions=tuple(
            atlas.SkeletonRegion(
                id=rid,
                label=rlabel,
                concepts=tuple(
                    atlas.SkeletonConcept(id=cid, label=clabel) for cid, clabel in concepts
                ),
            )
            for rid, rlabel, concepts in regions
        ),
        id_migrations=tuple(
            atlas.IdMigration(from_id=f, to_id=t, version=v) for f, t, v in id_migrations
        ),
    )


# ---------------------------------------------------------------------------
# 1. 候補導出
# ---------------------------------------------------------------------------


class TestDeriveCandidates:
    def test_lexical_match_pairs_removed_node_to_renamed_id(self):
        """経路①: 正規化ラベルが一致すれば旧 id → 新 id の候補になる。"""
        frozen = _skeleton(regions=(("r_cosmo", "宇宙論", (("c_dm", "暗黒物質"),)),))
        draft = _skeleton(
            status=atlas.STATUS_DRAFT,
            version="",
            regions=(("r_cosmo", "宇宙論", (("dark_matter", " 暗黒物質 "),)),),
        )

        out = ac.derive_correspondence_candidates(
            frozen=frozen, draft=draft, confirmed_aliases_by_node={}
        )

        assert [(c["from_id"], c["to_id"]) for c in out["candidates"]] == [
            ("c_dm", "dark_matter")
        ]
        candidate = out["candidates"][0]
        assert candidate["via"] == ac.VIA_LABEL
        assert candidate["justification"] == ac.JUSTIFICATION_LEXICAL
        assert candidate["from_label"] == "暗黒物質"
        assert candidate["to_label"] == " 暗黒物質 "
        assert out["unmatched_removed"] == []

    def test_kind_must_match(self):
        """region のラベルと concept のラベルが同じでも対応候補にしない。"""
        frozen = _skeleton(regions=(("r_gone", "重力波", ()),))
        draft = _skeleton(
            status=atlas.STATUS_DRAFT,
            version="",
            regions=(("r_other", "別領域", (("c_gw", "重力波"),)),),
        )

        out = ac.derive_correspondence_candidates(
            frozen=frozen, draft=draft, confirmed_aliases_by_node={}
        )

        assert out["candidates"] == []
        assert [u["node_id"] for u in out["unmatched_removed"]] == ["r_gone"]
        assert out["unmatched_removed"][0]["kind"] == ac.NODE_KIND_REGION

    def test_alias_route_uses_teacher_confirmed_aliases(self):
        """経路②: 教員確定別名の正規形が新ラベルに一致すれば候補になる。"""
        frozen = _skeleton(regions=(("r", "領域", (("c_old", "ダークマター"),)),))
        draft = _skeleton(
            status=atlas.STATUS_DRAFT,
            version="",
            regions=(("r", "領域", (("c_new", "暗黒物質"),)),),
        )

        out = ac.derive_correspondence_candidates(
            frozen=frozen,
            draft=draft,
            confirmed_aliases_by_node={"c_old": ["暗黒物質"]},
        )

        assert [(c["from_id"], c["to_id"], c["via"]) for c in out["candidates"]] == [
            ("c_old", "c_new", ac.VIA_ALIAS)
        ]
        assert out["candidates"][0]["justification"] == ac.JUSTIFICATION_LEXICAL

    def test_registry_route_uses_confirmed_links_only(self):
        """経路③: confirmed のレジストリリンクで同じ entry に結ばれた node 同士。"""
        frozen = _skeleton(regions=(("r", "領域", (("c_old", "旧名"),)),))
        draft = _skeleton(
            status=atlas.STATUS_DRAFT,
            version="",
            regions=(("r", "領域", (("c_new", "まったく別の表記"),)),),
        )
        links = [
            {"entry_id": "e1", "node_id": "c_old", "status": "confirmed"},
            {"entry_id": "e1", "node_id": "c_new", "status": "confirmed"},
        ]

        out = ac.derive_correspondence_candidates(
            frozen=frozen, draft=draft, confirmed_aliases_by_node={}, registry_links=links
        )

        assert [(c["from_id"], c["to_id"], c["via"]) for c in out["candidates"]] == [
            ("c_old", "c_new", ac.VIA_REGISTRY)
        ]
        assert out["candidates"][0]["justification"] == ac.JUSTIFICATION_MANUAL

    def test_candidate_links_ignore_unconfirmed_registry_rows(self):
        frozen = _skeleton(regions=(("r", "領域", (("c_old", "旧名"),)),))
        draft = _skeleton(
            status=atlas.STATUS_DRAFT,
            version="",
            regions=(("r", "領域", (("c_new", "別表記"),)),),
        )
        links = [
            {"entry_id": "e1", "node_id": "c_old", "status": "candidate"},
            {"entry_id": "e1", "node_id": "c_new", "status": "candidate"},
        ]

        out = ac.derive_correspondence_candidates(
            frozen=frozen, draft=draft, confirmed_aliases_by_node={}, registry_links=links
        )

        assert out["candidates"] == []

    def test_priority_is_label_then_registry_then_alias(self):
        """同じ from に複数候補 → ① > ③ > ② で1つ、残りは alternatives（NC4/NC7）。"""
        frozen = _skeleton(regions=(("r", "領域", (("c_old", "同じ名前"),)),))
        draft = _skeleton(
            status=atlas.STATUS_DRAFT,
            version="",
            regions=(
                (
                    "r",
                    "領域",
                    (("by_label", "同じ名前"), ("by_registry", "R"), ("by_alias", "別名")),
                ),
            ),
        )
        links = [
            {"entry_id": "e1", "node_id": "c_old", "status": "confirmed"},
            {"entry_id": "e1", "node_id": "by_registry", "status": "confirmed"},
        ]

        out = ac.derive_correspondence_candidates(
            frozen=frozen,
            draft=draft,
            confirmed_aliases_by_node={"c_old": ["別名"]},
            registry_links=links,
        )

        assert [c["to_id"] for c in out["candidates"]] == ["by_label"]
        assert [a["to_id"] for a in out["alternatives"]] == ["by_registry", "by_alias"]

    def test_merge_is_allowed_but_from_stays_unique(self):
        """複数の旧ノードが1つの新ノードへ束なる（merge）のは可（NC7）。"""
        frozen = _skeleton(
            regions=(("r", "領域", (("old_a", "重力"), ("old_b", "重力"))),)
        )
        draft = _skeleton(
            status=atlas.STATUS_DRAFT,
            version="",
            regions=(("r", "領域", (("gravity", "重力"),)),),
        )

        out = ac.derive_correspondence_candidates(
            frozen=frozen, draft=draft, confirmed_aliases_by_node={}
        )

        pairs = [(c["from_id"], c["to_id"]) for c in out["candidates"]]
        assert pairs == [("old_a", "gravity"), ("old_b", "gravity")]
        assert len({p[0] for p in pairs}) == len(pairs)

    def test_already_declared_is_not_proposed_again(self):
        """draft が既に宣言している対応は候補にも unmatched にも出さない。"""
        frozen = _skeleton(regions=(("r", "領域", (("c_old", "暗黒物質"),)),))
        draft = _skeleton(
            status=atlas.STATUS_DRAFT,
            version="",
            regions=(("r", "領域", (("c_new", "暗黒物質"),)),),
            id_migrations=(("c_old", "c_new", "2026.2"),),
        )

        out = ac.derive_correspondence_candidates(
            frozen=frozen, draft=draft, confirmed_aliases_by_node={}
        )

        assert out["candidates"] == []
        assert out["unmatched_removed"] == []
        # キーの綴りは骨格の id_migrations と同じ（フロントが from / to で読む）。
        assert out["already_declared"] == [
            {
                "from": "c_old",
                "to": "c_new",
                "from_label": "暗黒物質",
                "to_label": "暗黒物質",
                "version": "2026.2",
            }
        ]

    def test_added_nodes_are_listed_for_manual_pairing(self):
        frozen = _skeleton(regions=(("r", "領域", (("c_old", "旧"),)),))
        draft = _skeleton(
            status=atlas.STATUS_DRAFT,
            version="",
            regions=(("r", "領域", (("c_new", "新"),)),),
        )

        out = ac.derive_correspondence_candidates(
            frozen=frozen, draft=draft, confirmed_aliases_by_node={}
        )

        assert out["added_nodes"] == [
            {"node_id": "c_new", "label": "新", "kind": ac.NODE_KIND_CONCEPT}
        ]

    def test_first_freeze_has_no_candidates(self):
        draft = _skeleton(status=atlas.STATUS_DRAFT, version="")
        out = ac.derive_correspondence_candidates(
            frozen=None, draft=draft, confirmed_aliases_by_node={}
        )
        assert out["candidates"] == [] and out["facts"] == []

    def test_facts_are_fixed_sentences_without_numbers(self):
        frozen = _skeleton(
            regions=(("r", "領域", (("c_old", "暗黒物質"), ("c_gone", "消える概念"))),)
        )
        draft = _skeleton(
            status=atlas.STATUS_DRAFT,
            version="",
            regions=(("r", "領域", (("c_new", "暗黒物質"),)),),
        )

        out = ac.derive_correspondence_candidates(
            frozen=frozen, draft=draft, confirmed_aliases_by_node={}
        )

        assert out["facts"] == [
            ac.FACT_LEXICAL_ONLY,
            ac.FACT_CANDIDATES_NOT_PRESELECTED,
            ac.FACT_UNMATCHED_REMOVED,
        ]
        for fact in out["facts"]:
            assert not any(ch.isdigit() for ch in fact)

    def test_all_justifications_are_in_the_allowed_two(self):
        frozen = _skeleton(
            regions=(("r", "領域", (("a", "X"), ("b", "Y"), ("c", "Z"))),)
        )
        draft = _skeleton(
            status=atlas.STATUS_DRAFT,
            version="",
            regions=(("r", "領域", (("a2", "X"), ("b2", "別名 Y"), ("c2", "reg"))),),
        )
        out = ac.derive_correspondence_candidates(
            frozen=frozen,
            draft=draft,
            confirmed_aliases_by_node={"b": ["別名 Y"]},
            registry_links=[
                {"entry_id": "e", "node_id": "c", "status": "confirmed"},
                {"entry_id": "e", "node_id": "c2", "status": "confirmed"},
            ],
        )
        for item in out["candidates"] + out["alternatives"]:
            assert item["justification"] in ac.ALLOWED_JUSTIFICATIONS

    def test_derivation_does_not_mutate_inputs(self):
        frozen = _skeleton(regions=(("r", "領域", (("c_old", "暗黒物質"),)),))
        draft = _skeleton(
            status=atlas.STATUS_DRAFT,
            version="",
            regions=(("r", "領域", (("c_new", "暗黒物質"),)),),
        )
        aliases = {"c_old": ["別名"]}

        ac.derive_correspondence_candidates(
            frozen=frozen, draft=draft, confirmed_aliases_by_node=aliases
        )

        assert aliases == {"c_old": ["別名"]}
        assert frozen.id_migrations == () and draft.id_migrations == ()


# ---------------------------------------------------------------------------
# 2. 版連鎖の解決（NC8）
# ---------------------------------------------------------------------------


class TestNodeResolver:
    def test_node_in_current_version_is_current(self):
        resolver = ac.build_node_resolver([_skeleton(version="2026.1")])
        out = resolver.resolve("c_dm")
        assert out["status"] == ac.NODE_STATUS_CURRENT
        assert out["current_node_id"] == "c_dm"
        assert out["via"] == []
        assert out["last_version"] == "2026.1"

    def test_single_hop_migration(self):
        v1 = _skeleton(version="2026.1", regions=(("r", "領域", (("c_old", "旧"),)),))
        v2 = _skeleton(
            version="2026.2",
            regions=(("r", "領域", (("c_new", "新"),)),),
            id_migrations=(("c_old", "c_new", "2026.2"),),
        )
        resolver = ac.build_node_resolver([v1, v2])

        out = resolver.resolve("c_old")
        assert out["status"] == ac.NODE_STATUS_MIGRATED
        assert out["current_node_id"] == "c_new"
        assert out["via"] == ["c_new"]
        # 旧 node が実在した最後の版（事実文の {old} の材料）。
        assert out["last_version"] == "2026.1"

    def test_chain_across_multiple_versions(self):
        """1版飛ばして改訂されても鎖が切れない（NC8）。"""
        v1 = _skeleton(version="2026.1", regions=(("r", "領域", (("a", "A"),)),))
        v2 = _skeleton(
            version="2026.2",
            regions=(("r", "領域", (("b", "B"),)),),
            id_migrations=(("a", "b", "2026.2"),),
        )
        v3 = _skeleton(
            version="2026.3",
            regions=(("r", "領域", (("c", "C"),)),),
            id_migrations=(("b", "c", "2026.3"),),
        )
        resolver = ac.build_node_resolver([v1, v2, v3])

        out = resolver.resolve("a")
        assert out["status"] == ac.NODE_STATUS_MIGRATED
        assert out["current_node_id"] == "c"
        assert out["via"] == ["b", "c"]

    def test_cycle_is_unmapped_not_an_exception(self):
        v1 = _skeleton(version="2026.1", regions=(("r", "領域", (("a", "A"),)),))
        v2 = _skeleton(
            version="2026.2",
            regions=(("r", "領域", (("z", "Z"),)),),
            id_migrations=(("a", "b", "2026.2"), ("b", "a", "2026.2")),
        )
        resolver = ac.build_node_resolver([v1, v2])

        out = resolver.resolve("a")
        assert out["status"] == ac.NODE_STATUS_UNMAPPED
        assert out["current_node_id"] is None

    def test_missing_node_is_unmapped(self):
        resolver = ac.build_node_resolver([_skeleton(version="2026.1")])
        out = resolver.resolve("never_existed")
        assert out["status"] == ac.NODE_STATUS_UNMAPPED
        assert out["current_node_id"] is None
        assert out["last_version"] == ""

    def test_broken_chain_that_never_reaches_current_is_unmapped(self):
        v1 = _skeleton(version="2026.1", regions=(("r", "領域", (("a", "A"),)),))
        v2 = _skeleton(
            version="2026.2",
            regions=(("r", "領域", (("z", "Z"),)),),
            id_migrations=(("a", "ghost", "2026.2"),),
        )
        resolver = ac.build_node_resolver([v1, v2])
        assert resolver.resolve("a")["status"] == ac.NODE_STATUS_UNMAPPED

    def test_empty_history_knows_nothing(self):
        resolver = ac.build_node_resolver([])
        assert resolver.available is False
        out = resolver.resolve("anything")
        assert out["status"] == ac.NODE_STATUS_UNMAPPED
        assert out["current_node_id"] is None

    def test_none_history_does_not_raise(self):
        assert ac.build_node_resolver(None).resolve("x")["status"] == ac.NODE_STATUS_UNMAPPED

    def test_empty_node_id_is_unmapped(self):
        resolver = ac.build_node_resolver([_skeleton()])
        assert resolver.resolve("")["status"] == ac.NODE_STATUS_UNMAPPED

    def test_oldest_declaration_wins_for_the_same_from(self):
        """同じ from が複数版で宣言されていたら、古い版（消えた時点）の対応が正。"""
        v1 = _skeleton(version="2026.1", regions=(("r", "領域", (("a", "A"),)),))
        v2 = _skeleton(
            version="2026.2",
            regions=(("r", "領域", (("b", "B"),)),),
            id_migrations=(("a", "b", "2026.2"),),
        )
        v3 = _skeleton(
            version="2026.3",
            regions=(("r", "領域", (("b", "B"),)),),
            id_migrations=(("a", "ghost", "2026.3"),),
        )
        resolver = ac.build_node_resolver([v1, v2, v3])
        assert resolver.resolve("a")["current_node_id"] == "b"


# ---------------------------------------------------------------------------
# 3. 凍結 body の検証（NC7）
# ---------------------------------------------------------------------------


class TestValidateMigrations:
    def _pair(self):
        frozen = _skeleton(regions=(("r", "領域", (("c_old", "旧"),)),))
        draft = _skeleton(
            status=atlas.STATUS_DRAFT,
            version="",
            regions=(("r", "領域", (("c_new", "新"),)),),
        )
        return frozen, draft

    def test_empty_input_is_empty_output(self):
        frozen, draft = self._pair()
        assert ac.validate_migrations([], frozen=frozen, draft=draft) == []
        assert ac.validate_migrations(None, frozen=frozen, draft=draft) == []

    def test_valid_pair_becomes_id_migration_with_the_new_version(self):
        frozen, draft = self._pair()
        out = ac.validate_migrations(
            [{"from": "c_old", "to": "c_new"}], frozen=frozen, draft=draft, version="2026.2"
        )
        assert out == [atlas.IdMigration(from_id="c_old", to_id="c_new", version="2026.2")]

    @pytest.mark.parametrize(
        "item",
        [
            {"from_id": "c_old", "to_id": "c_new"},
            ("c_old", "c_new"),
            ["c_old", "c_new"],
        ],
    )
    def test_alternate_input_shapes(self, item):
        frozen, draft = self._pair()
        out = ac.validate_migrations([item], frozen=frozen, draft=draft, version="v")
        assert (out[0].from_id, out[0].to_id) == ("c_old", "c_new")

    def test_from_outside_the_frozen_skeleton_is_rejected(self):
        frozen, draft = self._pair()
        with pytest.raises(ValueError) as exc:
            ac.validate_migrations(
                [{"from": "ghost", "to": "c_new"}], frozen=frozen, draft=draft
            )
        assert "ghost" in str(exc.value)

    def test_to_outside_the_draft_is_rejected(self):
        frozen, draft = self._pair()
        with pytest.raises(ValueError) as exc:
            ac.validate_migrations(
                [{"from": "c_old", "to": "ghost"}], frozen=frozen, draft=draft
            )
        assert "ghost" in str(exc.value)

    def test_duplicate_from_is_rejected(self):
        """1 旧ノード → 複数新ノード（split）は v1 では受け付けない（NC7）。"""
        frozen = _skeleton(regions=(("r", "領域", (("c_old", "旧"),)),))
        draft = _skeleton(
            status=atlas.STATUS_DRAFT,
            version="",
            regions=(("r", "領域", (("a", "A"), ("b", "B"))),),
        )
        with pytest.raises(ValueError):
            ac.validate_migrations(
                [{"from": "c_old", "to": "a"}, {"from": "c_old", "to": "b"}],
                frozen=frozen,
                draft=draft,
            )

    def test_empty_side_is_rejected(self):
        frozen, draft = self._pair()
        with pytest.raises(ValueError):
            ac.validate_migrations([{"from": "", "to": "c_new"}], frozen=frozen, draft=draft)
        with pytest.raises(ValueError):
            ac.validate_migrations([{"from": "c_old", "to": ""}], frozen=frozen, draft=draft)

    def test_first_freeze_cannot_declare_correspondence(self):
        _frozen, draft = self._pair()
        with pytest.raises(ValueError):
            ac.validate_migrations(
                [{"from": "c_old", "to": "c_new"}], frozen=None, draft=draft
            )

    def test_error_messages_carry_no_numbers(self):
        frozen, draft = self._pair()
        messages: list[str] = []
        for bad in ([{"from": "ghost", "to": "c_new"}], [{"from": "c_old", "to": "ghost"}]):
            with pytest.raises(ValueError) as exc:
                ac.validate_migrations(bad, frozen=frozen, draft=draft)
            messages.append(str(exc.value))
        for message in messages:
            assert not any(ch.isdigit() for ch in message)


# ---------------------------------------------------------------------------
# 4. 事実文（NC6）
# ---------------------------------------------------------------------------


class TestFacts:
    def test_unmapped_fact_carries_both_versions(self):
        text = ac.unmapped_placement_fact("2026.1", "2026.2")
        assert "版 2026.1" in text and "版 2026.2" in text

    def test_unknown_old_version_is_not_invented(self):
        text = ac.unmapped_placement_fact("", "2026.2")
        assert text == ac.FACT_UNMAPPED_PLACEMENT_NO_OLD_VERSION.format(new="2026.2")
        assert "版 " in text

    def test_migrated_fact_uses_labels_not_ids(self):
        text = ac.migrated_fact(
            old_version="2026.1", new_version="2026.2",
            from_label="暗黒物質", to_label="ダークマター",
        )
        assert "暗黒物質" in text and "ダークマター" in text
