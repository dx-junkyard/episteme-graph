"""分野の地図 — コーパス概念の骨格配置 (Issue E-2) core.atlas_placement のユニットテスト。

受け入れ条件3 (同一入力での再実行が同一の配置・状態を返す) をここで固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import atlas_placement as ap  # noqa: E402


class TestVectorOps:
    def test_cosine_distance_identical_is_zero(self):
        assert ap.cosine_distance([1.0, 0.0], [2.0, 0.0]) < 1e-9

    def test_cosine_distance_orthogonal_is_one(self):
        assert abs(ap.cosine_distance([1.0, 0.0], [0.0, 1.0]) - 1.0) < 1e-9

    def test_zero_vector_is_farthest(self):
        assert ap.cosine_distance([0.0, 0.0], [1.0, 0.0]) == 2.0

    def test_mean_vector(self):
        assert ap.mean_vector([[1.0, 0.0], [0.0, 1.0]]) == [0.5, 0.5]

    def test_mean_vector_empty_is_none(self):
        assert ap.mean_vector([]) is None

    def test_parse_vector_from_pgvector_text(self):
        assert ap.parse_vector("[0.1,0.2,0.3]") == [0.1, 0.2, 0.3]

    def test_parse_vector_from_list(self):
        assert ap.parse_vector([1, 2]) == [1.0, 2.0]

    def test_parse_vector_invalid(self):
        assert ap.parse_vector("not a vector") is None
        assert ap.parse_vector(None) is None


class TestAssignment:
    def test_nearest_region_wins(self):
        results = ap.assign_concepts_to_regions(
            {"c1": [1.0, 0.0]},
            {"ra": [0.9, 0.1], "rb": [0.0, 1.0]},
        )
        assert results[0].region_id == "ra"
        assert results[0].method == "embedding"

    def test_binding_overrides_embedding(self):
        """concept_bindings (レビュー済み明示対応) は埋め込み割当をスキップする。"""
        results = ap.assign_concepts_to_regions(
            {"c1": [1.0, 0.0]},
            {"ra": [0.9, 0.1], "rb": [0.0, 1.0]},
            bindings={"c1": "rb"},
        )
        assert results[0].region_id == "rb"
        assert results[0].method == "binding"

    def test_distance_over_threshold_is_unplaced(self):
        """信頼度の低い割当は未配置 (誤配置より非表示を優先)。"""
        results = ap.assign_concepts_to_regions(
            {"c1": [1.0, 0.0]},
            {"ra": [0.0, 1.0]},  # 直交 → 距離 1.0 > 閾値
        )
        assert results[0].region_id is None
        assert results[0].method == "unplaced"

    def test_no_prototypes_is_unplaced(self):
        results = ap.assign_concepts_to_regions({"c1": [1.0, 0.0]}, {})
        assert results[0].region_id is None

    def test_deterministic_reproducibility(self):
        """受け入れ条件3: 同一入力 → 同一の割当 (タイブレーク含む)。"""
        vectors = {"a": [0.5, 0.5], "b": [1.0, 0.0], "c": [0.0, 1.0]}
        prototypes = {"r1": [0.5, 0.5], "r2": [0.5, 0.5]}  # 完全同距離
        first = ap.assign_concepts_to_regions(vectors, prototypes)
        second = ap.assign_concepts_to_regions(dict(reversed(list(vectors.items()))), prototypes)
        assert first == second
        # 同距離タイは region id の辞書順で r1
        assert first[0].region_id == "r1"

    def test_output_sorted_by_concept_key(self):
        results = ap.assign_concepts_to_regions(
            {"z": [1.0, 0.0], "a": [1.0, 0.0]}, {"r": [1.0, 0.0]}
        )
        assert [r.concept_key for r in results] == ["a", "z"]


class TestLayout:
    REGION = {"x": 0.1, "y": 0.2, "w": 0.4, "h": 0.3}

    def test_layout_is_inside_region(self):
        x, y = ap.layout_in_region("concept", self.REGION, skeleton_version="2026.1")
        assert 0.1 <= x <= 0.5
        assert 0.2 <= y <= 0.5

    def test_layout_is_deterministic(self):
        """受け入れ条件3: 乱数を使わず、同一 (骨格版, 概念) → 同一座標。"""
        a = ap.layout_in_region("concept", self.REGION, skeleton_version="2026.1")
        b = ap.layout_in_region("concept", self.REGION, skeleton_version="2026.1")
        assert a == b

    def test_layout_changes_with_version(self):
        a = ap.layout_in_region("concept", self.REGION, skeleton_version="2026.1")
        b = ap.layout_in_region("concept", self.REGION, skeleton_version="2027.1")
        assert a != b

    def test_layout_avoids_occupied(self):
        first = ap.layout_in_region("concept", self.REGION, skeleton_version="v")
        second = ap.layout_in_region(
            "concept2", self.REGION, skeleton_version="v", occupied=[first]
        )
        assert ((first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2) ** 0.5 >= 0.08

    def test_fog_dots_deterministic_and_inside(self):
        dots1 = ap.fog_dots("lattice_qcd", self.REGION, skeleton_version="2026.1")
        dots2 = ap.fog_dots("lattice_qcd", self.REGION, skeleton_version="2026.1")
        assert dots1 == dots2
        assert len(dots1) == 3
        for x, y in dots1:
            assert 0.1 <= x <= 0.5
            assert 0.2 <= y <= 0.5
