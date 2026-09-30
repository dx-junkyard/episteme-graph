"""焦点論文の純関数（段順・優先規則・ID 衝突規則）と消費者の振る舞い。"""

from __future__ import annotations

import pytest

from core import focus_document as fd
from core.focus_document import (
    FocusDocument,
    prefer_focus,
    resolve_focus_document,
    resolve_in_focus,
    scope_chunks_to_focus,
    unique_row_lookup,
)


class TestTierOrder:
    def test_explicit_beats_everything(self):
        f = resolve_focus_document(
            allowed_document_ids={"a", "b", "c", "d"},
            explicit_document_id="a",
            topic_document_ids={"b"},
            previous_cited_document_ids=["c"],
            opening_document_id="d",
        )
        assert f == FocusDocument(("a",), "explicit")

    def test_topic_beats_previous_and_opening(self):
        f = resolve_focus_document(
            allowed_document_ids={"b", "c", "d"},
            topic_document_ids={"b"},
            previous_cited_document_ids=["c"],
            opening_document_id="d",
        )
        assert f.source == "topic" and f.document_ids == ("b",)

    def test_previous_beats_opening(self):
        f = resolve_focus_document(
            allowed_document_ids={"c", "d"},
            previous_cited_document_ids=["c"],
            opening_document_id="d",
        )
        assert f.source == "previous_citation"

    def test_opening_is_last(self):
        f = resolve_focus_document(allowed_document_ids={"d"}, opening_document_id="d")
        assert f.source == "opening" and f.document_ids == ("d",)

    def test_tiers_are_intersected_with_scope_and_fall_through(self):
        f = resolve_focus_document(
            allowed_document_ids={"c"},
            explicit_document_id="x",
            topic_document_ids={"y"},
            previous_cited_document_ids=["z", "c"],
        )
        assert f.source == "previous_citation" and f.document_ids == ("c",)

    def test_never_widens_scope(self):
        f = resolve_focus_document(allowed_document_ids=set(), topic_document_ids={"a"})
        assert f.source == "none" and not f
        f = resolve_focus_document(allowed_document_ids=None, explicit_document_id="a")
        assert f.source == "none"

    def test_previous_order_is_kept(self):
        f = resolve_focus_document(
            allowed_document_ids={"a", "b"}, previous_cited_document_ids=["b", "a", "b"]
        )
        assert f.document_ids == ("b", "a")


def _r(cid, doc, score):
    return {"id": cid, "document_id": doc, "score": score}


class TestPreferFocus:
    def test_focus_first_then_score(self):
        rows = [_r("o1", "o", 0.9), _r("f1", "f", 0.5), _r("f0", "f", 0.1)]
        out = prefer_focus(rows, {"f"})
        assert [r["id"] for r in out] == ["f1", "o1", "f0"]

    def test_strict_greater_rule(self):
        rows = [_r("f1", "f", 0.6), _r("eq", "o", 0.6), _r("hi", "o", 0.61), _r("lo", "o", 0.5)]
        out = prefer_focus(rows, {"f"})
        assert [r["id"] for r in out] == ["f1", "hi"]

    def test_no_adoptable_focus_row_drops_nothing(self):
        rows = [_r("f1", "f", 0.2), _r("o1", "o", 0.5)]
        assert [r["id"] for r in prefer_focus(rows, {"f"})] == ["o1", "f1"]

    def test_adoption_boundary_is_inclusive_for_focus(self):
        rows = [_r("f1", "f", 0.30), _r("o1", "o", 0.29)]
        assert [r["id"] for r in prefer_focus(rows, {"f"})] == ["f1"]

    def test_limit_applies_before_drop(self):
        rows = [_r(f"f{i}", "f", 0.5) for i in range(5)] + [_r("o", "o", 0.9)]
        out = prefer_focus(rows, {"f"}, limit=3)
        assert [r["id"] for r in out] == ["f0", "f1", "f2"]

    def test_empty_focus_is_identity(self):
        rows = [_r("a", "x", 0.1)]
        assert prefer_focus(rows, FocusDocument()) == rows

    def test_accepts_focus_object(self):
        rows = [_r("o", "o", 0.9), _r("f", "f", 0.5)]
        out = prefer_focus(rows, FocusDocument(("f",), "topic"))
        assert [r["id"] for r in out] == ["f", "o"]


