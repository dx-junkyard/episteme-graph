"""core/atlas_generator.py の単体テスト (Issue A-2)。

LLM出力の後処理 (上限・座標クランプ・語彙・来歴記録) を検証する。
LLM は呼び出さず、GeneratedSkeleton を直接与える。
"""

from __future__ import annotations

import pytest

from core import atlas
from core.atlas_generator import (
    GeneratedConcept,
    GeneratedEdge,
    GeneratedRegion,
    GeneratedSkeleton,
    PatchApplyError,
    apply_json_patch,
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
                    # 領域はキャンバス内のグリッドに置く (x/y は正規化座標)。
                    # 一列に並べると MAX_REGIONS の引き上げ (7→12,
                    # knowledge_landscape_design.md §6.2) でキャンバス右端を
                    # 越えてしまうため、行方向へ折り返す。
                    x=0.24 * (i % 4),
                    y=0.24 * (i // 4),
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


# ---------------------------------------------------------------------------
# JSON Patch 適用器 (skeleton editor upgrade P4): AIアシスト提案の適用に使う
# ---------------------------------------------------------------------------


def _patch_skeleton() -> dict:
    return {
        "regions": [
            {
                "id": "r1",
                "label": "R1",
                "layout": {"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3},
                "concepts": [
                    {"id": "c1", "label": "旧", "layout": {"x": 0.5, "y": 0.5}},
                ],
            }
        ],
        "edges": [],
    }


class TestApplyJsonPatch:
    def test_replace_is_non_destructive(self):
        sk = _patch_skeleton()
        out = apply_json_patch(
            sk,
            [{"op": "replace", "path": "/regions/0/concepts/0/label", "value_json": '"新"'}],
        )
        assert out["regions"][0]["concepts"][0]["label"] == "新"
        # 元 dict は変更されない
        assert sk["regions"][0]["concepts"][0]["label"] == "旧"

    def test_add_to_array_end(self):
        out = apply_json_patch(
            _patch_skeleton(),
            [
                {
                    "op": "add",
                    "path": "/regions/0/concepts/-",
                    "value_json": '{"id":"c2","label":"追加","layout":{"x":0.2,"y":0.2}}',
                }
            ],
        )
        assert len(out["regions"][0]["concepts"]) == 2
        assert out["regions"][0]["concepts"][1]["id"] == "c2"

    def test_remove(self):
        out = apply_json_patch(
            _patch_skeleton(), [{"op": "remove", "path": "/regions/0/concepts/0"}]
        )
        assert out["regions"][0]["concepts"] == []

    def test_bad_path_raises(self):
        with pytest.raises(PatchApplyError):
            apply_json_patch(
                _patch_skeleton(),
                [{"op": "replace", "path": "/regions/9/label", "value_json": '"x"'}],
            )

    def test_out_of_range_final_index_raises_patch_error(self):
        # 末尾トークンの範囲外インデックスも IndexError ではなく PatchApplyError
        with pytest.raises(PatchApplyError):
            apply_json_patch(
                _patch_skeleton(),
                [{"op": "replace", "path": "/regions/0/concepts/9", "value_json": "{}"}],
            )
        with pytest.raises(PatchApplyError):
            apply_json_patch(_patch_skeleton(), [{"op": "remove", "path": "/regions/5"}])

    def test_non_numeric_list_token_raises_patch_error(self):
        with pytest.raises(PatchApplyError):
            apply_json_patch(
                _patch_skeleton(),
                [{"op": "add", "path": "/regions/x", "value_json": "{}"}],
            )

    def test_unsupported_op_raises(self):
        with pytest.raises(PatchApplyError):
            apply_json_patch(_patch_skeleton(), [{"op": "copy", "path": "/regions/0"}])

    def test_patched_result_can_be_parsed_and_validated(self):
        out = apply_json_patch(
            _patch_skeleton(),
            [{"op": "replace", "path": "/regions/0/concepts/0/label", "value_json": '"新ラベル"'}],
        )
        parsed = atlas.parse_skeleton(out)
        assert parsed.regions[0].concepts[0].label == "新ラベル"
