"""「いまここの周り」と旅の焦点論文の段（第 17 周 D1 / D2）。fake rows のみ。

正本: docs/features/personal_map_nearby_design.md §11（第 17 周追補）。
"""

from __future__ import annotations

from core.personal_graph import journey, nearby, queries
from core.personal_graph.schema import PersonalAnchor, PersonalNode


def _node(anchor_id="_discussion", node_id="t1"):
    return PersonalNode(
        id=node_id, node_kind="question", label="q",
        anchor=PersonalAnchor(anchor_type="topic", anchor_id=anchor_id),
        topic_id=anchor_id, course_id="c1", created_at="", facts=[], source={},
    )


def _graph(doc):
    return {"nodes": [{"component_id": f"m-{doc}", "graph_layer": "main", "label": "Theory basis"}], "edges": []}


class _Net:
    nodes: list = []


def _patch(monkeypatch, *, cited, graphs):
    monkeypatch.setattr(queries, "fetch_topic_claim_binding", lambda cid, topic_id: {})
    monkeypatch.setattr(queries, "fetch_course_document_ids", lambda cid: {"da", "db", "dc"})
    monkeypatch.setattr(queries, "fetch_trace_cited_document_ids", lambda tid: list(cited))
    monkeypatch.setattr(queries, "fetch_component_graph", lambda d: _graph(d) if d in graphs else {})
    monkeypatch.setattr(queries, "fetch_document_titles", lambda ids: {d: f"論文{d}" for d in ids})
    monkeypatch.setattr(queries, "fetch_component_ledger_statuses", lambda ids, **_kw: {})
    monkeypatch.setattr(nearby, "_resolve_range_atlas_context", lambda cid, tid: None)
    monkeypatch.setattr(nearby, "_claim_summaries_for_graphs", lambda entries: {})


def _range(start):
    return nearby._nearby_for_topic_anchor(
        start, _Net(), mode="near", center_component_id=None,
        can_view_document=None, user_id="u1",
    )


class TestNearbyFocusStops:
    def test_cited_paper_without_graph_does_not_widen_to_course(self, monkeypatch):
        _patch(monkeypatch, cited=["db"], graphs={"da", "dc"})
        result = _range(_node())
        assert result["available"] is False
        assert result["notice"] == nearby.NOTICE_CITED_DOCUMENTS_NO_GRAPH
        assert any("論文db" in f for f in result["facts"])
        # 無関係な論文（da / dc）を並べない。
        assert not any("論文da" in f or "論文dc" in f for f in result["facts"])
        assert result["facts"][-1] == nearby.FACT_DISCUSS_SHARPEN

    def test_no_focus_falls_back_to_course(self, monkeypatch):
        _patch(monkeypatch, cited=[], graphs={"da"})
        result = _range(_node(anchor_id="t-plain"))
        assert nearby.FACT_RANGE_COURSE_FALLBACK in result["facts"]
        assert result["facts"][-1] == nearby.FACT_RANGE_SHARPEN

    def test_cited_paper_with_graph_is_shown(self, monkeypatch):
        _patch(monkeypatch, cited=["db"], graphs={"da", "db"})
        result = _range(_node())
        assert nearby.FACT_RANGE_CITED_DOCUMENTS in result["facts"]
        assert result["facts"][-1] == nearby.FACT_DISCUSS_SHARPEN


class TestSharpenFact:
    def test_discussion_exit_is_executable_in_discuss(self):
        assert nearby.sharpen_fact_for(_node()) == nearby.FACT_DISCUSS_SHARPEN
        assert "テキストを選んで" not in nearby.FACT_DISCUSS_SHARPEN
        assert nearby.sharpen_fact_for(_node(anchor_id="t1")) == nearby.FACT_RANGE_SHARPEN
        assert nearby.sharpen_fact_for(None) == nearby.FACT_RANGE_SHARPEN


class TestJourneyCitedTier:
    def test_cited_range_entries(self, monkeypatch):
        _patch(monkeypatch, cited=["db"], graphs={"db"})
        rng = journey._cited_range_for_node("u1", _node(), can_view_document=lambda u, d: True)
        assert rng["basis"] == "cited"
        assert rng["entries"] and rng["entries"][0]["document_title"] == "論文db"

    def test_cited_range_fail_closed_without_permission_callback(self, monkeypatch):
        _patch(monkeypatch, cited=["db"], graphs={"db"})
        assert journey._cited_range_for_node("u1", _node(), can_view_document=None) is None
        assert journey._cited_range_for_node("u1", _node(), can_view_document=lambda u, d: False) is None

    def test_journey_uses_cited_preamble(self, monkeypatch):
        start = _node()
        rng = {"topic_label": "", "entries": [{"component_id": "m-db", "label": "理論の基礎", "document_title": "論文db"}], "basis": "cited"}
        net = type("N", (), {"nodes": [start]})()
        result = journey.build_person_journey(start, net, {}, [], {}, {}, {}, set(), topic_range=rng)
        facts = [s["fact"] for s in result["steps"]]
        assert facts[0] == journey.FACT_CITED_RANGE_PREAMBLE
        assert "論文db" in facts[1] and "触れています" not in facts[1]

    def test_empty_discuss_journey_points_to_discuss_exit(self):
        out = journey._with_empty_journey_notice({"steps": []}, _node())
        assert out["facts"] == [nearby.FACT_DISCUSS_SHARPEN]
        out2 = journey._with_empty_journey_notice({"steps": []}, _node(anchor_id="t1"))
        assert out2["facts"] == [nearby.FACT_RANGE_SHARPEN]
