"""D2-1 / D3-3 — 依存グラフ・到達可能性・反実仮想伝播のテスト。"""

from __future__ import annotations

from core.doubt.counterfactual import compute_propagation
from core.doubt.dependency import DependencyGraph, seed_nodes_for_target


def _graph(edges: list[tuple[str, str]], nodes: set[str] | None = None) -> DependencyGraph:
    graph = DependencyGraph()
    for source, target in edges:
        graph.node_ids.add(source)
        graph.node_ids.add(target)
        graph.successors.setdefault(source, set()).add(target)
        graph.predecessors.setdefault(target, set()).add(source)
    for node in nodes or set():
        graph.node_ids.add(node)
    return graph


class TestDownstreamReachability:
    def test_linear_chain(self):
        graph = _graph([("a", "b"), ("b", "c")])
        assert graph.downstream("a") == {"b", "c"}
        assert graph.downstream("c") == set()

    def test_cycle_does_not_hang(self):
        """閉路があってもハングしない（D2-1 受け入れ条件）。"""
        graph = _graph([("a", "b"), ("b", "c"), ("c", "a")])
        assert graph.downstream("a") == {"b", "c"}
        assert graph.downstream("b") == {"c", "a"}

    def test_downstream_of_set_union(self):
        graph = _graph([("a", "x"), ("b", "y"), ("x", "z")])
        assert graph.downstream_of_set({"a", "b"}) == {"x", "y", "z"}

    def test_unknown_node_is_empty(self):
        graph = _graph([("a", "b")])
        assert graph.downstream("nope") == set()


class TestSeedResolution:
    def test_component_seed_is_itself(self):
        graph = _graph([("a", "b")])
        assert seed_nodes_for_target(graph, "component", "a") == {"a"}
        assert seed_nodes_for_target(graph, "component", "zzz") == set()

    def test_claim_and_equation_seeds(self):
        graph = _graph([("a", "b")])
        graph.claim_refs["claim-1"] = {"a"}
        graph.equation_refs["eq_2_7"] = {"b"}
        assert seed_nodes_for_target(graph, "claim", "claim-1") == {"a"}
        assert seed_nodes_for_target(graph, "equation", "eq_2_7") == {"b"}


class TestCounterfactualPropagation:
    def test_simple_collapse(self):
        graph = _graph([("a", "b"), ("b", "c")], nodes={"d"})
        result = compute_propagation(graph, {"a"})
        assert set(result.collapsed_node_ids) == {"a", "b", "c"}
        assert set(result.surviving_node_ids) == {"d"}
        assert result.indeterminate_node_ids == []

    def test_indeterminate_not_collapsed(self):
        """両者に依存するノードは崩壊側に倒さず indeterminate（D3-3）。"""
        # a → c ← b（b は生存側）。c は a にも b にも依存する。
        graph = _graph([("a", "c"), ("b", "c")])
        result = compute_propagation(graph, {"a"})
        assert set(result.collapsed_node_ids) == {"a"}
        assert set(result.indeterminate_node_ids) == {"c"}
        assert set(result.surviving_node_ids) == {"b"}

    def test_deterministic_same_input_same_output(self):
        graph = _graph([("a", "b"), ("b", "c"), ("a", "c"), ("x", "c")])
        first = compute_propagation(graph, {"a"})
        second = compute_propagation(graph, {"a"})
        assert first.model_dump() == second.model_dump()

    def test_cycle_propagation_terminates(self):
        graph = _graph([("a", "b"), ("b", "a"), ("b", "c")])
        result = compute_propagation(graph, {"a"})
        assert "c" in result.collapsed_node_ids + result.indeterminate_node_ids

    def test_no_reconstruction_fields(self):
        """「再構築」は計算しない — 出力は 3 区分のみ（docstring の限界明記に対応）。"""
        graph = _graph([("a", "b")])
        payload = compute_propagation(graph, {"a"}).model_dump()
        assert set(payload.keys()) == {
            "toggled_assumption_ids", "collapsed_node_ids",
            "surviving_node_ids", "indeterminate_node_ids", "graph_snapshot_key",
        }
