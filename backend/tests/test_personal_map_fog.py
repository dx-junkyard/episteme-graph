"""わたしの地図「名前のある霧」（atlas 近傍提示, 広がりの装置1）のユニットテスト + ガードレール。

仕様の正本は ``docs/features/personal_map_nearby_design.md``（PN-1〜PN-7 / PMN-1〜PMN-7）。
``test_personal_map_nearby.py`` と同じ流儀（fake データ契約 + queries を monkeypatch）で、
DB にも FastAPI にも触らずに導出規則を固定する。

固定する不変条項:

1. ``core/personal_graph/atlas_fog.py`` が FastAPI / services / core.llm / openai を import しない
2. ``node_id`` が本人の個人ネットワークに無ければ ``None``（route が 404 にする・PN-1）
3. ``atlas_node_id`` / ``course_id`` が無い場合は事実文で ``available=False``（異常演出しない）
4. 骨格読みは凍結版のみ（``atlas_store.load_learner_skeleton``）— draft を読む経路を作らない
5. neighbors は edge 群 → sibling 群の順・自分自身を除外・全体で最大8件
6. DTO に数値キーが再帰的に現れない（PMN-4）
7. ``me_router`` に書き込みメソッドが増えていない
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import core.atlas as atlas_module  # noqa: E402
import core.atlas_store as atlas_store_module  # noqa: E402
import core.personal_graph.queries as queries_mod  # noqa: E402
from core.personal_graph import atlas_fog as F  # noqa: E402
from core.personal_graph.schema import (  # noqa: E402
    PersonalAnchor,
    PersonalNetwork,
    PersonalNode,
)
from tests.guardrail_helpers import assert_module_tree_does_not_import  # noqa: E402

_QUERIES_SRC = (BACKEND / "core" / "personal_graph" / "queries.py").read_text(encoding="utf-8")
_ROUTE_SRC = (BACKEND / "api" / "routes" / "personal_map.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# fake ビルダ
# ---------------------------------------------------------------------------


def _fake_skeleton():
    return atlas_module.AtlasSkeleton(
        cartridge="physics",
        status=atlas_module.STATUS_FROZEN,
        version="v1",
        reviewed_by=("teacher1",),
        regions=(
            atlas_module.SkeletonRegion(
                id="mechanics",
                label="力学",
                concepts=(
                    atlas_module.SkeletonConcept(id="motion_eq", label="運動方程式"),
                    atlas_module.SkeletonConcept(id="energy", label="エネルギー"),
                ),
            ),
            atlas_module.SkeletonRegion(
                id="waves",
                label="波動",
                concepts=(atlas_module.SkeletonConcept(id="wave_eq", label="波動方程式"),),
            ),
        ),
        edges=(atlas_module.SkeletonEdge(from_id="motion_eq", to_id="wave_eq"),),
    )


def _node(node_id="n1", *, atlas_node_id="motion_eq", course_id="c1"):
    return PersonalNode(
        id=node_id,
        node_kind="question",
        label="この記録の本文",
        anchor=PersonalAnchor(anchor_type="topic", anchor_id="t1", atlas_node_id=atlas_node_id),
        topic_id="t1",
        course_id=course_id,
        created_at="2026-08-01",
        facts=[],
        source={},
    )


_NUMERIC_KEYS = ("confidence", "load_score", "level", "weight", "count", "score", "seed_status")


def _walk(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield f"{path}.{key}", key, item
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _walk(item, f"{path}[{i}]")


# ---------------------------------------------------------------------------
# queries.fetch_atlas_concept_context（骨格読みの単一集約点）
# ---------------------------------------------------------------------------


class TestFetchAtlasConceptContext:
    def test_resolves_region_edge_neighbor_and_siblings(self, monkeypatch):
        monkeypatch.setattr(
            atlas_store_module, "load_learner_skeleton", lambda cartridge_id: _fake_skeleton()
        )
        ctx = queries_mod.fetch_atlas_concept_context("physics", "motion_eq")
        assert ctx is not None
        assert ctx["concept_id"] == "motion_eq"
        assert ctx["concept_label"] == "運動方程式"
        assert ctx["region_id"] == "mechanics"
        assert ctx["region_label"] == "力学"
        assert ctx["edge_neighbor_ids"] == ["wave_eq"]
        assert ctx["edge_neighbors"] == [
            {"id": "wave_eq", "label": "波動方程式", "region_label": "波動"}
        ]
        assert ctx["sibling_concepts"] == [{"id": "energy", "label": "エネルギー"}]

    def test_no_skeleton_returns_none(self, monkeypatch):
        monkeypatch.setattr(atlas_store_module, "load_learner_skeleton", lambda cid: None)
        assert queries_mod.fetch_atlas_concept_context("physics", "motion_eq") is None

    def test_unresolvable_concept_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            atlas_store_module, "load_learner_skeleton", lambda cid: _fake_skeleton()
        )
        assert queries_mod.fetch_atlas_concept_context("physics", "does-not-exist") is None

    def test_exception_during_load_is_fail_soft(self, monkeypatch):
        def _boom(cid):
            raise RuntimeError("db down")

        monkeypatch.setattr(atlas_store_module, "load_learner_skeleton", _boom)
        assert queries_mod.fetch_atlas_concept_context("physics", "motion_eq") is None

    def test_empty_arguments_return_none(self):
        assert queries_mod.fetch_atlas_concept_context("", "motion_eq") is None
        assert queries_mod.fetch_atlas_concept_context("physics", "") is None

    def test_edge_neighbor_not_present_in_any_region_is_dropped_from_enriched_list(
        self, monkeypatch
    ):
        """骨格エッジが指す先が骨格中に実在しない場合、id には残すが label 付き列には出さない。"""
        skeleton = atlas_module.AtlasSkeleton(
            cartridge="physics",
            status=atlas_module.STATUS_FROZEN,
            version="v1",
            reviewed_by=("t1",),
            regions=(
                atlas_module.SkeletonRegion(
                    id="mechanics",
                    label="力学",
                    concepts=(atlas_module.SkeletonConcept(id="motion_eq", label="運動方程式"),),
                ),
            ),
            edges=(atlas_module.SkeletonEdge(from_id="motion_eq", to_id="ghost"),),
        )
        monkeypatch.setattr(atlas_store_module, "load_learner_skeleton", lambda cid: skeleton)
        ctx = queries_mod.fetch_atlas_concept_context("physics", "motion_eq")
        assert ctx["edge_neighbor_ids"] == ["ghost"]
        assert ctx["edge_neighbors"] == []


# ---------------------------------------------------------------------------
# atlas_fog.atlas_neighbors_for_person_node
# ---------------------------------------------------------------------------


class TestAtlasNeighborsForPersonNode:
    def test_unknown_node_id_is_none(self, monkeypatch):
        monkeypatch.setattr(F, "derive_person_network", lambda user_id: PersonalNetwork(nodes=[]))
        assert F.atlas_neighbors_for_person_node("u1", "missing") is None

    def test_other_users_node_is_not_visible(self, monkeypatch):
        """本人以外の user_id で導出されたネットワークにそのノードが無ければ 404 相当。"""
        monkeypatch.setattr(
            F, "derive_person_network", lambda user_id: PersonalNetwork(nodes=[_node("n1")])
        )
        # 別 user のネットワークには同じ node_id が無い、という前提を模す。
        monkeypatch.setattr(F, "derive_person_network", lambda user_id: PersonalNetwork(nodes=[]))
        assert F.atlas_neighbors_for_person_node("someone-else", "n1") is None

    def test_missing_atlas_node_id_is_unavailable_with_the_fixed_note(self, monkeypatch):
        node = _node(atlas_node_id=None)
        monkeypatch.setattr(
            F, "derive_person_network", lambda user_id: PersonalNetwork(nodes=[node])
        )
        result = F.atlas_neighbors_for_person_node("u1", "n1")
        assert result == {
            "available": False,
            "here": None,
            "neighbors": [],
            "note": "この記録は、まだ分野の地図に結びついていません。",
        }

    def test_missing_course_id_is_unavailable(self, monkeypatch):
        node = _node(course_id=None)
        monkeypatch.setattr(
            F, "derive_person_network", lambda user_id: PersonalNetwork(nodes=[node])
        )
        result = F.atlas_neighbors_for_person_node("u1", "n1")
        assert result["available"] is False
        assert result["note"]

    def test_missing_cartridge_id_is_unavailable(self, monkeypatch):
        node = _node()
        monkeypatch.setattr(
            F, "derive_person_network", lambda user_id: PersonalNetwork(nodes=[node])
        )
        monkeypatch.setattr(queries_mod, "fetch_course_cartridge_id", lambda cid: "")
        result = F.atlas_neighbors_for_person_node("u1", "n1")
        assert result["available"] is False

    def test_unresolvable_concept_is_unavailable(self, monkeypatch):
        node = _node()
        monkeypatch.setattr(
            F, "derive_person_network", lambda user_id: PersonalNetwork(nodes=[node])
        )
        monkeypatch.setattr(queries_mod, "fetch_course_cartridge_id", lambda cid: "physics")
        monkeypatch.setattr(
            queries_mod, "fetch_atlas_concept_context", lambda cartridge_id, node_id: None
        )
        result = F.atlas_neighbors_for_person_node("u1", "n1")
        assert result["available"] is False

    def test_success_orders_edges_before_siblings_and_caps_at_eight(self, monkeypatch):
        node = _node()
        monkeypatch.setattr(
            F, "derive_person_network", lambda user_id: PersonalNetwork(nodes=[node])
        )
        monkeypatch.setattr(queries_mod, "fetch_course_cartridge_id", lambda cid: "physics")
        context = {
            "concept_label": "運動方程式",
            "region_label": "力学",
            "edge_neighbor_ids": ["e1", "e2"],
            "edge_neighbors": [
                {"id": "e1", "label": "エッジ1", "region_label": "波動"},
                {"id": "e2", "label": "エッジ2", "region_label": "力学"},
            ],
            "sibling_concepts": [{"id": f"s{i}", "label": f"同領域{i}"} for i in range(10)],
        }
        monkeypatch.setattr(
            queries_mod, "fetch_atlas_concept_context", lambda cartridge_id, node_id: context
        )
        result = F.atlas_neighbors_for_person_node("u1", "n1")
        assert result["available"] is True
        assert result["here"] == {"label": "運動方程式", "region_label": "力学"}
        assert result["note"] is None
        assert len(result["neighbors"]) == F.MAX_NEIGHBORS
        assert [n["relation"] for n in result["neighbors"]] == ["edge", "edge"] + ["sibling"] * 6
        assert result["neighbors"][0] == {
            "id": "e1",
            "label": "エッジ1",
            "region_label": "波動",
            "relation": "edge",
        }
        # sibling は「here」の region_label を継承する。
        assert result["neighbors"][2]["region_label"] == "力学"

    def test_self_reference_and_duplicates_are_excluded(self, monkeypatch):
        node = _node()
        monkeypatch.setattr(
            F, "derive_person_network", lambda user_id: PersonalNetwork(nodes=[node])
        )
        monkeypatch.setattr(queries_mod, "fetch_course_cartridge_id", lambda cid: "physics")
        context = {
            "concept_label": "運動方程式",
            "region_label": "力学",
            "edge_neighbor_ids": ["motion_eq"],
            "edge_neighbors": [{"id": "motion_eq", "label": "自己", "region_label": "力学"}],
            "sibling_concepts": [{"id": "motion_eq", "label": "自己"}, {"id": "s1", "label": "隣"}],
        }
        monkeypatch.setattr(
            queries_mod, "fetch_atlas_concept_context", lambda cartridge_id, node_id: context
        )
        result = F.atlas_neighbors_for_person_node("u1", "n1")
        assert [n["id"] for n in result["neighbors"]] == ["s1"]

    def test_no_numeric_keys_anywhere_in_the_dto(self, monkeypatch):
        node = _node()
        monkeypatch.setattr(
            F, "derive_person_network", lambda user_id: PersonalNetwork(nodes=[node])
        )
        monkeypatch.setattr(queries_mod, "fetch_course_cartridge_id", lambda cid: "physics")
        context = {
            "concept_label": "運動方程式",
            "region_label": "力学",
            "edge_neighbor_ids": ["e1"],
            "edge_neighbors": [{"id": "e1", "label": "エッジ1", "region_label": "波動"}],
            "sibling_concepts": [{"id": "s1", "label": "隣"}],
        }
        monkeypatch.setattr(
            queries_mod, "fetch_atlas_concept_context", lambda cartridge_id, node_id: context
        )
        result = F.atlas_neighbors_for_person_node("u1", "n1")
        hits = [path for path, key, _ in _walk(result) if key in _NUMERIC_KEYS]
        assert hits == [], f"数値・内部キーが DTO に現れた: {hits}"

    def test_unavailable_dto_has_no_numeric_keys(self):
        hits = [path for path, key, _ in _walk(F._unavailable()) if key in _NUMERIC_KEYS]
        assert hits == []


# ---------------------------------------------------------------------------
# ガードレール
# ---------------------------------------------------------------------------


class TestGuardrails:
    def test_personal_graph_tree_does_not_import_fastapi_or_llm(self):
        assert_module_tree_does_not_import(
            BACKEND / "core" / "personal_graph",
            ("fastapi", "services", "core.llm", "openai"),
        )

    def test_atlas_concept_context_reads_frozen_skeleton_only(self):
        """凍結骨格のみを読む正本（``load_learner_skeleton``）を使う。draft を読む経路が無い。"""
        assert "load_learner_skeleton" in _QUERIES_SRC
        assert "load_draft" not in _QUERIES_SRC

    def test_atlas_neighbors_route_is_registered(self):
        assert '@me_router.get("/personal-network/atlas-neighbors")' in _ROUTE_SRC
        assert "atlas_neighbors_for_person_node" in _ROUTE_SRC

    def test_route_takes_no_user_id_parameter(self):
        """PN-1: user_id を受け取るクエリパラメータを作らない（current_user 経由のみ）。"""
        import inspect

        from api.routes.personal_map import get_me_personal_network_atlas_neighbors

        params = set(inspect.signature(get_me_personal_network_atlas_neighbors).parameters)
        assert "user_id" not in params
        assert {"node_id", "current_user"} <= params

    def test_me_router_has_no_write_methods(self):
        for verb in ("post", "put", "patch", "delete"):
            assert f"@me_router.{verb}" not in _ROUTE_SRC

    def test_atlas_fog_module_has_no_write_paths(self):
        src = (BACKEND / "core" / "personal_graph" / "atlas_fog.py").read_text(encoding="utf-8")
        for banned in ("INSERT", "UPDATE ", "DELETE FROM", "session.commit"):
            assert banned not in src
