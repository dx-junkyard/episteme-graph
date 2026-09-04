"""確定文脈の記帳 — 構造的ガードレール。

正本: ``docs/features/decision_context_design.md``（DC1〜DC4）。
上位の根拠は ``docs/vision.md`` §4 改訂原則1（2026-09-04）。

検査するのは「一括確定が確定文脈**なしに**記帳できない」こと（DC1）と、プリミティブの
純粋性・語彙の固定・提示/適用の分離（DC2）・代替必須（DC3）・来歴申告の隔離（DC4）。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import decision_context as dc  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
)

CORE_SRC = (BACKEND / "core" / "decision_context.py").read_text(encoding="utf-8")
LANDSCAPE_SRC = (BACKEND / "api" / "routes" / "landscape.py").read_text(encoding="utf-8")
EXPLANATION_SRC = (
    BACKEND / "api" / "routes" / "element_explanations.py"
).read_text(encoding="utf-8")
RELEASE_JS = (
    ROOT / "frontend" / "public" / "js" / "admin-release-review.js"
).read_text(encoding="utf-8")


class TestCoreIsPure:
    def test_core_does_not_import_web_or_db_or_llm(self):
        assert_source_does_not_import(
            CORE_SRC,
            ["fastapi", "sqlalchemy", "pydantic", "openai"],
            context="core/decision_context.py",
        )

    def test_core_has_no_sql_or_delete(self):
        assert_source_forbids(
            CORE_SRC,
            ["DELETE FROM", "session.execute", "sa_text"],
            context="core/decision_context.py",
        )

    def test_module_docstring_declares_invariants(self):
        doc = dc.__doc__ or ""
        for term in ("DC1", "DC2", "DC3", "DC4"):
            assert term in doc
        assert "test_decision_context_guardrails.py" in doc


class TestVocabulary:
    def test_key_and_basis_constants_are_fixed(self):
        assert dc.DECISION_CONTEXT_KEY == "decision_context"
        assert dc.BASIS_RELEASE_REVIEW_PLACEMENTS == "release_review.placements"
        assert dc.BASIS_EXPLANATION_REVIEW_BULK == "explanation_review.bulk"

    def test_alternative_vocabulary_is_fixed(self):
        assert dc.ALTERNATIVES == (
            "deselect",
            "dismiss",
            "edit",
            "reconsider",
            "reject",
            "skip_step",
        )
        assert dc.PRESENTED_IDS_MAX == 200

    def test_exports_are_sorted(self):
        assert dc.__all__ == sorted(dc.__all__)


class TestDeclineIsDerived:
    def test_decline_possible_is_not_a_parameter(self):
        """DC3: 「断れなかった」を申告できる口を作らない。"""
        params = inspect.signature(dc.build_decision_context).parameters
        assert "decline_possible" not in params
        # 呼び出し側が上書きできないよう、キーワード専用引数だけで構成する。
        assert all(
            p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values()
        )

    def test_presented_matches_applied_is_not_a_parameter(self):
        """DC2: 一致は導出。呼び出し側が「一致した」と申告できない。"""
        assert "presented_matches_applied" not in inspect.signature(
            dc.build_decision_context
        ).parameters


class TestBulkRoutesRecordContext:
    """DC1: 一括確定の記帳が確定文脈を必ず含む。"""

    def test_release_review_accept_builds_and_attaches_context(self):
        src = extract_function_source(
            LANDSCAPE_SRC, "accept_course_landscape_placements"
        )
        assert "decision_context.build_decision_context(" in src
        assert "decision_context.attach_decision_context(" in src
        assert "BASIS_RELEASE_REVIEW_PLACEMENTS" in src
        # 提示集合はサーバが更新前に取り直す（クライアント申告に依存しない）。
        assert "list_for_documents" in src
        assert "STATUS_INFERRED" in src

    def test_explanation_bulk_review_builds_and_attaches_context(self):
        src = extract_function_source(
            EXPLANATION_SRC, "bulk_review_element_explanations"
        )
        assert "decision_context.build_decision_context(" in src
        assert "decision_context.attach_decision_context(" in src
        assert "BASIS_EXPLANATION_REVIEW_BULK" in src
        # 既存の来歴申告（TT3）の作法は保つ。
        assert "teacher_triage.sort_metadata(" in src
        assert '"bulk": True' in src

    def test_client_reported_is_isolated_in_both_routes(self):
        """DC4: 来歴申告は専用引数へ渡す（トップレベルのサーバ導出値に混ぜない）。"""
        for src, fn in (
            (LANDSCAPE_SRC, "accept_course_landscape_placements"),
            (EXPLANATION_SRC, "bulk_review_element_explanations"),
        ):
            fn_src = extract_function_source(src, fn)
            assert "client_reported=" in fn_src


class TestReleaseReviewFrontend:
    def test_accept_reports_presented_ids_and_evidence(self):
        accept = RELEASE_JS[
            RELEASE_JS.index("function acceptPlacements") :
            RELEASE_JS.index("function renderPublishStep")
        ]
        assert "presented_placement_ids" in accept
        assert "evidence_shown" in accept

    def test_evidence_affordance_is_rendered_with_its_anchor(self):
        assert 'data-ui-anchor="release-review.evidence"' in RELEASE_JS
        assert "<details" in RELEASE_JS
        assert "根拠を見る" in RELEASE_JS

    def test_reopen_fact_is_shown(self):
        assert (
            "確認後も、教材管理の「位置づけ（分野マップ）」から個別に再検討・却下へ戻せます。"
            in RELEASE_JS
        )
        # 「次へ」の意味の明示（RR2）は残す。
        assert "確認したものとして記録" in RELEASE_JS

    def test_match_facts_do_not_carry_numbers(self):
        for fact in (
            "表示されていた配置と確認した配置は一致しています",
            "表示と確認した配置に差がありました（画面を再読み込みしてください）",
        ):
            assert fact in RELEASE_JS
            assert "%" not in fact
