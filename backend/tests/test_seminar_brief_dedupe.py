"""ゼミ前ブリーフの同じ文面の項目・自己支持・学習者からの問い（IK-0593）。

実 DB・実 LLM 不使用。fake の読み時辞書・依存グラフ・セッションで純関数を確かめる:

- (1) 同じ文面の項目: 同じ対象は 1 行にまとめ事実文を添える / 別の対象は式の名前で見分ける
- (2) 支持線: 対象と同じ主張を根拠にもつノードは root にも崩れる対象にも数えず、
  残らなければ既存の閉世界の事実文（SL1）に縮退する
- (4) 第4区画: 素朴な問いシグナルの k-匿名集約を論文の範囲に絞り、名前とレンジだけを出す
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api"), str(BACKEND.parent / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.doubt import naive_signal as ns  # noqa: E402
from core.doubt import seminar_brief as sb  # noqa: E402
from core.doubt import support_paths as sp  # noqa: E402
from core.doubt.dependency import DependencyGraph  # noqa: E402


def _labels(*, node_labels=None, node_claim_ids=None, node_operations=None,
            node_equation_ids=None, equation_records=None) -> "sb._ReadableLabels":
    labels = sb._ReadableLabels.__new__(sb._ReadableLabels)
    labels.equation_records = dict(equation_records or {})
    labels.node_labels = dict(node_labels or {})
    labels.claim_texts = {}
    labels.node_claim_ids = dict(node_claim_ids or {})
    labels.node_operations = dict(node_operations or {})
    labels.node_equation_ids = dict(node_equation_ids or {})
    return labels


_TEXT = "中間主張を導出: The method does not use an external TOV solver"


class TestSameTextDisambiguation:
    def test_same_claim_same_operation_without_clue_is_merged(self):
        labels = _labels(
            node_labels={"n1": _TEXT, "n2": _TEXT},
            node_claim_ids={"n1": ["c-1"], "n2": ["claim:c-1"]},
            node_operations={"n1": "infer_intermediate_claim", "n2": "infer_intermediate_claim"},
        )
        items = [
            {"target_id": "n1", "target_type": "component", "statement": _TEXT},
            {"target_id": "n2", "target_type": "component", "statement": _TEXT},
        ]
        out = sb._disambiguate_same_text(items, labels)
        assert len(out) == 1
        assert out[0]["merged_note"] == sb.MERGED_SAME_TEXT_NOTE
        assert out[0]["statement"] == _TEXT

    def test_different_nodes_are_told_apart_by_equation_names(self):
        labels = _labels(
            node_labels={"n1": _TEXT, "n2": _TEXT},
            node_claim_ids={"n1": ["c-1"], "n2": ["c-1"]},
            node_operations={"n1": "infer_intermediate_claim", "n2": "infer_intermediate_claim"},
            node_equation_ids={"n1": ["eq_3"], "n2": ["eq_4"]},
            equation_records={"eq_3": {"equation_id": "eq_3", "label": "(3)"},
                              "eq_4": {"equation_id": "eq_4", "label": "(4)"}},
        )
        items = [
            {"target_id": "n1", "target_type": "component", "statement": _TEXT},
            {"target_id": "n2", "target_type": "component", "statement": _TEXT},
        ]
        out = sb._disambiguate_same_text(items, labels)
        assert [i["statement"] for i in out] == [f"{_TEXT}（式 (3)）", f"{_TEXT}（式 (4)）"]
        assert all("merged_note" not in i for i in out)
        # 内部 ID は文面に出ない
        assert all("eq_" not in i["statement"] and "n1" not in i["statement"] for i in out)

    def test_different_targets_without_clue_get_the_indistinct_note(self):
        labels = _labels()
        items = [
            {"target_id": "a-1", "target_type": "assumption", "statement": "検出器は線形である"},
            {"target_id": "a-2", "target_type": "assumption", "statement": "検出器は線形である"},
        ]
        out = sb._disambiguate_same_text(items, labels)
        assert len(out) == 1
        assert out[0]["merged_note"] == sb.MERGED_INDISTINCT_NOTE

    def test_distinct_texts_are_untouched_and_order_is_kept(self):
        labels = _labels()
        items = [
            {"target_id": "a-1", "target_type": "assumption", "statement": "前提 A"},
            {"target_id": "a-2", "target_type": "assumption", "statement": "前提 B"},
        ]
        out = sb._disambiguate_same_text([dict(i) for i in items], labels)
        assert out == items

    def test_extra_key_keeps_lines_with_different_facts(self):
        labels = _labels()
        items = [
            {"target_id": "a-1", "target_type": "claim", "statement": "S", "fact_line": "F1"},
            {"target_id": "a-2", "target_type": "claim", "statement": "S", "fact_line": "F2"},
        ]
        out = sb._disambiguate_same_text(items, labels, extra_key="fact_line")
        assert len(out) == 2

    def test_notes_carry_no_digits(self):
        for note in (sb.MERGED_SAME_TEXT_NOTE, sb.MERGED_INDISTINCT_NOTE,
                     sb.LEARNER_HANDOFF_NO_RECORD_NOTE, sb.LEARNER_HANDOFF_SOURCE_NOTE):
            assert not any(ch.isdigit() for ch in note), note


def _graph(*, node_claims: dict, edges: list[tuple[str, str]], obs_claim: str) -> DependencyGraph:
    graph = DependencyGraph()
    for nid, claims in node_claims.items():
        graph.node_ids.add(nid)
        graph.node_labels[nid] = f"label-{nid}"
        graph.node_claim_ids[nid] = list(claims)
        for c in claims:
            graph.claim_refs.setdefault(c, set()).add(nid)
    for u, v in edges:
        graph.successors.setdefault(u, set()).add(v)
        graph.predecessors.setdefault(v, set()).add(u)
    return graph


def _ctx(graph: DependencyGraph, edges, obs_claim: str) -> sp.SupportPathContext:
    adjacency: dict[str, set[str]] = {}
    for u, v in edges:
        adjacency.setdefault(u, set()).add(v)
    return sp.SupportPathContext(graph=graph, obs_targets=[{"claim_id": obs_claim, "label": "obs"}],
                                 adjacency=adjacency)


class TestSelfSupportExcluded:
    EDGES = [("r1", "mid"), ("r2", "mid"), ("mid", "t")]

    def test_cut_member_with_the_target_claim_falls_back_to_closed_world(self):
        graph = _graph(node_claims={"r1": ["obs"], "r2": ["obs"], "mid": ["c-t"], "t": ["c-t"]},
                       edges=self.EDGES, obs_claim="obs")
        lines = sp.compute_support_lines_from_context(_ctx(graph, self.EDGES, "obs"), "component", "t")
        assert lines["level"] == "none"
        assert lines["fact_line"] == sp.FACT_LINE_NONE
        assert "このコーパスの中では" in lines["fact_line"]
        assert lines["cut_members"] == []

    def test_cut_member_with_a_different_claim_is_kept(self):
        graph = _graph(node_claims={"r1": ["obs"], "r2": ["obs"], "mid": ["c-mid"], "t": ["c-t"]},
                       edges=self.EDGES, obs_claim="obs")
        lines = sp.compute_support_lines_from_context(_ctx(graph, self.EDGES, "obs"), "component", "t")
        assert lines["level"] == "single"
        assert [m["node_id"] for m in lines["cut_members"]] == ["mid"]
        assert "label-mid" in lines["fact_line"]

    def test_root_that_restates_the_target_claim_is_not_a_root(self):
        edges = [("r", "mid"), ("mid", "t")]
        graph = _graph(node_claims={"r": ["obs", "claim:c-t"], "mid": ["c-mid"], "t": ["c-t"]},
                       edges=edges, obs_claim="obs")
        # r は観測の主張と対象の主張を両方根拠にもつ = 対象の言い直し。root に数えない。
        lines = sp.compute_support_lines_from_context(_ctx(graph, edges, "obs"), "component", "t")
        assert lines["level"] == "none"
        # 対照: r が対象の主張を持たなければ単一の支持線
        graph2 = _graph(node_claims={"r": ["obs"], "mid": ["c-mid"], "t": ["c-t"]}, edges=edges, obs_claim="obs")
        assert sp.compute_support_lines_from_context(_ctx(graph2, edges, "obs"), "component", "t")["level"] == "single"

    def test_fact_line_has_no_digits(self):
        graph = _graph(node_claims={"r1": ["obs"], "r2": ["obs"], "mid": ["c-mid"], "t": ["c-t"]},
                       edges=self.EDGES, obs_claim="obs")
        lines = sp.compute_support_lines_from_context(_ctx(graph, self.EDGES, "obs"), "component", "t")
        assert not any(ch.isdigit() for ch in lines["fact_line"])


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _HandoffSession:
    def __init__(self, *, courses=(("course-a",),), claims=(("c-1",),)):
        self.courses = courses
        self.claims = claims

    def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM learning_courses" in sql:
            return _Result(self.courses)
        if "FROM theory_claims_live" in sql:
            return _Result([r for r in self.claims if r[0] in (params or {}).get("ids", [])])
        if "FROM theory_components_live" in sql:
            return _Result([])
        raise RuntimeError(sql[:60])

    def rollback(self):
        pass


class TestLearnerHandoff:
    def _signals(self):
        return [
            {"anchor_type": "claim", "anchor_id": "c-1", "anchor_label": "", "count_range": "3-5", "doubt_types": []},
            {"anchor_type": "claim", "anchor_id": "c-other-doc", "anchor_label": "", "count_range": "6-10", "doubt_types": []},
            {"anchor_type": "equation", "anchor_id": "eq_7", "anchor_label": "", "count_range": "11+", "doubt_types": []},
        ]

    def test_signals_become_named_range_facts_scoped_to_the_document(self, monkeypatch):
        seen = {}

        def fake_agg(session, course_ids):
            seen["course_ids"] = list(course_ids)
            return self._signals()

        monkeypatch.setattr(sb, "aggregate_naive_signals_for_courses", fake_agg)
        monkeypatch.setattr(sb, "target_label", lambda session, t, i, **_kw: "検出器の応答は線形である")
        labels = _labels(equation_records={"eq_7": {"equation_id": "eq_7", "label": "(7)"}})
        out = sb._learner_handoff(_HandoffSession(), "doc-1", labels)
        assert seen["course_ids"] == ["course-a"]
        assert out["note"] == sb.LEARNER_HANDOFF_SOURCE_NOTE
        assert [i["statement"] for i in out["items"]] == ["検出器の応答は線形である", "式 (7)"]
        assert out["items"][0]["fact_line"] == "複数の学習者が、ここで問いを立てています（3-5 人）。"
        # 別の論文の箇所は出さない・内部 ID を出さない・人の識別子を持たない
        flat = repr(out)
        assert "c-other-doc" not in flat and "eq_7" not in flat and "user" not in flat

    def test_no_course_means_no_record_fact(self, monkeypatch):
        monkeypatch.setattr(sb, "aggregate_naive_signals_for_courses", lambda s, c: self._signals())
        out = sb._learner_handoff(_HandoffSession(courses=()), "doc-1", _labels())
        assert out == {"reserved": False, "items": [], "note": sb.LEARNER_HANDOFF_NO_RECORD_NOTE}

    def test_unreadable_names_are_dropped(self, monkeypatch):
        monkeypatch.setattr(sb, "aggregate_naive_signals_for_courses", lambda s, c: self._signals()[:1])
        monkeypatch.setattr(sb, "target_label", lambda session, t, i, **_kw: i)  # 名前が引けず id が返る
        out = sb._learner_handoff(_HandoffSession(), "doc-1", _labels())
        assert out["items"] == []
        assert out["note"] == sb.LEARNER_HANDOFF_NO_RECORD_NOTE


class TestNaiveSignalsAcrossCourses:
    def test_k_anonymity_counts_unique_learners_across_courses(self, monkeypatch):
        rows = {
            "course-a": [("u1", "claim", "c-1", "", "premise"), ("u2", "claim", "c-1", "", "premise")],
            "course-b": [("u3", "claim", "c-1", "", "premise"), ("u1", "claim", "c-2", "", "premise")],
        }
        monkeypatch.setattr(ns, "_collect_anchor_rows", lambda session, cid: rows[cid])
        signals = ns.aggregate_naive_signals_for_courses(object(), ["course-b", "course-a", ""])
        assert [(s["anchor_id"], s["count_range"]) for s in signals] == [("c-1", "3-5")]
        assert all("user" not in k for s in signals for k in s)

    def test_failing_course_is_skipped(self, monkeypatch):
        def boom(session, cid):
            raise RuntimeError("x")

        class _S:
            def rollback(self):
                pass

        monkeypatch.setattr(ns, "_collect_anchor_rows", boom)
        assert ns.aggregate_naive_signals_for_courses(_S(), ["course-a"]) == []
