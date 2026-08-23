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

import core.personal_graph.derive as derive_mod  # noqa: E402
import core.personal_graph.journey as J  # noqa: E402
import core.personal_graph.queries as queries_mod  # noqa: E402
from core.personal_graph.journey import (  # noqa: E402
    MAX_FANOUT_PER_SEGMENT,
    MAX_STEPS,
    _has_cross_course_sibling,
    build_person_journey,
    journey_for_person_node,
)
from core.personal_graph.nearby import FACT_RANGE_SHARPEN, MAX_DOCUMENTS_SCANNED  # noqa: E402
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
    topic_range=None,
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
        topic_range=topic_range,
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


# ---------------------------------------------------------------------------
# [1'] topic 縮退アンカーの範囲エントリ（純粋部）
# ---------------------------------------------------------------------------


def _stage_main_node(component_id: str, label: str, *, order: int = 0, claims=None) -> dict:
    return _main_node(component_id, label, linked_claim_ids=claims or [], display_order=order)


class TestTopicRangeEntriesPure:
    """``build_person_journey(topic_range=...)`` の純粋部（DB 非依存）。"""

    def _entry(self, component_id="n_basis", label="理論の土台", title="論文A") -> dict:
        return {"component_id": component_id, "label": label, "document_title": title}

    def test_preamble_and_entry_steps_are_prepended_with_literal_wording(self):
        start = _node(id_="n1", anchor=_topic_anchor("t1"), course_id="courseA", label="この記録")
        result = _journey(
            start,
            PersonalNetwork(nodes=[start]),
            topic_range={"topic_label": "トピック1", "entries": [self._entry()]},
        )
        facts = [s["fact"] for s in result["steps"]]
        assert facts[0] == (
            "このトピック『トピック1』の教材が触れている理論構成をたどります（トピック単位の対応）。"
        )
        assert facts[1] == "論文『論文A』の理論構成『理論の土台』に触れています。"
        # 前置き step も ref を必ず持つ（ref なし step を DTO に混ぜない）
        assert result["steps"][0]["ref"]["kind"] == "personal_node"
        assert result["steps"][0]["ref"]["id"] == "n1"
        assert result["steps"][1]["ref"] == {
            "kind": "graph_node", "id": "n_basis", "label": "理論の土台",
        }

    def test_missing_topic_label_uses_the_label_free_preamble(self):
        start = _node(id_="n1", anchor=_topic_anchor("t1"), course_id="courseA")
        result = _journey(
            start,
            PersonalNetwork(nodes=[start]),
            topic_range={"topic_label": "", "entries": [self._entry()]},
        )
        assert result["steps"][0]["fact"] == (
            "このトピックの教材が触れている理論構成をたどります（トピック単位の対応）。"
        )

    def test_missing_document_title_falls_back_without_fabricating_one(self):
        start = _node(id_="n1", anchor=_topic_anchor("t1"), course_id="courseA")
        result = _journey(
            start,
            PersonalNetwork(nodes=[start]),
            topic_range={"topic_label": "", "entries": [self._entry(title="")]},
        )
        assert result["steps"][1]["fact"] == "論文『この教材』の理論構成『理論の土台』に触れています。"

    def test_entries_capped_at_fanout(self):
        start = _node(id_="n1", anchor=_topic_anchor("t1"), course_id="courseA")
        entries = [
            self._entry(component_id=f"n{i}", label=f"構成{i}")
            for i in range(MAX_FANOUT_PER_SEGMENT + 3)
        ]
        result = _journey(
            start,
            PersonalNetwork(nodes=[start]),
            topic_range={"topic_label": "T", "entries": entries},
        )
        graph_steps = [s for s in result["steps"] if s["ref"]["kind"] == "graph_node"]
        assert len(graph_steps) == MAX_FANOUT_PER_SEGMENT
        assert len(result["steps"]) <= MAX_STEPS

    def test_empty_entries_produce_no_steps_at_all(self):
        start = _node(id_="n1", anchor=_topic_anchor("t1"), course_id="courseA")
        result = _journey(
            start,
            PersonalNetwork(nodes=[start]),
            topic_range={"topic_label": "トピック1", "entries": []},
        )
        assert result["steps"] == []

    def test_non_topic_anchor_ignores_topic_range(self):
        """component アンカーは [1] を使う。topic 範囲エントリを混ぜない。"""
        start = _node(id_="n1", anchor=_component_anchor("c1"), course_id="courseA")
        result = _journey(
            start,
            PersonalNetwork(nodes=[start]),
            topic_range={"topic_label": "トピック1", "entries": [self._entry()]},
        )
        assert not any("トピック" in s["fact"] for s in result["steps"])

    def test_topic_range_facts_carry_no_numbers_or_advice(self):
        start = _node(id_="n1", anchor=_topic_anchor("t1"), course_id="courseA")
        result = _journey(
            start,
            PersonalNetwork(nodes=[start]),
            topic_range={
                "topic_label": "トピック",
                "entries": [self._entry(), self._entry(component_id="n_obs", label="観測量の構成")],
            },
        )
        joined = " ".join(s["fact"] for s in result["steps"])
        assert not re.search(r"\d", joined)
        for forbidden in ("すべき", "安心", "おすすめ", "この分野では未検証"):
            assert forbidden not in joined

    def test_default_argument_keeps_backward_compatible_dto(self):
        start = _node(id_="n1", anchor=_topic_anchor("t1"), course_id="courseA")
        result = _journey(start, PersonalNetwork(nodes=[start]))
        assert set(result.keys()) == {"steps", "frontier_note", "truncated"}


