"""参照の健全性の「欠落」検査（2026-09-19 実測の是正）。

実測（12 論文）では ``core/reference_health.py`` が 12/12 本で ``status: ok`` /
「参照の切れはありません。」を返す一方、同じ run は ``document_completeness`` が
11/12 本で ``complete=false``、8/11 本は要素の説明が日次上限で 0 件、1 本は学ぶ単位が
0 件だった。**参照の破断しか見ていないので「壊れていない」と読める**のが原因。

ここで固定するのは:

1. 追加した 4 種の参照検査（ノード→式 / 辺→導出ステップ / 詳細ノード→親 / DSL 辺→根拠）。
2. 導出ステップの ID は裸（``step_001``）と合成（``{derivation_id}:{step_id}``）の
   どちらでも解決する（綴りの違いを「切れ」と言わない）。
3. 存在の検査（層が空 / 段階の打ち切り / 段階の未実行 / 取り込みの完全性）は
   ``status`` を ``broken`` にせず ``facts`` に併記される。
4. ``FACT_OK`` の主語が「検査した参照」であること（「この教材は健全」と読ませない）。
5. 事実文に数字が入らない（T-3）・存在の検査の入力が無ければ何も言わない（未検査 ≠ 空）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for path in (str(BACKEND), os.path.join(str(BACKEND), "api"), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core import coverage_facts  # noqa: E402
from core.reference_health import (  # noqa: E402
    DETAIL_KINDS,
    FACT_DETACHED_DETAIL,
    FACT_OK,
    GAP_DETACHED_DETAIL,
    GAP_KINDS,
    KIND_FACTS,
    STATUS_BROKEN,
    STATUS_OK,
    build_reference_health,
    check_document_references,
)

CLAIM_UUID = "11111111-1111-4111-8111-111111111111"


def _claim(**over):
    row = {
        "id": CLAIM_UUID,
        "agent_claim_id": "span_0001",
        "source_scope": {"legacy_ids": ["blk_3:span_0001"]},
        "chunk_id": "33333333-3333-4333-8333-333333333333",
        "text": "観測量は温度に比例する。",
    }
    row.update(over)
    return row


def _equation(**over):
    row = {
        "id": "44444444-4444-4444-8444-444444444444",
        "agent_equation_id": "eq_2_7",
        "label": "(7)",
    }
    row.update(over)
    return row


def _derivation(**over):
    row = {
        "id": "66666666-6666-4666-8666-666666666666",
        "agent_derivation_id": "derivation_claim_0001",
        "agent_step_id": "derivation_claim_0001:step_001",
        "stable_key": "k1:abcdef",
    }
    row.update(over)
    return row


def _main_node(**over):
    node = {
        "component_id": "theory_op_0001",
        "label": "Theory basis",
        "graph_layer": "main",
    }
    node.update(over)
    return node


def _detail_node(**over):
    node = {
        "component_id": "eq_op_0001",
        "label": "Linearize field equation",
        "graph_layer": "equation_detail",
        "parent_component_id": "theory_op_0001",
    }
    node.update(over)
    return node


def _edge(**over):
    edge = {
        "edge_id": "theory_edge_0001",
        "relation": "derives",
        "evidence": {"evidence_derivation_ids": ["step_001"]},
    }
    edge.update(over)
    return edge


def _dsl_edge(**over):
    edge = {
        "from": "n1",
        "to": "n2",
        "verb": "assumes",
        "edge_type": "REQUIRES",
        "evidence_refs": {"claim_ids": [CLAIM_UUID], "equation_ids": ["eq_2_7"]},
    }
    edge.update(over)
    return edge


# ---------------------------------------------------------------------------
# 1. 追加した参照検査
# ---------------------------------------------------------------------------


class TestNewReferenceChecks:
    def test_every_detail_kind_has_a_fact(self):
        assert set(DETAIL_KINDS) == set(KIND_FACTS)
        for kind in (
            "graph_node_equation",
            "graph_edge_derivation",
            "dsl_edge_evidence",
        ):
            assert kind in DETAIL_KINDS
        # 吊られていない式の詳細ノードは破断ではなく欠落（R-5）。
        assert "detail_node_parent" not in DETAIL_KINDS
        assert GAP_DETACHED_DETAIL == "detail_node_parent"
        assert GAP_DETACHED_DETAIL in GAP_KINDS

    def test_graph_node_equation_break(self):
        result = build_reference_health(
            claims=[_claim()],
            equations=[],
            graph_nodes=[_main_node(linked_equation_ids=["eq_2_7"])],
        )
        assert result["status"] == STATUS_BROKEN
        assert result["details"]["graph_node_equation"] == [
            {"ref": "eq_2_7", "from_label": "Theory basis"}
        ]

    def test_graph_node_equation_resolves_by_agent_id(self):
        result = build_reference_health(
            claims=[_claim()],
            equations=[_equation()],
            graph_nodes=[_main_node(linked_equation_ids=["eq_2_7"])],
        )
        assert result["status"] == STATUS_OK

    def test_edge_derivation_resolves_with_the_bare_step_id(self):
        """グラフの辺は裸の ``step_001``、DB は合成 ``chain:step_001``（KO2 の明示例外）。"""
        result = build_reference_health(
            claims=[_claim()],
            derivations=[_derivation()],
            graph_nodes=[_main_node()],
            graph_edges=[_edge()],
        )
        assert result["status"] == STATUS_OK

    def test_edge_derivation_resolves_with_the_composite_step_id(self):
        result = build_reference_health(
            claims=[_claim()],
            derivations=[_derivation()],
            graph_nodes=[_main_node()],
            graph_edges=[
                _edge(evidence={"evidence_derivation_ids": ["derivation_claim_0001:step_001"]})
            ],
        )
        assert result["status"] == STATUS_OK

    def test_edge_derivation_break_when_the_layer_is_empty(self):
        result = build_reference_health(
            claims=[_claim()],
            derivations=[],
            graph_nodes=[_main_node()],
            graph_edges=[_edge()],
        )
        assert result["status"] == STATUS_BROKEN
        assert result["details"]["graph_edge_derivation"] == [
            {"ref": "step_001", "from_label": "derives"}
        ]

    def test_detail_node_without_a_parent_is_a_gap_not_a_break(self):
        """R-5: 親の無い式の詳細ノードは欠落として報告するが status は ok のまま。

        旧 run のグラフ（親付けが claim 交差に広がる前）を一斉に broken へ振らない。
        """
        result = build_reference_health(
            claims=[_claim()],
            graph_nodes=[_main_node(), _detail_node(parent_component_id="")],
        )
        assert result["status"] == STATUS_OK
        assert result["details"]["detail_node_parent"] == [
            {"ref": "eq_op_0001", "from_label": "Linearize field equation"}
        ]
        assert FACT_DETACHED_DETAIL in result["facts"]
        assert FACT_OK in result["facts"]

    def test_detail_node_pointing_at_a_missing_parent_is_reported(self):
        result = build_reference_health(
            claims=[_claim()],
            graph_nodes=[_main_node(), _detail_node(parent_component_id="theory_op_9999")],
        )
        assert result["status"] == STATUS_OK
        assert result["details"]["detail_node_parent"]
        assert FACT_DETACHED_DETAIL in result["facts"]

    def test_detail_node_with_a_real_parent_is_fine(self):
        result = build_reference_health(
            claims=[_claim()],
            graph_nodes=[_main_node(), _detail_node()],
        )
        assert result["status"] == STATUS_OK

    def test_dsl_edge_evidence_resolves(self):
        result = build_reference_health(
            claims=[_claim()], equations=[_equation()], dsl_edges=[_dsl_edge()]
        )
        assert result["status"] == STATUS_OK

    def test_dsl_edge_evidence_break(self):
        result = build_reference_health(
            claims=[_claim()],
            equations=[_equation()],
            dsl_edges=[_dsl_edge(evidence_refs={"claim_ids": ["ghost"], "equation_ids": ["eq_x"]})],
        )
        assert result["status"] == STATUS_BROKEN
        refs = {e["ref"] for e in result["details"]["dsl_edge_evidence"]}
        assert refs == {"ghost", "eq_x"}


# ---------------------------------------------------------------------------
# 2. 存在の検査（欠落は破断ではない）
# ---------------------------------------------------------------------------


class TestExistenceChecks:
    def test_ok_fact_states_what_was_checked(self):
        """「参照の切れはありません」だと「この教材は健全」と読める（実測 12/12 本が ok）。"""
        assert FACT_OK == "検査した参照はすべて解決しています。"

    def test_no_gap_inputs_means_no_gap_facts(self):
        """未検査を「問題なし」とも「空」とも言わない。"""
        result = build_reference_health(claims=[_claim()])
        assert result["facts"] == [FACT_OK]
        assert result["details"] == {}

    def test_empty_layers_are_facts_not_breaks(self):
        result = build_reference_health(
            claims=[_claim()],
            layer_presence={
                "graph_main": True,
                "equations": True,
                "derivation_steps": True,
                "learning_units": False,
                "element_explanations": False,
                "figures": True,
            },
        )
        assert result["status"] == STATUS_OK
        assert result["facts"][0] == FACT_OK
        assert coverage_facts.EMPTY_LAYER_FACTS["learning_units"] in result["facts"]
        assert coverage_facts.EMPTY_LAYER_FACTS["element_explanations"] in result["facts"]
        refs = {e["ref"] for e in result["details"]["empty_layers"]}
        assert refs == {"learning_units", "element_explanations"}

    def test_layers_not_in_the_mapping_are_not_declared_empty(self):
        result = build_reference_health(claims=[_claim()], layer_presence={"figures": True})
        assert result["facts"] == [FACT_OK]

    def test_skipped_stage_is_reported(self):
        result = build_reference_health(
            claims=[_claim()],
            stage_outputs={
                "contextual_explanation": {
                    "llm_calls": 0,
                    "saved_candidates": 0,
                    "skipped_by_limit": True,
                    "coverage": {"population": 311, "processed": 0, "truncated": 311,
                                 "reasons": ["max_elements", "skipped_by_limit"]},
                }
            },
        )
        assert result["status"] == STATUS_OK
        assert "「要素の二層説明の生成」の段階は実行されていません。" in result["facts"]
        # 上限で実行されなかった段階を「打ち切り」としても二重に言わない。
        assert coverage_facts.GAP_TRUNCATED not in result["details"]
        assert [e["ref"] for e in result["details"][coverage_facts.GAP_SKIPPED]] == [
            "contextual_explanation"
        ]

    def test_truncated_stage_is_reported(self):
        result = build_reference_health(
            claims=[_claim()],
            stage_outputs={
                "equation_semantics": {
                    "coverage": {"population": 120, "processed": 64, "truncated": 56,
                                 "reasons": ["max_equations"]},
                }
            },
        )
        assert result["status"] == STATUS_OK
        assert "「数式の意味付け」の段階で、入力の一部を処理していない記録があります。" in result["facts"]

    def test_teacher_chosen_skip_is_not_a_gap(self):
        """図の解析はオプトイン。既定の off を毎回「欠けている」と言うと誤報になる。"""
        result = build_reference_health(
            claims=[_claim()],
            stage_outputs={
                "apparatus_semantics": {
                    "skipped_by_option": True,
                    "coverage": {"population": 19, "processed": 0, "truncated": 19,
                                 "reasons": ["skipped_by_option"]},
                }
            },
        )
        assert result["facts"] == [FACT_OK]
        assert result["details"] == {}

    def test_completeness_reasons_are_reported(self):
        result = build_reference_health(
            claims=[_claim()],
            completeness={
                "complete": False,
                "review_reasons": ["structure_page_coverage_low", "equation_label_discontinuity"],
            },
        )
        assert result["status"] == STATUS_OK
        assert (
            coverage_facts.COMPLETENESS_REASON_FACTS["structure_page_coverage_low"]
            in result["facts"]
        )
        assert len(result["details"]["completeness_reasons"]) == 2

    def test_complete_document_reports_nothing(self):
        result = build_reference_health(
            claims=[_claim()], completeness={"complete": True, "review_reasons": []}
        )
        assert result["facts"] == [FACT_OK]

    def test_unknown_completeness_reason_still_speaks(self):
        result = build_reference_health(
            claims=[_claim()], completeness={"complete": False, "review_reasons": ["brand_new"]}
        )
        assert len(result["facts"]) == 2

    def test_gaps_do_not_add_top_level_keys(self):
        result = build_reference_health(
            claims=[_claim()],
            layer_presence={"figures": False},
            stage_outputs={"contextual_explanation": {"skipped_by_limit": True}},
            completeness={"complete": False, "review_reasons": ["ingest_incomplete"]},
        )
        assert set(result) == {"status", "checked_at", "facts", "details"}
        assert result["status"] == STATUS_OK

    def test_no_fact_contains_a_digit(self):
        result = build_reference_health(
            claims=[_claim(chunk_id=None, origin="span")],
            layer_presence={key: False for key in coverage_facts.LAYER_KEYS},
            stage_outputs={
                "contextual_explanation": {"skipped_by_limit": True},
                "equation_semantics": {"coverage": {"truncated": 56, "reasons": ["max_equations"]}},
            },
            completeness={"complete": False, "review_reasons": list(
                coverage_facts.COMPLETENESS_REASON_FACTS
            )},
        )
        for fact in result["facts"]:
            assert not any(ch.isdigit() for ch in fact), fact

    def test_gap_kinds_are_disjoint_from_detail_kinds(self):
        """欠落は破断の種別と混ざらない（``status`` の意味が壊れない）。"""
        assert not (set(GAP_KINDS) & set(DETAIL_KINDS))


# ---------------------------------------------------------------------------
# 3. DB 読み部（fake session）
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, *, by_table=None, failing=()):
        self.by_table = by_table or {}
        self.failing = tuple(failing)
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        for token in self.failing:
            if token in sql:
                raise RuntimeError("relation does not exist")
        for table, rows in self.by_table.items():
            if table in sql:
                return _FakeResult(rows)
        return _FakeResult([])


_RUN = {
    "run_id": "run-1",
    "status": "completed",
    "stage_outputs": {
        "contextual_explanation": {"skipped_by_limit": True},
        "_artifacts": {
            "document_completeness": {
                "complete": False,
                "review_reasons": ["structure_page_coverage_low"],
            }
        },
    },
}


class TestDbLayer:
    def _session(self, **over):
        by_table = {
            "theory_claims_live": [_claim()],
            "knowledge_equations": [_equation()],
            "knowledge_derivation_steps": [_derivation()],
            "theory_component_graphs": [
                (
                    {
                        "nodes": [_main_node(), _detail_node()],
                        "edges": [_edge()],
                        "dsl": {"edges": [_dsl_edge()]},
                    },
                )
            ],
            "document_figures": [{"figures": True, "element_explanations": False}],
        }
        by_table.update(over)
        return _FakeSession(by_table=by_table)

    def _stub_runs(self, monkeypatch, *, adopted=_RUN, latest=None):
        """run 選択は persistence の正本（C-8）。ここではその戻り値だけを差し替える。"""
        from core.document_pipeline import persistence

        def _resolve(session, document_ids, *, policy="adopted"):
            entry = adopted if policy == "adopted" else (latest if latest is not None else adopted)
            return {document_ids[0]: entry} if entry else {}

        monkeypatch.setattr(persistence, "resolve_artifact_runs", _resolve)

    def test_full_read_collects_gaps(self, monkeypatch):
        self._stub_runs(monkeypatch)
        session = self._session()
        result = check_document_references(session, "doc-uuid")
        assert result["status"] == STATUS_OK
        assert "「要素の二層説明の生成」の段階は実行されていません。" in result["facts"]
        assert (
            coverage_facts.COMPLETENESS_REASON_FACTS["structure_page_coverage_low"]
            in result["facts"]
        )
        assert coverage_facts.EMPTY_LAYER_FACTS["element_explanations"] in result["facts"]
        # 学ぶ単位は 0 件（fake に行が無い）→ 空層として事実になる
        assert coverage_facts.EMPTY_LAYER_FACTS["learning_units"] in result["facts"]

    def test_running_latest_run_wins_over_the_adopted_pointer(self, monkeypatch):
        """パイプライン完了直前のスナップショットは、いま走っている run を説明する。"""
        running = {
            "run_id": "run-2",
            "status": "running",
            "stage_outputs": {"equation_semantics": {
                "coverage": {"truncated": 56, "reasons": ["max_equations"]}
            }},
        }
        self._stub_runs(monkeypatch, adopted=_RUN, latest=running)
        result = check_document_references(self._session(), "doc-uuid")
        assert "「数式の意味付け」の段階で、入力の一部を処理していない記録があります。" in result["facts"]
        assert "「要素の二層説明の生成」の段階は実行されていません。" not in result["facts"]

    def test_every_statement_scopes_the_document(self, monkeypatch):
        self._stub_runs(monkeypatch)
        session = self._session()
        check_document_references(session, "doc-uuid")
        for sql in session.statements:
            assert "CAST(:document_id AS uuid)" in sql

    def test_never_writes(self, monkeypatch):
        self._stub_runs(monkeypatch)
        session = self._session()
        check_document_references(session, "doc-uuid")
        joined = " ".join(session.statements).upper()
        for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
            assert verb not in joined

    def test_unreadable_run_context_does_not_fail_the_check(self):
        """run 選択が読めない環境（fake session）でも参照の検査は成立する。"""
        session = self._session()
        result = check_document_references(session, "doc-uuid")
        assert result["status"] == STATUS_OK
        assert "「要素の二層説明の生成」の段階は実行されていません。" not in result["facts"]

    def test_legacy_recorded_snapshot_is_not_served_as_is(self):
        """旧版の「参照の切れはありません。」を保存済みの事実として返さない。

        旧スナップショットは**参照の破断しか見ていない**結果なので、そのまま返すと
        新しい検査が入ったあとも「壊れていません」と読める。保存が無いのと同じ扱いに
        して、呼び出し側（照会 API）にその場で検査し直させる。
        """
        from core.reference_health import LEGACY_FACT_OK, load_recorded_reference_health

        class _RunSession:
            def __init__(self, facts):
                self.facts = facts

            def execute(self, statement, params=None):
                payload = {"status": "ok", "checked_at": "t", "facts": self.facts, "details": {}}

                class _R:
                    @staticmethod
                    def fetchone():
                        return (payload, "run-1")

                return _R()

        assert load_recorded_reference_health(_RunSession([LEGACY_FACT_OK]), "doc") is None
        fresh = load_recorded_reference_health(_RunSession([FACT_OK]), "doc")
        assert fresh is not None and fresh["source"] == "recorded"

    def test_unreadable_derivations_do_not_become_broken_references(self, monkeypatch):
        """読めない層を「切れている」と言い換えない（未確認は未確認）。"""
        self._stub_runs(monkeypatch)
        session = self._session()
        session.failing = ("knowledge_derivation_steps",)
        result = check_document_references(session, "doc-uuid")
        assert "graph_edge_derivation" not in result["details"]
        refs = {e["ref"] for e in result["details"].get("empty_layers", [])}
        assert "derivation_steps" not in refs


# ---------------------------------------------------------------------------
# 4. 語彙モジュール（core/coverage_facts.py）のガードレール
# ---------------------------------------------------------------------------


class TestCoverageFactsVocabulary:
    _SRC = (BACKEND / "core" / "coverage_facts.py").read_text(encoding="utf-8")
    _PRESSURE = ("！", "今すぐ", "急いで", "必ず", "早く", "至急", "してください")

    def test_module_is_pure(self):
        from tests.guardrail_helpers import assert_source_does_not_import

        assert_source_does_not_import(
            self._SRC,
            ["fastapi", "sqlalchemy", "core.postgres", "openai", "services", "routes"],
            context="core/coverage_facts.py",
        )

    def test_no_fact_contains_a_digit(self):
        facts = (
            list(coverage_facts.EMPTY_LAYER_FACTS.values())
            + list(coverage_facts.COMPLETENESS_REASON_FACTS.values())
            + [coverage_facts.truncated_stage_fact(s) for s in coverage_facts.EXTRA_STAGE_LABELS]
            + [coverage_facts.skipped_stage_fact(s) for s in coverage_facts.EXTRA_STAGE_LABELS]
        )
        for fact in facts:
            assert not any(ch.isdigit() for ch in fact), fact

    def test_no_pressure_vocabulary(self):
        from tests.guardrail_helpers import assert_source_forbids

        assert_source_forbids(self._SRC, self._PRESSURE, context="core/coverage_facts.py")

    def test_every_layer_key_has_a_fact_and_a_label(self):
        for layer in coverage_facts.LAYER_KEYS:
            assert coverage_facts.EMPTY_LAYER_FACTS[layer]
            assert coverage_facts.LAYER_LABELS[layer]

    def test_stage_label_prefers_the_existing_canon(self):
        """訳語表を増やさない: LLM 段階は llm_policy の表、無い段階だけを補う。"""
        from core import llm_policy

        assert coverage_facts.stage_label("contextual_explanation") == (
            llm_policy.PIPELINE_STAGE_LABELS["contextual_explanation"]
        )
        assert not (
            set(coverage_facts.EXTRA_STAGE_LABELS) & set(llm_policy.PIPELINE_STAGE_LABELS)
        )
        assert coverage_facts.stage_label("brand_new_stage") == "brand_new_stage"

    def test_completeness_vocabulary_covers_the_pipeline_reasons(self):
        """``completeness.py`` が積む review_reasons を全部訳せている（黙って一般形に落ちない）。"""
        import re

        src = (
            BACKEND / "core" / "document_pipeline" / "completeness.py"
        ).read_text(encoding="utf-8")
        reasons = set(re.findall(r"review_reasons\.append\(\"([a-z_]+)\"\)", src))
        assert reasons, "completeness.py の review_reasons 抽出に失敗（走査の健全性）"
        missing = reasons - set(coverage_facts.COMPLETENESS_REASON_FACTS)
        assert missing == set(), f"事実文が無い完全性の理由: {sorted(missing)}"
