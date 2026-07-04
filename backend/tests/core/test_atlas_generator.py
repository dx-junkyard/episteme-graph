"""core/atlas_generator.py の単体テスト (Issue A-2)。

LLM出力の後処理 (上限・座標クランプ・語彙・来歴記録) を検証する。
LLM は呼び出さず、GeneratedSkeleton を直接与える。
"""

from __future__ import annotations

from core import atlas
from core.atlas_generator import (
    GeneratedConcept,
    GeneratedEdge,
    GeneratedRegion,
    GeneratedSkeleton,
    normalize_generated,
)


# ---------------------------------------------------------------------------
# 生成後処理 (A-2: スナップショット的な性質のテスト)
# ---------------------------------------------------------------------------


class TestGenerationPostProcessing:
    def _oversized_generated(self) -> GeneratedSkeleton:
        return GeneratedSkeleton(
            regions=[
                GeneratedRegion(
                    id=f"Region {i}!",
                    label=f"領域{i}",
                    x=0.1 * i,
                    y=0.05,
                    w=0.2,
                    h=0.2,
                    concepts=[
                        GeneratedConcept(
                            id=f"Concept {i}-{j}",
                            label=f"概念{i}-{j}",
                            x=1.5,  # 範囲外 → クランプされる
                            y=-0.2,
                            seed_status="verified" if j == 0 else "invalid_status",
                        )
                        for j in range(atlas.MAX_CONCEPTS_PER_REGION + 2)
                    ],
                )
                for i in range(atlas.MAX_REGIONS + 3)
            ],
            edges=[
                GeneratedEdge(from_id="Region 0!", to_id="Region 1!", kind="adjacent"),
                GeneratedEdge(from_id="Region 0!", to_id="ghost", kind="adjacent"),
            ],
        )

    def test_limits_and_coordinates_are_enforced(self):
        skeleton = normalize_generated(
            self._oversized_generated(),
            cartridge_id="particle_physics",
            generated_by="model:test batch:b1",
        )
        assert len(skeleton.regions) == atlas.MAX_REGIONS
        for region in skeleton.regions:
            assert len(region.concepts) == atlas.MAX_CONCEPTS_PER_REGION
            assert region.layout is not None
            assert 0.0 <= region.layout.x <= 1.0
            assert region.layout.x + region.layout.w <= 1.0 + 1e-9
            for concept in region.concepts:
                assert concept.layout is not None
                assert 0.0 <= concept.layout.x <= 1.0
                assert 0.0 <= concept.layout.y <= 1.0
        report = atlas.validate_skeleton(skeleton)
        assert report.ok, report.errors

    def test_generated_draft_is_draft_and_never_reviewed(self):
        skeleton = normalize_generated(
            self._oversized_generated(),
            cartridge_id="particle_physics",
            generated_by="model:test batch:b1",
        )
        assert skeleton.status == atlas.STATUS_DRAFT
        assert skeleton.generated_by == "model:test batch:b1"
        assert skeleton.reviewed_by == ()
        assert not skeleton.is_learner_visible
        # LLM が seed_status を提案しても reviewed は必ず False (最終確定は教員のみ)
        for region in skeleton.regions:
            for concept in region.concepts:
                if concept.seed_status is not None:
                    assert concept.seed_status.reviewed is False
                    assert concept.display_seed_status is None

    def test_invalid_seed_status_is_dropped(self):
        skeleton = normalize_generated(
            self._oversized_generated(),
            cartridge_id="particle_physics",
            generated_by="model:test batch:b1",
        )
        statuses = {
            c.seed_status.value
            for r in skeleton.regions
            for c in r.concepts
            if c.seed_status is not None
        }
        assert statuses <= set(atlas.SEED_STATUS_VALUES)

    def test_edges_to_unknown_ids_are_dropped(self):
        skeleton = normalize_generated(
            self._oversized_generated(),
            cartridge_id="particle_physics",
            generated_by="model:test batch:b1",
        )
        known = set(skeleton.region_ids()) | set(skeleton.concept_ids())
        assert all(e.from_id in known and e.to_id in known for e in skeleton.edges)
        assert len(skeleton.edges) == 1
