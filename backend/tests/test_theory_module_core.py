"""理論モジュール層 — core（``build_theory_modules``）のテスト。

正本: ``docs/features/theory_module_layer_design.md`` §5（導出規則）/ §8.1（DTO）/ §12（実装記録）。

fixture は実 DB の採用 run artifact を間引いたもの（``tests/fixtures/theory_modules/``）。
ゴールデン（外枠の数・構造）は §3.2 / §3.3 の試作と一致することを受け入れ条件にする。
合成データのテストは §5.2〜5.5 の個々の規則を1つずつ固定する。
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.theory_modules import build_theory_modules  # noqa: E402
from core.theory_modules.schema import (  # noqa: E402
    FACT_CLAIM_CHAINS_ONLY,
    FACT_CYCLE_PREFIX,
    FACT_INFERRED_STEPS_EXCLUDED,
    FACT_NO_DERIVATIONS,
    FACT_NO_EQUATION_STEPS,
    FACT_NO_GRAPH,
    INTERNAL_ID_RE,
    RULE_VERSION,
    equation_display_label,
)

FIXTURES = BACKEND / "tests" / "fixtures" / "theory_modules"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _build(fixture: dict) -> dict:
    return build_theory_modules(
        document_id=fixture["document_id"],
        artifacts=fixture["artifacts"],
        graph_json=fixture["graph_json"],
    )


def _outer(result: dict) -> list[dict]:
    return [m for m in result["modules"] if m["level"] == "outer"]


def _inner(result: dict) -> list[dict]:
    return [m for m in result["modules"] if m["level"] == "inner"]


@pytest.fixture(scope="module")
def tex_2407():
    return _load("arxiv_2407_01221v2_tex.json")


@pytest.fixture(scope="module")
def tex_2609():
    return _load("arxiv_2609_15375v1_tex.json")


@pytest.fixture(scope="module")
def pdf_2609():
    return _load("arxiv_2609_15375v1_pdf.json")


# ---------------------------------------------------------------------------
# 合成データの組み立て
# ---------------------------------------------------------------------------


def _step(step_id, operation, inputs, outputs, **extra):
    row = {
        "step_id": step_id,
        "operation": operation,
        "input_equation_ids": list(inputs),
        "output_equation_ids": list(outputs),
        "required_claim_ids": [],
        "assumption_ids": [],
    }
    row.update(extra)
    return row


def _chain(derivation_id, steps, chain_type="equation_chain"):
    return {"derivation_id": derivation_id, "chain_type": chain_type, "steps": steps}


def _artifacts(chains, *, records=None, claims=None, components=None):
    return {
        "derivation_chain": {"chains": chains},
        "equation_semantics": {"records": records or []},
        "claim_object_builder": {"claims": claims or []},
        "component_assembly": {"components": components or []},
    }


def _run(chains, graph=None, **kwargs):
    return build_theory_modules(
        document_id="doc-1",
        artifacts=_artifacts(chains, **kwargs),
        graph_json=graph or {"nodes": [], "edges": []},
    )


def _member_refs(result: dict, level: str = "outer") -> list[list[str]]:
    return [
        sorted(ref for member in m["members"] for ref in member["step_refs"])
        for m in result["modules"]
        if m["level"] == level
    ]


def _inner_outputs(fixture: dict, result: dict) -> list[frozenset[str]]:
    """内側モジュールごとの「成員 step が生む式」の集合（出現順）。step_ref → 出力式は fixture から引く。"""
    outputs_by_ref: dict[str, list[str]] = {}
    for chain in fixture["artifacts"]["derivation_chain"]["chains"]:
        for step in chain.get("steps") or []:
            ref = f"{chain['derivation_id']}:{step['step_id']}"
            outputs_by_ref[ref] = list(step.get("output_equation_ids") or [])
    return [
        frozenset(eq for member in m["members"] for ref in member["step_refs"] for eq in outputs_by_ref[ref])
        for m in _inner(result)
    ]


# ---------------------------------------------------------------------------
# ゴールデン（§3.2 / §3.3 の試作との一致）
# ---------------------------------------------------------------------------


class TestGolden2407:
    def test_outer_is_eight_modules_base_seven_plus_calculation_body(self, tex_2407):
        result = _build(tex_2407)
        assert result["available"] is True
        sizes = sorted(len(m["members"]) for m in _outer(result))
        # 基礎側 7 モジュール（成員 10 step）+ 計算本体 1 モジュール（10 step）
        assert sizes == [1, 1, 1, 1, 2, 2, 2, 10]

    def test_shared_foundation(self, tex_2407):
        result = _build(tex_2407)
        assert {f["equation_id"] for f in result["foundations"]} == {
            "eq_delta_bias_real",
            "eq_tex_b24",
            "eq_tex_b64",
            "eq_tex_b97",
        }

    def test_calculation_body_has_inner_modules(self, tex_2407):
        result = _build(tex_2407)
        body = max(_outer(result), key=lambda m: len(m["members"]))
        inner = [m for m in _inner(result) if m["parent_module_key"] == body["module_key"]]
        assert len(inner) >= 2
        assert all(m["parent_module_key"] == body["module_key"] for m in _inner(result))
        assert sum(len(m["members"]) for m in inner) == len(body["members"])

    def test_cycle_is_reported_and_kept_together(self, tex_2407):
        result = _build(tex_2407)
        cycle_facts = [f for f in result["facts"] if f.startswith(FACT_CYCLE_PREFIX)]
        assert len(cycle_facts) == 1
        # 循環 b78 ↔ b80 の2手順は同じ外枠（計算本体）に入り、内側では同じ内側モジュールの先頭になる。
        # 後続の b85 がつながるので単独（isolated_reason = cycle）にはならない（§12.3）。
        holders = [out for out in _inner_outputs(tex_2407, result) if {"eq_tex_b78", "eq_tex_b80"} & out]
        assert holders == [frozenset({"eq_tex_b78", "eq_tex_b80", "eq_tex_b85"})]
        assert not [m for m in _inner(result) if m["isolated_reason"] == "cycle"]

    def test_inner_golden(self, tex_2407):
        """計算本体（10 step）の内側 = 基礎を導く手順 / S の機構 / L の機構と変数変換 / 核の陽な形 / 結果（§12.3）。"""
        result = _build(tex_2407)
        assert _inner_outputs(tex_2407, result) == [
            frozenset({"eq_tex_b97"}),
            frozenset({"eq_tex_b66", "eq_tex_b68"}),
            frozenset({"eq_tex_b72", "eq_tex_b74", "eq_tex_b76"}),
            frozenset({"eq_tex_b78", "eq_tex_b80", "eq_tex_b85"}),
            frozenset({"eq_skewness_bias_ex"}),
        ]
        assert all(m["isolated_reason"] is None for m in _inner(result))
        # 内側どうしの受け渡し: 角度積分の機構 → 核の陽な形（b76）は内側の辺として残る
        inner_edges = [e for e in result["edges"] if e["level"] == "inner"]
        assert inner_edges
        assert all(e["source"] != e["target"] for e in inner_edges)

    def test_small_outers_have_no_inner(self, tex_2407):
        result = _build(tex_2407)
        parents = {m["parent_module_key"] for m in _inner(result)}
        body = max(_outer(result), key=lambda m: len(m["members"]))
        assert parents == {body["module_key"]}

    def test_system_level_is_a_sink_not_a_member(self, tex_2407):
        result = _build(tex_2407)
        assert len(result["sinks"]) == 1
        sink = result["sinks"][0]
        assert sink["operation_label"] == "消去"
        assert sink["output_labels"] == ["式 (skewness-bias-ex)"]
        outer_keys = {m["module_key"] for m in _outer(result)}
        assert sink["source_module_keys"]
        assert set(sink["source_module_keys"]) <= outer_keys
        refs = [ref for refs in _member_refs(result) for ref in refs]
        assert not any(ref.startswith("system_derivation_") for ref in refs)

    def test_every_member_maps_to_a_detail_node(self, tex_2407):
        result = _build(tex_2407)
        detail_ids = {
            n["component_id"] for n in tex_2407["graph_json"]["nodes"] if n["graph_layer"] == "equation_detail"
        }
        for module in _outer(result):
            for member in module["members"]:
                assert member["node_ids"], member
                assert set(member["node_ids"]) <= detail_ids

    def test_duplicate_step_across_chains_is_one_member_with_all_refs(self, tex_2407):
        result = _build(tex_2407)
        members = [mm for m in _outer(result) for mm in m["members"]]
        assert len(members) == 20  # chain×step 22 → 重複除去で 20（§3.2）
        assert any(len(mm["step_refs"]) == 3 for mm in members)


class TestGolden2609Tex:
    def test_outer_is_ten_modules(self, tex_2609):
        result = _build(tex_2609)
        assert result["available"] is True
        sizes = sorted(len(m["members"]) for m in _outer(result))
        assert sizes == [1, 1, 1, 1, 2, 2, 2, 3, 5, 17]

    def test_shared_foundation_ignores_in_degree(self, tex_2609):
        result = _build(tex_2609)
        assert {f["equation_id"] for f in result["foundations"]} == {
            "eq_eqcand_inline_tex_b58_10_f757bb7d",
            "eq_tex_b184",
            "eq_tex_b210",
            "eq_tex_b194",
            "eq_eqcand_inline_tex_b127_6_26306e07",
            "eq_tex_b218",
        }
        # eq_tex_b210 は入次数 1（他の手順から導かれる）でも共有の基礎になる（§5.3 改訂）
        b210 = next(f for f in result["foundations"] if f["equation_id"] == "eq_tex_b210")
        assert b210["producer_module_keys"]

    def test_calculation_body_has_seventeen_members_and_closed_interface(self, tex_2609):
        result = _build(tex_2609)
        body = max(_outer(result), key=lambda m: len(m["members"]))
        assert len(body["members"]) == 17
        # 共有の基礎と結果だけで閉じている（接点 0 = §3.3）
        assert body["inputs"] == [] and body["outputs"] == []

    def test_claim_chain_nodes_are_marked_not_members(self, tex_2609):
        result = _build(tex_2609)
        claim_seq = set(result["claim_sequence_node_ids"])
        assert len(claim_seq) == 329
        member_nodes = {n for m in result["modules"] for mm in m["members"] for n in mm["node_ids"]}
        assert not claim_seq & member_nodes
        refs = [ref for refs in _member_refs(result) for ref in refs]
        assert not any(ref.startswith("derivation_claim_") for ref in refs)

    def test_cycle_fact(self, tex_2609):
        result = _build(tex_2609)
        assert any(f.startswith(FACT_CYCLE_PREFIX) for f in result["facts"])

    def test_inner_golden(self, tex_2609):
        """計算本体（17 step・接点 0）の内側 = 相互相関の書き換え / 平均密度と面密度 / 基礎を導く手順 /
        ペア数の観測量（§12.3）。閉じた本体でも丸ごと 1 つに戻らない。"""
        result = _build(tex_2609)
        body = max(_outer(result), key=lambda m: len(m["members"]))
        assert {m["parent_module_key"] for m in _inner(result)} == {body["module_key"]}
        assert _inner_outputs(tex_2609, result) == [
            frozenset({"eq_tex_b186", "eq_tex_b188", "eq_tex_b190", "eq_tex_b192", "eq_tex_b194", "eq_tex_b196"}),
            frozenset({"eq_tex_b198", "eq_tex_b200", "eq_tex_b202", "eq_tex_b204", "eq_tex_b206"}),
            frozenset({"eq_tex_b210"}),
            frozenset({"eq_eqn_pairs", "eq_tex_b208", "eq_tex_b220", "eq_tex_b222", "eq_tex_b224"}),
        ]
        assert sum(len(m["members"]) for m in _inner(result)) == 17

    def test_three_step_outer_is_not_split_into_singletons(self, tex_2609):
        # Phase 0（接点 ≤ 2 の貪欲併合）は面密度まわりの 3 手順の外枠を 1 手順ずつ 3 つに割っていた。
        result = _build(tex_2609)
        three = [m for m in _outer(result) if len(m["members"]) == 3]
        assert len(three) == 1
        assert not [m for m in _inner(result) if m["parent_module_key"] == three[0]["module_key"]]


class TestGolden2609Pdf:
    def test_unavailable_with_fact(self, pdf_2609):
        result = _build(pdf_2609)
        assert result["available"] is False
        assert result["modules"] == [] and result["edges"] == [] and result["sinks"] == []
        assert result["facts"][0] == FACT_NO_EQUATION_STEPS
        assert FACT_CLAIM_CHAINS_ONLY in result["facts"]

    def test_claim_sequence_marks_every_detail_node(self, pdf_2609):
        result = _build(pdf_2609)
        detail_ids = [
            n["component_id"] for n in pdf_2609["graph_json"]["nodes"] if n["graph_layer"] == "equation_detail"
        ]
        assert len(detail_ids) == 470
        assert result["claim_sequence_node_ids"] == detail_ids


# ---------------------------------------------------------------------------
# §5.2 成員の選別
# ---------------------------------------------------------------------------


class TestMemberSelection:
    def test_claim_chain_steps_are_not_members(self):
        result = _run([
            _chain("d_eq", [_step("s1", "define", ["a"], ["b"])]),
            _chain("d_claim", [_step("s1", "infer_intermediate_claim", ["a"], ["c"])], chain_type="claim_chain"),
        ])
        assert _member_refs(result) == [["d_eq:s1"]]

    def test_steps_without_input_or_output_equations_are_not_members(self):
        result = _run([
            _chain("d1", [
                _step("s1", "define", [], ["b"]),
                _step("s2", "define", ["a"], []),
                _step("s3", "substitute", ["a"], ["c"]),
            ]),
        ])
        assert _member_refs(result) == [["d1:s3"]]

    def test_system_level_becomes_sink(self):
        result = _run([
            _chain("d1", [_step("s1", "define", ["a"], ["b"])]),
            _chain("sys", [_step("x1", "eliminate", ["a", "b"], ["z"])], chain_type="system_level"),
        ])
        assert _member_refs(result) == [["d1:s1"]]
        assert len(result["sinks"]) == 1
        assert result["sinks"][0]["source_module_keys"] == [_outer(result)[0]["module_key"]]

    def test_duplicate_step_is_deduplicated_with_all_occurrences(self):
        step = _step("s1", "define", ["a"], ["b"])
        result = _run([_chain("d1", [step]), _chain("d2", [dict(step, step_id="s7")])])
        modules = _outer(result)
        assert len(modules) == 1
        assert modules[0]["members"][0]["step_refs"] == ["d1:s1", "d2:s7"]

    def test_inferred_only_step_is_excluded_with_fact(self):
        graph = {"nodes": [
            {"component_id": "eq_op_1", "graph_layer": "equation_detail", "source_backing_status": "inferred",
             "linked_derivation_ids": ["d1:s1"], "input_equation_ids": ["a"], "output_equation_ids": ["b"]},
            {"component_id": "eq_op_2", "graph_layer": "equation_detail", "source_backing_status": "source_backed",
             "linked_derivation_ids": ["d1:s2"], "input_equation_ids": ["b"], "output_equation_ids": ["c"]},
        ]}
        result = _run([_chain("d1", [
            _step("s1", "define", ["a"], ["b"]),
            _step("s2", "substitute", ["b"], ["c"]),
        ])], graph=graph)
        assert _member_refs(result) == [["d1:s2"]]
        assert FACT_INFERRED_STEPS_EXCLUDED in result["facts"]

    def test_node_match_by_bare_step_requires_derivation_id(self):
        graph = {"nodes": [
            {"component_id": "eq_op_1", "graph_layer": "equation_detail", "source_backing_status": "source_backed",
             "linked_derivation_ids": ["d1", "s1"], "input_equation_ids": ["a"], "output_equation_ids": ["b"]},
            {"component_id": "eq_op_2", "graph_layer": "equation_detail", "source_backing_status": "partially_source_backed",
             "linked_derivation_ids": ["d2", "s1"], "input_equation_ids": ["b"], "output_equation_ids": ["c"]},
        ]}
        result = _run([
            _chain("d1", [_step("s1", "define", ["a"], ["b"])]),
            _chain("d2", [_step("s1", "substitute", ["b"], ["c"])]),
        ], graph=graph)
        by_ref = {mm["step_refs"][0]: mm["node_ids"] for m in _outer(result) for mm in m["members"]}
        assert by_ref == {"d1:s1": ["eq_op_1"], "d2:s1": ["eq_op_2"]}
        # 裏付けは成員の最も弱いもの
        assert _outer(result)[0]["source_backing_status"] == "partially_source_backed"

    def test_missing_graph_is_a_fact_not_an_error(self):
        result = _run([_chain("d1", [_step("s1", "define", ["a"], ["b"])])])
        assert result["available"] is True
        assert FACT_NO_GRAPH in result["facts"]
        assert _outer(result)[0]["source_backing_status"] == "review_required"


# ---------------------------------------------------------------------------
# §5.3 共有の基礎・§5.4 接点
# ---------------------------------------------------------------------------


class TestFoundationAndInterface:
    def test_equation_consumed_by_four_steps_is_foundation(self):
        steps = [_step(f"s{i}", "define", ["base", f"x{i}"], [f"y{i}"]) for i in range(4)]
        result = _run([_chain("d1", steps)])
        assert [f["equation_id"] for f in result["foundations"]] == ["base"]
        for module in _outer(result):
            assert "base" not in {e["equation_id"] for e in module["inputs"]}
            assert {e["equation_id"] for e in module["foundation"]} == {"base"}

    def test_three_consumers_is_not_foundation(self):
        steps = [_step(f"s{i}", "define", ["base", f"x{i}"], [f"y{i}"]) for i in range(3)]
        result = _run([_chain("d1", steps)])
        assert result["foundations"] == []

    def test_foundation_ignores_in_degree(self):
        steps = [_step("s0", "define", ["root"], ["base"])]
        steps += [_step(f"s{i}", "substitute", ["base", f"x{i}"], [f"y{i}"]) for i in range(1, 5)]
        result = _run([_chain("d1", steps)])
        assert [f["equation_id"] for f in result["foundations"]] == ["base"]

    def test_interface_inputs_and_outputs(self):
        result = _run([_chain("d1", [
            _step("s1", "define", ["a"], ["b"]),
            _step("s2", "substitute", ["b"], ["c"]),
        ])])
        modules = _outer(result)
        assert len(modules) == 1
        assert [e["equation_id"] for e in modules[0]["inputs"]] == ["a"]
        # どの step にも消費されない式は論文の結果として外へ出す
        assert [e["equation_id"] for e in modules[0]["outputs"]] == ["c"]

    def test_sink_consumed_equation_is_not_an_output(self):
        result = _run([
            _chain("d1", [_step("s1", "define", ["a"], ["b"])]),
            _chain("sys", [_step("x1", "eliminate", ["b"], ["z"])], chain_type="system_level"),
        ])
        assert _outer(result)[0]["outputs"] == []

    def test_sink_produced_equation_is_not_an_output(self):
        result = _run([
            _chain("d1", [_step("s1", "approximate", ["a"], ["z"])]),
            _chain("sys", [_step("x1", "eliminate", ["a"], ["z"])], chain_type="system_level"),
        ])
        assert _outer(result)[0]["outputs"] == []

    def test_interface_limit_blocks_merge(self):
        # 2つの手順を併合すると外から受ける式が4本（> 3）になる → 併合しない
        result = _run([_chain("d1", [
            _step("s1", "define", ["a1", "a2"], ["b"]),
            _step("s2", "substitute", ["b", "a3", "a4"], ["c"]),
        ])])
        assert len(_outer(result)) == 2

    def test_non_adjacent_steps_are_not_merged(self):
        result = _run([_chain("d1", [
            _step("s1", "define", ["a"], ["b"]),
            _step("s2", "define", ["c"], ["d"]),
        ])])
        assert len(_outer(result)) == 2

    def test_interface_too_wide_is_marked(self):
        result = _run([_chain("d1", [
            _step("s1", "define", ["a1", "a2", "a3", "a4"], ["b"]),
        ])])
        assert _outer(result)[0]["isolated_reason"] == "interface_too_wide"


# ---------------------------------------------------------------------------
# 決定論（同点規則）・循環・外枠と内側
# ---------------------------------------------------------------------------


class TestDeterminismCycleLevels:
    def _tie_chains(self):
        # s2 は s1 とも s3 とも併合でき、どちらも併合後の接点が同じ。
        return [_chain("d1", [
            _step("s1", "define", ["a"], ["b"]),
            _step("s2", "substitute", ["b", "x"], ["c"]),
            _step("s3", "substitute", ["c", "y"], ["d"]),
        ])]

    def test_tie_break_is_deterministic(self):
        first = _run(self._tie_chains())
        second = _run(copy.deepcopy(self._tie_chains()))
        assert first == second

    def test_tie_break_prefers_earliest_member(self):
        # 3手順を1つにまとめると外から受ける式が a,x,y の3本 + 外へ出す d = 4 > 3。
        # 先に併合されるのは出現順の早い s1+s2（接点 a,x,c の3本）。
        result = _run(self._tie_chains())
        assert sorted(_member_refs(result)) == [["d1:s1", "d1:s2"], ["d1:s3"]]

    def test_cycle_steps_are_condensed_and_reported(self):
        result = _run([_chain("d1", [
            _step("s1", "define", ["a", "q"], ["p"]),
            _step("s2", "substitute", ["p"], ["q"]),
            _step("s3", "substitute", ["q"], ["r"]),
        ])])
        assert any(f.startswith(FACT_CYCLE_PREFIX) for f in result["facts"])
        members = _member_refs(result)
        assert any({"d1:s1", "d1:s2"} <= set(refs) for refs in members)

    def test_inner_only_when_outer_splits(self):
        # 外枠は1つ（外から a / 外へ e, f）。s2 が生む c を s3 と s4 の 2 手順が使うので s2 が分岐点
        # = 内側の先頭になり、s1 → s2 の受け渡しで切れる（切ったあとの接点は {s1}=1・{s2,s3,s4}=1）。
        result = _run([_chain("d1", [
            _step("s1", "define", ["a"], ["b"]),
            _step("s2", "substitute", ["b"], ["c"]),
            _step("s3", "substitute", ["c"], ["e"]),
            _step("s4", "substitute", ["c"], ["f"]),
        ])])
        assert len(_outer(result)) == 1
        inner = _inner(result)
        assert sorted(_member_refs(result, "inner")) == [["d1:s1"], ["d1:s2", "d1:s3", "d1:s4"]]
        assert len(inner) == 2
        parent = _outer(result)[0]["module_key"]
        assert all(m["parent_module_key"] == parent for m in inner)

    def test_no_inner_when_outer_does_not_split(self):
        result = _run([_chain("d1", [
            _step("s1", "define", ["a"], ["b"]),
            _step("s2", "substitute", ["b"], ["c"]),
        ])])
        assert len(_outer(result)) == 1
        assert _inner(result) == []


class TestInnerRule:
    """内側 2 段目の規則（設計書 §5.4 / §12.3）を合成データで1つずつ固定する。"""

    def test_straight_chain_without_hub_is_not_split(self):
        # 分岐点も循環も無い一本道は、接点だけでは境目が決まらないので割らない。
        result = _run([_chain("d1", [
            _step("s1", "define", ["a"], ["b"]),
            _step("s2", "substitute", ["b"], ["c"]),
            _step("s3", "substitute", ["c", "x"], ["d"]),
        ])])
        assert len(_outer(result)) == 1
        assert _inner(result) == []

    def test_hub_producer_starts_an_inner_module(self):
        # b を生む s1 は外枠の中の 2 手順（s3, s4）に使われる分岐点。s0 → s1 の受け渡しで切る。
        result = _run([_chain("d1", [
            _step("s0", "define", ["a"], ["z"]),
            _step("s1", "substitute", ["z"], ["b"]),
            _step("s3", "substitute", ["b"], ["c"]),
            _step("s4", "substitute", ["b", "c"], ["d"]),
        ])])
        assert sorted(_member_refs(result, "inner")) == [["d1:s0"], ["d1:s1", "d1:s3", "d1:s4"]]

    def test_cycle_group_starts_an_inner_module(self):
        # s2 ↔ s3 は循環（p と q を互いに定める）。循環のまとまりは内側の先頭になり、手前の s1 と切れる。
        result = _run([_chain("d1", [
            _step("s0", "define", ["a"], ["m"]),
            _step("s1", "substitute", ["m"], ["n"]),
            _step("s2", "define", ["n", "q"], ["p"]),
            _step("s3", "substitute", ["p"], ["q"]),
            _step("s4", "substitute", ["q"], ["r"]),
        ])])
        assert any(f.startswith(FACT_CYCLE_PREFIX) for f in result["facts"])
        assert sorted(_member_refs(result, "inner")) == [
            ["d1:s0", "d1:s1"],
            ["d1:s2", "d1:s3", "d1:s4"],
        ]

    def test_cycle_alone_is_marked(self):
        # 循環のまとまりに何もつながらなければ、その内側モジュールは isolated_reason = cycle。
        result = _run([_chain("d1", [
            _step("s0", "define", ["a"], ["m"]),
            _step("s1", "substitute", ["m"], ["n"]),
            _step("s2", "define", ["n", "q"], ["p"]),
            _step("s3", "substitute", ["p"], ["q"]),
        ])])
        reasons = {tuple(refs): m["isolated_reason"] for refs, m in zip(_member_refs(result, "inner"), _inner(result))}
        assert reasons == {("d1:s0", "d1:s1"): None, ("d1:s2", "d1:s3"): "cycle"}

    def test_wide_cut_is_not_taken(self):
        # s3 は c を s4, s5 に配る分岐点だが、s3 の手前で切ると {s3,s4,s5} が p, q, r の 3 本を
        # 外枠の中で受け取ることになり、内側の接点の上限 2 を超える。境目は採らない（g は共有の基礎）。
        result = _run([_chain("d1", [
            _step("s0", "define", ["g"], ["p"]),
            _step("s1", "define", ["g"], ["q"]),
            _step("s2", "define", ["g"], ["r"]),
            _step("s3", "substitute", ["p", "q", "r"], ["c"]),
            _step("s4", "substitute", ["c", "g"], ["e"]),
            _step("s5", "substitute", ["c"], ["f"]),
        ])])
        assert len(_outer(result)) == 1
        assert _inner(result) == []

    def test_foundation_does_not_glue_inner_modules(self):
        # g は外枠の中の 4 手順に使われる共有の基礎。基礎だけでつながる手順（g を導く s0、g だけから
        # 結果を出す s5）は単独の内側モジュールになり、基礎は内側の接点にも結び付きにも数えない。
        result = _run([_chain("d1", [
            _step("s0", "define", ["a"], ["g"]),
            _step("s1", "substitute", ["g"], ["b"]),
            _step("s2", "substitute", ["b", "g"], ["c"]),
            _step("s3", "substitute", ["c", "g"], ["d"]),
            _step("s4", "substitute", ["c", "d"], ["e"]),
            _step("s5", "approximate", ["g"], ["h"]),
        ])])
        assert len(_outer(result)) == 1
        # c を生む s2 は s3・s4 の 2 手順に使われる分岐点なので、s1 → s2 で切れる
        assert _member_refs(result, "inner") == [["d1:s0"], ["d1:s1"], ["d1:s2", "d1:s3", "d1:s4"], ["d1:s5"]]
        assert all(m["isolated_reason"] is None for m in _inner(result))

    def test_inner_rule_is_deterministic(self, tex_2609):
        first = _build(tex_2609)
        second = _build(copy.deepcopy(tex_2609))
        assert _member_refs(first, "inner") == _member_refs(second, "inner")
        assert [m["module_key"] for m in _inner(first)] == [m["module_key"] for m in _inner(second)]

    def test_module_key_is_deterministic_and_versioned(self, tex_2407):
        first = _build(tex_2407)
        second = _build(copy.deepcopy(tex_2407))
        assert [m["module_key"] for m in first["modules"]] == [m["module_key"] for m in second["modules"]]
        keys = [m["module_key"] for m in first["modules"]]
        assert len(keys) == len(set(keys))
        assert all(key.startswith(RULE_VERSION + ":") for key in keys)

    def test_module_key_depends_on_document(self):
        chains = [_chain("d1", [_step("s1", "define", ["a"], ["b"])])]
        a = build_theory_modules(document_id="doc-a", artifacts=_artifacts(chains), graph_json={})
        b = build_theory_modules(document_id="doc-b", artifacts=_artifacts(chains), graph_json={})
        assert a["modules"][0]["module_key"] != b["modules"][0]["module_key"]


# ---------------------------------------------------------------------------
# 入力非破壊・fail-soft・表示（TM1 / TM8 / TM10）
# ---------------------------------------------------------------------------


class TestPurityAndDisplay:
    @pytest.mark.parametrize(
        "name",
        ["arxiv_2407_01221v2_tex.json", "arxiv_2609_15375v1_tex.json", "arxiv_2609_15375v1_pdf.json"],
    )
    def test_inputs_are_not_mutated(self, name):
        fixture = _load(name)
        before = copy.deepcopy(fixture)
        _build(fixture)
        assert fixture == before

    def test_missing_derivation_artifact(self):
        result = build_theory_modules(document_id="d", artifacts={}, graph_json={})
        assert result["available"] is False
        assert result["facts"] == [FACT_NO_DERIVATIONS]

    @pytest.mark.parametrize("bad", [None, "x", 3, [], {"derivation_chain": "oops"},
                                     {"derivation_chain": {"chains": [None, "x", {"steps": "bad"}]}}])
    def test_malformed_inputs_do_not_raise(self, bad):
        result = build_theory_modules(document_id="d", artifacts=bad, graph_json=bad)
        assert result["available"] is False
        assert isinstance(result["facts"], list) and result["facts"]

    def test_equation_display_label(self):
        assert equation_display_label({"equation_id": "eq_tex_b1", "label": "12"}) == "式 (12)"
        assert equation_display_label(
            {"equation_id": "eq_tex_b1", "semantics": {"summary": "Defines the thing " * 5}}
        ).startswith("番号なし: Defines the thing")
        assert equation_display_label({"equation_id": "eq_tex_b1"}) == "番号なし"
        assert equation_display_label(None) == "番号なし"

    @pytest.mark.parametrize("name", ["arxiv_2407_01221v2_tex.json", "arxiv_2609_15375v1_tex.json"])
    def test_no_internal_ids_in_display_strings(self, name):
        result = _build(_load(name))
        shown: list[str] = []
        for module in result["modules"]:
            shown += [module["label"], module["theory_object"], *module["process_verbs"]]
            for key in ("inputs", "outputs", "foundation"):
                shown += [e["display_label"] for e in module[key]]
            for member in module["members"]:
                shown += [member["operation_label"], *member["input_labels"], *member["output_labels"]]
            shown += [c["name"] for c in module["components_for_comparison"]]
            shown += [c["text"] for c in module["required_claims"]]
        for edge in result["edges"]:
            shown += edge["equation_labels"]
        for sink in result["sinks"]:
            shown += [sink["operation_label"], *sink["input_labels"], *sink["output_labels"]]
        shown += [f["display_label"] for f in result["foundations"]]
        shown += result["facts"]
        offending = [s for s in shown if INTERNAL_ID_RE.search(s or "")]
        assert offending == []

    def test_label_is_verbs_plus_theory_object(self, tex_2407):
        result = _build(tex_2407)
        for module in _outer(result):
            assert module["label"]
            assert module["process_verbs"]
            assert module["label"].startswith("・".join(module["process_verbs"]))
            assert module["theory_object"] in module["label"]

    def test_process_verbs_are_deduplicated_in_order(self):
        result = _run([_chain("d1", [
            _step("s1", "define", ["a"], ["b"]),
            _step("s2", "substitute", ["b"], ["c"]),
            _step("s3", "define", ["c"], ["d"]),
        ])])
        assert _outer(result)[0]["process_verbs"] == ["定義", "代入"]

    def test_theory_object_prefers_specific_atomic_claim(self):
        claims = [
            {"claim_id": "c_sys", "is_atomic": True, "text": "System claim across everything.",
             "equation_ids": ["b", "c", "zz", "yy"]},
            {"claim_id": "c_b", "is_atomic": True, "text": "The result relation holds.", "equation_ids": ["c"]},
        ]
        result = _run([_chain("d1", [
            _step("s1", "define", ["a"], ["b"]),
            _step("s2", "substitute", ["b"], ["c"]),
        ])], claims=claims)
        assert _outer(result)[0]["theory_object"] == "The result relation holds."

    def test_theory_object_falls_back_to_printed_label(self):
        records = [{"equation_id": "c", "label": "7"}]
        result = _run([_chain("d1", [_step("s1", "define", ["a"], ["c"])])], records=records)
        assert _outer(result)[0]["theory_object"] == "式 (7)"

    def test_components_for_comparison_match_outputs(self):
        components = [
            {"name": "Output part", "output_equation_ids": ["c"]},
            {"name": "Unrelated", "output_equation_ids": ["q"]},
            {"name": "comp_003", "output_equation_ids": ["c"]},
        ]
        result = _run([_chain("d1", [_step("s1", "define", ["a"], ["c"])])], components=components)
        assert _outer(result)[0]["components_for_comparison"] == [{"name": "Output part"}]

    def test_required_claims_and_assumptions(self):
        claims = [{"claim_id": "synth_claim_0001", "is_atomic": True,
                   "text": "Equation (eq_x) defines $y$.", "equation_ids": ["eq_x"]}]
        records = [{"equation_id": "eq_x", "label": "3"}]
        result = _run([_chain("d1", [
            _step("s1", "define", ["a"], ["b"], required_claim_ids=["synth_claim_0001"],
                  assumption_ids=["An assumption."]),
        ])], claims=claims, records=records)
        module = _outer(result)[0]
        assert module["required_claims"] == [
            {"claim_id": "synth_claim_0001", "text": "Equation (式 (3)) defines $y$."}
        ]
        assert module["assumption_ids"] == ["An assumption."]

    def test_available_payload_keys(self, tex_2407):
        result = _build(tex_2407)
        assert set(result) == {
            "document_id", "available", "facts", "rule_version", "modules", "edges",
            "sinks", "foundations", "claim_sequence_node_ids",
        }
        assert result["rule_version"] == RULE_VERSION
        module_keys = {
            "module_key", "level", "parent_module_key", "label", "visual_label", "process_verbs", "theory_object",
            "stage_keys", "dominant_stage", "source_backing_status", "inputs", "outputs", "foundation",
            "required_claims", "assumption_ids", "members", "components_for_comparison", "isolated_reason",
        }
        for module in result["modules"]:
            assert set(module) == module_keys
            assert module["dominant_stage"] in module["stage_keys"]
            assert re.fullmatch(r"(source_backed|partially_source_backed|review_required)",
                                module["source_backing_status"])


class TestSynthesizedClaimLabels:
    """理論対象: 合成主張は本文を並べず記号だけを取り出す（§5.6）。ノード用の visual_label は
    $…$ をプレーンテキストに落とす。"""

    def test_symbol_extraction_from_synthesized_templates(self):
        from core.theory_modules.builder import _symbol_of_synthesized

        assert _symbol_of_synthesized("Equation (3) defines $\\Delta\\Sigma(R)$.") == "$\\Delta\\Sigma(R)$"
        assert _symbol_of_synthesized("An equation of this paper defines $C_{ij}$.") == "$C_{ij}$"
        assert _symbol_of_synthesized("In an equation of this paper, $D_1 R$ depends on $D_2 R$, $R$.") == "$D_1 R$"
        assert _symbol_of_synthesized("In equation (12), $\\xi(r)$ depends on $b$.") == "$\\xi(r)$"
        assert _symbol_of_synthesized("The estimator is unbiased at large scales.") == ""

    def test_synthesized_claim_detection(self):
        from core.theory_modules.builder import _is_synthesized_claim

        assert _is_synthesized_claim({"claim_id": "synth_claim_0001", "text": "x"})
        assert _is_synthesized_claim({"claim_id": "c1", "origin": "equation_synthesis", "text": "x"})
        assert _is_synthesized_claim({"claim_id": "c1", "text": "In an equation of this paper, $a$ depends on $b$."})
        assert not _is_synthesized_claim({"claim_id": "claim_span_001_sub01", "text": "The bias is linear."})

    def test_plain_math_minimal_conversion(self):
        from core.theory_modules.builder import plain_math

        assert plain_math("$\\Delta\\Sigma(R)$") == "ΔΣ(R)"
        assert plain_math("$\\Sigma_{\\text{crit}}$") == "Σ_crit"
        assert plain_math("$\\tilde{\\delta}(t,{\\bm{k}})$") == "δ(t,k)"
        assert plain_math("$\\hat{n}\\cdot\\vec{k}$") == "n·k"
        assert plain_math("$M_{200\\mathrm{m}}$") == "M_200m"
        assert plain_math("") == ""

    def test_visual_label_has_no_dollar_and_module_label_prefers_symbols(self):
        fixture = _load("arxiv_2609_15375v1_tex.json")
        result = build_theory_modules(
            document_id=fixture["document_id"], artifacts=fixture["artifacts"], graph_json=fixture["graph_json"]
        )
        outer = [m for m in result["modules"] if m["level"] == "outer"]
        assert outer
        for module in outer:
            assert "$" not in module["visual_label"]
            # 合成主張の定型文（"In an equation of this paper" / "defines"）を理論対象に並べない
            assert "of this paper" not in module["theory_object"]
            assert " defines " not in module["theory_object"]


# ---------------------------------------------------------------------------
# Phase 1: module_key の材料（§13.3）と保存用出力・構造の指紋（§13.4）
# ---------------------------------------------------------------------------


def _records(chains, *, equation_stable_keys=None, document_id="doc-1", graph=None, **kwargs):
    from core.theory_modules import build_theory_module_records

    return build_theory_module_records(
        document_id=document_id,
        artifacts=_artifacts(chains, **kwargs),
        graph_json=graph or {"nodes": [], "edges": []},
        equation_stable_keys=equation_stable_keys,
    )


def _fixture_records(fixture: dict) -> dict:
    from core.theory_modules import build_theory_module_records

    return build_theory_module_records(
        document_id=fixture["document_id"], artifacts=fixture["artifacts"], graph_json=fixture["graph_json"],
    )


class TestModuleKeyMaterial:
    CHAINS = [_chain("d1", [_step("s1", "define_x", ["a"], ["b"]), _step("s2", "substitute_y", ["b"], ["c"])])]

    def test_rule_version_is_m2(self):
        assert RULE_VERSION == "m2"

    def test_deterministic_with_and_without_the_map(self):
        keys = {"b": "k1:bbb", "c": "k1:ccc"}
        with_map = [m["module_key"] for m in _run(self.CHAINS)["modules"]]
        again = [m["module_key"] for m in _run(copy.deepcopy(self.CHAINS))["modules"]]
        assert with_map == again
        mapped = build_theory_modules(
            document_id="doc-1", artifacts=_artifacts(self.CHAINS), graph_json={}, equation_stable_keys=keys,
        )
        mapped_again = build_theory_modules(
            document_id="doc-1", artifacts=_artifacts(self.CHAINS), graph_json={}, equation_stable_keys=dict(keys),
        )
        assert [m["module_key"] for m in mapped["modules"]] == [m["module_key"] for m in mapped_again["modules"]]
        assert all(m["module_key"].startswith("m2:") for m in mapped["modules"])

    def test_material_is_the_equation_stable_key_not_the_equation_id(self):
        """同じ式の stable_key なら equation_id が変わってもキーは同じ（KO2 の内容由来）。"""
        renamed = [_chain("d1", [_step("s1", "define_x", ["a"], ["B"]), _step("s2", "substitute_y", ["B"], ["C"])])]
        first = build_theory_modules(
            document_id="doc-1", artifacts=_artifacts(self.CHAINS), graph_json={},
            equation_stable_keys={"b": "k1:bbb", "c": "k1:ccc"},
        )
        second = build_theory_modules(
            document_id="doc-1", artifacts=_artifacts(renamed), graph_json={},
            equation_stable_keys={"B": "k1:bbb", "C": "k1:ccc"},
        )
        assert [m["module_key"] for m in first["modules"]] == [m["module_key"] for m in second["modules"]]

    def test_equation_without_a_key_falls_back_to_eqid_material(self):
        from core.theory_modules.schema import EQUATION_ID_KEY_PREFIX

        result = _records(self.CHAINS, equation_stable_keys={"b": "k1:bbb"})
        assert result["equations_without_stable_key"] == ["c"]
        record = result["records"][0]
        assert sorted(record["produced_equation_keys"]) == sorted(["k1:bbb", EQUATION_ID_KEY_PREFIX + "c"])

    def test_map_is_not_mutated(self):
        keys = {"b": "k1:bbb"}
        _records(self.CHAINS, equation_stable_keys=keys)
        assert keys == {"b": "k1:bbb"}

    def test_dto_has_no_fact_about_missing_keys(self):
        result = build_theory_modules(
            document_id="doc-1", artifacts=_artifacts(self.CHAINS), graph_json={}, equation_stable_keys={},
        )
        assert not any("stable" in fact or "eqid" in fact for fact in result["facts"])


class TestRecords:
    def test_persistable_flags(self):
        from core.theory_modules.schema import RECORDS_SKIP_NO_DERIVATIONS

        assert _records([])["persistable"] is False
        assert _records([])["skip_reason"] == RECORDS_SKIP_NO_DERIVATIONS
        claims_only = _records([_chain("d1", [_step("s1", "support", ["a"], ["b"])], chain_type="claim_chain")])
        assert claims_only["persistable"] is True and claims_only["records"] == []
        assert claims_only["skip_reason"] == ""

    def test_builder_exception_is_not_persistable(self, monkeypatch):
        from core.theory_modules import builder
        from core.theory_modules.schema import RECORDS_SKIP_BUILD_FAILED

        def _boom(*args, **kwargs):
            raise RuntimeError("x")

        monkeypatch.setattr(builder, "_build", _boom)
        result = builder.build_theory_module_records(document_id="d", artifacts={}, graph_json={})
        assert result["persistable"] is False and result["skip_reason"] == RECORDS_SKIP_BUILD_FAILED

    def test_record_keys_and_members(self):
        chains = [_chain("d1", [
            _step("s1", "define_x", ["a"], ["b"], required_claim_ids=["c1"]),
            _step("s2", "substitute_y", ["b"], ["c"]),
        ])]
        record = _records(chains)["records"][0]
        assert set(record) == {
            "module_key", "level", "parent_module_key", "produced_equation_ids", "produced_equation_keys",
            "label", "visual_label", "theory_object", "process_verbs", "stage_keys", "dominant_stage",
            "source_backing_status", "isolated_reason", "input_equation_ids", "output_equation_ids",
            "foundation_equation_ids", "sink_equation_ids", "required_claim_ids", "assumptions", "members",
            "structure_fingerprint", "identity_eligible", "components_for_comparison",
        }
        assert record["produced_equation_ids"] == ["b", "c"]
        assert record["input_equation_ids"] == ["a"]
        assert record["required_claim_ids"] == ["c1"]
        assert [m["edge_type"] for m in record["members"]] == ["defines", "substitutes"]
        assert [m["step_refs"] for m in record["members"]] == [["d1:s1"], ["d1:s2"]]

    def test_fingerprint_format_and_determinism(self):
        chains = [_chain("d1", [
            _step("s1", "define_x", ["a"], ["b"]),
            _step("s2", "substitute_y", ["b"], ["c"]),
            _step("s3", "substitute_z", ["c"], ["d"], assumption_ids=["仮定"]),
        ])]
        first = _records(chains)["records"][0]["structure_fingerprint"]
        second = _records(copy.deepcopy(chains), document_id="another")["records"][0]["structure_fingerprint"]
        assert first == second == "m2|outer|ops=defines:1,substitutes:2|in=1|out=1|premise=1"

    def test_fingerprint_ignores_equation_ids_and_order_of_multiset(self):
        a = [_chain("d1", [_step("s1", "define_x", ["a"], ["b"]), _step("s2", "substitute_y", ["b"], ["c"])])]
        b = [_chain("d9", [_step("t1", "define_p", ["p"], ["q"]), _step("t2", "substitute_r", ["q"], ["r"])])]
        assert _records(a)["records"][0]["structure_fingerprint"] == _records(b)["records"][0]["structure_fingerprint"]

    def test_identity_eligibility_thresholds(self):
        from core.theory_modules.schema import MODULE_IDENTITY_MIN_MEMBERS, MODULE_IDENTITY_MIN_PROCESS_KINDS

        assert (MODULE_IDENTITY_MIN_MEMBERS, MODULE_IDENTITY_MIN_PROCESS_KINDS) == (3, 2)
        three_two = [_chain("d1", [
            _step("s1", "define_x", ["a"], ["b"]),
            _step("s2", "substitute_y", ["b"], ["c"]),
            _step("s3", "substitute_z", ["c"], ["d"]),
        ])]
        assert _records(three_two)["records"][0]["identity_eligible"] is True
        two = [_chain("d1", [_step("s1", "define_x", ["a"], ["b"]), _step("s2", "substitute_y", ["b"], ["c"])])]
        assert _records(two)["records"][0]["identity_eligible"] is False
        one_kind = [_chain("d1", [
            _step("s1", "substitute_x", ["a"], ["b"]),
            _step("s2", "substitute_y", ["b"], ["c"]),
            _step("s3", "substitute_z", ["c"], ["d"]),
        ])]
        assert _records(one_kind)["records"][0]["identity_eligible"] is False
        generic_second_kind = [_chain("d1", [
            _step("s1", "substitute_x", ["a"], ["b"]),
            _step("s2", "transform", ["b"], ["c"]),
            _step("s3", "substitute_z", ["c"], ["d"]),
        ])]
        assert _records(generic_second_kind)["records"][0]["identity_eligible"] is False

    def test_unclassifiable_steps_are_never_eligible(self, monkeypatch):
        from core.theory_modules import builder
        from core.theory_modules.schema import UNCLASSIFIED_EDGE_TYPE

        monkeypatch.setattr(builder, "_a_layer", lambda: None)
        chains = [_chain("d1", [
            _step("s1", "define_x", ["a"], ["b"]),
            _step("s2", "substitute_y", ["b"], ["c"]),
            _step("s3", "approximate_z", ["c"], ["d"]),
        ])]
        record = _records(chains)["records"][0]
        assert record["identity_eligible"] is False
        assert f"ops={UNCLASSIFIED_EDGE_TYPE}:3" in record["structure_fingerprint"]

    def test_inner_records_are_never_eligible(self, tex_2609):
        records = _fixture_records(tex_2609)["records"]
        assert [r for r in records if r["level"] == "inner"]
        assert not any(r["identity_eligible"] for r in records if r["level"] == "inner")

    def test_fixture_eligibility_matches_the_design_estimate(self, tex_2407, tex_2609):
        """§13.4 の見込み: 2407 は計算本体 1、2609 は 3 が下限を満たし、両論文の指紋は一致しない。"""
        r2407 = [r for r in _fixture_records(tex_2407)["records"] if r["identity_eligible"]]
        r2609 = [r for r in _fixture_records(tex_2609)["records"] if r["identity_eligible"]]
        assert len(r2407) == 1 and len(r2609) == 3
        assert not {r["structure_fingerprint"] for r in r2407} & {r["structure_fingerprint"] for r in r2609}

    def test_records_align_with_dto_modules(self, tex_2407):
        records = _fixture_records(tex_2407)["records"]
        dto = _build(tex_2407)
        assert [r["module_key"] for r in records] == [m["module_key"] for m in dto["modules"]]
        assert [r["parent_module_key"] for r in records] == [m["parent_module_key"] for m in dto["modules"]]
        assert [r["visual_label"] for r in records] == [m["visual_label"] for m in dto["modules"]]


class TestFingerprintNeverInDto:
    @pytest.mark.parametrize("name", [
        "arxiv_2407_01221v2_tex.json", "arxiv_2609_15375v1_tex.json", "arxiv_2609_15375v1_pdf.json",
    ])
    def test_dto_has_no_fingerprint_stable_key_or_edge_type(self, name):
        fixture = _load(name)
        dto = build_theory_modules(
            document_id=fixture["document_id"], artifacts=fixture["artifacts"], graph_json=fixture["graph_json"],
            equation_stable_keys={"eq_tex_b14": "k1:deadbeef"},
        )
        text = json.dumps(dto, ensure_ascii=False)
        for term in ("fingerprint", "structure_fingerprint", "stable_key", "produced_equation_keys",
                     "identity_eligible", "edge_type", "k1:", "eqid:", "ops="):
            assert term not in text, term