class TestResolveInFocus:
    def test_focus_document_wins_collision(self):
        cands = {"a": ["eqA"], "b": ["eqB"]}
        assert resolve_in_focus(cands, ("b",)) == "eqB"

    def test_unique_in_scope_without_focus(self):
        assert resolve_in_focus({"a": ["eqA"]}, ()) == "eqA"

    def test_ambiguous_across_papers_is_none(self):
        assert resolve_in_focus({"a": ["eqA"], "b": ["eqB"]}, ()) is None

    def test_ambiguous_within_focus_paper_is_none(self):
        assert resolve_in_focus({"a": ["x", "y"]}, ("a",)) is None

    def test_focus_without_candidate_falls_back_to_unique(self):
        assert resolve_in_focus({"b": ["eqB"]}, ("a",)) == "eqB"

    def test_pairs_form(self):
        assert resolve_in_focus([("a", 1), ("b", 2)], ("a",)) == 1

    def test_scope_filters_mapping(self):
        assert resolve_in_focus({"a": [1], "z": [2]}, (), scope_document_ids={"a"}) == 1

    def test_lazy_lookup_reads_focus_first(self):
        calls = []

        def lookup(doc_ids):
            calls.append(list(doc_ids))
            return [(d, d.upper()) for d in doc_ids if d in {"a", "b"}]

        assert resolve_in_focus(lookup, ("b",), scope_document_ids=["a", "b", "c"]) == "B"
        assert calls == [["b"]]

    def test_lazy_lookup_ambiguous_rest(self):
        def lookup(doc_ids):
            return [(d, d) for d in doc_ids if d in {"a", "b"}]

        assert resolve_in_focus(lookup, ("c",), scope_document_ids=["a", "b", "c"]) is None

    def test_unique_row_lookup_checks_other_papers(self):
        rows = {"a": ("rowA",), "b": ("rowB",)}

        def fetch_one(doc_ids):
            for d in doc_ids:
                if d in rows:
                    return d, rows[d][0]
            return None

        lookup = unique_row_lookup(fetch_one)
        assert resolve_in_focus(lookup, (), scope_document_ids=["a", "b"]) is None
        assert resolve_in_focus(lookup, ("b",), scope_document_ids=["a", "b"]) == "rowB"

    def test_unique_row_lookup_exact_match_is_decisive(self):
        def fetch_one(doc_ids):
            return (doc_ids[0], "uuid-1") if doc_ids else None

        lookup = unique_row_lookup(fetch_one, is_exact=lambda item: item == "uuid-1")
        assert resolve_in_focus(lookup, (), scope_document_ids=["a", "b"]) == "uuid-1"


class TestScopeChunks:
    def test_keeps_focus_chunks(self):
        assert scope_chunks_to_focus(["c1", "c2"], {"c1": "a", "c2": "b"}, {"b"}) == ["c2"]

    def test_fail_soft_when_none_left(self):
        assert scope_chunks_to_focus(["c1"], {"c1": "a"}, {"b"}) == ["c1"]


# ---------------------------------------------------------------------------
# 消費者の振る舞い（フェイク DB / artifact）
# ---------------------------------------------------------------------------


class TestElementContextEquationCollision:
    def _patch(self, monkeypatch, by_doc):
        from core import element_context

        monkeypatch.setattr(element_context, "document_run_artifacts", lambda d: {})
        monkeypatch.setattr(
            element_context,
            "equation_records",
            lambda d, artifacts=None: [{"equation_id": e} for e in by_doc.get(d, [])],
        )
        return element_context

    def test_focus_paper_wins(self, monkeypatch):
        ec = self._patch(monkeypatch, {"da": ["eq_5"], "db": ["eq_5"]})
        assert ec._resolve_equation("eq_5", {"da", "db"}, ("db",)) == ("eq_5", "db")

    def test_ambiguous_without_focus(self, monkeypatch):
        ec = self._patch(monkeypatch, {"da": ["eq_5"], "db": ["eq_5"]})
        assert ec._resolve_equation("eq_5", {"da", "db"}) is None

    def test_unique_without_focus(self, monkeypatch):
        ec = self._patch(monkeypatch, {"da": ["eq_5"], "db": ["eq_1"]})
        assert ec._resolve_equation("eq_5", {"da", "db"}) == ("eq_5", "da")


