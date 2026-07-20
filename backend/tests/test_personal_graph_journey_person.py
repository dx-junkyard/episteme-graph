"""``core/personal_graph/journey.py`` のコース横断拡張（Phase P-2「コース横断の橋」）の
純粋ユニットテスト。

仕様の正本は ``/Users/Shared/issues/episteme_graph_personal_knowledge_network_ux_proposal.md``
§9 Phase P-2、および ``docs/features/personal_knowledge_network_design.md`` §6。

検証対象は2つの純粋関数（DB 接続を持たない fake データ契約）:

- ``build_person_journey(start_node, network, local_graph, identity_links, library_entries,
  atlas_by_course, course_titles, viewable_document_ids)`` — コース横断版の旅の traversal。
- ``_has_cross_course_sibling(start_node, person_network, atlas_by_course)`` — 単一コース
  スコープの ``journey_for_node`` が返す ``cross_course_hint`` の真偽判定。

既存 ``test_personal_graph_journey.py``（``build_journey`` 用の37テスト）は変更しない
（全部green維持）。このファイルはそれと同じ流儀（fake ビルダ + 純粋関数呼び出し）を踏襲する。

注意: このファイルは ``backend/core/personal_graph/journey.py`` に依存する。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.personal_graph.journey import (  # noqa: E402
    MAX_FANOUT_PER_SEGMENT,
    MAX_STEPS,
    _has_cross_course_sibling,
    build_person_journey,
)
from core.personal_graph.schema import (  # noqa: E402
    NODE_KIND_QUESTION,
    NODE_KIND_TENSION,
    PersonalAnchor,
    PersonalNetwork,
    PersonalNode,
)


# ---------------------------------------------------------------------------
# fake ビルダ（既存 test_personal_graph_journey.py と同じ流儀 + course_id 対応）
# ---------------------------------------------------------------------------


def _node(
    *,
    id_: str,
    anchor: PersonalAnchor,
    node_kind: str = NODE_KIND_TENSION,
    topic_id: str | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
    label: str = "この件について",
    course_id: str | None = "courseA",
) -> PersonalNode:
    return PersonalNode(
        id=id_,
        node_kind=node_kind,
        label=label,
        anchor=anchor,
        topic_id=topic_id,
        created_at=created_at,
        facts=[],
        source={},
        course_id=course_id,
    )


def _component_anchor(component_id: str = "c1", atlas_node_id: str | None = None) -> PersonalAnchor:
    return PersonalAnchor(anchor_type="component", anchor_id=component_id, atlas_node_id=atlas_node_id)


def _claim_anchor(claim_id: str = "claim1", atlas_node_id: str | None = None) -> PersonalAnchor:
    return PersonalAnchor(anchor_type="claim", anchor_id=claim_id, atlas_node_id=atlas_node_id)


def _topic_anchor(topic_id: str = "topicX", atlas_node_id: str | None = None) -> PersonalAnchor:
    return PersonalAnchor(anchor_type="topic", anchor_id=topic_id, atlas_node_id=atlas_node_id)


def _main_node(component_id: str, label: str, *, member_component_ids=None, linked_claim_ids=None, display_order: int = 0) -> dict:
    return {
        "component_id": component_id,
        "label": label,
        "graph_layer": "main",
        "member_component_ids": member_component_ids or [],
        "linked_claim_ids": linked_claim_ids or [],
        "display_order": display_order,
    }


def _link(
    *,
    id_: str,
    shared_part_id: str,
    instance_element_type: str = "theory_component",
    instance_element_id: str = "c1",
    instance_document_id: str = "docA",
    created_at: str = "2026-01-01T00:00:00Z",
) -> dict:
    return {
        "id": id_,
        "shared_part_id": shared_part_id,
        "instance_element_type": instance_element_type,
        "instance_element_id": instance_element_id,
        "instance_document_id": instance_document_id,
        "status": "confirmed",
        "created_at": created_at,
    }


_EMPTY_GRAPH: dict = {}


def _journey(
    start,
    network,
    *,
    local_graph=None,
    links=None,
    library_entries=None,
    atlas_by_course=None,
    course_titles=None,
    viewable_document_ids=None,
):
    """既定値付きの ``build_person_journey`` 呼び出しヘルパ（テスト本文を短くする）。"""
    return build_person_journey(
        start,
        network,
        local_graph if local_graph is not None else _EMPTY_GRAPH,
        links if links is not None else [],
        library_entries if library_entries is not None else {},
        atlas_by_course if atlas_by_course is not None else {},
        course_titles if course_titles is not None else {},
        viewable_document_ids if viewable_document_ids is not None else set(),
    )


# ---------------------------------------------------------------------------
# [5] コース横断の兄弟ノード: 事実文にコース出所を含める
# ---------------------------------------------------------------------------


class TestCrossCourseSiblingFacts:
    def test_cross_course_sibling_with_same_atlas_node_id_carries_course_title(self):
        start = _node(
            id_="n1",
            anchor=_topic_anchor("topicX", atlas_node_id="atlas_1"),
            course_id="courseA",
            label="わたしの問い",
        )
        other = _node(
            id_="n2",
            anchor=_topic_anchor("topicY", atlas_node_id="atlas_1"),
            course_id="courseB",
            label="以前の再構成",
            node_kind=NODE_KIND_QUESTION,
        )
        network = PersonalNetwork(nodes=[start, other])
        atlas_by_course = {
            "courseA": {"topicX": "atlas_1"},
            "courseB": {"topicY": "atlas_1"},
        }
        course_titles = {"courseB": "量子力学基礎"}
        result = _journey(start, network, atlas_by_course=atlas_by_course, course_titles=course_titles)
        personal_steps = [s for s in result["steps"] if s["ref"]["kind"] == "personal_node"]
        assert len(personal_steps) == 1
        assert personal_steps[0]["ref"]["id"] == "n2"
        assert personal_steps[0]["ref"]["course_id"] == "courseB"
        assert "量子力学基礎" in personal_steps[0]["fact"]
        assert "以前の再構成" in personal_steps[0]["fact"]
        # 「おすすめ」「学習済み」等の評価文にしない
        assert "おすすめ" not in personal_steps[0]["fact"]
        assert "学習済み" not in personal_steps[0]["fact"]

    def test_same_course_sibling_keeps_existing_wording(self):
        start = _node(id_="n1", anchor=_topic_anchor("topicX", atlas_node_id="atlas_1"), course_id="courseA")
        other = _node(
            id_="n2", anchor=_topic_anchor("topicX", atlas_node_id="atlas_1"), course_id="courseA",
            label="別の問い", created_at="2026-02-01T00:00:00Z",
        )
        network = PersonalNetwork(nodes=[start, other])
        atlas_by_course = {"courseA": {"topicX": "atlas_1"}}
        result = _journey(start, network, atlas_by_course=atlas_by_course)
        personal_steps = [s for s in result["steps"] if s["ref"]["kind"] == "personal_node"]
        assert len(personal_steps) == 1
        assert "あなたの別の" in personal_steps[0]["fact"]
        assert "もこの近くにあります" in personal_steps[0]["fact"]
        # 別コース文言（「で残した」）は使わない
        assert "で残した" not in personal_steps[0]["fact"]

    def test_unknown_course_title_falls_back_to_generic_wording(self):
        start = _node(id_="n1", anchor=_topic_anchor("topicX", atlas_node_id="atlas_1"), course_id="courseA")
        other = _node(
            id_="n2", anchor=_topic_anchor("topicY", atlas_node_id="atlas_1"), course_id="courseZ",
            label="過去の問い",
        )
        network = PersonalNetwork(nodes=[start, other])
        atlas_by_course = {
            "courseA": {"topicX": "atlas_1"},
            "courseZ": {"topicY": "atlas_1"},
        }
        # course_titles に courseZ が無い
        result = _journey(start, network, atlas_by_course=atlas_by_course, course_titles={})
        personal_steps = [s for s in result["steps"] if s["ref"]["kind"] == "personal_node"]
        assert len(personal_steps) == 1
        assert "以前の学習" in personal_steps[0]["fact"]


# ---------------------------------------------------------------------------
# [5] アンカー完全一致（atlas 無しでも成立）
# ---------------------------------------------------------------------------


class TestCrossCourseSiblingByAnchorMatchWithoutAtlas:
    def test_component_anchor_exact_match_across_courses_without_atlas(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"), course_id="courseA")
        other = _node(id_="n2", anchor=_component_anchor("c1"), course_id="courseB", label="別コースの引っかかり")
        network = PersonalNetwork(nodes=[start, other])
        # atlas_by_course・course_titles とも空——atlas 解決は一切できない
        result = _journey(start, network)
        personal_steps = [s for s in result["steps"] if s["ref"]["kind"] == "personal_node"]
        assert len(personal_steps) == 1
        assert personal_steps[0]["ref"]["id"] == "n2"
        assert personal_steps[0]["ref"]["course_id"] == "courseB"
        # atlas step [4] 自体は出ない(atlas_node_id が無いため)
        assert not any(s["ref"]["kind"] == "atlas_node" for s in result["steps"])

    def test_claim_anchor_exact_match_across_courses(self):
        start = _node(id_="n1", anchor=_claim_anchor("claim1"), course_id="courseA")
        other = _node(id_="n2", anchor=_claim_anchor("claim1"), course_id="courseB")
        network = PersonalNetwork(nodes=[start, other])
        result = _journey(start, network)
        personal_steps = [s for s in result["steps"] if s["ref"]["kind"] == "personal_node"]
        assert [s["ref"]["id"] for s in personal_steps] == ["n2"]

    def test_different_anchor_id_does_not_match(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"), course_id="courseA")
        other = _node(id_="n2", anchor=_component_anchor("c2"), course_id="courseB")
        network = PersonalNetwork(nodes=[start, other])
        result = _journey(start, network)
        assert not any(s["ref"]["kind"] == "personal_node" for s in result["steps"])

    def test_topic_anchor_pair_with_identical_topic_id_but_no_atlas_does_not_match(self):
        """topic アンカーは「非topic」判定から除外されるため、topic_id が偶然同じ文字列
        でも atlas 解決が伴わなければ同一視しない(コース間で topic_id が無関係な値になりうる)。"""
        start = _node(id_="n1", anchor=_topic_anchor("sameTopicId", atlas_node_id=None), course_id="courseA")
        other = _node(id_="n2", anchor=_topic_anchor("sameTopicId", atlas_node_id=None), course_id="courseB")
        network = PersonalNetwork(nodes=[start, other])
        result = _journey(start, network)
        assert not any(s["ref"]["kind"] == "personal_node" for s in result["steps"])


# ---------------------------------------------------------------------------
# [3] viewable_document_ids フィルタ(コース横断)
# ---------------------------------------------------------------------------


class TestViewableDocumentFiltering:
    def test_hub_hit_outside_viewable_document_ids_is_silently_omitted(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"), course_id="courseA")
        links = [_link(id_="link1", shared_part_id="sp1", instance_document_id="docA")]
        library_entries = {
            "sp1": {
                "name": "共通装置H",
                "other_documents": [
                    {"document_id": "docB", "title": "論文B"},
                    {"document_id": "docC", "title": "論文C(閲覧不可)"},
                ],
            }
        }
        result = _journey(
            start, PersonalNetwork(nodes=[start]), links=links, library_entries=library_entries,
            viewable_document_ids={"docB"},
        )
        doc_steps = [s for s in result["steps"] if s["ref"]["kind"] == "document"]
        assert len(doc_steps) == 1
        assert doc_steps[0]["ref"]["id"] == "docB"
        all_facts = " ".join(s["fact"] for s in result["steps"])
        assert "論文C" not in all_facts
        # 件数への言及も無い(黙って省く)
        assert not re.search(r"\d", all_facts)

    def test_no_viewable_documents_produces_no_document_step_and_no_count_mention(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"), course_id="courseA")
        links = [_link(id_="link1", shared_part_id="sp1", instance_document_id="docA")]
        library_entries = {
            "sp1": {"name": "H", "other_documents": [{"document_id": "docC", "title": "外部教材"}]},
        }
        result = _journey(
            start, PersonalNetwork(nodes=[start]), links=links, library_entries=library_entries,
            viewable_document_ids=set(),
        )
        assert not any(s["ref"]["kind"] == "document" for s in result["steps"])
        assert "docC" not in " ".join(s["fact"] for s in result["steps"])


# ---------------------------------------------------------------------------
# fan-out / MAX_STEPS 上限(コース横断でも維持される)
# ---------------------------------------------------------------------------


class TestFanoutAndTruncation:
    def test_cross_course_sibling_fanout_capped(self):
        start = _node(id_="n0", anchor=_component_anchor("c0"), course_id="courseA")
        siblings = [
            _node(
                id_=f"s{i}", anchor=_component_anchor("c0"), course_id=f"course{i}",
                created_at=f"2026-01-{i + 1:02d}T00:00:00Z",
            )
            for i in range(MAX_FANOUT_PER_SEGMENT + 4)
        ]
        network = PersonalNetwork(nodes=[start] + siblings)
        result = _journey(start, network)
        personal_steps = [s for s in result["steps"] if s["ref"]["kind"] == "personal_node"]
        assert len(personal_steps) == MAX_FANOUT_PER_SEGMENT

    def test_total_steps_truncated_at_max_steps_with_cross_course_siblings(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"), course_id="courseA", topic_id="topic1")
        local_graph = {
            "nodes": [_main_node("c1", "Equation system")]
            + [_main_node(f"nb{i}", f"Neighbor{i}") for i in range(MAX_FANOUT_PER_SEGMENT)],
            "edges": [
                {"source_component_id": "c1", "target_component_id": f"nb{i}"}
                for i in range(MAX_FANOUT_PER_SEGMENT)
            ],
        }
        links = [_link(id_=f"link{i}", shared_part_id=f"sp{i}") for i in range(MAX_FANOUT_PER_SEGMENT)]
        library_entries = {
            f"sp{i}": {
                "name": f"H{i}",
                "other_documents": [{"document_id": f"doc{i}", "title": f"Paper{i}"}],
            }
            for i in range(MAX_FANOUT_PER_SEGMENT)
        }
        viewable_document_ids = {f"doc{i}" for i in range(MAX_FANOUT_PER_SEGMENT)}
        atlas_by_course = {"courseA": {"topic1": "atlas_1"}, "courseB": {"topic1": "atlas_1"}}
        siblings = [
            _node(
                id_=f"sib{i}", anchor=_topic_anchor("topic1", atlas_node_id="atlas_1"), course_id="courseB",
                created_at=f"2026-02-{i + 1:02d}T00:00:00Z",
            )
            for i in range(MAX_FANOUT_PER_SEGMENT)
        ]
        network = PersonalNetwork(nodes=[start] + siblings)
        result = _journey(
            start, network, local_graph=local_graph, links=links, library_entries=library_entries,
            atlas_by_course=atlas_by_course, viewable_document_ids=viewable_document_ids,
        )
        assert result["truncated"] is True
        assert len(result["steps"]) == MAX_STEPS


# ---------------------------------------------------------------------------
# 決定論順
# ---------------------------------------------------------------------------


class TestDeterministicOrder:
    def test_sibling_order_independent_of_network_order_across_courses(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"), course_id="courseA")
        sib1 = _node(
            id_="sib1", anchor=_component_anchor("c1"), course_id="courseB",
            created_at="2026-01-01T00:00:00Z",
        )
        sib2 = _node(
            id_="sib2", anchor=_component_anchor("c1"), course_id="courseC",
            created_at="2026-02-01T00:00:00Z",
        )
        r1 = _journey(start, PersonalNetwork(nodes=[start, sib1, sib2]))
        r2 = _journey(start, PersonalNetwork(nodes=[sib2, sib1, start]))
        assert r1 == r2

    def test_no_digits_in_cross_course_facts(self):
        start = _node(id_="n1", anchor=_topic_anchor("topicX", atlas_node_id="atlas_1"), course_id="courseA")
        other = _node(
            id_="n2", anchor=_topic_anchor("topicY", atlas_node_id="atlas_1"), course_id="courseB",
            label="以前の再構成",
        )
        network = PersonalNetwork(nodes=[start, other])
        atlas_by_course = {"courseA": {"topicX": "atlas_1"}, "courseB": {"topicY": "atlas_1"}}
        course_titles = {"courseB": "量子力学基礎"}
        result = _journey(start, network, atlas_by_course=atlas_by_course, course_titles=course_titles)
        for step in result["steps"]:
            assert not re.search(r"\d", step["fact"]), step["fact"]


# ---------------------------------------------------------------------------
# _has_cross_course_sibling: journey_for_node の cross_course_hint が使う真偽判定
# ---------------------------------------------------------------------------


class TestHasCrossCourseSibling:
    def test_same_course_only_returns_false(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"), course_id="courseA")
        same_course_sibling = _node(id_="n2", anchor=_component_anchor("c1"), course_id="courseA")
        network = PersonalNetwork(nodes=[start, same_course_sibling])
        assert _has_cross_course_sibling(start, network, {}) is False

    def test_cross_course_anchor_match_returns_true(self):
        start = _node(id_="n1", anchor=_claim_anchor("claim1"), course_id="courseA")
        other_course_sibling = _node(id_="n2", anchor=_claim_anchor("claim1"), course_id="courseB")
        network = PersonalNetwork(nodes=[start, other_course_sibling])
        assert _has_cross_course_sibling(start, network, {}) is True

    def test_cross_course_atlas_resolution_match_returns_true(self):
        start = _node(id_="n1", anchor=_topic_anchor("topicX", atlas_node_id=None), course_id="courseA", topic_id="topicX")
        other = _node(id_="n2", anchor=_topic_anchor("topicY", atlas_node_id=None), course_id="courseB", topic_id="topicY")
        network = PersonalNetwork(nodes=[start, other])
        atlas_by_course = {"courseA": {"topicX": "atlas_1"}, "courseB": {"topicY": "atlas_1"}}
        assert _has_cross_course_sibling(start, network, atlas_by_course) is True

    def test_topic_anchor_pairs_are_judged_by_atlas_resolution_only(self):
        """topic アンカー同士は topic_id の生文字列一致では判定しない
        (別コースで無関係な topic が同じ id を持つことがある)。atlas 解決が一致しない
        限り False であり、たとえ topic_id の文字列が同じでも一致とはみなさない。"""
        start = _node(id_="n1", anchor=_topic_anchor("sameId", atlas_node_id=None), course_id="courseA", topic_id="sameId")
        other = _node(id_="n2", anchor=_topic_anchor("sameId", atlas_node_id=None), course_id="courseB", topic_id="sameId")
        network = PersonalNetwork(nodes=[start, other])
        # atlas binding が無い(=解決できない)ので、topic_id が同じでも False
        assert _has_cross_course_sibling(start, network, {}) is False

    def test_no_other_course_nodes_returns_false(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"), course_id="courseA")
        network = PersonalNetwork(nodes=[start])
        assert _has_cross_course_sibling(start, network, {}) is False

    def test_self_is_excluded_from_consideration(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"), course_id="courseA")
        network = PersonalNetwork(nodes=[start])
        assert _has_cross_course_sibling(start, network, {}) is False