# ---------------------------------------------------------------------------
# journey_for_person_node（DB 経路。queries / derive を monkeypatch）
# ---------------------------------------------------------------------------


def _always_viewable(user_id, document_id) -> bool:
    return True


def _stub_person_journey_db(
    monkeypatch,
    *,
    network,
    binding=None,
    claim_docs=None,
    graphs=None,
    titles=None,
    component_doc=None,
    identity_links=None,
):
    """``journey_for_person_node`` が触る DB プリミティブ全部をスタブする。"""
    monkeypatch.setattr(derive_mod, "derive_person_network", lambda user_id: network)
    monkeypatch.setattr(queries_mod, "fetch_topic_atlas_binding_for_courses", lambda ids: {})
    monkeypatch.setattr(queries_mod, "fetch_course_titles", lambda ids: {})
    monkeypatch.setattr(
        queries_mod,
        "fetch_topic_claim_binding",
        lambda course_id, topic_id: dict(binding or {"claim_ids": [], "topic_label": ""}),
    )
    monkeypatch.setattr(
        queries_mod, "fetch_claim_document_id", lambda claim_id: (claim_docs or {}).get(claim_id),
    )
    monkeypatch.setattr(
        queries_mod, "fetch_component_graph", lambda doc_id: (graphs or {}).get(doc_id, {}),
    )
    monkeypatch.setattr(queries_mod, "fetch_document_titles", lambda ids: dict(titles or {}))
    monkeypatch.setattr(
        queries_mod, "fetch_component_document_id", lambda component_id: component_doc,
    )
    monkeypatch.setattr(
        queries_mod, "fetch_confirmed_identity_links", lambda doc_id: list(identity_links or []),
    )


class TestEmptyJourneyNotice:
    """steps が1件も組めなかった旅は無言で空を返さない（notice + 出口案内の事実文）。"""

    def test_empty_journey_gets_notice_and_exit_fact(self, monkeypatch):
        start = _node(id_="n1", anchor=_topic_anchor("t1"), course_id="courseA")
        _stub_person_journey_db(monkeypatch, network=PersonalNetwork(nodes=[start]))
        result = journey_for_person_node("u1", "n1", can_view_document=_always_viewable)
        assert result["steps"] == []
        assert result["notice"] == J.NOTICE_JOURNEY_EMPTY
        assert result["facts"] == [FACT_RANGE_SHARPEN]
        assert len(result["facts"]) >= 1

    def test_notice_text_is_a_fact_without_numbers_or_advice(self, monkeypatch):
        start = _node(id_="n1", anchor=_topic_anchor("t1"), course_id="courseA")
        _stub_person_journey_db(monkeypatch, network=PersonalNetwork(nodes=[start]))
        result = journey_for_person_node("u1", "n1", can_view_document=_always_viewable)
        joined = " ".join([result["notice"], *result["facts"]])
        # notice 自体は件数・数値を含まない（facts は「いまここの周り」から流用した
        # 既存文言そのままなので、そこに含まれる「1点に絞り込まれます」は対象外）。
        assert not re.search(r"\d", result["notice"])
        for forbidden in ("すべき", "安心", "おすすめ", "この分野では未検証", "誰も検証していない"):
            assert forbidden not in joined

    def test_non_empty_journey_has_no_notice_or_facts_keys(self, monkeypatch):
        start = _node(id_="n1", anchor=_component_anchor("c1"), course_id="courseA")
        graph = {
            "nodes": [_stage_main_node("c1", "Theory basis", order=1)],
            "edges": [],
        }
        _stub_person_journey_db(
            monkeypatch,
            network=PersonalNetwork(nodes=[start]),
            component_doc="docA",
            graphs={"docA": graph},
        )
        result = journey_for_person_node("u1", "n1", can_view_document=_always_viewable)
        assert result["steps"]
        assert "notice" not in result
        assert "facts" not in result

    def test_unknown_node_id_still_returns_none(self, monkeypatch):
        start = _node(id_="n1", anchor=_topic_anchor("t1"), course_id="courseA")
        _stub_person_journey_db(monkeypatch, network=PersonalNetwork(nodes=[start]))
        assert journey_for_person_node("u1", "nope", can_view_document=_always_viewable) is None


