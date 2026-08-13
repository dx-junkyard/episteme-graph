"""SL層（賭け金の台帳, Stakes Ledger）core モジュールの挙動テスト。

対象: core/doubt/observation_targets.py（SL-2 多段同定）/
core/doubt/support_paths.py（SL-3 独立支持経路・純 Python max-flow）/
core/doubt/falsification_conditions/（SL-1 候補抽出 validator・agent・worker）。

DB / FastAPI TestClient を使わず、fake session（test_understanding_cycle_api.py /
test_personal_graph_map_ops.py と同型）でルート関数を直接呼ぶ方式で検証する。
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.doubt import observation_targets, support_paths  # noqa: E402
from core.doubt.falsification_conditions import worker as fc_worker  # noqa: E402
from core.doubt.falsification_conditions.agent import FalsificationConditionAgent  # noqa: E402
from core.doubt.falsification_conditions.schema import (  # noqa: E402
    FalsificationCandidateResult,
    FalsificationTargetContext,
    SourceBlock,
)
from core.doubt.falsification_conditions.validator import validate_output  # noqa: E402
from core.doubt.schema import FalsificationCandidate  # noqa: E402


# ===========================================================================
# フェイクセッション（SQL テキストのパターンにディスパッチする汎用フェイク）
# ===========================================================================


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows or []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _DispatchSession:
    """(predicate(sql) -> bool, rows) の列に先勝ちでディスパッチするフェイクセッション。

    どの handler にもマッチしない SQL は空の結果を返す（fail-soft な実装側の
    try/except を無理に発火させない）。
    """

    def __init__(self, handlers):
        self._handlers = list(handlers)
        self.calls: list[tuple[str, dict]] = []
        self.committed = 0
        self.rolled_back = 0
        self.closed = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = dict(params or {})
        self.calls.append((sql, params))
        for predicate, rows in self._handlers:
            if predicate(sql):
                return _FakeResult(rows)
        return _FakeResult([])

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


def _is_graph_rows_query(sql: str) -> bool:
    return "theory_component_graphs" in sql and "SELECT document_id, graph_json" in sql


def _is_qualified_adjacency_query(sql: str) -> bool:
    return "theory_component_graphs" in sql and "SELECT graph_json" in sql


def _is_claim_type_query(sql: str) -> bool:
    return "theory_claims" in sql and "claim_type = ANY" in sql


def _is_claim_label_query(sql: str) -> bool:
    return "theory_claims" in sql and "COALESCE(NULLIF(normalized_text" in sql


# ===========================================================================
# core/doubt/observation_targets.py — SL-2 多段同定
# ===========================================================================


class TestObservationClaimTargets:
    def _session(self, graph_rows=None, claim_type_rows=None, label_rows=None):
        return _DispatchSession([
            (_is_graph_rows_query, graph_rows or []),
            (_is_claim_type_query, claim_type_rows or []),
            (_is_claim_label_query, label_rows or []),
        ])

    def test_dsl_measures_is_identified_first(self):
        graph_json = {
            "nodes": [], "edges": [],
            "dsl": {"edges": [
                {"from": "n1", "to": "n2", "predicate": "MEASURES",
                 "evidence_refs": {"claim_ids": ["claim-a"]}},
            ]},
        }
        session = self._session(
            graph_rows=[("doc-1", graph_json)],
            label_rows=[("claim-a", "観測量Xの定義")],
        )
        result = observation_targets.observation_claim_targets(session, document_id="doc-1")
        assert result == [
            {"claim_id": "claim-a", "label": "観測量Xの定義", "identified_via": "dsl_measures"}
        ]

    def test_theory_stage_identified_when_dsl_empty(self):
        """dsl ブロックが空の旧 run では A が空になるだけで B が生きる（fail-soft）。"""
        graph_json = {
            "nodes": [{
                "component_id": "node-1",
                "label": "Diagnostic / application",
                "graph_layer": "main",
                "linked_claim_ids": ["claim-b"],
            }],
            "edges": [],
            "dsl": {},
        }
        session = self._session(
            graph_rows=[("doc-1", graph_json)],
            label_rows=[("claim-b", "診断的主張")],
        )
        result = observation_targets.observation_claim_targets(session, document_id="doc-1")
        assert result == [
            {"claim_id": "claim-b", "label": "診断的主張", "identified_via": "theory_stage"}
        ]

    def test_non_observation_stage_is_ignored(self):
        graph_json = {
            "nodes": [{
                "component_id": "node-1",
                "label": "Theory basis",  # 観測系ではない stage
                "graph_layer": "main",
                "linked_claim_ids": ["claim-b"],
            }],
            "edges": [],
        }
        session = self._session(graph_rows=[("doc-1", graph_json)])
        result = observation_targets.observation_claim_targets(session, document_id="doc-1")
        assert result == []

    def test_claim_type_fallback_when_no_graph_signal(self):
        """グラフが無い（旧 run/未解析）文書でも claim_type 縮退は生きる。"""
        session = self._session(
            graph_rows=[],
            claim_type_rows=[("claim-c",)],
            label_rows=[("claim-c", "観測量の定義文")],
        )
        result = observation_targets.observation_claim_targets(session, document_id="doc-1")
        assert result == [
            {"claim_id": "claim-c", "label": "観測量の定義文", "identified_via": "claim_type"}
        ]

    def test_priority_dsl_over_claim_type_for_same_claim(self):
        """同一 claim が複数経路で見つかったら最初（優先度の高い）経路を保持する。"""
        graph_json = {
            "nodes": [], "edges": [],
            "dsl": {"edges": [
                {"from": "n1", "to": "n2", "predicate": "MEASURES",
                 "evidence_refs": {"claim_ids": ["claim-x"]}},
            ]},
        }
        session = self._session(
            graph_rows=[("doc-1", graph_json)],
            claim_type_rows=[("claim-x",)],
            label_rows=[("claim-x", "X")],
        )
        result = observation_targets.observation_claim_targets(session, document_id="doc-1")
        assert result == [{"claim_id": "claim-x", "label": "X", "identified_via": "dsl_measures"}]

    def test_results_are_sorted_and_deduplicated(self):
        graph_json = {
            "nodes": [], "edges": [],
            "dsl": {"edges": [
                {"from": "n1", "to": "n2", "predicate": "MEASURES",
                 "evidence_refs": {"claim_ids": ["claim-2", "claim-1", "claim-1"]}},
            ]},
        }
        session = self._session(
            graph_rows=[("doc-1", graph_json)],
            label_rows=[("claim-1", "A"), ("claim-2", "B")],
        )
        result = observation_targets.observation_claim_targets(session, document_id="doc-1")
        assert [item["claim_id"] for item in result] == ["claim-1", "claim-2"]

    def test_predicate_matching_is_case_insensitive_and_edge_type_fallback(self):
        graph_json = {
            "nodes": [], "edges": [],
            "dsl": {"edges": [
                {"from": "n1", "to": "n2", "edge_type": "measures",
                 "evidence_refs": {"claim_ids": ["claim-y"]}},
            ]},
        }
        session = self._session(
            graph_rows=[("doc-1", graph_json)],
            label_rows=[("claim-y", "Y")],
        )
        result = observation_targets.observation_claim_targets(session, document_id="doc-1")
        assert result == [{"claim_id": "claim-y", "label": "Y", "identified_via": "dsl_measures"}]

    def test_empty_everything_returns_empty_list(self):
        session = self._session()
        result = observation_targets.observation_claim_targets(session, document_id="doc-1")
        assert result == []


# ===========================================================================
# core/doubt/support_paths.py — SL-3 純 Python max-flow
# ===========================================================================


class TestMaxFlowEdgeDisjointPaths:
    def test_no_roots_gives_zero_flow(self):
        flow, _capacity, _reach = support_paths._max_flow_edge_disjoint_paths(
            {}, set(), {"target"},
        )
        assert flow == 0

    def test_single_direct_path_gives_flow_one(self):
        adjacency = {"root1": {"target"}}
        flow, _capacity, _reach = support_paths._max_flow_edge_disjoint_paths(
            adjacency, {"root1"}, {"target"},
        )
        assert flow == 1

    def test_two_fully_disjoint_paths_give_flow_two(self):
        """単一 sink でも、真に独立な2本の経路があれば flow=2 になる
        （sink→仮想T の容量をボトルネックにしないことの正しさの核心テスト）。
        """
        adjacency = {
            "root1": {"mid1"}, "mid1": {"target"},
            "root2": {"mid2"}, "mid2": {"target"},
        }
        flow, _capacity, _reach = support_paths._max_flow_edge_disjoint_paths(
            adjacency, {"root1", "root2"}, {"target"},
        )
        assert flow == 2

    def test_shared_bottleneck_edge_caps_flow_at_one(self):
        """2つの root が同じ内部エッジ mid→target を共有すると、
        エッジ容量1の制約により flow は1に留まる（真の独立性のみを数える）。
        """
        adjacency = {"root1": {"mid"}, "root2": {"mid"}, "mid": {"target"}}
        flow, _capacity, _reach = support_paths._max_flow_edge_disjoint_paths(
            adjacency, {"root1", "root2"}, {"target"},
        )
        assert flow == 1

    def test_same_root_two_outgoing_edges_still_counts_once(self):
        """同一 root からの2本の出力エッジも、root 自体の容量1により1本しか数えない
        （同じ観測根拠からの分岐は独立支持線として二重に数えない）。
        """
        adjacency = {"root1": {"midA", "midB"}, "midA": {"target"}, "midB": {"target"}}
        flow, _capacity, _reach = support_paths._max_flow_edge_disjoint_paths(
            adjacency, {"root1"}, {"target"},
        )
        assert flow == 1

    def test_multiple_sink_nodes_each_reachable_independently(self):
        adjacency = {"root1": {"sinkA"}, "root2": {"sinkB"}}
        flow, _capacity, _reach = support_paths._max_flow_edge_disjoint_paths(
            adjacency, {"root1", "root2"}, {"sinkA", "sinkB"},
        )
        assert flow == 2

    def test_result_is_deterministic_across_repeated_calls(self):
        adjacency = {"root1": {"mid1"}, "mid1": {"target"}, "root2": {"mid2"}, "mid2": {"target"}}
        results = [
            support_paths._max_flow_edge_disjoint_paths(adjacency, {"root1", "root2"}, {"target"})[0]
            for _ in range(5)
        ]
        assert results == [2] * 5


class TestCutMembers:
    def test_single_path_cut_member_falls_back_to_real_downstream_node(self):
        """S→root の仮想エッジがカットの唯一の境界のとき、上流(仮想S)ではなく
        下流の実ノード(root)を cut_members に使う（一般化, 実装コメント参照）。
        """
        adjacency = {"root1": {"target"}}
        flow, capacity, s_reachable = support_paths._max_flow_edge_disjoint_paths(
            adjacency, {"root1"}, {"target"},
        )
        assert flow == 1
        original_edges = [
            ("root1", "target"),
            (support_paths._SOURCE_NODE, "root1"),
            ("target", support_paths._SINK_NODE),
        ]
        members = support_paths._cut_members(
            original_edges, s_reachable, {"root1": "Root One", "target": "Target"},
        )
        assert members == [{"node_id": "root1", "label": "Root One"}]

    def test_cut_members_are_sorted_and_capped(self):
        node_labels = {f"n{i}": f"Label{i}" for i in range(10)}
        original_edges = [
            (support_paths._SOURCE_NODE, f"n{i}") for i in range(10)
        ]
        s_reachable = {support_paths._SOURCE_NODE}
        members = support_paths._cut_members(original_edges, s_reachable, node_labels)
        assert len(members) == 5
        assert [m["node_id"] for m in members] == sorted(m["node_id"] for m in members)


class TestQualifiedAdjacency:
    def _session_with_graph(self, graph_json):
        return _DispatchSession([(_is_qualified_adjacency_query, [(graph_json,)])])

    def test_only_source_backed_and_partially_backed_edges_count(self):
        graph_json = {
            "edges": [
                {"source_component_id": "a", "target_component_id": "b",
                 "source_backing_status": "source_backed"},
                {"source_component_id": "b", "target_component_id": "c",
                 "source_backing_status": "partially_source_backed"},
                {"source_component_id": "c", "target_component_id": "d",
                 "source_backing_status": "inferred"},
                {"source_component_id": "d", "target_component_id": "e",
                 "source_backing_status": "review_required"},
                {"source_component_id": "e", "target_component_id": "f",
                 "source_backing_status": ""},
            ]
        }
        session = self._session_with_graph(graph_json)
        node_ids = {"a", "b", "c", "d", "e", "f"}
        adjacency = support_paths._qualified_adjacency(session, "", "", node_ids)
        assert adjacency == {"a": {"b"}, "b": {"c"}}

    def test_edges_outside_node_id_scope_are_excluded(self):
        graph_json = {
            "edges": [
                {"source_component_id": "a", "target_component_id": "outside",
                 "source_backing_status": "source_backed"},
            ]
        }
        session = self._session_with_graph(graph_json)
        adjacency = support_paths._qualified_adjacency(session, "", "", {"a"})
        assert adjacency == {}


class TestComputeSupportLinesIntegration:
    """compute_support_lines() のエンドツーエンド（3種類の SQL 形状すべてを
    1つのフェイクセッションでディスパッチする）。
    """

    def _session(self, graph_rows, claim_type_rows=None, label_rows=None):
        return _DispatchSession([
            (_is_graph_rows_query, graph_rows),
            (_is_qualified_adjacency_query, [(g,) for _doc, g in graph_rows]),
            (_is_claim_type_query, claim_type_rows or []),
            (_is_claim_label_query, label_rows or []),
        ])

    def test_target_not_in_graph_returns_none(self):
        session = self._session(graph_rows=[])
        result = support_paths.compute_support_lines(session, "component", "missing-node")
        assert result is None

    def test_no_observation_claims_gives_none_level(self):
        graph_json = {
            "nodes": [{"component_id": "target-node", "label": "Consistency relation",
                       "graph_layer": "main"}],
            "edges": [],
            "dsl": {},
        }
        session = self._session(graph_rows=[("doc-1", graph_json)])
        result = support_paths.compute_support_lines(
            session, "component", "target-node", document_id="doc-1",
        )
        assert result == {
            "level": "none",
            "fact_line": support_paths.FACT_LINE_NONE,
            "cut_members": [],
            "observation_roots": [],
        }
        assert "このコーパスの中では" in result["fact_line"]

    def test_single_qualifying_root_gives_single_level(self):
        graph_json = {
            "nodes": [
                {
                    "component_id": "root-node-1",
                    "label": "Diagnostic / application",
                    "graph_layer": "main",
                    "linked_claim_ids": ["claim-1"],
                },
                {
                    "component_id": "target-node",
                    "label": "Consistency relation",
                    "graph_layer": "main",
                },
            ],
            "edges": [
                {
                    "source_component_id": "root-node-1",
                    "target_component_id": "target-node",
                    "source_backing_status": "source_backed",
                },
            ],
            "dsl": {},
        }
        session = self._session(
            graph_rows=[("doc-1", graph_json)],
            label_rows=[("claim-1", "診断的主張の一例")],
        )
        result = support_paths.compute_support_lines(
            session, "component", "target-node", document_id="doc-1",
        )
        assert result["level"] == "single"
        assert result["cut_members"] == [
            {"node_id": "root-node-1", "label": "Diagnostic / application"}
        ]
        assert result["observation_roots"] == [
            {"claim_id": "claim-1", "label": "診断的主張の一例", "identified_via": "theory_stage"}
        ]
        assert "単一の支持線に立っています" in result["fact_line"]
        assert "Diagnostic / application" in result["fact_line"]

    def test_two_independent_roots_give_several_level(self):
        graph_json = {
            "nodes": [
                {
                    "component_id": "root-node-1",
                    "label": "Diagnostic / application",
                    "graph_layer": "main",
                    "linked_claim_ids": ["claim-1"],
                },
                {
                    "component_id": "root-node-2",
                    "label": "Observation model",
                    "graph_layer": "main",
                    "linked_claim_ids": ["claim-2"],
                },
                {"component_id": "mid-1", "graph_layer": "main"},
                {"component_id": "mid-2", "graph_layer": "main"},
                {"component_id": "target-node", "label": "Consistency relation", "graph_layer": "main"},
            ],
            "edges": [
                {"source_component_id": "root-node-1", "target_component_id": "mid-1",
                 "source_backing_status": "source_backed"},
                {"source_component_id": "mid-1", "target_component_id": "target-node",
                 "source_backing_status": "source_backed"},
                {"source_component_id": "root-node-2", "target_component_id": "mid-2",
                 "source_backing_status": "partially_source_backed"},
                {"source_component_id": "mid-2", "target_component_id": "target-node",
                 "source_backing_status": "source_backed"},
            ],
            "dsl": {},
        }
        session = self._session(
            graph_rows=[("doc-1", graph_json)],
            label_rows=[("claim-1", "A"), ("claim-2", "B")],
        )
        result = support_paths.compute_support_lines(
            session, "component", "target-node", document_id="doc-1",
        )
        assert result["level"] == "several"
        assert result["fact_line"] == support_paths.FACT_LINE_SEVERAL
        assert result["cut_members"] == []
        assert {r["claim_id"] for r in result["observation_roots"]} == {"claim-1", "claim-2"}

    def test_unqualified_edge_does_not_count_as_support(self):
        """review_required / inferred の edge は資格を持たない（§2-7 のすり抜けを塞ぐ）。"""
        graph_json = {
            "nodes": [
                {
                    "component_id": "root-node-1",
                    "label": "Diagnostic / application",
                    "graph_layer": "main",
                    "linked_claim_ids": ["claim-1"],
                },
                {"component_id": "target-node", "label": "Consistency relation", "graph_layer": "main"},
            ],
            "edges": [
                {
                    "source_component_id": "root-node-1",
                    "target_component_id": "target-node",
                    "source_backing_status": "inferred",
                },
            ],
            "dsl": {},
        }
        session = self._session(
            graph_rows=[("doc-1", graph_json)],
            label_rows=[("claim-1", "A")],
        )
        result = support_paths.compute_support_lines(
            session, "component", "target-node", document_id="doc-1",
        )
        assert result["level"] == "none"


# ===========================================================================
# core/doubt/falsification_conditions/validator.py — SL-1 候補検証
# ===========================================================================


class TestFalsificationValidator:
    def _context(self, text: str) -> FalsificationTargetContext:
        context = FalsificationTargetContext(target_id="c1", target_type="claim")
        context.source_blocks = [SourceBlock("S1", "claim 本文", text)]
        return context

    def test_valid_candidate_is_accepted(self):
        context = self._context("測定値が理論予測から5%以上ずれれば覆る")
        data = {
            "candidates": [
                {
                    "statement": "測定値が理論予測から5%以上ずれれば覆る",
                    "kind": "observation_value",
                    "evidence_quote": "測定値が理論予測から5%以上ずれれば覆る",
                    "reason": "出典に明記",
                    "confidence": 0.7,
                }
            ]
        }
        result, errors, _warnings = validate_output(data, context)
        assert errors == []
        assert result is not None
        assert len(result.candidates) == 1
        assert result.candidates[0].status == "candidate"
        assert result.candidates[0].kind == "observation_value"

    def test_empty_candidates_list_is_valid(self):
        context = self._context("何かの本文")
        result, errors, _warnings = validate_output({"candidates": []}, context)
        assert errors == []
        assert result is not None
        assert result.candidates == []

    def test_non_verbatim_quote_is_hard_error(self):
        context = self._context("出典の原文")
        data = {
            "candidates": [
                {
                    "statement": "何かが覆る",
                    "kind": "auxiliary_hypothesis",
                    "evidence_quote": "出典に無い文言",
                    "reason": "r",
                    "confidence": 0.5,
                }
            ]
        }
        result, errors, _warnings = validate_output(data, context)
        assert result is None
        assert any("verbatim" in e for e in errors)

    def test_missing_statement_is_hard_error(self):
        context = self._context("出典の原文")
        data = {
            "candidates": [
                {
                    "statement": "",
                    "kind": "observation_value",
                    "evidence_quote": "出典の原文",
                    "reason": "r",
                    "confidence": 0.5,
                }
            ]
        }
        result, errors, _warnings = validate_output(data, context)
        assert result is None
        assert any("statement" in e for e in errors)

    def test_invalid_kind_is_hard_error(self):
        context = self._context("出典の原文")
        data = {
            "candidates": [
                {
                    "statement": "何かが覆る",
                    "kind": "bogus_kind",
                    "evidence_quote": "出典の原文",
                    "reason": "r",
                    "confidence": 0.5,
                }
            ]
        }
        result, errors, _warnings = validate_output(data, context)
        assert result is None
        assert any("kind" in e for e in errors)

    def test_too_many_candidates_is_hard_error(self):
        context = self._context("出典の原文")
        candidate = {
            "statement": "何かが覆る",
            "kind": "observation_value",
            "evidence_quote": "出典の原文",
            "reason": "r",
            "confidence": 0.5,
        }
        data = {"candidates": [candidate, candidate, candidate, candidate]}
        result, errors, _warnings = validate_output(data, context)
        assert result is None
        assert any("too many candidates" in e for e in errors)

    def test_not_a_dict_is_hard_error(self):
        result, errors, _warnings = validate_output([], self._context("x"))
        assert result is None
        assert errors


# ===========================================================================
# core/doubt/falsification_conditions/agent.py
# ===========================================================================


class _StubLLMClient:
    def __init__(self, response: dict):
        self._response = response
        self.calls = 0

    def complete_json(self, _content: str) -> dict:
        self.calls += 1
        return self._response


class TestFalsificationConditionAgent:
    def test_agent_skips_llm_when_no_sources(self):
        stub = _StubLLMClient({"candidates": []})
        agent = FalsificationConditionAgent(llm_client=stub)
        context = FalsificationTargetContext(target_id="c1", target_type="claim")
        result = agent.run(context)
        assert stub.calls == 0
        assert result.candidates == []
        assert "no source texts; skipped" in result.warnings

    def test_agent_returns_validated_candidates(self):
        stub = _StubLLMClient({
            "candidates": [
                {
                    "statement": "測定値がずれれば覆る",
                    "kind": "observation_value",
                    "evidence_quote": "出典の原文",
                    "reason": "r",
                    "confidence": 0.6,
                }
            ]
        })
        agent = FalsificationConditionAgent(llm_client=stub)
        context = FalsificationTargetContext(target_id="c1", target_type="claim")
        context.source_blocks = [SourceBlock("S1", "本文", "出典の原文")]
        result = agent.run(context)
        assert stub.calls == 1
        assert len(result.candidates) == 1
        assert result.repair_failed is False

    def test_agent_marks_repair_failed_after_persistent_validation_errors(self):
        stub = _StubLLMClient({"candidates": [{"statement": ""}]})  # 常に検証失敗
        agent = FalsificationConditionAgent(llm_client=stub)
        context = FalsificationTargetContext(target_id="c1", target_type="claim")
        context.source_blocks = [SourceBlock("S1", "本文", "出典の原文")]
        result = agent.run(context)
        assert result.repair_failed is True
        assert result.candidates == []
        # 1回目 + repair 2回 = 3回呼ばれる
        assert stub.calls == 3


# ===========================================================================
# core/doubt/falsification_conditions/worker.py
# ===========================================================================


def _is_claim_query(sql: str) -> bool:
    return "SET falsification_analyzed_at = now()" in sql


def _is_reset_query(sql: str) -> bool:
    return "SET falsification_analyzed_at = NULL" in sql


def _is_append_query(sql: str) -> bool:
    return "falsification_candidates = falsification_candidates ||" in sql


class TestFalsificationWorker:
    def test_claim_pending_targets_marks_analyzed_and_filters_by_document(self):
        session = _DispatchSession([
            (_is_claim_query, [("claim", "c1"), ("equation", "e1")]),
        ])
        rows = fc_worker._claim_pending_targets(session, "doc-1", "")
        assert rows == [("claim", "c1"), ("equation", "e1")]
        assert session.committed >= 1
        sql, params = session.calls[-1]
        assert "document_id = :doc" in sql
        assert params["doc"] == "doc-1"
        assert "FOR UPDATE SKIP LOCKED" in sql

    def test_append_candidates_writes_expected_column(self):
        session = _DispatchSession([])
        fc_worker._append_candidates(session, "claim", "c1", [{"statement": "x"}])
        sql, params = session.calls[-1]
        assert _is_append_query(sql)
        assert params["tid"] == "c1"
        assert params["ttype"] == "claim"

    def test_append_candidates_is_noop_for_empty_list(self):
        session = _DispatchSession([])
        fc_worker._append_candidates(session, "claim", "c1", [])
        assert session.calls == []

    def test_run_mining_processes_target_and_appends_candidate(self, monkeypatch):
        session = _DispatchSession([(_is_claim_query, [("claim", "c1")])])
        monkeypatch.setattr(fc_worker, "get_session", lambda: session)
        monkeypatch.setattr(
            fc_worker,
            "build_target_context",
            lambda _session, ttype, tid: FalsificationTargetContext(
                target_id=tid, target_type=ttype,
                source_blocks=[SourceBlock("S1", "claim", "text")],
            ),
        )
        canned = FalsificationCandidateResult(
            target_id="c1", target_type="claim",
            candidates=[
                FalsificationCandidate(
                    statement="x", kind="observation_value",
                    evidence_quote="text", reason="r", confidence=0.5,
                )
            ],
        )
        monkeypatch.setattr(
            FalsificationConditionAgent, "run", lambda self, ctx: canned,
        )
        result = fc_worker.run_falsification_condition_mining(document_id="doc-1")
        assert result == {"processed": 1, "candidates": 1}
        append_calls = [c for c in session.calls if _is_append_query(c[0])]
        assert len(append_calls) == 1
        assert append_calls[0][1]["tid"] == "c1"

    def test_run_mining_skips_target_without_sources(self, monkeypatch):
        session = _DispatchSession([(_is_claim_query, [("claim", "c1")])])
        monkeypatch.setattr(fc_worker, "get_session", lambda: session)
        monkeypatch.setattr(
            fc_worker,
            "build_target_context",
            lambda _session, ttype, tid: FalsificationTargetContext(target_id=tid, target_type=ttype),
        )
        result = fc_worker.run_falsification_condition_mining(document_id="doc-1")
        assert result == {"processed": 1, "candidates": 0}
        assert not any(_is_append_query(c[0]) for c in session.calls)

    def test_daily_cap_reached_resets_analyzed_at_to_null(self, monkeypatch):
        session = _DispatchSession([(_is_claim_query, [("claim", "c1")])])
        monkeypatch.setattr(fc_worker, "get_session", lambda: session)
        monkeypatch.setattr(
            fc_worker,
            "build_target_context",
            lambda _session, ttype, tid: FalsificationTargetContext(
                target_id=tid, target_type=ttype,
                source_blocks=[SourceBlock("S1", "claim", "text")],
            ),
        )
        monkeypatch.setattr(fc_worker, "_check_and_count_llm_call", lambda: False)
        result = fc_worker.run_falsification_condition_mining(document_id="doc-1")
        assert result == {"processed": 0, "candidates": 0}
        reset_calls = [c for c in session.calls if _is_reset_query(c[0])]
        assert len(reset_calls) == 1
        assert reset_calls[0][1] == {"tid": "c1", "ttype": "claim"}

    def test_claim_failure_returns_zero_processed(self, monkeypatch):
        class _BoomSession(_DispatchSession):
            def execute(self, *_a, **_kw):
                raise RuntimeError("boom")

        session = _BoomSession([])
        monkeypatch.setattr(fc_worker, "get_session", lambda: session)
        result = fc_worker.run_falsification_condition_mining(document_id="doc-1")
        assert result == {"processed": 0}
        assert session.rolled_back >= 1
        assert session.closed is True

    def test_maybe_schedule_starts_daemon_thread(self, monkeypatch):
        started = {}

        class _FakeThread:
            def __init__(self, target=None, kwargs=None, name=None, daemon=None):
                started["target"] = target
                started["kwargs"] = kwargs
                started["name"] = name
                started["daemon"] = daemon

            def start(self):
                started["started"] = True

        monkeypatch.setattr(fc_worker.threading, "Thread", _FakeThread)
        assert fc_worker.maybe_schedule_falsification_candidates(document_id="doc-1") is True
        assert started["started"] is True
        assert started["daemon"] is True
        assert started["name"] == "doubt-falsification-conditions"
        assert started["kwargs"] == {"document_id": "doc-1", "course_id": ""}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
