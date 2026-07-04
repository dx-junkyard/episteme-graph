"""core/atlas.py の単体テスト (Issue A-1/A-3)。

骨格スキーマのバリデーション・学習者向けビュー・凍結・シリアライズ再現性を検証する。
外部 API は一切呼び出さない。
"""

from __future__ import annotations

import pytest

from core import atlas


def _minimal_skeleton(**overrides) -> atlas.AtlasSkeleton:
    base = dict(
        cartridge="particle_physics",
        status=atlas.STATUS_DRAFT,
        generated_by="model:test batch:t1",
        regions=(
            atlas.SkeletonRegion(
                id="region_a",
                label="領域A",
                layout=atlas.RegionLayout(0.1, 0.1, 0.4, 0.4),
                concepts=(
                    atlas.SkeletonConcept(
                        id="concept_a",
                        label="概念A",
                        layout=atlas.ConceptLayout(0.5, 0.5),
                        seed_status=atlas.SeedStatus("verified", reviewed=True),
                    ),
                ),
            ),
        ),
    )
    base.update(overrides)
    return atlas.AtlasSkeleton(**base)


# ---------------------------------------------------------------------------
# バリデータ
# ---------------------------------------------------------------------------


class TestValidator:
    def test_valid_draft_passes(self):
        report = atlas.validate_skeleton(_minimal_skeleton())
        assert report.ok
        assert report.warnings == ()

    def test_missing_generated_by_is_error(self):
        report = atlas.validate_skeleton(_minimal_skeleton(generated_by=""))
        assert not report.ok

    def test_non_draft_without_reviewed_by_is_rejected(self):
        skeleton = _minimal_skeleton(
            status=atlas.STATUS_FROZEN,
            version="2026.1",
            changelog=(atlas.ChangelogEntry("2026.1", "初版"),),
            reviewed_by=(),
        )
        report = atlas.validate_skeleton(skeleton)
        assert any("reviewed_by" in e for e in report.errors)

    def test_region_limit_enforced(self):
        regions = tuple(
            atlas.SkeletonRegion(id=f"r{i}", label=f"R{i}") for i in range(atlas.MAX_REGIONS + 1)
        )
        report = atlas.validate_skeleton(_minimal_skeleton(regions=regions))
        assert any("上限" in e for e in report.errors)

    def test_concept_limit_enforced(self):
        concepts = tuple(
            atlas.SkeletonConcept(id=f"c{i}", label=f"C{i}")
            for i in range(atlas.MAX_CONCEPTS_PER_REGION + 1)
        )
        regions = (atlas.SkeletonRegion(id="r1", label="R1", concepts=concepts),)
        report = atlas.validate_skeleton(_minimal_skeleton(regions=regions))
        assert any("代表概念数" in e for e in report.errors)

    def test_coordinates_out_of_range_is_error(self):
        regions = (
            atlas.SkeletonRegion(
                id="r1", label="R1", layout=atlas.RegionLayout(0.9, 0.9, 0.5, 0.5)
            ),
        )
        report = atlas.validate_skeleton(_minimal_skeleton(regions=regions))
        assert any("はみ出し" in e for e in report.errors)

    def test_region_overlap_is_warning(self):
        regions = (
            atlas.SkeletonRegion(id="r1", label="R1", layout=atlas.RegionLayout(0.1, 0.1, 0.4, 0.4)),
            atlas.SkeletonRegion(id="r2", label="R2", layout=atlas.RegionLayout(0.3, 0.3, 0.4, 0.4)),
        )
        report = atlas.validate_skeleton(_minimal_skeleton(regions=regions))
        assert report.ok
        assert any("重なって" in w for w in report.warnings)

    def test_duplicate_concept_id_is_error(self):
        regions = (
            atlas.SkeletonRegion(
                id="r1",
                label="R1",
                concepts=(
                    atlas.SkeletonConcept(id="dup", label="A"),
                    atlas.SkeletonConcept(id="dup", label="B"),
                ),
            ),
        )
        report = atlas.validate_skeleton(_minimal_skeleton(regions=regions))
        assert any("重複" in e for e in report.errors)

    def test_edge_referencing_unknown_id_is_error(self):
        report = atlas.validate_skeleton(
            _minimal_skeleton(edges=(atlas.SkeletonEdge("region_a", "nowhere"),))
        )
        assert any("未知" in e for e in report.errors)

    def test_frozen_with_unreviewed_binding_is_error(self):
        skeleton = _minimal_skeleton(
            status=atlas.STATUS_FROZEN,
            version="2026.1",
            reviewed_by=("t1",),
            changelog=(atlas.ChangelogEntry("2026.1", "初版"),),
            concept_bindings=(atlas.ConceptBinding("concept_a", "uuid-1", reviewed=False),),
        )
        report = atlas.validate_skeleton(skeleton)
        assert any("未レビューの concept_binding" in e for e in report.errors)