class TestComponentContextCollision:
    def _patch(self, monkeypatch, rows):
        from core import component_context

        def query(component_id, document_ids):
            hits = [r for r in rows if r["document_id"] in set(document_ids)
                    and (r["id"] == component_id or component_id in r["legacy"])]
            hits.sort(key=lambda r: r["id"] == component_id, reverse=True)
            return dict(hits[0]) if hits else None

        monkeypatch.setattr(component_context, "_query_component_row", query)
        return component_context

    ROWS = [
        {"id": "u-a", "document_id": "da", "legacy": ["comp_001"]},
        {"id": "u-b", "document_id": "db", "legacy": ["comp_001"]},
    ]

    def test_focus_paper_wins(self, monkeypatch):
        cc = self._patch(monkeypatch, self.ROWS)
        assert cc._resolve_component_row("comp_001", {"da", "db"}, ("db",))["id"] == "u-b"

    def test_ambiguous_without_focus_is_none(self, monkeypatch):
        cc = self._patch(monkeypatch, self.ROWS)
        assert cc._resolve_component_row("comp_001", {"da", "db"}) is None

    def test_uuid_is_unique(self, monkeypatch):
        cc = self._patch(monkeypatch, self.ROWS)
        assert cc._resolve_component_row("u-a", {"da", "db"})["id"] == "u-a"


class TestDescentCollision:
    def test_equation_focus_and_ambiguity(self, monkeypatch):
        from core.descent import resolve

        by_doc = {"da": ["eq_5"], "db": ["eq_5"]}
        monkeypatch.setattr(resolve, "document_run_artifacts", lambda d: {})
        monkeypatch.setattr(
            resolve, "equation_records",
            lambda d, artifacts=None: [{"equation_id": e} for e in by_doc.get(d, [])],
        )
        monkeypatch.setattr(resolve, "_equation_display_label", lambda record: "")
        assert resolve._resolve_equation("eq_5", ["da", "db"]) is None
        hit = resolve._resolve_equation("eq_5", ["da", "db"], ["db"])
        assert hit is not None and hit.document_id == "db"

    def test_row_ambiguity_is_fail_closed(self, monkeypatch):
        from core.descent import resolve

        rows = {"da": ("u-a", "da", {}, "A"), "db": ("u-b", "db", {}, "B")}

        def fake_row(table, element_id, document_ids):
            for d in document_ids:
                if d in rows:
                    return rows[d]
            return None

        monkeypatch.setattr(resolve, "_resolve_row", fake_row)
        monkeypatch.setattr(resolve, "course_document_ids", lambda cd: {"da", "db"})
        assert resolve.resolve_element("claim", "claim_span_001", {}) is None
        hit = resolve.resolve_element("claim", "claim_span_001", {}, preferred_document_ids=("db",))
        assert hit is not None and hit.document_id == "db"


class TestNearbyDiscussRange:
    def test_discussion_trace_uses_cited_papers_before_course(self, monkeypatch):
        from core.personal_graph import nearby, queries
        from core.personal_graph.schema import PersonalAnchor, PersonalNode

        start = PersonalNode(
            id="t1", node_kind="question", label="q",
            anchor=PersonalAnchor(anchor_type="topic", anchor_id="_discussion"),
            topic_id="_discussion", course_id="c1", created_at="", facts=[], source={},
        )
        monkeypatch.setattr(queries, "fetch_topic_claim_binding", lambda cid, topic_id: {})
        monkeypatch.setattr(queries, "fetch_course_document_ids", lambda cid: {"da", "db", "dc"})
        monkeypatch.setattr(queries, "fetch_trace_cited_document_ids", lambda tid: ["db"])
        seen = []

        def graph(doc):
            seen.append(doc)
            return {"nodes": [{"component_id": f"m-{doc}", "graph_layer": "main", "label": "Theory basis"}], "edges": []}

        monkeypatch.setattr(queries, "fetch_component_graph", graph)
        monkeypatch.setattr(queries, "fetch_document_titles", lambda ids: {d: d for d in ids})
        monkeypatch.setattr(queries, "fetch_component_ledger_statuses", lambda ids: {})
        monkeypatch.setattr(nearby, "_resolve_range_atlas_context", lambda cid, tid: None)
        monkeypatch.setattr(nearby, "_claim_summaries_for_graphs", lambda entries: {})

        class _Net:
            nodes: list = []

        result = nearby._nearby_for_topic_anchor(
            start, _Net(), mode="near", center_component_id=None,
            can_view_document=None, user_id="u1",
        )
        assert seen == ["db"]
        assert nearby.FACT_RANGE_CITED_DOCUMENTS in (result or {}).get("facts", [])


