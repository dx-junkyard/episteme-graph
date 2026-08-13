"""理解サイクル（Understanding Cycle, UCサイクル）Phase 1 ガードレールテスト。

対象仕様: docs/features/understanding_cycle_design.md §11。

- kind='intention' / 'anchor_mark' が tension/anchor worker・personal_graph 導出・
  問いの軌跡・教員向け集約のいずれにも現れない（構造的除外）
- intention / 軽量アンカーに行削除 API が存在しない（status 遷移のみ・UC6）
- core/cycle/ が FastAPI を import しない
- cycle 系 API が current_user["id"] のみを使う（本人以外の user_id を受け付けない）
- 差分事実文・LEAVE 候補に数値（件数・率）が現れない（UC9）
- discuss 観測基盤の METRIC_EVENT_VOCAB に cycle_ 6語彙が登録されている
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    assert_source_forbids,
    extract_function_source,
)

_CORE_CYCLE_DIR = BACKEND / "core" / "cycle"
_SERVICES_SRC = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")
_CYCLE_ROUTE_SRC = (BACKEND / "api" / "routes" / "cycle.py").read_text(encoding="utf-8")
_LEARNING_ROUTE_SRC = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
_PERSONAL_GRAPH_QUERIES_SRC = (BACKEND / "core" / "personal_graph" / "queries.py").read_text(
    encoding="utf-8"
)
_PERSONAL_GRAPH_DERIVE_SRC = (BACKEND / "core" / "personal_graph" / "derive.py").read_text(
    encoding="utf-8"
)
_TENSION_WORKER_SRC = (BACKEND / "core" / "tension" / "worker.py").read_text(encoding="utf-8")
_STRUCTURE_ANCHOR_WORKER_SRC = (BACKEND / "core" / "structure_anchor" / "worker.py").read_text(
    encoding="utf-8"
)


# ===========================================================================
# 1. kind 語彙の登録・構造的除外
# ===========================================================================


class TestKindRegistrationAndExclusion:
    def test_interest_kinds_include_new_kinds(self):
        from api import services

        assert "intention" in services._INTEREST_KINDS
        assert "anchor_mark" in services._INTEREST_KINDS

    def test_get_interest_traces_excludes_new_kinds(self):
        body = extract_function_source(_SERVICES_SRC, "get_interest_traces")
        assert "'intention'" in body
        assert "'anchor_mark'" in body
        assert re.search(r"kind\s+NOT IN\s*\(\s*'intention'\s*,\s*'anchor_mark'\s*\)", body), (
            "get_interest_traces が intention/anchor_mark を明示除外していない"
        )

    def test_aggregate_interest_dashboard_excludes_new_kinds(self):
        body = extract_function_source(_SERVICES_SRC, "aggregate_interest_dashboard")
        assert "'intention'" in body
        assert "'anchor_mark'" in body
        assert "kind NOT IN" in body

    def test_personal_graph_queries_do_not_reference_new_kinds(self):
        assert_source_forbids(
            _PERSONAL_GRAPH_QUERIES_SRC,
            ["intention", "anchor_mark"],
            context="personal_graph/queries.py",
        )

    def test_personal_graph_derive_does_not_branch_on_new_kinds(self):
        assert_source_forbids(
            _PERSONAL_GRAPH_DERIVE_SRC,
            ["intention", "anchor_mark"],
            context="personal_graph/derive.py",
        )

    def test_tension_worker_does_not_reference_new_kinds(self):
        assert_source_forbids(
            _TENSION_WORKER_SRC, ["intention", "anchor_mark"], context="tension/worker.py"
        )

    def test_structure_anchor_worker_does_not_reference_new_kinds(self):
        assert_source_forbids(
            _STRUCTURE_ANCHOR_WORKER_SRC,
            ["intention", "anchor_mark"],
            context="structure_anchor/worker.py",
        )


# ===========================================================================
# 2. 行削除 API の不在（UC6: status 遷移のみで保持する）
# ===========================================================================


class TestNoDeleteEndpoint:
    def test_no_delete_route_in_cycle_routes(self):
        assert_source_forbids(
            _CYCLE_ROUTE_SRC,
            ["@learning_router.delete", "@router.delete", ".delete("],
            context="routes/cycle.py",
        )

    def test_no_delete_from_in_core_cycle(self):
        assert_module_tree_forbids(_CORE_CYCLE_DIR, ["DELETE FROM"])

    def test_dismiss_uses_status_transition_not_deletion(self):
        body = extract_function_source(_SERVICES_SRC, "dismiss_cycle_intention")
        assert_source_forbids(body, ["DELETE"], context="dismiss_cycle_intention")
        assert "SET status = 'dismissed'" in body


# ===========================================================================
# 3. core/cycle は FastAPI を import しない
# ===========================================================================


class TestCoreCycleDoesNotImportFastAPI:
    def test_core_cycle_tree_forbids_fastapi(self):
        assert_module_tree_does_not_import(_CORE_CYCLE_DIR, ["fastapi"])


# ===========================================================================
# 4. 本人のみ（current_user["id"] のみ使用。fail-closed）
# ===========================================================================


class TestRoutesAreOwnerScoped:
    _ROUTE_FUNCS = (
        "record_cycle_intention_route",
        "dismiss_cycle_intention_route",
        "record_cycle_anchor_route",
        "get_cycle_landing_candidates_route",
    )

    def test_routes_use_current_user_id_only(self):
        for fn in self._ROUTE_FUNCS:
            body = extract_function_source(_CYCLE_ROUTE_SRC, fn)
            assert 'current_user["id"]' in body, f"{fn} が current_user['id'] を使っていない"
            assert "user_id: str" not in body, f"{fn} が任意の user_id 引数を受け付けている"

    def test_cycle_route_file_never_accepts_bare_user_id_param(self):
        assert "user_id" not in _CYCLE_ROUTE_SRC


# ===========================================================================
# 5. 数値を見せない（UC9）— 実際に組み立てられる事実文・候補ラベルを検査する
#    （docstring 中の禁止語彙の説明自体が誤検出になるため、生成物を実行して検査する）
# ===========================================================================


class TestNoNumericCountLanguage:
    _COUNT_PATTERNS = (r"\d+\s*件", r"\d+\s*%", r"\d+\s*パーセント", r"\d+\s*回")

    def _sample_rows(self):
        return [
            {
                "id": f"t{i}",
                "kind": "tension",
                "status": "articulated",
                "payload": {"text": f"引っかかり{i}", "learner_text": f"引っかかり{i}"},
                "created_at": "2026-08-13T00:00:00+00:00",
            }
            for i in range(5)
        ] + [
            {
                "id": "a1",
                "kind": "anchor_mark",
                "status": "open",
                "payload": {
                    "text": "この式の導出",
                    "quick_label": "return_later",
                    "revisit": True,
                    "structure_anchor": {"status": "active"},
                },
                "created_at": "2026-08-13T00:00:00+00:00",
            },
            {
                "id": "q1",
                "kind": "question",
                "status": "open",
                "payload": {
                    "text": "この前提は妥当か",
                    "structure_anchor": {"attribution_source": "confirmed"},
                },
                "created_at": "2026-08-13T00:00:00+00:00",
            },
        ]

    def test_revisit_facts_have_no_count_language(self):
        from core.cycle.derive import build_revisit_facts

        carryover = {"id": "c1", "text": "問い", "created_at": "2026-08-12T00:00:00+00:00"}
        facts = build_revisit_facts(carryover, self._sample_rows())
        assert facts, "facts が空では検査できない"
        for f in facts:
            for pat in self._COUNT_PATTERNS:
                assert not re.search(pat, f), f"count-shaped text {pat!r} in fact: {f}"

    def test_landing_candidates_have_no_numeric_keys_or_count_language(self):
        from core.cycle.derive import build_landing_candidates

        candidates = build_landing_candidates(self._sample_rows())
        assert candidates, "candidates が空では検査できない"
        for c in candidates:
            assert set(c.keys()) == {"trace_id", "kind", "label", "revisit"}
            for pat in self._COUNT_PATTERNS:
                assert not re.search(pat, c["label"]), (
                    f"count-shaped text {pat!r} in candidate label: {c['label']}"
                )

    def test_intention_dto_has_no_numeric_keys(self):
        from core.cycle.derive import build_intention_dto

        dto = build_intention_dto(
            {"id": "c1", "text": "問い", "created_at": "2026-08-12T00:00:00+00:00"}, True
        )
        blob = str(dto)
        for forbidden in ("confidence", "load_score", "score"):
            assert forbidden not in blob


# ===========================================================================
# 6. discuss 観測基盤への相乗り（cycle_ 6語彙）
# ===========================================================================


class TestMetricEventVocab:
    def test_cycle_events_registered(self):
        from core.discuss.observation import METRIC_EVENT_VOCAB

        expected = {
            "cycle_motive_saved",
            "cycle_prediction_saved",
            "cycle_diff_viewed",
            "cycle_carryover_saved",
            "cycle_revisit_answered",
            "cycle_anchor_quick",
        }
        assert expected.issubset(METRIC_EVENT_VOCAB)


# ===========================================================================
# 7. 監査記帳を行わない（指揮官裁定: 本人専用メモ）
# ===========================================================================


class TestNoAuditForCycleTraces:
    def test_record_cycle_intention_does_not_record_review_event(self):
        body = extract_function_source(_SERVICES_SRC, "record_cycle_intention")
        assert "record_review_event" not in body

    def test_dismiss_cycle_intention_does_not_record_review_event(self):
        body = extract_function_source(_SERVICES_SRC, "dismiss_cycle_intention")
        assert "record_review_event" not in body

    def test_record_cycle_anchor_mark_does_not_record_review_event(self):
        body = extract_function_source(_SERVICES_SRC, "record_cycle_anchor_mark")
        assert "record_review_event" not in body

    def test_no_new_audit_entity_constant_introduced(self):
        """新しい AUDIT_ENTITY_* 定数は作らない（既存カタログのみを使う既存機能との対比）。"""
        assert "AUDIT_ENTITY" not in _CYCLE_ROUTE_SRC


# ===========================================================================
# 8. discuss opening への同梱は optional・fail-open（既存キー・シグネチャ不変）
# ===========================================================================


class TestDiscussOpeningIntentionMergeIsFailOpen:
    def test_opening_route_wraps_cycle_lookup_in_try_except(self):
        body = extract_function_source(_LEARNING_ROUTE_SRC, "get_discussion_opening")
        assert "try:" in body and "except Exception" in body
        assert 'result["intention"]' in body

    def test_opening_route_still_uses_get_course_data_gate(self):
        """discuss opening のゲートは get_course_data のまま（既存テスト固定・変更禁止）。"""
        body = extract_function_source(_LEARNING_ROUTE_SRC, "get_discussion_opening")
        assert "get_course_data(" in body


# ===========================================================================
# 9. Phase 2 — 式スケール ELICIT（R層 elicit_mode='regime'/'next_step'）
#    対象仕様: understanding_cycle_design.md §6（前提: §4.4 / §2 不変条項）。
#    詳細な生成ロジック・DIFF/REFLECT のふるまいテストは
#    test_understanding_cycle_regime.py / test_reconstruction_loop.py にある。
#    ここでは構造的な一線（語彙定義・非LLM・非破壊・配信 SQL 不変）のみを固定する。
# ===========================================================================

_RECON_CORE_DIR = BACKEND / "core" / "reconstruction"
_RECON_ROUTE_SRC = (BACKEND / "api" / "routes" / "reconstruction.py").read_text(encoding="utf-8")
_RECON_WORKER_SRC = (_RECON_CORE_DIR / "worker.py").read_text(encoding="utf-8")
_DERIVATION_SOURCE_SRC = (_RECON_CORE_DIR / "derivation_source.py").read_text(encoding="utf-8")


class TestRegimeEliciteModeVocabulary:
    def test_choice_modes_defined_in_schema(self):
        from core.reconstruction.schema import CHOICE_MODES, ELICIT_MODES

        assert "regime" in ELICIT_MODES
        assert "next_step" in ELICIT_MODES
        assert CHOICE_MODES == ("predict", "regime", "next_step")


class TestDerivationSourceIsNonLLMAndFrameworkFree:
    def test_derivation_source_does_not_import_fastapi(self):
        assert_source_forbids(
            _DERIVATION_SOURCE_SRC,
            ["import fastapi", "from fastapi"],
            context="core/reconstruction/derivation_source.py",
        )

    def test_derivation_source_does_not_import_llm_client(self):
        """出題生成は非LLM・決定論（LLM クライアントに依存しない）。"""
        assert_source_forbids(
            _DERIVATION_SOURCE_SRC,
            ["ReconstructionLLMClient", "llm_client", "openai"],
            context="core/reconstruction/derivation_source.py",
        )

    def test_no_delete_from_in_derivation_source(self):
        assert_source_forbids(
            _DERIVATION_SOURCE_SRC, ["DELETE FROM"], context="core/reconstruction/derivation_source.py"
        )

    def test_regime_operations_excludes_identity_transforms(self):
        """恒等変形・定義・汎用変換には出題しない（generic operation 非出題の裁定）。"""
        from core.reconstruction.derivation_source import REGIME_OPERATIONS

        for op in ("substitute", "transform", "define", "relate", "apply_definition", "apply_criterion"):
            assert op not in REGIME_OPERATIONS


class TestWorkerIdempotentSqlIncludesDerivationModes:
    """§Phase2 裁定: symbol と同様、regime/next_step も predict/restate オーサリング対象の
    冪等 SQL・累積上限 SQL から除外する（NOT IN 形への変更）。
    """

    def test_fetch_authorable_claims_excludes_derivation_modes(self):
        body = extract_function_source(_RECON_WORKER_SRC, "_fetch_authorable_claims")
        assert "elicit_mode NOT IN ('symbol', 'regime', 'next_step')" in body
        assert "c.support_status = :backed" in body
        assert "c.review_status = ANY(:approved)" in body

    def test_document_cap_query_excludes_derivation_modes(self):
        body = extract_function_source(_RECON_WORKER_SRC, "_run_llm_item_authoring_for_document")
        assert "elicit_mode NOT IN ('symbol', 'regime', 'next_step')" in body

    def test_derivation_authoring_uses_system_author_not_llm(self):
        body = extract_function_source(_RECON_WORKER_SRC, "_persist_derivation_item")
        assert "'system'" in body
        assert "'llm'" not in body

    def test_derivation_authoring_is_not_cost_gated(self):
        """CostGate（LLM 日次上限）は通さない（symbol と同じ扱い）。"""
        body = extract_function_source(_RECON_WORKER_SRC, "run_derivation_item_authoring_for_document")
        assert "_check_and_count_llm_call" not in body

    def test_run_item_authoring_calls_derivation_authoring(self):
        body = extract_function_source(_RECON_WORKER_SRC, "run_item_authoring_for_document")
        assert "run_derivation_item_authoring_for_document" in body


class TestDeliveryQueryLiteralsPreserved:
    """既存ガードレール（test_reconstruction_guardrails.py）が固定する配信 SQL の
    リテラルを Phase 2 実装後も維持する（本ファイルからも独立に確認する）。
    """

    def test_symbol_exclusion_and_return_shape_preserved(self):
        block = re.search(
            r"def get_next_item[\s\S]+?return \{\"item\": item_builder", _RECON_ROUTE_SRC
        )
        assert block is not None
        body = block.group(0)
        assert "i.elicit_mode <> 'symbol'" in body
        assert "c.support_status = :backed" in body
        assert "c.review_status = ANY(:approved)" in body

    def test_continuity_avoidance_reorders_but_does_not_exclude(self):
        """連続回避は ORDER BY の並べ替えのみ（WHERE で regime/next_step を除外しない）。"""
        block = re.search(
            r"def get_next_item[\s\S]+?return \{\"item\": item_builder", _RECON_ROUTE_SRC
        )
        assert block is not None
        body = block.group(0)
        assert "CASE WHEN i.elicit_mode IN ('regime', 'next_step') THEN 1 ELSE 0 END" in body
        assert "elicit_mode NOT IN ('regime', 'next_step')" not in body