# ---------------------------------------------------------------------------
# 学習者向けビュー (draft 非公開・未レビュー seed_status 除去)
# ---------------------------------------------------------------------------


class TestLearnerView:
    def test_draft_is_never_learner_visible(self):
        draft = _minimal_skeleton()
        assert not draft.is_learner_visible
        with pytest.raises(atlas.SkeletonNotVisibleError):
            atlas.learner_view(draft)

    def test_unreviewed_seed_status_is_stripped(self):
        regions = (
            atlas.SkeletonRegion(
                id="r1",
                label="R1",
                concepts=(
                    atlas.SkeletonConcept(
                        id="c1", label="C1", seed_status=atlas.SeedStatus("verified", reviewed=False)
                    ),
                    atlas.SkeletonConcept(
                        id="c2", label="C2", seed_status=atlas.SeedStatus("assumed", reviewed=True)
                    ),
                ),
            ),
        )
        frozen = atlas.freeze_skeleton(
            _minimal_skeleton(regions=regions), version="2026.1", reviewed_by=["t1"]
        )
        view = atlas.learner_view(frozen)
        concepts = {c["id"]: c for c in view["regions"][0]["concepts"]}
        assert "seed_status" not in concepts["c1"]
        assert concepts["c2"]["seed_status"] == "assumed"
        assert view["skeleton_version"] == "2026.1"


# ---------------------------------------------------------------------------
# 凍結
# ---------------------------------------------------------------------------


class TestFreeze:
    def test_freeze_records_attribution_and_changelog(self):
        frozen = atlas.freeze_skeleton(
            _minimal_skeleton(), version="2026.1", reviewed_by=["faculty-1"], note="初版"
        )
        assert frozen.status == atlas.STATUS_FROZEN
        assert frozen.reviewed_by == ("faculty-1",)
        assert frozen.changelog[-1].version == "2026.1"
        assert frozen.is_learner_visible

    def test_freeze_requires_reviewer(self):
        with pytest.raises(atlas.SkeletonFreezeError):
            atlas.freeze_skeleton(_minimal_skeleton(), version="2026.1", reviewed_by=[])

    def test_frozen_skeleton_cannot_be_refrozen(self):
        frozen = atlas.freeze_skeleton(
            _minimal_skeleton(), version="2026.1", reviewed_by=["t1"]
        )
        with pytest.raises(atlas.SkeletonFreezeError):
            atlas.freeze_skeleton(frozen, version="2026.2", reviewed_by=["t1"])


# ---------------------------------------------------------------------------
# シリアライズの再現性 (受け入れ条件4: 再ビルドでバイト同一)
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_dump_is_deterministic(self):
        frozen = atlas.freeze_skeleton(
            _minimal_skeleton(), version="2026.1", reviewed_by=["t1"], note="初版"
        )
        assert atlas.dump_skeleton(frozen) == atlas.dump_skeleton(frozen)

    def test_load_dump_roundtrip_is_stable(self, tmp_path):
        frozen = atlas.freeze_skeleton(
            _minimal_skeleton(), version="2026.1", reviewed_by=["t1"], note="初版"
        )
        path = tmp_path / "skeleton.yaml"
        atlas.write_skeleton(frozen, path)
        text1 = path.read_text(encoding="utf-8")
        reloaded = atlas.load_skeleton(path)
        assert atlas.dump_skeleton(reloaded) == text1

    def test_bundled_particle_physics_skeleton_is_reproducible(self):
        """同梱済みの骨格が dump(load(x)) == x を満たす (再ビルドでバイト同一)。"""
        from core.cartridges import _cartridges_root

        path = _cartridges_root() / "particle_physics" / atlas.SKELETON_FILENAME
        assert path.exists(), "particle_physics に骨格が同梱されていること (受け入れ条件1)"
        text = path.read_text(encoding="utf-8")
        assert atlas.dump_skeleton(atlas.load_skeleton(path)) == text
