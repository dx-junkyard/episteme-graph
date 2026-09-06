"""グラフの論文層 — ガードレール（設計 §7）。

不変条項 PL1〜PL8 のうち、構造で守れるものを機械検査する:

- core が fastapi / routes / sqlalchemy / core.llm / openai を import しない。
- core に SQL 文（DELETE / INSERT / UPDATE）が無い（読み時導出・保存しない）。
- DTO に ``FORBIDDEN_KEYS``（confidence / weight / candidate_score /
  qualification_reason）が再帰的に現れない（PL4）。
- 入力 dict を mutate しない（PL1）。
- display_label に内部 ID が出ない（PL7）。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_forbids,
)
from tests.test_graph_paper_layer_core import _build_full, _full_case  # noqa: E402

from core.graph_paper_layer import build_paper_layer  # noqa: E402
from core.graph_paper_layer import schema as pl_schema  # noqa: E402

CORE_DIR = BACKEND / "core" / "graph_paper_layer"


def _iter_keys(payload):
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield str(key)
            yield from _iter_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_keys(item)


def _iter_strings(payload):
    if isinstance(payload, dict):
        for value in payload.values():
            yield from _iter_strings(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_strings(item)
    elif isinstance(payload, str):
        yield payload


class TestCoreIsPure:
    def test_core_does_not_import_frameworks_db_or_llm(self):
        assert_module_tree_does_not_import(
            CORE_DIR,
            ["fastapi", "routes", "sqlalchemy", "core.llm", "openai", "services"],
        )

    def test_core_contains_no_sql_statements(self):
        for path in sorted(CORE_DIR.rglob("*.py")):
            assert_source_forbids(
                path.read_text(encoding="utf-8"),
                ["DELETE FROM", "INSERT INTO", "UPDATE ", "SELECT ", "sa_text", "get_session"],
                context=str(path),
            )

    def test_core_is_importable_without_the_api_package(self):
        """core だけを import 対象にしても解決できる（route 層に依存しない）。"""
        import importlib

        module = importlib.import_module("core.graph_paper_layer.builder")
        assert callable(module.build_paper_layer)

    def test_module_exports_the_pure_entry_point(self):
        import core.graph_paper_layer as package

        assert package.__all__ == ["build_paper_layer"]
        assert package.build_paper_layer is build_paper_layer


class TestNoNumbersLeak:
    def test_forbidden_keys_are_absent_from_a_full_fixture(self):
        keys = set(_iter_keys(_build_full()))
        leaked = [key for key in pl_schema.FORBIDDEN_KEYS if key in keys]
        assert leaked == [], f"forbidden keys leaked into the DTO: {leaked}"

    def test_forbidden_keys_are_absent_even_when_artifacts_carry_them(self):
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        # artifact 側に生数値を足しても DTO には出ない。
        artifacts["component_assembly"]["components"][0]["confidence"] = 0.99
        artifacts["equation_semantics"]["equations"][0]["reconstruction"]["confidence"] = 0.5
        artifacts["derivation_chain"]["chains"][0]["steps"][0]["confidence"] = 0.11
        artifacts["figure_table_semantics"]["figures"][0]["weight"] = 3
        out = build_paper_layer(
            graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
        )
        keys = set(_iter_keys(out))
        assert not (set(pl_schema.FORBIDDEN_KEYS) & keys)

    def test_llm_reason_is_not_projected_for_claims_evidence_or_figures(self):
        out = _build_full()
        for node in out["nodes"].values():
            for bucket in ("claims", "evidence", "figures", "tables", "symbols", "equations"):
                for item in node[bucket]:
                    assert "reason" not in item
        # 導出ステップの reason（非LLM の決定論的理由文）は残す。
        step = out["nodes"]["eq_op_0001"]["derivations"][0]["steps"][0]
        assert step["reason"] == "expand to first order"


class TestNoInternalIdsInLabels:
    def test_display_labels_never_contain_internal_ids(self):
        out = _build_full()
        for node in out["nodes"].values():
            for item in node["equations"]:
                for prefix in pl_schema.INTERNAL_ID_PREFIXES:
                    assert prefix not in item["display_label"]
            for item in node["figures"] + node["tables"]:
                for prefix in pl_schema.INTERNAL_ID_PREFIXES:
                    assert prefix not in item["display_label"]

    def test_facts_are_plain_japanese_sentences_without_ids(self):
        out = build_paper_layer({"nodes": []}, {})
        for fact in out["facts"]:
            assert fact.endswith("。")
            for prefix in pl_schema.INTERNAL_ID_PREFIXES:
                assert prefix not in fact


class TestReadOnly:
    def test_inputs_are_not_mutated(self):
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        snapshot = copy.deepcopy((graph, artifacts, figure_rows, explanation_rows))
        build_paper_layer(graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows)
        assert (graph, artifacts, figure_rows, explanation_rows) == snapshot

    def test_output_does_not_alias_input_containers(self):
        """DTO の入れ子が入力オブジェクトを共有していない（後段の書き換えが漏れない）。"""
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        out = build_paper_layer(
            graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
        )
        out["nodes"]["eq_op_0001"]["equations"][0]["display_label"] = "MUTATED"
        out["paper"]["sections"][0]["title"] = "MUTATED"
        assert artifacts["equation_semantics"]["equations"][0]["label"] == "12"
        assert artifacts["document_structure"]["sections"][0]["title"] == "Introduction"

    def test_no_write_verbs_in_the_public_surface(self):
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(CORE_DIR.rglob("*.py"))
        )
        assert_source_forbids(
            source, ["def save_", "def store_", "def persist_", "def delete_"], context="graph_paper_layer"
        )


class TestVocabularyIsDeclared:
    def test_equation_roles_match_the_dto_vocabulary(self):
        assert pl_schema.EQUATION_ROLES == (
            "input", "intermediate", "output", "definition", "constraint", "linked",
        )
        assert {role for role, _ in pl_schema.EQUATION_ROLE_NODE_KEYS} == set(pl_schema.EQUATION_ROLES)

    def test_symbol_roles_match_the_dto_vocabulary(self):
        assert pl_schema.SYMBOL_ROLES == ("eliminated", "retained")
        assert {role for role, _ in pl_schema.SYMBOL_ROLE_NODE_KEYS} == set(pl_schema.SYMBOL_ROLES)

    def test_snippet_limit_matches_the_reference_index(self):
        assert pl_schema.TEXT_SNIPPET_MAX == 200

    def test_every_missing_artifact_fact_is_unique(self):
        facts = [fact for _stage, _key, fact in pl_schema.MISSING_ARTIFACT_FACTS]
        assert len(set(facts)) == len(facts)

    def test_emitted_role_values_stay_inside_the_vocabulary(self):
        out = _build_full()
        for node in out["nodes"].values():
            for item in node["equations"]:
                assert item["role"] in pl_schema.EQUATION_ROLES
            for item in node["symbols"]:
                assert item["role"] in pl_schema.SYMBOL_ROLES

    def test_no_english_debug_text_leaks_into_facts(self):
        out = build_paper_layer({"nodes": []}, {})
        for text in _iter_strings({"facts": out["facts"]}):
            assert "Traceback" not in text and "Error" not in text
