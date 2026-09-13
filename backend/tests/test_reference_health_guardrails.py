"""参照の健全性のガードレール（知識の転用層 P4-3 / ``knowledge_transfer_design.md`` §6・§11）。

構造的に固定するのは:

1. **KT3**: ``core/reference_health.py`` が FastAPI / ``core.llm`` / embedding を import しない。
2. **KT5**: DB を書かない（``DELETE FROM`` / ``INSERT INTO`` / ``UPDATE`` を発行しない）。
   結果は「解決済みフラグ」ではないので、解決済みを記録する列・テーブルを作らない。
3. **KO5**: 基表 ``theory_claims`` / ``theory_components`` を読まず live ビューを読む。
4. **T-3**: 事実文に数字を書かない・件数キー（``count`` 等）を返さない。
5. パイプライン hook は ``_stage_completed`` 内の best-effort（try/except で握る）であり、
   **新しいステージにしない**（``_PIPELINE_STEPS`` / ``PIPELINE_STAGES`` を増やさない）。
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for path in (str(BACKEND), os.path.join(str(BACKEND), "api"), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
)

CORE_PATH = BACKEND / "core" / "reference_health.py"
ROUTE_PATH = BACKEND / "api" / "routes" / "reference_health.py"
ORCHESTRATOR_PATH = BACKEND / "core" / "document_pipeline" / "orchestrator.py"

CORE_SRC = CORE_PATH.read_text(encoding="utf-8")
ORCHESTRATOR_SRC = ORCHESTRATOR_PATH.read_text(encoding="utf-8")


class TestCorePurity:
    def test_core_does_not_import_fastapi_or_llm(self):
        """KT3: 決定論・LLM 0 回・embedding 0 回（core/ 共通ルール）。"""
        assert_source_does_not_import(
            CORE_SRC,
            ["fastapi", "core.llm", "openai", "services", "routes"],
            context=str(CORE_PATH),
        )

    def test_core_imports_cleanly_without_the_api_package(self):
        import importlib

        module = importlib.import_module("core.reference_health")
        assert callable(module.check_document_references)
        assert callable(module.build_reference_health)


class TestNoWrites:
    def test_core_never_writes(self):
        """KT5: 検査は事実の生成であって状態の更新ではない。"""
        assert_source_forbids(
            CORE_SRC,
            ["DELETE FROM", "INSERT INTO", "UPDATE ", "session.commit", "session.add"],
            context=str(CORE_PATH),
        )

    def test_route_never_writes(self):
        assert_source_forbids(
            ROUTE_PATH.read_text(encoding="utf-8"),
            ["DELETE FROM", "INSERT INTO", "session.commit", "record_review_event"],
            context=str(ROUTE_PATH),
        )


class TestLiveViews:
    def test_core_reads_live_views_not_base_tables(self):
        """KO5: 基表を FROM / JOIN しない（allowlist は persistence / deletion のみ）。"""
        pattern = re.compile(r"\b(?:FROM|JOIN)\s+theory_(?:claims|components)\b(?!_live)")
        assert pattern.search(CORE_SRC) is None
        assert "FROM theory_claims_live" in CORE_SRC
        assert "FROM theory_components_live" in CORE_SRC
        assert "FROM learning_units_live" in CORE_SRC

    def test_knowledge_equations_excludes_superseded_rows(self):
        """live ビューが無い表は superseded_at を明示で外す（同じ意味論を保つ）。"""
        assert "FROM knowledge_equations" in CORE_SRC
        assert "superseded_at IS NULL" in CORE_SRC

    def test_document_scope_is_forced_in_sql(self):
        """すべての SELECT が document_id を uuid で強制する。"""
        for match in re.finditer(r"_(?:CLAIMS|COMPONENTS|EQUATIONS|UNITS|GRAPH)_SQL = \"\"\"(.*?)\"\"\"",
                                 CORE_SRC, re.S):
            assert "CAST(:document_id AS uuid)" in match.group(1)


class TestNoNumbers:
    def test_fact_constants_have_no_digits(self):
        """T-3: 事実文に数字を書かない。"""
        from core.reference_health import (
            FACT_NO_MATERIAL,
            FACT_NOT_CHECKED,
            FACT_OK,
            KIND_FACTS,
        )

        for fact in list(KIND_FACTS.values()) + [FACT_OK, FACT_NO_MATERIAL, FACT_NOT_CHECKED]:
            assert not any(ch.isdigit() for ch in fact), fact

    def test_result_has_no_count_keys(self):
        """件数バッジを作らない（読み手は details の配列長として数えられるだけ）。"""
        from core.reference_health import build_reference_health

        result = build_reference_health(
            claims=[],
            graph_nodes=[{"component_id": "n1", "label": "L", "graph_layer": "main",
                          "linked_claim_ids": ["ghost"]}],
        )
        forbidden = {"count", "counts", "total", "broken_count", "ratio", "score", "confidence"}
        assert set(result) == {"status", "checked_at", "facts", "details"}
        assert not (forbidden & set(result))

    def test_kind_facts_cover_every_detail_kind(self):
        """種別を増やしたら事実文も足す（無言で増えた破断を隠さない）。"""
        from core.reference_health import DETAIL_KINDS, KIND_FACTS

        assert set(DETAIL_KINDS) == set(KIND_FACTS)


class TestPipelineHook:
    def test_hook_lives_inside_stage_completed(self):
        body = extract_function_source(ORCHESTRATOR_SRC, "_stage_completed")
        assert '"reference_health": _reference_health_snapshot(ctx.document_id)' in body

    def test_hook_is_best_effort(self):
        """失敗は握り、pipeline を止めない（「未確認」という事実だけ残す）。"""
        snapshot = extract_function_source(ORCHESTRATOR_SRC, "_reference_health_snapshot")
        tree = ast.parse(snapshot.strip())
        fn = tree.body[0]
        handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
        assert handlers, "reference health hook must swallow failures"
        # 握ったうえで「未確認」という事実を残す（何も書かずに黙らない）。
        assert "unchecked" in snapshot

    def test_hook_is_not_a_new_pipeline_stage(self):
        """``_PIPELINE_STEPS`` / ``PIPELINE_STAGES`` を増やさない。"""
        from core.document_pipeline import orchestrator as orch

        assert "reference_health" not in set(orch.PIPELINE_STAGES)
        assert "reference_health" not in {s.name for s in orch._PIPELINE_STEPS if s.name}
        assert "reference_health" not in set(orch.LLM_STAGE_NAMES)

    def test_v1_does_not_touch_the_export_bundle(self):
        """v1 は束（export_validation.json）へ同梱しない（§6 からの意図的差分）。"""
        assert "export_validation" not in CORE_SRC
        assert "export_validation" not in ROUTE_PATH.read_text(encoding="utf-8")
