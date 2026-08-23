"""カテゴリギャップ候補 — ``core/atlas_gaps/patching.py`` の単体テスト。

設計書: ``docs/features/category_gap_candidates_design.md`` §5.5（additive-only・
決定論生成・id 採番・座標の空きスロット配置）/ §5.4（満杯領域の扱い）。

検証観点:
  1. **op は add のみ**（remove / replace を生成しない）
  2. 決定論性（同一入力 → 同一出力）と入力の非破壊
  3. id 衝突回避（領域 id と概念 id は同一名前空間）・日本語ラベルの採番縮退
  4. 満杯（MAX_REGIONS / MAX_CONCEPTS_PER_REGION）は専用例外
  5. 生成した patch を実際に ``apply_json_patch`` で適用でき、骨格として valid
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import atlas  # noqa: E402
from core.atlas_gaps import patching, schema  # noqa: E402
from core.atlas_generator import apply_json_patch  # noqa: E402


def _draft(*, regions=2, concepts=1) -> dict:
    return {
        "version": "",
        "cartridge": "astrophysics",
        "status": "draft",
        "generated_by": "copy_of_frozen:2026.1",
        "reviewed_by": [],
        "changelog": [],
        "regions": [
            {
                "id": f"region_{i}",
                "label": f"領域{i}",
                "layout": {"x": 0.02 + 0.25 * i, "y": 0.03, "w": 0.23, "h": 0.28},
                "concepts": [
                    {
                        "id": f"region_{i}_c{j}",
                        "label": f"概念{i}-{j}",
                        "layout": {"x": 0.25, "y": 0.2 + 0.3 * j},
                    }
                    for j in range(concepts)
                ],
            }
            for i in range(regions)
        ],
        "edges": [],
        "concept_bindings": [],
    }


def _full_skeleton_dict(region_count: int, concept_count: int) -> dict:
    draft = _draft(regions=0, concepts=0)
    draft["regions"] = [
        {
            "id": f"r{i}",
            "label": f"領域{i}",
            "layout": {"x": 0.0, "y": 0.0, "w": 0.05, "h": 0.05},
            "concepts": [
                {"id": f"r{i}_c{j}", "label": f"概念{j}"} for j in range(concept_count)
            ],
        }
        for i in range(region_count)
    ]
    return draft


# ---------------------------------------------------------------------------
# 1. additive-only（§5.5 / 合意事項8）
# ---------------------------------------------------------------------------


class TestAdditiveOnly:
    def test_concept_patch_is_a_single_add(self):
        result = patching.build_gap_patch(
            _draft(), layer="concept", parent_region_id="region_1",
            proposed_label="Cosmic Web",
        )
        assert [op["op"] for op in result["patch"]] == ["add"]
        assert result["patch"][0]["path"] == "/regions/1/concepts/-"

    def test_region_patch_is_a_single_add(self):
        result = patching.build_gap_patch(
            _draft(), layer="region", proposed_label="Gravitational Waves"
        )
        assert [op["op"] for op in result["patch"]] == ["add"]
        assert result["patch"][0]["path"] == "/regions/-"

    def test_module_never_emits_remove_or_replace(self):
        src = (BACKEND / "core" / "atlas_gaps" / "patching.py").read_text(encoding="utf-8")
        for forbidden in ('"remove"', "'remove'", '"replace"', "'replace'"):
            assert forbidden not in src, f"gap 経路の patch に {forbidden} が現れている"

    def test_wrapped_skeleton_gets_prefixed_paths(self):
        wrapped = {"atlas_skeleton": _draft()}
        result = patching.build_gap_patch(
            wrapped, layer="region", proposed_label="Gravitational Waves"
        )
        assert result["patch"][0]["path"] == "/atlas_skeleton/regions/-"


# ---------------------------------------------------------------------------
# 2. 決定論性・非破壊
# ---------------------------------------------------------------------------


class TestDeterminismAndPurity:
    def test_same_input_gives_the_same_output(self):
        draft = _draft()
        a = patching.build_gap_patch(
            draft, layer="concept", parent_region_id="region_0", proposed_label="Cosmic Web"
        )
        b = patching.build_gap_patch(
            _draft(), layer="concept", parent_region_id="region_0",
            proposed_label="Cosmic Web",
        )
        assert a == b

    def test_input_is_not_mutated(self):
        draft = _draft()
        before = copy.deepcopy(draft)
        patching.build_gap_patch(
            draft, layer="concept", parent_region_id="region_0", proposed_label="Cosmic Web"
        )
        patching.build_gap_patch(draft, layer="region", proposed_label="New Region")
        assert draft == before

    def test_concept_slot_avoids_the_occupied_position(self):
        draft = _draft(concepts=1)  # (0.25, 0.2) が埋まっている
        result = patching.build_gap_patch(
            draft, layer="concept", parent_region_id="region_0", proposed_label="Cosmic Web"
        )
        assert result["node"]["layout"] != {"x": 0.25, "y": 0.2}
        assert 0.0 <= result["node"]["layout"]["x"] <= 1.0
        assert 0.0 <= result["node"]["layout"]["y"] <= 1.0

    def test_region_slot_avoids_overlapping_existing_regions(self):
        draft = _draft(regions=2)
        layout = patching.build_gap_patch(
            draft, layer="region", proposed_label="New Region"
        )["node"]["layout"]
        for region in draft["regions"]:
            existing = region["layout"]
            overlaps = not (
                layout["x"] + layout["w"] <= existing["x"]
                or existing["x"] + existing["w"] <= layout["x"]
                or layout["y"] + layout["h"] <= existing["y"]
                or existing["y"] + existing["h"] <= layout["y"]
            )
            assert not overlaps
        assert layout["x"] + layout["w"] <= 1.0
        assert layout["y"] + layout["h"] <= 1.0


# ---------------------------------------------------------------------------
# 3. id 採番
# ---------------------------------------------------------------------------


class TestNodeIds:
    def test_slug_from_ascii_label(self):
        result = patching.build_gap_patch(
            _draft(), layer="concept", parent_region_id="region_0",
            proposed_label="Cosmic Web",
        )
        assert result["node_id"] == "cosmic_web"

    def test_id_collision_is_avoided_across_regions_and_concepts(self):
        draft = _draft()
        draft["regions"][0]["concepts"].append(
            {"id": "cosmic_web", "label": "Cosmic Web (old)"}
        )
        result = patching.build_gap_patch(
            draft, layer="concept", parent_region_id="region_1",
            proposed_label="Cosmic Web",
        )
        assert result["node_id"] == "cosmic_web_2"

    def test_region_id_namespace_is_shared_with_concepts(self):
        draft = _draft()
        draft["regions"][0]["id"] = "cosmic_web"
        result = patching.build_gap_patch(
            draft, layer="concept", parent_region_id="region_1",
            proposed_label="Cosmic Web",
        )
        assert result["node_id"] == "cosmic_web_2"

    def test_japanese_label_falls_back_to_a_deterministic_slug(self):
        concept = patching.build_gap_patch(
            _draft(concepts=2), layer="concept", parent_region_id="region_0",
            proposed_label="重力波天文学",
        )
        assert concept["node_id"] == "region_0_c3"
        region = patching.build_gap_patch(
            _draft(regions=3), layer="region", proposed_label="重力波天文学"
        )
        assert region["node_id"] == "region4"

    def test_generated_ids_match_the_skeleton_id_pattern(self):
        for label in ("Cosmic Web", "重力波天文学", "f(R) gravity", "21cm cosmology"):
            result = patching.build_gap_patch(
                _draft(), layer="region", proposed_label=label
            )
            assert atlas._ID_PATTERN.match(result["node_id"]), result["node_id"]


# ---------------------------------------------------------------------------
# 4. 上限・入力検証
# ---------------------------------------------------------------------------


class TestCapacityAndValidation:
    def test_full_region_raises_capacity_error(self):
        draft = _full_skeleton_dict(2, atlas.MAX_CONCEPTS_PER_REGION)
        with pytest.raises(patching.SkeletonCapacityError) as exc:
            patching.build_gap_patch(
                draft, layer="concept", parent_region_id="r0", proposed_label="Cosmic Web"
            )
        assert str(atlas.MAX_CONCEPTS_PER_REGION) in str(exc.value)
        # 督促ではなく事実 + 解消方法（§5.4）
        assert "上限" in str(exc.value)

    def test_full_map_raises_capacity_error_for_regions(self):
        draft = _full_skeleton_dict(atlas.MAX_REGIONS, 0)
        with pytest.raises(patching.SkeletonCapacityError):
            patching.build_gap_patch(draft, layer="region", proposed_label="New Region")

    def test_capacity_error_is_a_gap_patch_error(self):
        assert issubclass(patching.SkeletonCapacityError, patching.GapPatchError)
        assert issubclass(patching.GapPatchError, ValueError)

    def test_unknown_parent_region_raises(self):
        with pytest.raises(patching.GapPatchError):
            patching.build_gap_patch(
                _draft(), layer="concept", parent_region_id="nowhere",
                proposed_label="Cosmic Web",
            )

    def test_concept_without_parent_raises(self):
        with pytest.raises(patching.GapPatchError):
            patching.build_gap_patch(
                _draft(), layer="concept", parent_region_id="", proposed_label="Cosmic Web"
            )

    @pytest.mark.parametrize("label", ["", "   ", None])
    def test_empty_label_raises(self, label):
        with pytest.raises(patching.GapPatchError):
            patching.build_gap_patch(_draft(), layer="region", proposed_label=label)

    def test_unknown_layer_raises(self):
        with pytest.raises(patching.GapPatchError):
            patching.build_gap_patch(
                _draft(), layer="galaxy", proposed_label="Cosmic Web"
            )

    def test_non_object_draft_raises(self):
        with pytest.raises(patching.GapPatchError):
            patching.build_gap_patch(None, layer="region", proposed_label="X")


# ---------------------------------------------------------------------------
# 5. 生成 patch の適用（既存の適用器・検証器と噛み合うこと）
# ---------------------------------------------------------------------------


class TestPatchAppliesToTheSkeleton:
    def _frozen_shaped(self, draft: dict) -> dict:
        """``validate_skeleton`` が通る最小の凍結相当メタを足す。"""
        out = copy.deepcopy(draft)
        out["status"] = "frozen"
        out["version"] = "2026.2"
        out["reviewed_by"] = ["faculty:t"]
        out["changelog"] = [{"version": "2026.2", "note": "t", "credits": []}]
        return out

    def test_concept_patch_applies_and_validates(self):
        draft = self._frozen_shaped(_draft())
        result = patching.build_gap_patch(
            draft, layer="concept", parent_region_id="region_0",
            proposed_label="Cosmic Web",
        )
        patched = apply_json_patch(draft, result["patch"])
        parsed = atlas.parse_skeleton({"atlas_skeleton": patched})
        report = atlas.validate_skeleton(parsed)
        assert report.ok, [str(e) for e in report.errors]
        assert result["node_id"] in parsed.concept_ids()
        # 元の draft は変わらない
        assert len(draft["regions"][0]["concepts"]) == 1

    def test_region_patch_applies_and_validates_without_overlap_warning(self):
        draft = self._frozen_shaped(_draft())
        result = patching.build_gap_patch(
            draft, layer="region", proposed_label="Gravitational Waves"
        )
        patched = apply_json_patch(draft, result["patch"])
        parsed = atlas.parse_skeleton({"atlas_skeleton": patched})
        report = atlas.validate_skeleton(parsed)
        assert report.ok, [str(e) for e in report.errors]
        assert report.warnings == (), [str(w) for w in report.warnings]
        assert result["node_id"] in parsed.region_ids()

    def test_summary_is_factual(self):
        result = patching.build_gap_patch(
            _draft(), layer="concept", parent_region_id="region_0",
            proposed_label="Cosmic Web",
        )
        summary = result["summary"]
        assert "Cosmic Web" in summary and result["node_id"] in summary
        for forbidden in ("穴", "不足", "未整備", "埋めましょう", "べきです"):
            assert forbidden not in summary

    def test_result_shape_is_stable(self):
        result = patching.build_gap_patch(
            _draft(), layer="region", proposed_label="Gravitational Waves"
        )
        assert set(result) == {
            "patch",
            "node_id",
            "layer",
            "parent_region_id",
            "node",
            "summary",
        }
        assert result["layer"] == schema.GAP_LAYER_REGION
