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
        # 循環 b78 ↔ b80 の2手順は同じ外枠（計算本体）に入り、内側では1まとまり（cycle）
        body = max(_outer(result), key=lambda m: len(m["members"]))
        cycle_inner = [m for m in _inner(result) if m["isolated_reason"] == "cycle"]
        assert len(cycle_inner) == 1
        assert cycle_inner[0]["parent_module_key"] == body["module_key"]
        assert len(cycle_inner[0]["members"]) == 2

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
        # 外枠 k=3 では1つにまとまり、内側 k=2 では2つに割れる。
        # 全体: 外から a, x / 外へ d = 3（外枠で1つ）。内側: s1+s2 は a → c の接点 2 で併合、
        # そこに s3 を足すと 3 > 2 なので {s1,s2} と {s3} に割れる。
        result = _run([_chain("d1", [
            _step("s1", "define", ["a"], ["b"]),
            _step("s2", "substitute", ["b"], ["c"]),
            _step("s3", "substitute", ["c", "x"], ["d"]),
        ])])
        assert len(_outer(result)) == 1
        inner = _inner(result)
        assert sorted(_member_refs(result, "inner")) == [["d1:s1", "d1:s2"], ["d1:s3"]]
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