class TestAnchorWorkerConceptScope:
    def test_concepts_are_scoped_to_cited_chunk_documents(self):
        from core.structure_anchor.worker import _collect_context_blocks

        queries = []

        class _Res:
            def fetchall(self):
                return []

        class _Session:
            def execute(self, stmt, params):
                queries.append((str(stmt), dict(params)))
                return _Res()

        _collect_context_blocks(_Session(), "c1", ["ch1"])
        concept_sql = [q for q in queries if "theory_components_live" in q[0]]
        assert concept_sql and "course_id" not in concept_sql[0][0]
        assert "FROM chunks" in concept_sql[0][0]

    def test_concepts_fall_back_to_course_without_citations(self):
        from core.structure_anchor.worker import _collect_context_blocks

        queries = []

        class _Res:
            def fetchall(self):
                return []

        class _Session:
            def execute(self, stmt, params):
                queries.append(str(stmt))
                return _Res()

        _collect_context_blocks(_Session(), "c1", [])
        assert any("course_id = :cid" in q for q in queries)


def test_module_exports():
    assert set(fd.__all__) >= {"resolve_focus_document", "prefer_focus", "resolve_in_focus"}


@pytest.mark.parametrize("value,expected", [(None, []), ("a", ["a"]), ({"b", "a"}, ["a", "b"]), (["b", "a"], ["b", "a"])])
def test_ordered_ids(value, expected):
    assert fd._ordered_ids(value) == expected


# ---------------------------------------------------------------------------
# レビュー是正 M5: 焦点の段はまとまりとして判定する（FD4・推測で選ばない）
# ---------------------------------------------------------------------------


class TestFocusGroupAmbiguity:
    def test_two_focus_papers_with_candidates_is_none(self):
        # トピックが2本の論文を束ね、両方に eq_5 がある → UUID 順の先頭を選ばない。
        cands = {"a": ["eqA"], "b": ["eqB"]}
        assert resolve_in_focus(cands, ("a", "b")) is None
        assert resolve_in_focus(cands, ("b", "a")) is None

    def test_only_one_focus_paper_has_candidate(self):
        assert resolve_in_focus({"a": [], "b": ["eqB"], "c": ["eqC"]}, ("a", "b")) == "eqB"

    def test_focus_without_candidates_falls_back_to_unique_in_scope(self):
        assert resolve_in_focus({"c": ["eqC"]}, ("a", "b")) == "eqC"

    def test_lazy_group_ambiguity_is_none(self):
        def lookup(doc_ids):
            return [(d, d.upper()) for d in doc_ids if d in {"a", "b"}]

        assert resolve_in_focus(lookup, ("a", "b"), scope_document_ids=["a", "b", "c"]) is None

    def test_lazy_unique_row_lookup_group_ambiguity_is_none(self):
        rows = {"a": "rowA", "b": "rowB"}

        def fetch_one(doc_ids):
            for d in doc_ids:
                if d in rows:
                    return d, rows[d]
            return None

        lookup = unique_row_lookup(fetch_one)
        assert resolve_in_focus(lookup, ("a", "b"), scope_document_ids=["a", "b"]) is None


# ---------------------------------------------------------------------------
# レビュー是正 M4 (b): services.learner_focus_document の直前の引用の段は議論だけ
# ---------------------------------------------------------------------------


class TestLearnerFocusPreviousCitationGate:
    def _patch(self, monkeypatch):
        from api import services

        calls: list = []

        def prev(user_id, course_id, topic_id):
            calls.append(topic_id)
            return ["d-prev"]

        monkeypatch.setattr(services, "previous_cited_document_ids", prev)
        monkeypatch.setattr(services, "find_course_topic", lambda cd, tid: {"id": tid})
        monkeypatch.setattr(services, "topic_source_document_ids", lambda topic: set())
        return services, calls

    def test_ordinary_topic_without_docs_does_not_stick_to_previous(self, monkeypatch):
        services, calls = self._patch(monkeypatch)
        focus = services.learner_focus_document(
            {"id": "u"}, {}, "c1", "t1", allowed_document_ids={"d-prev", "d-other"}
        )
        assert focus.source == "none" and not focus.document_ids
        assert calls == []

    def test_discussion_uses_previous_citation(self, monkeypatch):
        services, calls = self._patch(monkeypatch)
        focus = services.learner_focus_document(
            {"id": "u"}, {}, "c1", "_discussion", allowed_document_ids={"d-prev", "d-other"}
        )
        assert focus.source == "previous_citation" and focus.document_ids == ("d-prev",)

    def test_opening_tier_is_passed_through(self, monkeypatch):
        services, _calls = self._patch(monkeypatch)
        focus = services.learner_focus_document(
            {"id": "u"}, {}, "c1", "t1",
            allowed_document_ids={"d-prev", "d-other"}, opening_document_id="d-other",
        )
        assert focus.source == "opening" and focus.document_ids == ("d-other",)
