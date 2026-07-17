"""``core/personal_graph/journey.py::build_journey`` の純粋ユニットテスト。

設計書 ``docs/features/personal_knowledge_network_design.md`` §6（旅の経路探索）を検証する。
``build_journey(start_node, network, local_graph, identity_links, library_entries,
topic_atlas, course_document_ids)`` は DB 接続を持たない純粋関数契約（fake データを
直接渡してテストできる）。

検証観点:
- [1] 論文ローカルグラフ: component/claim アンカーから main layer node を辿り、
  1 hop 隣接ノードを fact 化する。topic アンカーは [1]〜[3] を丸ごと飛ばす。
- [2] 同一性リンク: confirmed のみを辿り、空なら frontier_note を立てる
  （topic アンカーでは同一性リンク区間自体を試みないので frontier_note は立たない）。
- [3] L層ハブ: コース内の document のみ出す（コース外は件数にも触れず省く）。
- [4]/[5]: atlas_node_id が無ければ丸ごと省く。あれば地図 fact + 同じ atlas_node_id を
  持つ本人の別ノードを出す（起点自身は除く）。
- fan-out（MAX_FANOUT_PER_SEGMENT）・MAX_STEPS の切り詰めと ``truncated`` フラグ。
- 決定論順（入力順序を変えても出力が同一）。
- fact 文に数字が混入しない（PN-4）。

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
    build_journey,
)
from core.personal_graph.schema import (  # noqa: E402
    NODE_KIND_QUESTION,
    NODE_KIND_TENSION,
    PersonalAnchor,
    PersonalNetwork,
    PersonalNode,
)


# ---------------------------------------------------------------------------
# fake ビルダ
# ---------------------------------------------------------------------------


def _node(
    *,
    id_: str,
    anchor: PersonalAnchor,
    node_kind: str = NODE_KIND_TENSION,
    topic_id: str | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
    label: str = "この件について",
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


# ---------------------------------------------------------------------------
# [1] 論文ローカルグラフ
# ---------------------------------------------------------------------------


class TestLocalGraphSegment:
    def test_component_anchor_resolves_main_node_directly(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"))
        local_graph = {"nodes": [_main_node("c1", "Equation system")], "edges": []}
        result = build_journey(start, PersonalNetwork(nodes=[start]), local_graph, [], {}, {}, set())
        graph_steps = [s for s in result["steps"] if s["ref"]["kind"] == "graph_node"]
        assert len(graph_steps) == 1
        assert graph_steps[0]["ref"]["id"] == "c1"
        assert "Equation system" in graph_steps[0]["fact"]

    def test_component_anchor_resolves_via_member_component_ids(self):
        start = _node(id_="n1", anchor=_component_anchor("detail_c"))
        local_graph = {
            "nodes": [_main_node("main1", "Elimination", member_component_ids=["detail_c"])],
            "edges": [],
        }
        result = build_journey(start, PersonalNetwork(nodes=[start]), local_graph, [], {}, {}, set())
        graph_steps = [s for s in result["steps"] if s["ref"]["kind"] == "graph_node"]
        assert len(graph_steps) == 1
        assert graph_steps[0]["ref"]["id"] == "main1"

    def test_claim_anchor_resolves_via_linked_claim_ids(self):
        start = _node(id_="n1", anchor=_claim_anchor("claim1"))
        local_graph = {
            "nodes": [_main_node("main1", "Consistency relation", linked_claim_ids=["claim1"])],
            "edges": [],
        }
        result = build_journey(start, PersonalNetwork(nodes=[start]), local_graph, [], {}, {}, set())
        graph_steps = [s for s in result["steps"] if s["ref"]["kind"] == "graph_node"]
        assert len(graph_steps) == 1
        assert graph_steps[0]["ref"]["id"] == "main1"

    def test_adjacent_main_node_is_included_as_1hop(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"))
        local_graph = {
            "nodes": [
                _main_node("c1", "Equation system"),
                _main_node("main2", "Elimination"),
                {"component_id": "detail1", "label": "detail", "graph_layer": "equation_detail"},
            ],
            "edges": [
                {"source_component_id": "c1", "target_component_id": "main2", "edge_type": "eliminates"},
                # equation_detail 層は main-main 隣接に含めない
                {"source_component_id": "c1", "target_component_id": "detail1", "edge_type": "derives"},
            ],
        }
        result = build_journey(start, PersonalNetwork(nodes=[start]), local_graph, [], {}, {}, set())
        graph_ids = [s["ref"]["id"] for s in result["steps"] if s["ref"]["kind"] == "graph_node"]
        assert graph_ids == ["c1", "main2"]

    def test_unresolvable_anchor_produces_no_graph_node_step(self):
        start = _node(id_="n1", anchor=_component_anchor("missing"))
        local_graph = {"nodes": [_main_node("main1", "Equation system")], "edges": []}
        result = build_journey(start, PersonalNetwork(nodes=[start]), local_graph, [], {}, {}, set())
        assert not any(s["ref"]["kind"] == "graph_node" for s in result["steps"])

    def test_topic_anchor_skips_local_graph_even_if_provided(self):
        start = _node(id_="n1", anchor=_topic_anchor("topicX"), node_kind=NODE_KIND_QUESTION)
        local_graph = {"nodes": [_main_node("main1", "Equation system")], "edges": []}
        result = build_journey(start, PersonalNetwork(nodes=[start]), local_graph, [], {}, {}, set())
        assert not any(s["ref"]["kind"] == "graph_node" for s in result["steps"])


# ---------------------------------------------------------------------------
# [2] 同一性リンク（confirmed のみ）+ frontier_note
# ---------------------------------------------------------------------------


class TestIdentityLinkSegment:
    def test_empty_identity_links_sets_frontier_note_for_component_anchor(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"))
        result = build_journey(start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, [], {}, {}, set())
        assert result["frontier_note"]
        assert "同一性リンク" in result["frontier_note"]

    def test_empty_identity_links_sets_frontier_note_for_claim_anchor(self):
        start = _node(id_="n1", anchor=_claim_anchor("claim1"))
        result = build_journey(start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, [], {}, {}, set())
        assert result["frontier_note"]

    def test_topic_anchor_never_sets_frontier_note(self):
        start = _node(id_="n1", anchor=_topic_anchor("topicX"))
        result = build_journey(start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, [], {}, {}, set())
        assert result["frontier_note"] is None

    def test_present_identity_link_produces_shared_part_step_and_no_frontier_note(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"))
        links = [_link(id_="link1", shared_part_id="sp1")]
        library_entries = {"sp1": {"name": "共通装置H", "other_documents": []}}
        result = build_journey(start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, links, library_entries, {}, set())
        assert result["frontier_note"] is None
        shared_steps = [s for s in result["steps"] if s["ref"]["kind"] == "shared_part"]
        assert len(shared_steps) == 1
        assert shared_steps[0]["ref"]["id"] == "sp1"
        assert "共通装置H" in shared_steps[0]["fact"]

    def test_retired_or_missing_library_entry_produces_no_step_and_sets_frontier_note(self):
        """指摘3（review P2）回帰: ``library_entries`` に無い shared_part_id は
        retired/存在しないエントリを意味する（``journey_for_node`` は active entry のみ
        辞書に積む）。active であることを traversal の前提条件にし、generic な
        「共通部品」名へのフォールバックも含め一切 hop を生成しない（§6/§10）。
        有効なハブが1件も残らないため、リンクが空だった場合と同じく frontier_note が立つ。"""
        start = _node(id_="n1", anchor=_component_anchor("c1"))
        links = [_link(id_="link1", shared_part_id="sp_retired")]
        result = build_journey(
            start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, links, {}, {}, set(),
        )
        assert not any(s["ref"]["kind"] == "shared_part" for s in result["steps"])
        assert result["frontier_note"]

    def test_mixed_active_and_retired_entries_only_active_one_produces_step(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"))
        links = [
            _link(id_="link_active", shared_part_id="sp_active", created_at="2026-01-01T00:00:00Z"),
            _link(id_="link_retired", shared_part_id="sp_retired", created_at="2026-01-02T00:00:00Z"),
        ]
        # journey_for_node は active entry のみを library_entries に積む(§6/§10) ので、
        # retired な "sp_retired" はここに現れない状態を模する。
        library_entries = {"sp_active": {"name": "共通装置H", "other_documents": []}}
        result = build_journey(
            start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, links, library_entries, {}, set(),
        )
        shared_steps = [s for s in result["steps"] if s["ref"]["kind"] == "shared_part"]
        assert [s["ref"]["id"] for s in shared_steps] == ["sp_active"]
        assert result["frontier_note"] is None


# ---------------------------------------------------------------------------
# [3] L層ハブ → 他インスタンス（コース内限定）
# ---------------------------------------------------------------------------


class TestHubSegmentCourseScoping:
    def test_only_in_course_documents_are_emitted(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"))
        links = [_link(id_="link1", shared_part_id="sp1", instance_document_id="docA")]
        library_entries = {
            "sp1": {
                "name": "共通装置H",
                "other_documents": [
                    {"document_id": "docB", "title": "論文B"},
                    {"document_id": "docC", "title": "論文C(コース外)"},
                ],
            }
        }
        course_document_ids = {"docA", "docB"}
        result = build_journey(
            start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, links, library_entries, {}, course_document_ids,
        )
        doc_steps = [s for s in result["steps"] if s["ref"]["kind"] == "document"]
        assert len(doc_steps) == 1
        assert doc_steps[0]["ref"]["id"] == "docB"
        assert "論文C" not in " ".join(s["fact"] for s in result["steps"])

    def test_no_out_of_course_document_mentioned_at_all_when_none_qualify(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"))
        links = [_link(id_="link1", shared_part_id="sp1", instance_document_id="docA")]
        library_entries = {
            "sp1": {"name": "H", "other_documents": [{"document_id": "docC", "title": "外部教材"}]},
        }
        result = build_journey(
            start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, links, library_entries, {}, {"docA"},
        )
        doc_steps = [s for s in result["steps"] if s["ref"]["kind"] == "document"]
        assert doc_steps == []
        # 「件」等の言及も無い(件数に触れず黙って省く)
        assert "docC" not in " ".join(s["fact"] for s in result["steps"])

    def test_retired_shared_part_never_reaches_hub_traversal(self):
        """指摘3（review P2）: retired entry（library_entries に無い shared_part_id）は
        [2] の shared_part ステップを生成しない時点で ``shared_part_ids_in_order`` にも
        入らないため、[3] の他インスタンス traversal（other_documents）にも一切現れない。"""
        start = _node(id_="n1", anchor=_component_anchor("c1"))
        links = [_link(id_="link1", shared_part_id="sp_retired", instance_document_id="docA")]
        # このテストでは library_entries 自体に "sp_retired" のキーが無い状態を模する
        # (journey_for_node が active entry のみ積むため)。仮にキーがあっても
        # other_documents が読まれるのは shared_part_ids_in_order に入ったときだけ。
        result = build_journey(
            start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, links, {}, {}, {"docA", "docB"},
        )
        doc_steps = [s for s in result["steps"] if s["ref"]["kind"] == "document"]
        assert doc_steps == []


# ---------------------------------------------------------------------------
# [4]/[5] atlas 骨格 node + 本人の別ノード
# ---------------------------------------------------------------------------


class TestAtlasAndSiblingSegments:
    def test_no_atlas_node_id_skips_both_segments(self):
        start = _node(id_="n1", anchor=_topic_anchor("topicX", atlas_node_id=None))
        sibling = _node(id_="n2", anchor=_topic_anchor("topicX", atlas_node_id=None))
        network = PersonalNetwork(nodes=[start, sibling])
        result = build_journey(start, network, _EMPTY_GRAPH, [], {}, {}, set())
        assert not any(s["ref"]["kind"] in ("atlas_node", "personal_node") for s in result["steps"])

    def test_atlas_node_id_from_anchor_produces_atlas_step(self):
        start = _node(id_="n1", anchor=_topic_anchor("topicX", atlas_node_id="atlas_1"))
        result = build_journey(start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, [], {}, {}, set())
        atlas_steps = [s for s in result["steps"] if s["ref"]["kind"] == "atlas_node"]
        assert len(atlas_steps) == 1
        assert atlas_steps[0]["ref"]["id"] == "atlas_1"

    def test_atlas_node_id_falls_back_to_topic_binding(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"), topic_id="topic1")
        topic_atlas = {"topic1": "atlas_9"}
        result = build_journey(start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, [], {}, topic_atlas, set())
        atlas_steps = [s for s in result["steps"] if s["ref"]["kind"] == "atlas_node"]
        assert len(atlas_steps) == 1
        assert atlas_steps[0]["ref"]["id"] == "atlas_9"

    def test_sibling_with_same_atlas_node_id_is_included_and_self_excluded(self):
        start = _node(id_="n1", anchor=_topic_anchor("topicX", atlas_node_id="atlas_1"), label="わたしの問い")
        sibling_same = _node(
            id_="n2", anchor=_topic_anchor("topicY", atlas_node_id="atlas_1"), label="別の問い",
            created_at="2026-02-01T00:00:00Z",
        )
        sibling_other = _node(id_="n3", anchor=_topic_anchor("topicZ", atlas_node_id="atlas_2"), label="無関係")
        network = PersonalNetwork(nodes=[start, sibling_same, sibling_other])
        result = build_journey(start, network, _EMPTY_GRAPH, [], {}, {}, set())
        personal_steps = [s for s in result["steps"] if s["ref"]["kind"] == "personal_node"]
        ids = [s["ref"]["id"] for s in personal_steps]
        assert ids == ["n2"]
        assert "別の問い" in personal_steps[0]["fact"]


# ---------------------------------------------------------------------------
# fan-out / MAX_STEPS の切り詰め
# ---------------------------------------------------------------------------


class TestFanoutAndStepTruncation:
    def test_identity_link_fanout_capped(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"))
        links = [
            _link(id_=f"link{i}", shared_part_id=f"sp{i}", created_at=f"2026-01-{i + 1:02d}T00:00:00Z")
            for i in range(MAX_FANOUT_PER_SEGMENT + 3)
        ]
        library_entries = {f"sp{i}": {"name": f"H{i}", "other_documents": []} for i in range(len(links))}
        result = build_journey(start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, links, library_entries, {}, set())
        shared_steps = [s for s in result["steps"] if s["ref"]["kind"] == "shared_part"]
        assert len(shared_steps) == MAX_FANOUT_PER_SEGMENT

    def test_sibling_fanout_capped(self):
        start = _node(id_="n0", anchor=_topic_anchor("topicX", atlas_node_id="atlas_1"))
        siblings = [
            _node(id_=f"s{i}", anchor=_topic_anchor("topicX", atlas_node_id="atlas_1"), created_at=f"2026-01-{i + 1:02d}T00:00:00Z")
            for i in range(MAX_FANOUT_PER_SEGMENT + 4)
        ]
        network = PersonalNetwork(nodes=[start] + siblings)
        result = build_journey(start, network, _EMPTY_GRAPH, [], {}, {}, set())
        personal_steps = [s for s in result["steps"] if s["ref"]["kind"] == "personal_node"]
        assert len(personal_steps) == MAX_FANOUT_PER_SEGMENT

    def test_total_steps_truncated_at_max_steps(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"), topic_id="topic1")
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
        course_document_ids = {f"doc{i}" for i in range(MAX_FANOUT_PER_SEGMENT)}
        topic_atlas = {"topic1": "atlas_1"}
        siblings = [
            _node(id_=f"sib{i}", anchor=_topic_anchor("topic1", atlas_node_id="atlas_1"), created_at=f"2026-02-{i + 1:02d}T00:00:00Z")
            for i in range(MAX_FANOUT_PER_SEGMENT)
        ]
        network = PersonalNetwork(nodes=[start] + siblings)
        result = build_journey(
            start, network, local_graph, links, library_entries, topic_atlas, course_document_ids,
        )
        assert result["truncated"] is True
        assert len(result["steps"]) == MAX_STEPS

    def test_no_truncation_flag_when_within_limit(self):
        start = _node(id_="n1", anchor=_topic_anchor("topicX", atlas_node_id="atlas_1"))
        result = build_journey(start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, [], {}, {}, set())
        assert result["truncated"] is False


# ---------------------------------------------------------------------------
# 決定論順序
# ---------------------------------------------------------------------------


class TestDeterministicOrder:
    def test_identity_link_order_independent_of_input_order(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"))
        link_a = _link(id_="link_a", shared_part_id="spA", created_at="2026-01-01T00:00:00Z")
        link_b = _link(id_="link_b", shared_part_id="spB", created_at="2026-02-01T00:00:00Z")
        library_entries = {
            "spA": {"name": "HA", "other_documents": []},
            "spB": {"name": "HB", "other_documents": []},
        }
        r1 = build_journey(
            start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, [link_a, link_b], library_entries, {}, set(),
        )
        r2 = build_journey(
            start, PersonalNetwork(nodes=[start]), _EMPTY_GRAPH, [link_b, link_a], library_entries, {}, set(),
        )
        assert r1 == r2

    def test_sibling_order_independent_of_network_order(self):
        start = _node(id_="n1", anchor=_topic_anchor("topicX", atlas_node_id="atlas_1"))
        sib1 = _node(id_="sib1", anchor=_topic_anchor("topicX", atlas_node_id="atlas_1"), created_at="2026-01-01T00:00:00Z")
        sib2 = _node(id_="sib2", anchor=_topic_anchor("topicX", atlas_node_id="atlas_1"), created_at="2026-02-01T00:00:00Z")
        r1 = build_journey(start, PersonalNetwork(nodes=[start, sib1, sib2]), _EMPTY_GRAPH, [], {}, {}, set())
        r2 = build_journey(start, PersonalNetwork(nodes=[sib2, sib1, start]), _EMPTY_GRAPH, [], {}, {}, set())
        assert r1 == r2


# ---------------------------------------------------------------------------
# PN-4: fact に数字を混入させない
# ---------------------------------------------------------------------------


class TestNoNumbersInFacts:
    def test_no_digits_in_any_fact_or_frontier_note(self):
        start = _node(id_="n1", anchor=_component_anchor("c1"), topic_id="topic1")
        local_graph = {
            "nodes": [_main_node("main1", "Equation system"), _main_node("main2", "Elimination")],
            "edges": [{"source_component_id": "main1", "target_component_id": "main2"}],
        }
        links = [_link(id_="link1", shared_part_id="sp1")]
        library_entries = {
            "sp1": {"name": "共通装置H", "other_documents": [{"document_id": "docB", "title": "論文B"}]},
        }
        topic_atlas = {"topic1": "atlas_1"}
        sibling = _node(id_="n2", anchor=_topic_anchor("topic1", atlas_node_id="atlas_1"))
        network = PersonalNetwork(nodes=[start, sibling])
        result = build_journey(start, network, local_graph, links, library_entries, topic_atlas, {"docB"})
        for step in result["steps"]:
            assert not re.search(r"\d", step["fact"]), step["fact"]
        # 空のときの frontier_note にも数字は無い
        empty_result = build_journey(
            _node(id_="n3", anchor=_component_anchor("c2")), PersonalNetwork(nodes=[]), _EMPTY_GRAPH, [], {}, {}, set(),
        )
        if empty_result["frontier_note"]:
            assert not re.search(r"\d", empty_result["frontier_note"])


# ---------------------------------------------------------------------------
# journey_for_node: 起点 document の閲覧可否ゲート（指摘2 review P1 / PN-7）
#
# ``journey_for_node`` は DB 読み取りを ``core.personal_graph.queries`` /
# ``core.personal_graph.derive`` に委譲するエントリポイント。ここでは実 DB を使わず、
# それらのモジュール関数を monkeypatch で差し替えて検証する
# （import 自体は sqlalchemy 等に依存するが接続はしないため、DB なしで実行できる）。
# ---------------------------------------------------------------------------


class TestJourneyForNodeDocumentViewabilityGate:
    """起点アンカー（component/claim）が解決する document を学習者が閲覧できなければ、
    ``journey_for_node`` はローカルグラフ・同一性リンクを一切読まず、document が
    見つからなかった場合と同じ経路（[1] を省き、[2]/[3] は空の識別リンクとして
    処理される）に倒す（PN-7: 黙って省く）。
    """

    def _patch_common(self, monkeypatch):
        import core.personal_graph.derive as derive_mod
        import core.personal_graph.queries as queries_mod
        from core.personal_graph.journey import _FRONTIER_NOTE

        start = _node(id_="n1", anchor=_component_anchor("c1"), topic_id="topic1")
        network = PersonalNetwork(nodes=[start])

        monkeypatch.setattr(derive_mod, "derive_personal_network", lambda user_id, course_id: network)
        monkeypatch.setattr(queries_mod, "fetch_component_document_id", lambda cid: "doc1")
        monkeypatch.setattr(queries_mod, "fetch_claim_document_id", lambda cid: "doc1")
        monkeypatch.setattr(queries_mod, "fetch_topic_atlas_binding", lambda cid: {})
        monkeypatch.setattr(queries_mod, "fetch_course_document_ids", lambda cid: set())

        calls = {"graph": 0, "links": 0}

        def _fetch_component_graph(doc_id):
            calls["graph"] += 1
            return {"nodes": [_main_node("c1", "Equation system")], "edges": []}

        def _fetch_confirmed_identity_links(doc_id):
            calls["links"] += 1
            return []

        monkeypatch.setattr(queries_mod, "fetch_component_graph", _fetch_component_graph)
        monkeypatch.setattr(queries_mod, "fetch_confirmed_identity_links", _fetch_confirmed_identity_links)
        return start, calls, _FRONTIER_NOTE

    def test_document_not_viewable_skips_local_graph_and_identity_link_reads(self, monkeypatch):
        from core.personal_graph.journey import journey_for_node

        start, calls, frontier_note = self._patch_common(monkeypatch)
        result = journey_for_node(
            "user1", "course1", start.id, can_view_document=lambda uid, doc_id: False,
        )
        assert result is not None
        # ローカルグラフ・同一性リンクの DB 読み取り自体を一切行わない(黙って省く・PN-7)
        assert calls["graph"] == 0
        assert calls["links"] == 0
        assert not any(s["ref"]["kind"] == "graph_node" for s in result["steps"])
        assert not any(s["ref"]["kind"] == "shared_part" for s in result["steps"])
        # frontier_note は「document が見つからなかった場合」と同じ経路で立つ
        # (component アンカーはリンクが空でも [2] を試みるため。既存挙動と同じ)
        assert result["frontier_note"] == frontier_note

    def test_viewable_document_reads_local_graph_normally(self, monkeypatch):
        from core.personal_graph.journey import journey_for_node

        start, calls, _ = self._patch_common(monkeypatch)
        result = journey_for_node(
            "user1", "course1", start.id, can_view_document=lambda uid, doc_id: True,
        )
        assert result is not None
        assert calls["graph"] == 1
        assert any(s["ref"]["kind"] == "graph_node" for s in result["steps"])

    def test_missing_can_view_document_callback_is_backward_compatible(self, monkeypatch):
        """呼び出し側が ``can_view_document`` を省略した場合は判定をスキップする
        （後方互換）。本番の呼び出し元が必ず渡すことはガードレールテストで別途固定する。"""
        from core.personal_graph.journey import journey_for_node

        start, calls, _ = self._patch_common(monkeypatch)
        result = journey_for_node("user1", "course1", start.id)
        assert result is not None
        assert calls["graph"] == 1


# ---------------------------------------------------------------------------
# N16（2026-07-17）: 再構成成功ノード（claim アンカー + 解決済み topic_id）が
# [4] atlas 骨格・[5] 近傍の本人ノードに到達できることの回帰防止。
# derive.py の claim→topic 解決（claim_topic_map）により topic_id /
# anchor.atlas_node_id が設定されるようになったため、build_journey は追加変更なしで
# [4]/[5] を生成できる — その結線を journey 側から固定する。
# ---------------------------------------------------------------------------


class TestReconstructionNodeReachesAtlasSegments:
    def test_reconstruction_with_resolved_topic_reaches_atlas_step(self):
        start = _node(
            id_="r1",
            node_kind="reconstruction",
            anchor=_claim_anchor("claimX", atlas_node_id="atlas_1"),
            topic_id="topic1",
        )
        network = PersonalNetwork(nodes=[start])
        result = build_journey(
            start, network, _EMPTY_GRAPH, [], {}, {"topic1": "atlas_1"}, set(),
        )
        atlas_steps = [s for s in result["steps"] if s["ref"]["kind"] == "atlas_node"]
        assert len(atlas_steps) == 1
        assert atlas_steps[0]["ref"]["id"] == "atlas_1"

    def test_reconstruction_with_topic_binding_only_also_reaches_atlas_step(self):
        """anchor.atlas_node_id が無くても topic_id → topic_atlas のフォールバックで
        [4] に到達する（_resolved_atlas_node_id の既存フォールバック経路）。"""
        start = _node(
            id_="r1",
            node_kind="reconstruction",
            anchor=_claim_anchor("claimX"),
            topic_id="topic1",
        )
        network = PersonalNetwork(nodes=[start])
        result = build_journey(
            start, network, _EMPTY_GRAPH, [], {}, {"topic1": "atlas_1"}, set(),
        )
        atlas_steps = [s for s in result["steps"] if s["ref"]["kind"] == "atlas_node"]
        assert len(atlas_steps) == 1

    def test_reconstruction_finds_sibling_nodes_near_same_atlas_node(self):
        start = _node(
            id_="r1",
            node_kind="reconstruction",
            anchor=_claim_anchor("claimX", atlas_node_id="atlas_1"),
            topic_id="topic1",
        )
        sibling = _node(
            id_="t1",
            anchor=_topic_anchor("topic1", atlas_node_id="atlas_1"),
            created_at="2026-02-01T00:00:00Z",
        )
        network = PersonalNetwork(nodes=[start, sibling])
        result = build_journey(
            start, network, _EMPTY_GRAPH, [], {}, {"topic1": "atlas_1"}, set(),
        )
        personal_steps = [s for s in result["steps"] if s["ref"]["kind"] == "personal_node"]
        assert [s["ref"]["id"] for s in personal_steps] == ["t1"]

    def test_unresolved_reconstruction_still_skips_atlas_segments(self):
        """claim→topic 解決不能なノード（topic_id=None・atlas_node_id なし）は従来どおり
        [4]/[5] を静かに省く（設計書 §14 既知の限界）。"""
        start = _node(
            id_="r1",
            node_kind="reconstruction",
            anchor=_claim_anchor("claimX"),
            topic_id=None,
        )
        network = PersonalNetwork(nodes=[start])
        result = build_journey(
            start, network, _EMPTY_GRAPH, [], {}, {"topic1": "atlas_1"}, set(),
        )
        assert not any(s["ref"]["kind"] == "atlas_node" for s in result["steps"])
        assert not any(s["ref"]["kind"] == "personal_node" for s in result["steps"])