class TestTopicRangeEntriesDbPath:
    """``_topic_range_for_node``: topics[].linked_claim_ids → claim → main 層（AI 推定ゼロ）。"""

    def _graph(self):
        return {
            "nodes": [
                _stage_main_node("n_basis", "Theory basis", order=1, claims=["cl_basis"]),
                _stage_main_node("n_obs", "Observable construction", order=2, claims=["cl_obs"]),
                _stage_main_node("n_other", "Consistency relation", order=3, claims=["cl_x"]),
                # main 以外の層は範囲に載せない
                dict(
                    _stage_main_node("n_detail", "Define eq_1", order=4, claims=["cl_basis"]),
                    graph_layer="equation_detail",
                ),
            ],
            "edges": [],
        }

    def _start(self, course_id="courseA"):
        return _node(id_="n1", anchor=_topic_anchor("t1"), course_id=course_id, label="この記録")

    def test_touched_main_nodes_become_steps_in_deterministic_order(self, monkeypatch):
        start = self._start()
        _stub_person_journey_db(
            monkeypatch,
            network=PersonalNetwork(nodes=[start]),
            binding={"claim_ids": ["cl_obs", "cl_basis"], "topic_label": "トピック1"},
            claim_docs={"cl_obs": "docA", "cl_basis": "docA"},
            graphs={"docA": self._graph()},
            titles={"docA": "論文A"},
        )
        result = journey_for_person_node("u1", "n1", can_view_document=_always_viewable)
        facts = [s["fact"] for s in result["steps"]]
        assert facts[0].startswith("このトピック『トピック1』")
        # display_order 順（claim_ids の入力順ではない）
        assert facts[1] == "論文『論文A』の理論構成『理論の土台』に触れています。"
        assert facts[2] == "論文『論文A』の理論構成『観測量の構成』に触れています。"
        # 触れていない main ノード・equation_detail 層は出ない
        assert not any("整合関係" in f for f in facts)
        assert not any("eq_1" in f for f in facts)
        assert "notice" not in result

    def test_documents_are_scanned_in_sorted_order(self, monkeypatch):
        start = self._start()
        graph_b = {
            "nodes": [_stage_main_node("m_b", "Equation system", order=1, claims=["cl_b"])],
            "edges": [],
        }
        graph_a = {
            "nodes": [_stage_main_node("m_a", "Theory basis", order=1, claims=["cl_a"])],
            "edges": [],
        }
        _stub_person_journey_db(
            monkeypatch,
            network=PersonalNetwork(nodes=[start]),
            binding={"claim_ids": ["cl_b", "cl_a"], "topic_label": "T"},
            claim_docs={"cl_b": "docB", "cl_a": "docA"},
            graphs={"docA": graph_a, "docB": graph_b},
            titles={"docA": "論文A", "docB": "論文B"},
        )
        result = journey_for_person_node("u1", "n1", can_view_document=_always_viewable)
        graph_steps = [s for s in result["steps"] if s["ref"]["kind"] == "graph_node"]
        assert [s["ref"]["id"] for s in graph_steps] == ["m_a", "m_b"]

    def test_fanout_cap_applies_to_range_entries(self, monkeypatch):
        start = self._start()
        claim_ids = [f"cl_{i}" for i in range(MAX_FANOUT_PER_SEGMENT + 4)]
        graph = {
            "nodes": [
                _stage_main_node(f"m_{i}", "Theory basis", order=i, claims=[f"cl_{i}"])
                for i in range(MAX_FANOUT_PER_SEGMENT + 4)
            ],
            "edges": [],
        }
        _stub_person_journey_db(
            monkeypatch,
            network=PersonalNetwork(nodes=[start]),
            binding={"claim_ids": claim_ids, "topic_label": "T"},
            claim_docs={cid: "docA" for cid in claim_ids},
            graphs={"docA": graph},
            titles={"docA": "論文A"},
        )
        result = journey_for_person_node("u1", "n1", can_view_document=_always_viewable)
        graph_steps = [s for s in result["steps"] if s["ref"]["kind"] == "graph_node"]
        assert len(graph_steps) == MAX_FANOUT_PER_SEGMENT
        assert len(result["steps"]) <= MAX_STEPS

    def test_unviewable_document_is_excluded_fail_closed(self, monkeypatch):
        start = self._start()
        _stub_person_journey_db(
            monkeypatch,
            network=PersonalNetwork(nodes=[start]),
            binding={"claim_ids": ["cl_basis"], "topic_label": "トピック1"},
            claim_docs={"cl_basis": "docA"},
            graphs={"docA": self._graph()},
            titles={"docA": "論文A"},
        )
        result = journey_for_person_node(
            "u1", "n1", can_view_document=lambda user_id, doc_id: False,
        )
        assert result["steps"] == []
        assert result["notice"] == J.NOTICE_JOURNEY_EMPTY
        assert "論文A" not in " ".join(result["facts"])

    def test_missing_can_view_document_callback_yields_no_range_entries(self, monkeypatch):
        start = self._start()
        _stub_person_journey_db(
            monkeypatch,
            network=PersonalNetwork(nodes=[start]),
            binding={"claim_ids": ["cl_basis"], "topic_label": "トピック1"},
            claim_docs={"cl_basis": "docA"},
            graphs={"docA": self._graph()},
            titles={"docA": "論文A"},
        )
        result = journey_for_person_node("u1", "n1")
        assert result["steps"] == []
        assert result["notice"] == J.NOTICE_JOURNEY_EMPTY

    def test_identity_link_segments_are_not_executed_for_topic_anchors(self, monkeypatch):
        """[2][3] は topic では走らせない（粗い対応から確定リンクへ飛ぶのは帰属の偽装）。"""
        start = self._start()

        def _must_not_be_called(document_id):
            raise AssertionError("topic anchors must not traverse identity links")

        _stub_person_journey_db(
            monkeypatch,
            network=PersonalNetwork(nodes=[start]),
            binding={"claim_ids": ["cl_basis"], "topic_label": "トピック1"},
            claim_docs={"cl_basis": "docA"},
            graphs={"docA": self._graph()},
            titles={"docA": "論文A"},
        )
        monkeypatch.setattr(queries_mod, "fetch_confirmed_identity_links", _must_not_be_called)
        result = journey_for_person_node("u1", "n1", can_view_document=_always_viewable)
        assert not any(s["ref"]["kind"] == "shared_part" for s in result["steps"])
        assert not any(s["ref"]["kind"] == "document" for s in result["steps"])
        assert result["frontier_note"] is None

    def test_no_linked_claim_ids_leaves_the_journey_to_later_segments(self, monkeypatch):
        start = self._start()

        def _claims_must_not_be_resolved(claim_id):
            raise AssertionError("no claim ids means no claim resolution")

        _stub_person_journey_db(
            monkeypatch,
            network=PersonalNetwork(nodes=[start]),
            binding={"claim_ids": [], "topic_label": "トピック1"},
        )
        monkeypatch.setattr(
            queries_mod, "fetch_claim_document_id", _claims_must_not_be_resolved,
        )
        result = journey_for_person_node("u1", "n1", can_view_document=_always_viewable)
        assert result["steps"] == []
        assert result["notice"] == J.NOTICE_JOURNEY_EMPTY

    def test_graphless_document_is_skipped(self, monkeypatch):
        start = self._start()
        _stub_person_journey_db(
            monkeypatch,
            network=PersonalNetwork(nodes=[start]),
            binding={"claim_ids": ["cl_basis"], "topic_label": "トピック1"},
            claim_docs={"cl_basis": "docA"},
            graphs={},
            titles={"docA": "論文A"},
        )
        result = journey_for_person_node("u1", "n1", can_view_document=_always_viewable)
        assert result["steps"] == []
        assert result["notice"] == J.NOTICE_JOURNEY_EMPTY

    def test_db_failure_is_fail_soft(self, monkeypatch):
        start = self._start()
        _stub_person_journey_db(monkeypatch, network=PersonalNetwork(nodes=[start]))

        def _boom(course_id, topic_id):
            raise RuntimeError("db down")

        monkeypatch.setattr(queries_mod, "fetch_topic_claim_binding", _boom)
        result = journey_for_person_node("u1", "n1", can_view_document=_always_viewable)
        assert result["steps"] == []
        assert result["notice"] == J.NOTICE_JOURNEY_EMPTY

    def test_range_entries_coexist_with_atlas_segments(self, monkeypatch):
        """[1'] のあとに [4][5] が従来どおり続く（範囲エントリは置き換えではなく前置）。"""
        start = _node(
            id_="n1", anchor=_topic_anchor("t1", atlas_node_id="atlas_1"), course_id="courseA",
        )
        sibling = _node(
            id_="n2", anchor=_topic_anchor("t2", atlas_node_id="atlas_1"), course_id="courseB",
            label="以前の問い", node_kind=NODE_KIND_QUESTION,
        )
        _stub_person_journey_db(
            monkeypatch,
            network=PersonalNetwork(nodes=[start, sibling]),
            binding={"claim_ids": ["cl_basis"], "topic_label": "トピック1"},
            claim_docs={"cl_basis": "docA"},
            graphs={"docA": self._graph()},
            titles={"docA": "論文A"},
        )
        monkeypatch.setattr(
            queries_mod,
            "fetch_topic_atlas_binding_for_courses",
            lambda ids: {"courseA": {"t1": "atlas_1"}, "courseB": {"t2": "atlas_1"}},
        )
        result = journey_for_person_node("u1", "n1", can_view_document=_always_viewable)
        kinds = [s["ref"]["kind"] for s in result["steps"]]
        assert kinds[0] == "personal_node"          # [1'] 前置き
        assert kinds[1] == "graph_node"             # [1'] 範囲エントリ
        assert "atlas_node" in kinds                # [4]
        assert kinds[-1] == "personal_node"         # [5]

    def test_document_scan_limit_is_shared_with_the_nearby_view(self, monkeypatch):
        """走査する document 上限は「いまここの周り」と同じ定数を使う（別値を作らない）。"""
        start = self._start()
        claim_ids = [f"cl_{i}" for i in range(MAX_DOCUMENTS_SCANNED + 3)]
        scanned: list[str] = []

        def _graph_for(doc_id):
            scanned.append(doc_id)
            return {}

        _stub_person_journey_db(
            monkeypatch,
            network=PersonalNetwork(nodes=[start]),
            binding={"claim_ids": claim_ids, "topic_label": "T"},
            claim_docs={cid: f"doc_{i:02d}" for i, cid in enumerate(claim_ids)},
        )
        monkeypatch.setattr(queries_mod, "fetch_component_graph", _graph_for)
        journey_for_person_node("u1", "n1", can_view_document=_always_viewable)
        assert len(scanned) == MAX_DOCUMENTS_SCANNED
        assert scanned == sorted(scanned)

    def test_dto_has_no_numeric_keys(self, monkeypatch):
        start = self._start()
        _stub_person_journey_db(
            monkeypatch,
            network=PersonalNetwork(nodes=[start]),
            binding={"claim_ids": ["cl_basis"], "topic_label": "トピック1"},
            claim_docs={"cl_basis": "docA"},
            graphs={"docA": self._graph()},
            titles={"docA": "論文A"},
        )
        result = journey_for_person_node("u1", "n1", can_view_document=_always_viewable)

        forbidden = {"count", "confidence", "score", "load_score", "level", "weight", "total"}

        def _walk(value):
            if isinstance(value, dict):
                for key, sub in value.items():
                    assert key not in forbidden, key
                    _walk(sub)
            elif isinstance(value, list):
                for sub in value:
                    _walk(sub)

        _walk(result)
