"""わたしの地図「いまここの周り」（近傍関係ビュー）の純粋ユニットテスト + ガードレール。

仕様の正本は ``docs/features/personal_map_nearby_design.md``（PMN-1〜PMN-7）。
既存の ``test_personal_graph_journey*.py`` と同じ流儀（fake データ契約 + 純粋関数）で、
DB にも FastAPI にも触らずに導出規則を固定する。

固定する不変条項（設計書 §7 の 1〜8 に対応）:

1. ``core/personal_graph/nearby.py`` が FastAPI / LLM を import しない
2. ``inferred`` / ``review_required`` / 未分類の辺を採用しない（PMN-2）
3. DTO に数値キーが再帰的に現れない（PMN-4）
4. 禁止語彙（分野全体への言及・助言・評価）が実装ソースと DTO に現れない（PMN-3 / PMN-5）
5. 他人・未知の ``node_id`` は解決しない（PN-1。route が 404 にする）
6. ``me_router`` に書き込みメソッドが増えていない（既存ガードレールの継続確認）
7. 台帳ゼロで ``ledger_available=False`` でも図が成立する（PMN-7）
8. 訳語は ``element_vocab`` / ``label_vocab`` / ``personal_graph.schema`` からのみ引く
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import core.personal_graph.queries as queries_mod  # noqa: E402
from core.personal_graph import nearby as N  # noqa: E402
from core.personal_graph.queries import _topic_claim_binding_from_data  # noqa: E402
from core.personal_graph.schema import (  # noqa: E402
    NODE_KIND_QUESTION,
    NODE_KIND_RECONSTRUCTION,
    NODE_KIND_TENSION,
    PersonalAnchor,
    PersonalNetwork,
    PersonalNode,
)
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
)

_NEARBY_SRC = (BACKEND / "core" / "personal_graph" / "nearby.py").read_text(encoding="utf-8")
_ROUTE_SRC = (BACKEND / "api" / "routes" / "personal_map.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# fake ビルダ（graph_json / PersonalNode の実フィールド名に合わせる）
# ---------------------------------------------------------------------------


def gnode(component_id, label, *, order=0, layer="main", **links):
    node = {
        "component_id": component_id,
        "label": label,
        "display_order": order,
        "graph_layer": layer,
    }
    node.update(links)
    return node


def gedge(src, tgt, backing="source_backed"):
    return {
        "source_component_id": src,
        "target_component_id": tgt,
        "source_backing_status": backing,
    }


def pnode(node_id, kind, text, anchor_type, anchor_id, *, course_id="c1", created_at="2026-08-01"):
    return PersonalNode(
        id=node_id,
        node_kind=kind,
        label=text,
        anchor=PersonalAnchor(anchor_type=anchor_type, anchor_id=anchor_id),
        topic_id=None,
        course_id=course_id,
        created_at=created_at,
        facts=[],
        source={},
    )


def sample_graph():
    """土台 → 方程式系 → 観測量の構成 → {整合関係, 診断・応用} のバックボーン。"""
    return {
        "nodes": [
            gnode("n_basis", "Theory basis", order=1, linked_claim_ids=["cl_basis"]),
            gnode(
                "n_eq",
                "Equation system",
                order=2,
                linked_equation_ids=["eq_1"],
                linked_derivation_ids=["d_1"],
            ),
            gnode("n_obs", "Observable construction", order=3, linked_claim_ids=["cl_obs"]),
            gnode("n_cons", "Consistency relation", order=4),
            gnode("n_diag", "Diagnostic / application", order=5),
            # main 以外の層は使わない
            gnode("n_detail", "Define eq_1", order=6, layer="equation_detail"),
            gnode("n_dbg", "fallback", order=7, layer="debug"),
        ],
        "edges": [
            gedge("n_basis", "n_eq"),
            gedge("n_eq", "n_obs", backing="partially_source_backed"),
            gedge("n_obs", "n_cons"),
            gedge("n_obs", "n_diag"),
            # 採用してはならない辺
            gedge("n_dbg", "n_obs", backing="inferred"),
            gedge("n_cons", "n_diag", backing="review_required"),
            gedge("n_basis", "n_diag", backing=""),
        ],
    }


# ---------------------------------------------------------------------------
# §3.1 グラフ読み
# ---------------------------------------------------------------------------


class TestGraphReading:
    def test_main_nodes_only_and_deterministic_order(self):
        ids = [n["component_id"] for n in N.main_nodes(sample_graph())]
        assert ids == ["n_basis", "n_eq", "n_obs", "n_cons", "n_diag"]

    def test_stage_and_japanese_label_from_english_main_label(self):
        node = gnode("x", "Theory basis")
        assert N.node_stage(node) == "theory_basis"
        assert N.node_display_label(node) == "理論の土台"

    def test_unknown_label_is_kept_verbatim(self):
        """stage キーが引けない古いラベルも落とさない（P4）。英訳を捏造しない。"""
        node = gnode("x", "Some legacy label")
        assert N.node_stage(node) == ""
        assert N.node_display_label(node) == "Some legacy label"

    def test_qualified_edges_reject_inferred_and_review_required(self):
        """PMN-2: 推測の辺・出典未確認の辺は採用しない。"""
        edges = N.qualified_edges(sample_graph())
        assert edges == [
            ("n_basis", "n_eq"),
            ("n_eq", "n_obs"),
            ("n_obs", "n_cons"),
            ("n_obs", "n_diag"),
        ]
        for src, tgt in edges:
            assert (src, tgt) != ("n_cons", "n_diag")
            assert src != "n_dbg"

    def test_qualified_edges_drop_self_loops_and_dedupe(self):
        graph = {"edges": [gedge("a", "a"), gedge("a", "b"), gedge("a", "b")]}
        assert N.qualified_edges(graph) == [("a", "b")]


class TestAnchorMatching:
    @pytest.mark.parametrize(
        "anchor_type,anchor_id,expected",
        [
            ("component", "n_eq", "n_eq"),
            ("claim", "cl_obs", "n_obs"),
            ("equation", "eq_1", "n_eq"),
            ("derivation_step", "d_1", "n_eq"),
            ("stage", "theory_basis", "n_basis"),
        ],
    )
    def test_five_resolvable_anchor_types(self, anchor_type, anchor_id, expected):
        node = N.find_center_node(sample_graph(), anchor_type, anchor_id)
        assert node is not None and node["component_id"] == expected

    def test_member_component_ids_resolve_to_parent_main_node(self):
        graph = {"nodes": [gnode("m", "Elimination", member_component_ids=["child"])]}
        node = N.find_center_node(graph, "component", "child")
        assert node is not None and node["component_id"] == "m"

    @pytest.mark.parametrize("anchor_type", ["concept", "chunk", "segment", "topic", "graph_edge"])
    def test_out_of_scope_anchor_types_are_not_resolvable(self, anchor_type):
        assert anchor_type not in N.RESOLVABLE_ANCHOR_TYPES
        assert N.find_center_node(sample_graph(), anchor_type, "whatever") is None

    def test_empty_anchor_id_never_matches(self):
        assert N.find_center_node(sample_graph(), "claim", "") is None


# ---------------------------------------------------------------------------
# §3.2 / §4 DTO
# ---------------------------------------------------------------------------


def build(mode="near", *, ledger=None, personal=None, center="n_obs", support=""):
    graph = sample_graph()
    center_node = next(
        n for n in N.main_nodes(graph) if n["component_id"] == center
    )
    return N.build_nearby(
        graph,
        center_node=center_node,
        personal_nodes=personal if personal is not None else [],
        ledger=ledger if ledger is not None else {},
        support_fact_line=support,
        mode=mode,
    )


class TestNearMode:
    def test_upstream_and_downstream_follow_edge_direction(self):
        dto = build()
        assert dto["available"] is True
        assert dto["center"]["component_id"] == "n_obs"
        assert [n["component_id"] for n in dto["upstream"]] == ["n_eq"]
        assert [n["component_id"] for n in dto["downstream"]] == ["n_cons", "n_diag"]
        assert dto["root_path"] == []

    def test_center_label_is_japanese_stage_name(self):
        assert build()["center"]["label"] == "観測量の構成"

    def test_edges_are_limited_to_shown_nodes(self):
        dto = build()
        shown = {dto["center"]["component_id"]} | {
            n["component_id"] for n in dto["upstream"] + dto["downstream"]
        }
        for edge in dto["edges"]:
            assert edge["from"] in shown and edge["to"] in shown
        assert {"from": "n_basis", "to": "n_eq"} not in dto["edges"]

    def test_fanout_is_capped(self):
        graph = {
            "nodes": [gnode("c", "Equation system")]
            + [gnode(f"d{i}", "Elimination", order=i) for i in range(8)],
            "edges": [gedge("c", f"d{i}") for i in range(8)],
        }
        dto = N.build_nearby(
            graph,
            center_node=graph["nodes"][0],
            personal_nodes=[],
            ledger={},
        )
        assert len(dto["downstream"]) == N.MAX_FANOUT


class TestRootMode:
    def test_root_path_reaches_the_basis_and_excludes_center(self):
        dto = build("root")
        assert [n["component_id"] for n in dto["root_path"]] == ["n_basis", "n_eq"]
        assert dto["upstream"] == []
        assert [n["component_id"] for n in dto["downstream"]] == ["n_cons", "n_diag"]

    def test_cycle_does_not_loop_forever(self):
        edges = [("a", "b"), ("b", "a")]
        assert N.root_path_ids(edges, "a") == ["b"]

    def test_depth_is_capped(self):
        edges = [(f"n{i + 1}", f"n{i}") for i in range(20)]
        assert len(N.root_path_ids(edges, "n0")) == N.MAX_ROOT_DEPTH


class TestVerificationAndLedger:
    def test_ledger_label_comes_from_label_vocab(self):
        from core.label_vocab import VERIFICATION_STATUS_LABELS_LEDGER

        dto = build(ledger={"n_obs": "untested"})
        assert dto["ledger_available"] is True
        assert dto["center"]["verification"] == {
            "status": "untested",
            "label": VERIFICATION_STATUS_LABELS_LEDGER["untested"],
        }

    def test_missing_ledger_row_is_null_not_a_guess(self):
        dto = build(ledger={"n_eq": "indirectly_supported"})
        assert dto["center"]["verification"] is None
        assert dto["upstream"][0]["verification"]["status"] == "indirectly_supported"

    def test_no_ledger_at_all_degrades_but_graph_survives(self):
        """PMN-7: 台帳ゼロでも依存の向きは見せる。"""
        dto = build(ledger={})
        assert dto["ledger_available"] is False
        assert dto["available"] is True
        assert dto["upstream"] and dto["downstream"]

    def test_unknown_status_is_not_labelled(self):
        assert build(ledger={"n_obs": "bogus"})["center"]["verification"] is None


class TestMine:
    def test_own_traces_attach_to_the_matching_node_newest_first(self):
        personal = [
            pnode("t1", NODE_KIND_TENSION, "引っかかり1", "claim", "cl_obs", created_at="2026-08-05"),
            pnode("t2", NODE_KIND_QUESTION, "問い1", "claim", "cl_obs", created_at="2026-08-09"),
            pnode("t3", NODE_KIND_RECONSTRUCTION, "言い直し", "equation", "eq_1"),
        ]
        dto = build(personal=personal)
        assert [m["trace_id"] for m in dto["center"]["mine"]] == ["t2", "t1"]
        assert dto["center"]["mine"][0]["kind_label"] == "問い"
        assert [m["trace_id"] for m in dto["upstream"][0]["mine"]] == ["t3"]

    def test_untouched_nodes_have_empty_mine(self):
        dto = build(personal=[])
        assert all(n["mine"] == [] for n in dto["downstream"])

    def test_kind_labels_come_from_schema_table(self):
        from core.personal_graph.schema import NODE_KIND_LABELS

        personal = [pnode("t1", NODE_KIND_TENSION, "x", "claim", "cl_obs")]
        assert build(personal=personal)["center"]["mine"][0]["kind_label"] == (
            NODE_KIND_LABELS[NODE_KIND_TENSION]
        )


class TestFacts:
    def test_support_fact_line_is_passed_through_verbatim(self):
        line = "この対象への、観測記録からの支持線はこのコーパスの中では見つかりません。"
        assert build(support=line)["facts"][0] == line

    def test_downstream_and_upstream_are_enumerated(self):
        facts = build()["facts"]
        assert any("これに依存していること：" in f and "整合関係" in f for f in facts)
        assert any("これが前提にしていること：" in f and "式の体系" in f for f in facts)

    def test_zero_downstream_is_stated_as_a_fact_not_advice(self):
        facts = build(center="n_diag")["facts"]
        assert N.FACT_NO_DOWNSTREAM in facts

    def test_zero_upstream_is_stated_as_a_fact(self):
        facts = build(center="n_basis")["facts"]
        assert N.FACT_NO_UPSTREAM in facts

    def test_no_qualified_edges_is_stated(self):
        graph = {"nodes": [gnode("solo", "Theory basis")], "edges": [gedge("a", "b", "inferred")]}
        dto = N.build_nearby(
            graph, center_node=graph["nodes"][0], personal_nodes=[], ledger={}
        )
        assert N.FACT_NO_QUALIFIED_EDGES in dto["facts"]

    def test_root_mode_enumerates_the_chain_as_premises(self):
        facts = build("root")["facts"]
        assert any("これが前提にしていること：" in f and "理論の土台" in f for f in facts)


# ---------------------------------------------------------------------------
# 広がりの装置2（共通部品の糸）: 中心ノードのみ・点ビューのみ
# ---------------------------------------------------------------------------


def _center_node(component_id="n_obs"):
    return next(n for n in N.main_nodes(sample_graph()) if n["component_id"] == component_id)


class TestSharedPartThreadFacts:
    """``N._shared_part_thread_facts``（装置2）。journey.py の [2][3] 区間の鏡写し。"""

    def test_confirmed_link_to_active_entry_produces_a_thread_fact(self, monkeypatch):
        monkeypatch.setattr(
            queries_mod,
            "fetch_confirmed_identity_links",
            lambda doc_id: [
                {
                    "instance_element_type": "theory_claim",
                    "instance_element_id": "cl_obs",
                    "shared_part_id": "sp1",
                }
            ],
        )
        monkeypatch.setattr(
            queries_mod, "fetch_library_entry_names", lambda ids: {"sp1": "観測モデル"}
        )
        monkeypatch.setattr(
            queries_mod,
            "fetch_confirmed_links_for_shared_part",
            lambda sp_id: [{"instance_document_id": "docB"}],
        )
        monkeypatch.setattr(queries_mod, "fetch_document_titles", lambda ids: {"docB": "論文B"})

        lines = N._shared_part_thread_facts(
            _center_node(), document_id="docA",
            can_view_document=lambda uid, doc: True, user_id="u1",
        )
        assert lines == ["共通部品『観測モデル』は、論文『論文B』にも現れます。"]

    def test_equation_anchor_type_matches_linked_equation_ids(self, monkeypatch):
        monkeypatch.setattr(
            queries_mod,
            "fetch_confirmed_identity_links",
            lambda doc_id: [
                {"instance_element_type": "equation", "instance_element_id": "eq_1", "shared_part_id": "sp1"}
            ],
        )
        monkeypatch.setattr(queries_mod, "fetch_library_entry_names", lambda ids: {"sp1": "式モデル"})
        monkeypatch.setattr(
            queries_mod, "fetch_confirmed_links_for_shared_part",
            lambda sp_id: [{"instance_document_id": "docB"}],
        )
        monkeypatch.setattr(queries_mod, "fetch_document_titles", lambda ids: {"docB": "論文B"})

        lines = N._shared_part_thread_facts(
            _center_node("n_eq"), document_id="docA",
            can_view_document=lambda uid, doc: True, user_id="u1",
        )
        assert lines == ["共通部品『式モデル』は、論文『論文B』にも現れます。"]

    def test_missing_can_view_document_callback_yields_no_threads(self, monkeypatch):
        monkeypatch.setattr(
            queries_mod,
            "fetch_confirmed_identity_links",
            lambda doc_id: [
                {"instance_element_type": "theory_claim", "instance_element_id": "cl_obs", "shared_part_id": "sp1"}
            ],
        )
        lines = N._shared_part_thread_facts(
            _center_node(), document_id="docA", can_view_document=None, user_id="u1",
        )
        assert lines == []

    def test_retired_or_unnamed_entry_drops_the_thread_without_generic_fallback(self, monkeypatch):
        monkeypatch.setattr(
            queries_mod,
            "fetch_confirmed_identity_links",
            lambda doc_id: [
                {"instance_element_type": "theory_claim", "instance_element_id": "cl_obs", "shared_part_id": "sp1"}
            ],
        )
        monkeypatch.setattr(queries_mod, "fetch_library_entry_names", lambda ids: {})  # active 無し
        lines = N._shared_part_thread_facts(
            _center_node(), document_id="docA",
            can_view_document=lambda uid, doc: True, user_id="u1",
        )
        assert lines == []

    def test_unviewable_other_document_drops_the_thread(self, monkeypatch):
        monkeypatch.setattr(
            queries_mod,
            "fetch_confirmed_identity_links",
            lambda doc_id: [
                {"instance_element_type": "theory_claim", "instance_element_id": "cl_obs", "shared_part_id": "sp1"}
            ],
        )
        monkeypatch.setattr(
            queries_mod, "fetch_library_entry_names", lambda ids: {"sp1": "観測モデル"}
        )
        monkeypatch.setattr(
            queries_mod, "fetch_confirmed_links_for_shared_part",
            lambda sp_id: [{"instance_document_id": "docB"}],
        )
        lines = N._shared_part_thread_facts(
            _center_node(), document_id="docA",
            can_view_document=lambda uid, doc: False, user_id="u1",
        )
        assert lines == []

    def test_current_document_is_excluded_from_other_instances(self, monkeypatch):
        monkeypatch.setattr(
            queries_mod,
            "fetch_confirmed_identity_links",
            lambda doc_id: [
                {"instance_element_type": "theory_claim", "instance_element_id": "cl_obs", "shared_part_id": "sp1"}
            ],
        )
        monkeypatch.setattr(
            queries_mod, "fetch_library_entry_names", lambda ids: {"sp1": "観測モデル"}
        )
        monkeypatch.setattr(
            queries_mod, "fetch_confirmed_links_for_shared_part",
            lambda sp_id: [{"instance_document_id": "docA"}],
        )
        lines = N._shared_part_thread_facts(
            _center_node(), document_id="docA",
            can_view_document=lambda uid, doc: True, user_id="u1",
        )
        assert lines == []

    def test_capped_at_three_and_sorted_deterministically(self, monkeypatch):
        links = [
            {
                "instance_element_type": "theory_claim",
                "instance_element_id": "cl_obs",
                "shared_part_id": f"sp{i}",
            }
            for i in range(5)
        ]
        monkeypatch.setattr(queries_mod, "fetch_confirmed_identity_links", lambda doc_id: links)
        monkeypatch.setattr(
            queries_mod,
            "fetch_library_entry_names",
            lambda ids: {f"sp{i}": f"部品{i}" for i in range(5)},
        )
        monkeypatch.setattr(
            queries_mod,
            "fetch_confirmed_links_for_shared_part",
            lambda sp_id: [{"instance_document_id": f"doc_{sp_id}"}],
        )
        monkeypatch.setattr(
            queries_mod,
            "fetch_document_titles",
            lambda ids: {f"doc_sp{i}": f"論文{i}" for i in range(5)},
        )
        lines = N._shared_part_thread_facts(
            _center_node(), document_id="docA",
            can_view_document=lambda uid, doc: True, user_id="u1",
        )
        assert len(lines) == N.MAX_SHARED_PART_THREADS
        assert lines == [
            "共通部品『部品0』は、論文『論文0』にも現れます。",
            "共通部品『部品1』は、論文『論文1』にも現れます。",
            "共通部品『部品2』は、論文『論文2』にも現れます。",
        ]

    def test_missing_title_falls_back_to_generic_label(self, monkeypatch):
        monkeypatch.setattr(
            queries_mod,
            "fetch_confirmed_identity_links",
            lambda doc_id: [
                {"instance_element_type": "theory_claim", "instance_element_id": "cl_obs", "shared_part_id": "sp1"}
            ],
        )
        monkeypatch.setattr(
            queries_mod, "fetch_library_entry_names", lambda ids: {"sp1": "観測モデル"}
        )
        monkeypatch.setattr(
            queries_mod, "fetch_confirmed_links_for_shared_part",
            lambda sp_id: [{"instance_document_id": "docB"}],
        )
        monkeypatch.setattr(queries_mod, "fetch_document_titles", lambda ids: {})
        lines = N._shared_part_thread_facts(
            _center_node(), document_id="docA",
            can_view_document=lambda uid, doc: True, user_id="u1",
        )
        assert lines == ["共通部品『観測モデル』は、論文『別の教材』にも現れます。"]

    def test_no_document_id_yields_no_threads(self):
        assert N._shared_part_thread_facts(
            _center_node(), document_id="", can_view_document=lambda u, d: True, user_id="u1",
        ) == []

    def test_exception_is_swallowed_fail_soft(self, monkeypatch):
        def _boom(doc_id):
            raise RuntimeError("db down")

        monkeypatch.setattr(queries_mod, "fetch_confirmed_identity_links", _boom)
        lines = N._shared_part_thread_facts(
            _center_node(), document_id="docA",
            can_view_document=lambda uid, doc: True, user_id="u1",
        )
        assert lines == []


class TestSharedPartFactsIntegration:
    def test_lines_are_appended_to_facts(self):
        dto = N.build_nearby(
            sample_graph(),
            center_node=_center_node(),
            personal_nodes=[],
            ledger={},
            shared_part_fact_lines=["共通部品『X』は、論文『Y』にも現れます。"],
        )
        assert "共通部品『X』は、論文『Y』にも現れます。" in dto["facts"]

    def test_range_mode_has_no_such_parameter_at_all(self):
        """装置2は範囲ビューには出さない — build_topic_range にパラメータ自体が無い。"""
        import inspect

        assert "shared_part_fact_lines" not in inspect.signature(N.build_topic_range).parameters


# ---------------------------------------------------------------------------
# 広がりの装置3（検証の晴れ間の近接提示）: 点ビューのみ
# ---------------------------------------------------------------------------


class TestVerificationFogFacts:
    def test_untested_node_outside_shown_set_is_listed(self):
        facts = build(center="n_eq", ledger={"n_cons": "untested"})["facts"]
        fact = next((f for f in facts if f.startswith(N.FACT_FOG_NEARBY_PREFIX)), None)
        assert fact == N.FACT_FOG_NEARBY_PREFIX + "『整合関係』" + "。"

    def test_unknown_status_also_counts(self):
        facts = build(center="n_eq", ledger={"n_cons": "unknown"})["facts"]
        assert any(f.startswith(N.FACT_FOG_NEARBY_PREFIX) for f in facts)

    def test_other_statuses_are_not_fog_candidates(self):
        facts = build(
            center="n_eq", ledger={"n_cons": "directly_verified", "n_diag": "refuted"}
        )["facts"]
        assert not any(f.startswith(N.FACT_FOG_NEARBY_PREFIX) for f in facts)

    def test_node_without_ledger_row_is_not_a_candidate(self):
        """台帳行が無いノードは「行が無い＝何も主張しない」の意味論を保つ（対象外）。"""
        dto = build(center="n_eq", ledger={})
        assert dto["ledger_available"] is False
        assert not any(f.startswith(N.FACT_FOG_NEARBY_PREFIX) for f in dto["facts"])

    def test_shown_node_is_excluded_even_if_untested(self):
        # n_eq は center=n_obs の upstream（表示集合の内側）。
        facts = build(center="n_obs", ledger={"n_eq": "untested"})["facts"]
        assert not any(f.startswith(N.FACT_FOG_NEARBY_PREFIX) for f in facts)

    def test_three_or_fewer_candidates_end_with_period_only(self):
        graph = {
            "nodes": [gnode("c", "Equation system")]
            + [gnode(f"x{i}", f"Custom stage {i}", order=i) for i in range(3)],
            "edges": [],
        }
        ledger = {f"x{i}": "untested" for i in range(3)}
        dto = N.build_nearby(graph, center_node=graph["nodes"][0], personal_nodes=[], ledger=ledger)
        fact = next(f for f in dto["facts"] if f.startswith(N.FACT_FOG_NEARBY_PREFIX))
        assert fact.endswith("。") and not fact.endswith("など。")
        assert fact.count("『") == 3

    def test_four_or_more_candidates_are_truncated_with_etc_suffix(self):
        graph = {
            "nodes": [gnode("c", "Equation system")]
            + [gnode(f"x{i}", f"Custom stage {i}", order=i) for i in range(4)],
            "edges": [],
        }
        ledger = {f"x{i}": "untested" for i in range(4)}
        dto = N.build_nearby(graph, center_node=graph["nodes"][0], personal_nodes=[], ledger=ledger)
        fact = next(f for f in dto["facts"] if f.startswith(N.FACT_FOG_NEARBY_PREFIX))
        assert fact.endswith("など。")
        assert fact.count("『") == 3  # 先頭3件のみ（4件目以降は黙って切る）

    def test_duplicate_labels_are_not_repeated(self):
        graph = {
            "nodes": [gnode("c", "Equation system")]
            + [gnode(f"x{i}", "Custom stage same", order=i) for i in range(3)],
            "edges": [],
        }
        ledger = {f"x{i}": "untested" for i in range(3)}
        dto = N.build_nearby(graph, center_node=graph["nodes"][0], personal_nodes=[], ledger=ledger)
        fact = next(f for f in dto["facts"] if f.startswith(N.FACT_FOG_NEARBY_PREFIX))
        assert fact.count("『") == 1

    def test_range_mode_has_no_such_parameter_and_never_emits_fog_facts(self):
        dto = N.build_topic_range(
            [{"title": "論文A", "graph": sample_graph(), "touched_claim_ids": {"cl_obs"}}],
            personal_nodes=[],
            ledger={"n_basis": "untested", "n_cons": "unknown"},
            topic_label="トピック1",
        )
        assert not any(f.startswith(N.FACT_FOG_NEARBY_PREFIX) for f in dto["facts"])


class TestUnavailable:
    def test_unavailable_is_a_fact_not_an_error(self):
        dto = N.unavailable()
        assert dto["available"] is False
        assert dto["notice"] == N.NOTICE_UNRESOLVED
        assert dto["center"] is None
        assert dto["facts"] == []
        assert dto["ledger_available"] is False

    def test_unavailable_carries_the_exit_facts_when_given(self):
        """欠落を行き止まりにしない: 出口案内の事実文を DTO に載せられる。"""
        dto = N.unavailable(N.MODE_NEAR, N.NOTICE_TOPIC_NO_MAPPING, [N.FACT_RANGE_SHARPEN])
        assert dto["available"] is False
        assert dto["notice"] == N.NOTICE_TOPIC_NO_MAPPING
        assert dto["facts"] == [N.FACT_RANGE_SHARPEN]
        # 既存の DTO キー集合・意味は不変。
        assert set(dto) == {
            "available", "mode", "ledger_available", "center", "upstream",
            "downstream", "root_path", "edges", "facts", "notice",
        }

    def test_unavailable_drops_empty_fact_lines(self):
        assert N.unavailable(N.MODE_NEAR, N.NOTICE_UNRESOLVED, ["", N.FACT_RANGE_SHARPEN])[
            "facts"
        ] == [N.FACT_RANGE_SHARPEN]


# ---------------------------------------------------------------------------
# 範囲モード（topic アンカーの事実ベース粗表示）
# ---------------------------------------------------------------------------


class TestTopicClaimBindingPure:
    """``queries._topic_claim_binding_from_data``（純関数・DB 非依存）。"""

    def test_binding_reads_flat_and_nested_topics_with_dedupe(self):
        data = {
            "topics": [
                {"id": "t1", "title": "トピック1", "linked_claim_ids": ["cl_a", "cl_b", "cl_a"]},
            ],
            "chapters": [
                {"topics": [{"id": "t2", "title": "トピック2", "linked_claim_ids": ["cl_c"]}]},
            ],
        }
        assert _topic_claim_binding_from_data(data, "t1") == {
            "claim_ids": ["cl_a", "cl_b"],
            "topic_label": "トピック1",
        }
        assert _topic_claim_binding_from_data(data, "t2") == {
            "claim_ids": ["cl_c"],
            "topic_label": "トピック2",
        }

    def test_binding_missing_topic_returns_empty(self):
        assert _topic_claim_binding_from_data({"topics": []}, "missing") == {
            "claim_ids": [],
            "topic_label": "",
        }

    def test_binding_missing_title_and_claims_falls_back_to_empty(self):
        data = {"topics": [{"id": "t1"}]}
        assert _topic_claim_binding_from_data(data, "t1") == {
            "claim_ids": [],
            "topic_label": "",
        }


class TestBuildTopicRangePure:
    """``nearby.build_topic_range``（純関数・DB 非依存）。"""

    def test_marks_touched_nodes_and_omits_center(self):
        dto = N.build_topic_range(
            [{"title": "論文A", "graph": sample_graph(), "touched_claim_ids": {"cl_obs"}}],
            personal_nodes=[],
            ledger={},
            topic_label="トピック1",
        )
        assert dto["available"] is True
        assert dto["mode"] == "range"
        assert dto["center"] is None
        assert dto["upstream"] == [] and dto["downstream"] == [] and dto["root_path"] == []
        assert dto["edges"] == []
        doc = dto["range_documents"][0]
        assert doc["title"] == "論文A"
        touched = {n["component_id"]: n["touched"] for n in doc["nodes"]}
        assert touched == {
            "n_basis": False,
            "n_eq": False,
            "n_obs": True,
            "n_cons": False,
            "n_diag": False,
        }
        assert all(n["is_center"] is False for n in doc["nodes"])

    def test_edges_are_limited_to_qualified_ones(self):
        dto = N.build_topic_range(
            [{"title": "論文A", "graph": sample_graph(), "touched_claim_ids": set()}],
            personal_nodes=[],
            ledger={},
            topic_label="",
        )
        edges = dto["range_documents"][0]["edges"]
        assert {"from": "n_basis", "to": "n_eq"} in edges
        assert {"from": "n_dbg", "to": "n_obs"} not in edges
        assert {"from": "n_cons", "to": "n_diag"} not in edges
        assert {"from": "n_basis", "to": "n_diag"} not in edges

    def test_facts_are_in_order_and_literal(self):
        dto = N.build_topic_range(
            [{"title": "論文A", "graph": sample_graph(), "touched_claim_ids": {"cl_obs"}}],
            personal_nodes=[],
            ledger={},
            topic_label="トピック1",
        )
        facts = dto["facts"]
        assert facts[0] == "この記録は、トピック『トピック1』での記録です。"
        assert facts[1] == "このトピックの教材が触れている理論構成：『観測量の構成』"
        assert facts[2] == N.FACT_RANGE_UNKNOWN_POINT
        assert facts[3] == N.FACT_RANGE_SHARPEN

    def test_empty_topic_label_omits_the_topic_line(self):
        dto = N.build_topic_range(
            [{"title": "論文A", "graph": sample_graph(), "touched_claim_ids": {"cl_obs"}}],
            personal_nodes=[],
            ledger={},
            topic_label="",
        )
        assert dto["facts"][0].startswith("このトピックの教材が触れている理論構成：")

    def test_zero_touched_nodes_omits_the_enumeration_line(self):
        """空の列挙で終わる欠けた文（「…理論構成：」）を出さない。"""
        dto = N.build_topic_range(
            [{"title": "論文A", "graph": sample_graph(), "touched_claim_ids": set()}],
            personal_nodes=[],
            ledger={},
            topic_label="トピック1",
        )
        assert not any("触れている理論構成" in f for f in dto["facts"])
        assert dto["facts"] == [
            "この記録は、トピック『トピック1』での記録です。",
            N.FACT_RANGE_UNKNOWN_POINT,
            N.FACT_RANGE_SHARPEN,
        ]
        # 図そのものは成立する（縮退の是正: 表示をやめない）。
        assert [n["component_id"] for n in dto["range_documents"][0]["nodes"]] == [
            "n_basis", "n_eq", "n_obs", "n_cons", "n_diag",
        ]
        assert all(n["touched"] is False for n in dto["range_documents"][0]["nodes"])

    def test_touched_labels_are_deduped_across_documents_in_order(self):
        graph_a = {"nodes": [gnode("a1", "Theory basis", order=1, linked_claim_ids=["cl_x"])], "edges": []}
        graph_b = {"nodes": [gnode("b1", "Theory basis", order=1, linked_claim_ids=["cl_y"])], "edges": []}
        dto = N.build_topic_range(
            [
                {"title": "論文A", "graph": graph_a, "touched_claim_ids": {"cl_x"}},
                {"title": "論文B", "graph": graph_b, "touched_claim_ids": {"cl_y"}},
            ],
            personal_nodes=[],
            ledger={},
            topic_label="",
        )
        assert dto["facts"][0] == "このトピックの教材が触れている理論構成：『理論の土台』"

    def test_no_numeric_keys_in_range_dto(self):
        personal = [pnode("t1", NODE_KIND_TENSION, "x", "claim", "cl_obs")]
        dto = N.build_topic_range(
            [{"title": "論文A", "graph": sample_graph(), "touched_claim_ids": {"cl_obs"}}],
            personal_nodes=personal,
            ledger={"n_obs": "untested"},
            topic_label="トピック1",
        )
        hits = [path for path, key, _ in _walk(dto) if key in _NUMERIC_KEYS]
        assert hits == [], f"数値・内部キーが range DTO に現れた: {hits}"

    def test_mine_still_attaches_to_point_anchors_inside_range_nodes(self):
        """topic 縮退そのものは載らないが、claim 等の点アンカーの本人痕跡は通常どおり載る。"""
        personal = [pnode("t1", NODE_KIND_TENSION, "引っかかり", "claim", "cl_obs")]
        dto = N.build_topic_range(
            [{"title": "論文A", "graph": sample_graph(), "touched_claim_ids": {"cl_obs"}}],
            personal_nodes=personal,
            ledger={},
            topic_label="",
        )
        by_id = {n["component_id"]: n for n in dto["range_documents"][0]["nodes"]}
        assert [m["trace_id"] for m in by_id["n_obs"]["mine"]] == ["t1"]
        assert by_id["n_basis"]["mine"] == []

    def test_topic_anchored_trace_never_attaches_to_any_node(self):
        """PMN-1: topic に縮退した痕跡自体が範囲内の1点に偽精度で載ってはならない。"""
        personal = [pnode("t1", NODE_KIND_QUESTION, "この記録の本文", "topic", "t1")]
        dto = N.build_topic_range(
            [{"title": "論文A", "graph": sample_graph(), "touched_claim_ids": {"cl_obs"}}],
            personal_nodes=personal,
            ledger={},
            topic_label="",
        )
        for node in dto["range_documents"][0]["nodes"]:
            assert node["mine"] == []


class TestBuildTopicRangeCourseFallback:
    """``build_topic_range`` の ``fallback_fact``（コース範囲フォールバック、純関数）。"""

    def test_range_fallback_flag_distinguishes_fallback_from_claim_path(self):
        """UI が見出し・凡例を切り替えるための真偽キー（数値ではない）。"""
        graph = sample_graph()
        docs = [{"title": "T", "graph": graph, "touched_claim_ids": set()}]
        fallback = N.build_topic_range(
            docs, personal_nodes=[], ledger={}, topic_label="",
            fallback_fact=N.FACT_RANGE_COURSE_FALLBACK,
        )
        assert fallback["range_fallback"] is True
        normal = N.build_topic_range(
            docs, personal_nodes=[], ledger={}, topic_label="",
        )
        assert normal["range_fallback"] is False

    def _dto(self, **kwargs):
        return N.build_topic_range(
            [{"title": "論文A", "graph": sample_graph(), "touched_claim_ids": set()}],
            personal_nodes=[],
            ledger={},
            topic_label="トピック1",
            **kwargs,
        )

    def test_fallback_fact_is_stated_and_replaces_the_unknown_point_line(self):
        """粗い対応を隠さず「粗い」とラベルする。無い対応を語る行は出さない。"""
        dto = self._dto(fallback_fact=N.FACT_RANGE_COURSE_FALLBACK)
        assert dto["facts"] == [
            "この記録は、トピック『トピック1』での記録です。",
            N.FACT_RANGE_COURSE_FALLBACK,
            N.FACT_RANGE_SHARPEN,
        ]
        assert N.FACT_RANGE_UNKNOWN_POINT not in dto["facts"]

    def test_fallback_still_returns_a_usable_range_view(self):
        dto = self._dto(fallback_fact=N.FACT_RANGE_COURSE_FALLBACK)
        assert dto["available"] is True and dto["mode"] == "range"
        assert dto["range_documents"][0]["nodes"]
        assert all(n["touched"] is False for n in dto["range_documents"][0]["nodes"])

    def test_atlas_connection_line_still_comes_last(self):
        dto = self._dto(
            fallback_fact=N.FACT_RANGE_COURSE_FALLBACK,
            atlas_concept_context={"region_label": "力学", "concept_label": "運動方程式"},
        )
        assert dto["facts"][-2] == N.FACT_RANGE_SHARPEN
        assert dto["facts"][-1].startswith("このトピックは、分野の地図の")

    def test_no_numeric_keys_and_no_banned_vocabulary(self):
        """PMN-3 / PMN-4 は縮退是正後の経路にも効く。"""
        dto = self._dto(fallback_fact=N.FACT_RANGE_COURSE_FALLBACK)
        assert [path for path, key, _ in _walk(dto) if key in _NUMERIC_KEYS] == []
        blob = "".join(dto["facts"])
        assert [word for word in _BANNED_VOCAB if word in blob] == []

    def test_fallback_fact_is_a_server_side_constant(self):
        """文言の正本はサーバ側（フロントで組み立てない）。件数・数値を含まない。"""
        assert N.FACT_RANGE_COURSE_FALLBACK in _emitted_literals(_NEARBY_SRC)
        assert not any(ch.isdigit() for ch in N.FACT_RANGE_COURSE_FALLBACK)


class TestBuildTopicRangeAtlasConnection:
    """``build_topic_range`` の ``atlas_concept_context``（装置4、純関数）。"""

    def test_context_appends_a_fact_after_the_sharpen_line(self):
        dto = N.build_topic_range(
            [{"title": "論文A", "graph": sample_graph(), "touched_claim_ids": {"cl_obs"}}],
            personal_nodes=[],
            ledger={},
            topic_label="トピック1",
            atlas_concept_context={"region_label": "力学", "concept_label": "運動方程式"},
        )
        assert dto["facts"][-2] == N.FACT_RANGE_SHARPEN
        assert dto["facts"][-1] == (
            "このトピックは、分野の地図の『力学』にある『運動方程式』に対応づけられています。"
        )

    def test_missing_context_omits_the_line(self):
        dto = N.build_topic_range(
            [{"title": "論文A", "graph": sample_graph(), "touched_claim_ids": {"cl_obs"}}],
            personal_nodes=[],
            ledger={},
            topic_label="トピック1",
            atlas_concept_context=None,
        )
        assert dto["facts"][-1] == N.FACT_RANGE_SHARPEN

    def test_partial_context_missing_a_label_omits_the_line(self):
        dto = N.build_topic_range(
            [{"title": "論文A", "graph": sample_graph(), "touched_claim_ids": {"cl_obs"}}],
            personal_nodes=[],
            ledger={},
            topic_label="トピック1",
            atlas_concept_context={"region_label": "", "concept_label": "運動方程式"},
        )
        assert dto["facts"][-1] == N.FACT_RANGE_SHARPEN

    def test_empty_dict_context_omits_the_line(self):
        dto = N.build_topic_range(
            [{"title": "論文A", "graph": sample_graph(), "touched_claim_ids": {"cl_obs"}}],
            personal_nodes=[],
            ledger={},
            topic_label="トピック1",
            atlas_concept_context={},
        )
        assert dto["facts"][-1] == N.FACT_RANGE_SHARPEN


def _topic_pnode(node_id, topic_id, *, course_id="c1"):
    """topic アンカーの本人痕跡ノード（``anchor_id`` は topic_id 自身）。"""
    return pnode(node_id, NODE_KIND_QUESTION, "この記録の本文", "topic", topic_id, course_id=course_id)


class TestNearbyForPersonNodeTopicAnchor:
    """``nearby_for_person_node`` の topic アンカー分岐（DB 経路。queries を monkeypatch）。"""

    def _network(self, *, course_id="c1", extra_nodes=None):
        start = _topic_pnode("tnode", "t1", course_id=course_id)
        return start, PersonalNetwork(nodes=[start] + (extra_nodes or []))

    def test_range_mode_end_to_end(self, monkeypatch):
        start, network = self._network()
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        monkeypatch.setattr(
            queries_mod,
            "fetch_topic_claim_binding",
            lambda course_id, topic_id: {"claim_ids": ["cl_obs"], "topic_label": "トピック1"},
        )
        monkeypatch.setattr(queries_mod, "fetch_claim_document_id", lambda cid: "docA")
        monkeypatch.setattr(queries_mod, "fetch_component_graph", lambda doc_id: sample_graph())
        monkeypatch.setattr(queries_mod, "fetch_document_titles", lambda ids: {"docA": "論文A"})
        monkeypatch.setattr(queries_mod, "fetch_component_ledger_statuses", lambda ids: {})

        dto = N.nearby_for_person_node("user1", "tnode")
        assert dto["available"] is True
        assert dto["mode"] == "range"
        assert dto["center"] is None
        assert dto["topic_label"] == "トピック1"
        assert [d["title"] for d in dto["range_documents"]] == ["論文A"]

    def test_no_linked_claim_ids_falls_back_to_the_course_range(self, monkeypatch):
        """①トピック⇄claim の対応が無い → notice で終わらせずコース sources を範囲表示。"""
        start, network = self._network()
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        monkeypatch.setattr(
            queries_mod,
            "fetch_topic_claim_binding",
            lambda course_id, topic_id: {"claim_ids": [], "topic_label": "トピック1"},
        )

        def _claims_must_not_be_resolved(cid):
            raise AssertionError("no claim ids means no claim resolution")

        monkeypatch.setattr(queries_mod, "fetch_claim_document_id", _claims_must_not_be_resolved)
        monkeypatch.setattr(queries_mod, "fetch_course_document_ids", lambda cid: {"docA"})
        monkeypatch.setattr(queries_mod, "fetch_component_graph", lambda doc_id: sample_graph())
        monkeypatch.setattr(queries_mod, "fetch_document_titles", lambda ids: {"docA": "論文A"})
        monkeypatch.setattr(queries_mod, "fetch_component_ledger_statuses", lambda ids: {})
        monkeypatch.setattr(queries_mod, "fetch_topic_atlas_binding", lambda cid: {})

        dto = N.nearby_for_person_node("user1", "tnode")
        assert dto["available"] is True
        assert dto["mode"] == "range"
        assert dto["notice"] is None
        assert [d["title"] for d in dto["range_documents"]] == ["論文A"]
        # touched は推測で立てない（PMN-1: 粗さは事実文で言う）。
        assert all(
            n["touched"] is False for n in dto["range_documents"][0]["nodes"]
        )
        assert N.FACT_RANGE_COURSE_FALLBACK in dto["facts"]
        assert N.FACT_RANGE_UNKNOWN_POINT not in dto["facts"]

    def test_claims_unresolvable_to_any_document_falls_back_to_the_course_range(
        self, monkeypatch
    ):
        """②claim が document に解決できない → 同じくコース範囲フォールバック。"""
        start, network = self._network()
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        monkeypatch.setattr(
            queries_mod,
            "fetch_topic_claim_binding",
            lambda course_id, topic_id: {"claim_ids": ["cl_x"], "topic_label": "T"},
        )
        monkeypatch.setattr(queries_mod, "fetch_claim_document_id", lambda cid: None)
        monkeypatch.setattr(queries_mod, "fetch_course_document_ids", lambda cid: {"docA"})
        monkeypatch.setattr(queries_mod, "fetch_component_graph", lambda doc_id: sample_graph())
        monkeypatch.setattr(queries_mod, "fetch_document_titles", lambda ids: {"docA": "論文A"})
        monkeypatch.setattr(queries_mod, "fetch_component_ledger_statuses", lambda ids: {})
        monkeypatch.setattr(queries_mod, "fetch_topic_atlas_binding", lambda cid: {})

        dto = N.nearby_for_person_node("user1", "tnode")
        assert dto["available"] is True and dto["mode"] == "range"
        assert N.FACT_RANGE_COURSE_FALLBACK in dto["facts"]

    def test_claims_resolve_but_no_main_node_touched_still_shows_the_document(
        self, monkeypatch
    ):
        """③has_touch ゲートの撤廃: 交差ゼロでも図は出し、touched を1つも点けない。"""
        start, network = self._network()
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        monkeypatch.setattr(
            queries_mod,
            "fetch_topic_claim_binding",
            lambda course_id, topic_id: {"claim_ids": ["cl_unmapped"], "topic_label": "T"},
        )
        monkeypatch.setattr(queries_mod, "fetch_claim_document_id", lambda cid: "docA")
        monkeypatch.setattr(
            queries_mod,
            "fetch_component_graph",
            lambda doc_id: {
                "nodes": [gnode("n1", "Theory basis", linked_claim_ids=["other"])],
                "edges": [],
            },
        )
        monkeypatch.setattr(queries_mod, "fetch_document_titles", lambda ids: {"docA": "論文A"})
        monkeypatch.setattr(queries_mod, "fetch_component_ledger_statuses", lambda ids: {})
        monkeypatch.setattr(queries_mod, "fetch_topic_atlas_binding", lambda cid: {})

        def _must_not_be_called(cid):
            raise AssertionError("claim-resolved documents must not trigger the fallback")

        monkeypatch.setattr(queries_mod, "fetch_course_document_ids", _must_not_be_called)

        dto = N.nearby_for_person_node("user1", "tnode")
        assert dto["available"] is True and dto["mode"] == "range"
        assert dto["notice"] is None
        nodes = dto["range_documents"][0]["nodes"]
        assert [n["component_id"] for n in nodes] == ["n1"]
        assert all(n["touched"] is False for n in nodes)
        # claim は解決しているのでフォールバックの事実文は出さない。
        assert N.FACT_RANGE_COURSE_FALLBACK not in dto["facts"]
        assert not any("触れている理論構成" in f for f in dto["facts"])

    def test_unviewable_documents_are_excluded_from_both_paths(self, monkeypatch):
        """PMN-7: 閲覧不可はフォールバック経路でも fail-closed（グラフを読まない）。"""
        start, network = self._network()
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        monkeypatch.setattr(
            queries_mod,
            "fetch_topic_claim_binding",
            lambda course_id, topic_id: {"claim_ids": ["cl_obs"], "topic_label": "T"},
        )
        monkeypatch.setattr(queries_mod, "fetch_claim_document_id", lambda cid: "docA")
        monkeypatch.setattr(queries_mod, "fetch_course_document_ids", lambda cid: {"docA", "docB"})

        def _must_not_be_called(*args, **kwargs):
            raise AssertionError("excluded document should not be scanned for graph")

        monkeypatch.setattr(queries_mod, "fetch_component_graph", _must_not_be_called)

        dto = N.nearby_for_person_node(
            "user1", "tnode", can_view_document=lambda uid, doc_id: False
        )
        assert dto["available"] is False
        assert dto["notice"] == N.NOTICE_TOPIC_NO_MAPPING
        # 行き止まりにしない: 精密化の出口を事実文で案内する。
        assert dto["facts"] == [N.FACT_RANGE_SHARPEN]

    def test_fallback_without_any_analysed_document_stays_unavailable(self, monkeypatch):
        """③フォールバックでもグラフのある document がゼロなら従来どおり notice。"""
        start, network = self._network()
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        monkeypatch.setattr(
            queries_mod,
            "fetch_topic_claim_binding",
            lambda course_id, topic_id: {"claim_ids": [], "topic_label": "T"},
        )
        monkeypatch.setattr(queries_mod, "fetch_course_document_ids", lambda cid: {"docA"})
        monkeypatch.setattr(queries_mod, "fetch_component_graph", lambda doc_id: None)

        dto = N.nearby_for_person_node("user1", "tnode")
        assert dto["available"] is False
        assert dto["notice"] == N.NOTICE_TOPIC_NO_MAPPING
        assert dto["facts"] == [N.FACT_RANGE_SHARPEN]

    def test_fallback_scan_is_capped_and_deterministic(self, monkeypatch):
        """コース範囲は決定論順・上限つき（既存の境界を変えない）。"""
        start, network = self._network()
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        monkeypatch.setattr(
            queries_mod,
            "fetch_topic_claim_binding",
            lambda course_id, topic_id: {"claim_ids": [], "topic_label": ""},
        )
        all_docs = {f"doc{i}" for i in range(9)}
        monkeypatch.setattr(queries_mod, "fetch_course_document_ids", lambda cid: all_docs)
        monkeypatch.setattr(queries_mod, "fetch_component_graph", lambda doc_id: sample_graph())
        monkeypatch.setattr(
            queries_mod, "fetch_document_titles", lambda ids: {d: d.upper() for d in ids}
        )
        monkeypatch.setattr(queries_mod, "fetch_component_ledger_statuses", lambda ids: {})
        monkeypatch.setattr(queries_mod, "fetch_topic_atlas_binding", lambda cid: {})

        dto = N.nearby_for_person_node("user1", "tnode")
        titles = [d["title"] for d in dto["range_documents"]]
        assert titles == ["DOC0", "DOC1", "DOC2", "DOC3", "DOC4"]
        assert len(titles) == N.MAX_DOCUMENTS_SCANNED

    def test_multiple_documents_are_ordered_by_document_id(self, monkeypatch):
        start, network = self._network()
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        monkeypatch.setattr(
            queries_mod,
            "fetch_topic_claim_binding",
            lambda course_id, topic_id: {"claim_ids": ["cl_x", "cl_y"], "topic_label": ""},
        )
        doc_by_claim = {"cl_x": "docB", "cl_y": "docA"}
        monkeypatch.setattr(queries_mod, "fetch_claim_document_id", lambda cid: doc_by_claim[cid])
        graphs = {
            "docA": {
                "nodes": [gnode("a1", "Theory basis", order=1, linked_claim_ids=["cl_y"])],
                "edges": [],
            },
            "docB": {
                "nodes": [gnode("b1", "Elimination", order=1, linked_claim_ids=["cl_x"])],
                "edges": [],
            },
        }
        monkeypatch.setattr(queries_mod, "fetch_component_graph", lambda doc_id: graphs[doc_id])
        monkeypatch.setattr(
            queries_mod,
            "fetch_document_titles",
            lambda ids: {"docA": "論文A", "docB": "論文B"},
        )
        monkeypatch.setattr(queries_mod, "fetch_component_ledger_statuses", lambda ids: {})

        dto = N.nearby_for_person_node("user1", "tnode")
        assert [d["title"] for d in dto["range_documents"]] == ["論文A", "論文B"]

    def test_no_course_id_is_unavailable(self, monkeypatch):
        start, network = self._network(course_id=None)
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        dto = N.nearby_for_person_node("user1", "tnode")
        assert dto["available"] is False

    def test_unknown_node_id_is_none(self, monkeypatch):
        start, network = self._network()
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        assert N.nearby_for_person_node("user1", "does-not-exist") is None

    def test_center_component_id_returns_point_view(self, monkeypatch):
        start, network = self._network()
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        monkeypatch.setattr(queries_mod, "fetch_course_document_ids", lambda cid: {"docA"})
        monkeypatch.setattr(queries_mod, "fetch_component_graph", lambda doc_id: sample_graph())
        monkeypatch.setattr(queries_mod, "fetch_component_ledger_statuses", lambda ids: {})
        monkeypatch.setattr(queries_mod, "fetch_center_support_fact_line", lambda *a, **k: "")

        dto = N.nearby_for_person_node("user1", "tnode", center_component_id="n_obs")
        assert dto["available"] is True
        assert dto["mode"] == "near"
        assert dto["center"]["component_id"] == "n_obs"

    def test_center_component_id_root_mode_returns_point_view(self, monkeypatch):
        start, network = self._network()
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        monkeypatch.setattr(queries_mod, "fetch_course_document_ids", lambda cid: {"docA"})
        monkeypatch.setattr(queries_mod, "fetch_component_graph", lambda doc_id: sample_graph())
        monkeypatch.setattr(queries_mod, "fetch_component_ledger_statuses", lambda ids: {})
        monkeypatch.setattr(queries_mod, "fetch_center_support_fact_line", lambda *a, **k: "")

        dto = N.nearby_for_person_node(
            "user1", "tnode", mode="root", center_component_id="n_obs"
        )
        assert dto["available"] is True
        assert dto["mode"] == "root"
        assert dto["center"]["component_id"] == "n_obs"

    def test_center_component_id_missing_everywhere_is_none(self, monkeypatch):
        start, network = self._network()
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        monkeypatch.setattr(queries_mod, "fetch_course_document_ids", lambda cid: {"docA"})
        monkeypatch.setattr(queries_mod, "fetch_component_graph", lambda doc_id: sample_graph())

        result = N.nearby_for_person_node(
            "user1", "tnode", center_component_id="does-not-exist"
        )
        assert result is None


class TestNearbyForPersonNodeRangeModeAtlasConnection:
    """``nearby_for_person_node`` の範囲モード → 分野の地図の接続行（装置4、DB 経路）。"""

    def _network(self, *, course_id="c1"):
        start = _topic_pnode("tnode", "t1", course_id=course_id)
        return start, PersonalNetwork(nodes=[start])

    def _wire_common(self, monkeypatch, network):
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        monkeypatch.setattr(
            queries_mod,
            "fetch_topic_claim_binding",
            lambda course_id, topic_id: {"claim_ids": ["cl_obs"], "topic_label": "トピック1"},
        )
        monkeypatch.setattr(queries_mod, "fetch_claim_document_id", lambda cid: "docA")
        monkeypatch.setattr(queries_mod, "fetch_component_graph", lambda doc_id: sample_graph())
        monkeypatch.setattr(queries_mod, "fetch_document_titles", lambda ids: {"docA": "論文A"})
        monkeypatch.setattr(queries_mod, "fetch_component_ledger_statuses", lambda ids: {})

    def test_binding_and_cartridge_present_appends_the_fact(self, monkeypatch):
        _, network = self._network()
        self._wire_common(monkeypatch, network)
        monkeypatch.setattr(
            queries_mod, "fetch_topic_atlas_binding", lambda cid: {"t1": "concept1"}
        )
        monkeypatch.setattr(
            queries_mod, "fetch_course_cartridge_id", lambda cid: "particle_physics"
        )
        monkeypatch.setattr(
            queries_mod,
            "fetch_atlas_concept_context",
            lambda cartridge_id, node_id: {"region_label": "力学", "concept_label": "運動方程式"},
        )
        dto = N.nearby_for_person_node("user1", "tnode")
        assert dto["facts"][-1] == (
            "このトピックは、分野の地図の『力学』にある『運動方程式』に対応づけられています。"
        )

    def test_no_binding_omits_the_fact(self, monkeypatch):
        _, network = self._network()
        self._wire_common(monkeypatch, network)
        monkeypatch.setattr(queries_mod, "fetch_topic_atlas_binding", lambda cid: {})

        def _must_not_be_called(*a, **k):
            raise AssertionError("cartridge_id should not be fetched without a binding")

        monkeypatch.setattr(queries_mod, "fetch_course_cartridge_id", _must_not_be_called)
        dto = N.nearby_for_person_node("user1", "tnode")
        assert dto["facts"][-1] == N.FACT_RANGE_SHARPEN

    def test_empty_cartridge_id_omits_the_fact(self, monkeypatch):
        _, network = self._network()
        self._wire_common(monkeypatch, network)
        monkeypatch.setattr(
            queries_mod, "fetch_topic_atlas_binding", lambda cid: {"t1": "concept1"}
        )
        monkeypatch.setattr(queries_mod, "fetch_course_cartridge_id", lambda cid: "")

        def _must_not_be_called(*a, **k):
            raise AssertionError("concept context should not be queried without a cartridge_id")

        monkeypatch.setattr(queries_mod, "fetch_atlas_concept_context", _must_not_be_called)
        dto = N.nearby_for_person_node("user1", "tnode")
        assert dto["facts"][-1] == N.FACT_RANGE_SHARPEN

    def test_unresolvable_concept_omits_the_fact(self, monkeypatch):
        _, network = self._network()
        self._wire_common(monkeypatch, network)
        monkeypatch.setattr(
            queries_mod, "fetch_topic_atlas_binding", lambda cid: {"t1": "concept1"}
        )
        monkeypatch.setattr(
            queries_mod, "fetch_course_cartridge_id", lambda cid: "particle_physics"
        )
        monkeypatch.setattr(
            queries_mod, "fetch_atlas_concept_context", lambda cartridge_id, node_id: None
        )
        dto = N.nearby_for_person_node("user1", "tnode")
        assert dto["facts"][-1] == N.FACT_RANGE_SHARPEN

    def test_exception_during_resolution_is_fail_soft(self, monkeypatch):
        _, network = self._network()
        self._wire_common(monkeypatch, network)

        def _boom(cid):
            raise RuntimeError("db down")

        monkeypatch.setattr(queries_mod, "fetch_topic_atlas_binding", _boom)
        dto = N.nearby_for_person_node("user1", "tnode")
        assert dto["available"] is True
        assert dto["facts"][-1] == N.FACT_RANGE_SHARPEN

    def test_center_component_id_point_view_never_resolves_atlas_connection(self, monkeypatch):
        """点ビューへの中心移動時は装置4を適用しない（build_nearby にこの引数は無い）。"""
        _, network = self._network()
        monkeypatch.setattr(N, "derive_person_network", lambda user_id: network)
        monkeypatch.setattr(queries_mod, "fetch_course_document_ids", lambda cid: {"docA"})
        monkeypatch.setattr(queries_mod, "fetch_component_graph", lambda doc_id: sample_graph())
        monkeypatch.setattr(queries_mod, "fetch_component_ledger_statuses", lambda ids: {})
        monkeypatch.setattr(queries_mod, "fetch_center_support_fact_line", lambda *a, **k: "")

        def _must_not_be_called(*a, **k):
            raise AssertionError("atlas concept context should not be resolved for point views")

        monkeypatch.setattr(queries_mod, "fetch_topic_atlas_binding", _must_not_be_called)
        dto = N.nearby_for_person_node("user1", "tnode", center_component_id="n_obs")
        assert dto["available"] is True
        assert dto["mode"] == "near"


# ---------------------------------------------------------------------------
# §7 ガードレール
# ---------------------------------------------------------------------------

_NUMERIC_KEYS = (
    "confidence",
    "load_score",
    "level",
    "weight",
    "count",
    "score",
    "cut_members",
    "observation_roots",
)

# PMN-3（閉世界語彙）と PMN-5（助言・評価をしない）の denylist。
_BANNED_VOCAB = (
    "この分野では未検証",
    "誰も検証していない",
    "世界初",
    "未踏",
    "すべき",
    "安心",
    "踏破",
    "達成率",
    "ランキング",
)


def _emitted_literals(source: str) -> list[str]:
    """コードが**出力しうる**文字列リテラルだけを集める。

    docstring とコメントは除外する — 「この語は書かない」と説明するために禁止語を引用した
    コメント・docstring まで denylist に掛けると、規約の説明そのものが書けなくなる。
    検査したい不変条項は「禁止語が画面に届かない」なので、対象は実リテラルに限る。
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _walk(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield f"{path}.{key}", key, item
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _walk(item, f"{path}[{i}]")


class TestGuardrails:
    def test_core_module_does_not_import_fastapi_or_llm(self):
        assert_module_tree_does_not_import(
            BACKEND / "core" / "personal_graph",
            ("fastapi", "services", "core.llm", "openai"),
        )

    def test_no_numeric_keys_anywhere_in_the_dto(self):
        """PMN-4: 生数値・内部構成要素の列挙を返さない。"""
        personal = [pnode("t1", NODE_KIND_TENSION, "x", "claim", "cl_obs")]
        for mode in N.MODES:
            dto = build(mode, ledger={"n_obs": "untested"}, personal=personal, support="事実文")
            hits = [
                path for path, key, _ in _walk(dto) if key in _NUMERIC_KEYS
            ]
            assert hits == [], f"数値・内部キーが DTO に現れた: {hits}"

    def test_no_banned_vocabulary_in_emitted_strings(self):
        """PMN-3 / PMN-5: 画面に届く文字列リテラルに禁止語が入っていない。"""
        blob = "".join(_emitted_literals(_NEARBY_SRC))
        offending = [word for word in _BANNED_VOCAB if word in blob]
        assert offending == [], f"nearby.py の出力文字列に禁止語: {offending}"

    def test_no_banned_vocabulary_in_dto(self):
        dto = build(ledger={"n_obs": "untested"})
        blob = "".join(dto["facts"]) + (dto["center"]["verification"]["label"] or "")
        for word in _BANNED_VOCAB:
            assert word not in blob

    def test_module_has_no_write_paths(self):
        for banned in ("INSERT", "UPDATE ", "DELETE FROM", "session.commit"):
            assert banned not in _NEARBY_SRC

    def test_me_router_stays_read_only(self):
        """PN-2 の継続確認: nearby 追加で書き込みメソッドが増えていない。"""
        for verb in ("post", "put", "patch", "delete"):
            assert f"@me_router.{verb}" not in _ROUTE_SRC

    def test_route_rejects_unknown_mode(self):
        assert "NEARBY_MODES" in _ROUTE_SRC
        assert "status_code=422" in _ROUTE_SRC

    def test_route_injects_permission_callback(self):
        """PMN-7: document の閲覧可否は route 側の実体で fail-closed に判定する。"""
        assert "can_view_document=user_can_view_document" in _ROUTE_SRC

    def test_vocabulary_tables_are_not_duplicated(self):
        """§7-8: 訳語は既存の正本からのみ引く（新しい訳語表を作らない）。"""
        from core.element_vocab import THEORY_STAGE_LABELS
        from core.label_vocab import VERIFICATION_STATUS_LABELS_LEDGER

        assert "from core.element_vocab import" in _NEARBY_SRC
        assert "from core.label_vocab import" in _NEARBY_SRC
        literals = set(_emitted_literals(_NEARBY_SRC))
        duplicated = sorted(
            literals
            & (set(THEORY_STAGE_LABELS.values()) | set(VERIFICATION_STATUS_LABELS_LEDGER.values()))
        )
        assert duplicated == [], f"訳語をリテラルで再定義している: {duplicated}"
