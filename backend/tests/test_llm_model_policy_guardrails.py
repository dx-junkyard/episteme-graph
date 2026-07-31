"""LLM モデル選択（M層）ガードレールの自動テスト。

設計の正本は docs/features/llm_model_selection_design.md §11「ガードレール
（backend/tests/test_llm_model_policy_guardrails.py）」。Phase 0 で検証可能な項目
（1・3・6・8。DB ポリシー・API・fail-closed 検証・ユーザー分離は後続 Phase）を
構造的に守る。他の「○○層」ガードレールテスト（test_llm_usage_guardrails.py 等）と
同じ流儀（ソースコードを read_text で読み込む静的検査 + 非DB・非LLM のユニットテスト）。

外部サービス（PostgreSQL / OpenAI / Gemini）への実接続は行わない。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
)

_LLM_POLICY_SRC = (BACKEND / "core" / "llm_policy.py").read_text(encoding="utf-8")

from core.config import get_settings as _real_get_settings  # noqa: E402
import core.llm_policy as llm_policy  # noqa: E402
from core.llm_usage.schema import KNOWN_FEATURES, UNATTRIBUTED  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_policy_state():
    yield
    llm_policy.reset_policy_backend_for_tests()
    _real_get_settings.cache_clear()


# ===========================================================================
# 1. core/llm_policy.py が FastAPI を import しない
# ===========================================================================


class TestNoFastAPIImport:
    def test_llm_policy_does_not_import_fastapi(self):
        assert_source_does_not_import(_LLM_POLICY_SRC, ["fastapi"], context="core/llm_policy.py")

    def test_llm_policy_does_not_import_llm_sdks(self):
        # LLM SDK 非依存（core/llm.py がベンダ SDK を隠蔽する層であり、policy はそれより
        # 手前の「どのモデル名を使うか」だけを決める）。
        assert_source_forbids(
            _LLM_POLICY_SRC,
            ["import openai", "import google.generativeai", "import vertexai"],
            context="core/llm_policy.py",
        )

    def test_llm_policy_does_not_import_orchestrator(self):
        # Phase 4: PIPELINE_STAGE_LABELS はステージ名の日本語表示名を静的に持つだけで、
        # orchestrator を import して逆に読みにいかない（依存方向は
        # orchestrator → llm_policy のまま。M1 / 開発ルール2）。
        assert_source_forbids(
            _LLM_POLICY_SRC,
            [
                "import core.document_pipeline.orchestrator",
                "from core.document_pipeline.orchestrator",
                "from core.document_pipeline import orchestrator",
            ],
            context="core/llm_policy.py",
        )


# ===========================================================================
# 2. 未使用（DB ポリシー行の fail-closed 検証・API 権限は Phase 1 以降）
# ===========================================================================


# ===========================================================================
# 3. UI 文言・source_label に tier 名 (fast/standard/deep/analysis) が現れない (M3)
# ===========================================================================


class TestNoTierNameLeaksIntoLabels:
    def test_source_labels_do_not_contain_tier_names(self):
        forbidden_tier_words = ("fast", "standard", "deep", "analysis")
        for source, label in llm_policy._SOURCE_LABELS.items():  # noqa: SLF001
            lowered = label.lower()
            for word in forbidden_tier_words:
                assert word not in lowered, (
                    f"source_label for {source!r} leaks tier name {word!r}: {label!r}"
                )

    def test_resolved_model_source_label_never_a_bare_tier_name(self, monkeypatch):
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "analysis-model-q")
        _real_get_settings.cache_clear()
        resolved = llm_policy.resolve_scene_model("pipeline:paper_skeleton")
        assert resolved.source_label not in ("fast", "standard", "deep", "analysis")

    def test_scene_labels_do_not_contain_tier_names(self):
        forbidden_tier_words = ("fast", "standard", "deep", "analysis")
        for entry in llm_policy.SCENES.values():
            label = str(entry["label"]).lower()
            for word in forbidden_tier_words:
                assert word not in label


# ===========================================================================
# 6. DB ポリシー行が無いとき、解決結果が現在の env / tier 既定と完全に一致する
# ===========================================================================


class TestPhase0BehaviorUnchanged:
    """設計書 §11-6 を機械的に固定する。NullPolicyBackend が既定であることと、
    代表的な feature の解決結果が「素の getattr(settings, ...) or tier既定」と
    完全に一致することを確認する。
    """

    def test_null_policy_backend_is_the_default(self):
        assert isinstance(llm_policy.get_policy_backend(), llm_policy.NullPolicyBackend)

    @pytest.mark.parametrize(
        "feature,setting_attr,fallback_attr",
        [
            ("learning:tension", "tension_llm_model", "llm_fast_model"),
            ("learning:structure_anchor", "anchor_llm_model", "llm_fast_model"),
            ("admin:reconstruction_authoring", "recon_llm_model", "llm_fast_model"),
            ("doubt:scope_candidates", "doubt_scope_llm_model", "llm_fast_model"),
            ("doubt:assumption_normalize", "doubt_assumption_llm_model", "llm_fast_model"),
            ("admin:assistant", "assistant_llm_model", "llm_fast_model"),
            ("admin:atlas_skeleton", "atlas_assist_llm_model", "llm_analysis_model"),
            ("admin:atlas_assist", "atlas_assist_llm_model", "llm_analysis_model"),
            ("deliberation:chat", "deliberation_llm_model", "llm_fast_model"),
            ("deliberation:standardization", "stdpart_llm_model", "llm_fast_model"),
            ("learning:chat", "learning_chat_llm_model", "llm_analysis_model"),
            ("admin:course_builder", "course_builder_llm_model", "llm_analysis_model"),
            ("pipeline:apparatus_semantics", "apparatus_llm_model", "llm_analysis_model"),
        ],
    )
    def test_matches_legacy_getattr_or_fallback(self, feature, setting_attr, fallback_attr):
        settings = _real_get_settings()
        expected = getattr(settings, setting_attr, "") or getattr(settings, fallback_attr)
        resolved = llm_policy.resolve_scene_model(feature)
        assert resolved.model == expected

    def test_generic_pipeline_feature_matches_analysis_default(self):
        settings = _real_get_settings()
        resolved = llm_policy.resolve_scene_model("pipeline:component_assembly")
        assert resolved.model == settings.llm_analysis_model

    def test_unattributed_feature_matches_analysis_default(self):
        settings = _real_get_settings()
        resolved = llm_policy.resolve_scene_model(UNATTRIBUTED)
        assert resolved.model == settings.llm_analysis_model


# ===========================================================================
# 8. KNOWN_FEATURES の全 feature が scene_for_feature() で解決できる
# ===========================================================================


class TestFeatureCoverage:
    def test_every_known_feature_is_handled(self):
        """新機能の feature を追加したのに scene 未定義、を検出する。

        許容される None は embedding 系（選択対象外, M5）と unattributed のみ。
        """
        allowed_none = {
            UNATTRIBUTED,
            "embedding:chunks",
            "embedding:library_search",
            "admin:help_kb_embed",
        }
        for feature in KNOWN_FEATURES:
            scene = llm_policy.scene_for_feature(feature)
            if feature in allowed_none:
                continue
            assert scene is not None, f"feature {feature!r} has no scene mapping"
            assert scene in llm_policy.SCENES


# ===========================================================================
# Phase 1: core/llm_policy_store.py — FastAPI 非 import・LLM SDK 非 import
# ===========================================================================

_LLM_POLICY_STORE_SRC = (BACKEND / "core" / "llm_policy_store.py").read_text(encoding="utf-8")


class TestLlmPolicyStoreNoFastAPIImport:
    def test_llm_policy_store_does_not_import_fastapi(self):
        assert_source_does_not_import(
            _LLM_POLICY_STORE_SRC, ["fastapi"], context="core/llm_policy_store.py"
        )

    def test_llm_policy_store_does_not_import_llm_sdks(self):
        assert_source_forbids(
            _LLM_POLICY_STORE_SRC,
            ["import openai", "import google.generativeai", "import vertexai"],
            context="core/llm_policy_store.py",
        )


# ===========================================================================
# Phase 1: 削除 API が llm_usage_events を触らない (U6)
# ===========================================================================


class TestDeleteApiDoesNotTouchLlmUsageEvents:
    """U6（append-only）: 本 M層の delete/upsert 経路が ``llm_usage_events`` を
    書き換え・削除対象にしていないことを確認する。設計・docstring 内での言及
    （「触らない」という説明文自体）まで禁止すると自己矛盾になるため、
    実際に SQL 文の対象として現れていないかだけを機械的に検査する。
    """

    def test_routes_llm_models_does_not_target_llm_usage_events_in_sql(self):
        src = (BACKEND / "api" / "routes" / "llm_models.py").read_text(encoding="utf-8")
        assert_source_forbids(
            src,
            [
                "DELETE FROM llm_usage_events",
                "UPDATE llm_usage_events",
                "INSERT INTO llm_usage_events",
                "TRUNCATE llm_usage_events",
            ],
            context="routes/llm_models.py",
        )

    def test_llm_policy_store_does_not_target_llm_usage_events_in_sql(self):
        assert_source_forbids(
            _LLM_POLICY_STORE_SRC,
            [
                "DELETE FROM llm_usage_events",
                "UPDATE llm_usage_events",
                "INSERT INTO llm_usage_events",
                "TRUNCATE llm_usage_events",
            ],
            context="core/llm_policy_store.py",
        )


# ===========================================================================
# Phase 1: 学生向けエンドポイントにモデル名が出ない (M9) — ルーターは /api/admin 限定
# ===========================================================================


class TestNoLearnerFacingRoutes:
    def test_router_is_mounted_under_admin_only(self):
        import routes.llm_models as llm_models_routes

        for route in llm_models_routes.router.routes:
            path = getattr(route, "path", "")
            assert path.startswith("/api/admin/llm-models"), (
                f"unexpected route path outside /api/admin: {path}"
            )
            assert not path.startswith("/api/learning")


# ===========================================================================
# Phase 1: 教員向けレスポンスに金額 (cost_usd 等) が含まれない (M8)
# ===========================================================================


class TestNoMonetaryFieldsInSource:
    def test_routes_llm_models_source_has_no_cost_usd_or_price(self):
        src = (BACKEND / "api" / "routes" / "llm_models.py").read_text(encoding="utf-8")
        assert_source_forbids(src, ["cost_usd", "price_table", "USD", "円"], context="routes/llm_models.py")


# ===========================================================================
# Phase 1: DB ポリシー行が無いとき fail-open で None（呼び出しを止めない）
# ===========================================================================


class TestDbPolicyBackendFailOpen:
    def test_lookup_returns_none_and_does_not_raise_on_db_error(self):
        import core.llm_policy_store as llm_policy_store

        class _RaisingSession:
            def execute(self, *a, **kw):
                raise RuntimeError("db down")

            def close(self):
                pass

        import pytest as _pytest

        with _pytest.MonkeyPatch.context() as mp:
            mp.setattr(llm_policy_store, "get_session", lambda: _RaisingSession())
            backend = llm_policy_store.DbPolicyBackend()
            result = backend.lookup("pipeline", "pipeline:paper_skeleton", None)
        assert result is None


# ===========================================================================
# Phase 1: ユーザー分離 (§11-9) — my-policies API は本人以外の user_id を受け付けない
# ===========================================================================


class TestUserIsolationInApi:
    def test_put_my_policy_request_model_has_no_user_id_field(self):
        import routes.llm_models as llm_models_routes

        assert "user_id" not in llm_models_routes.PolicyUpsertRequest.model_fields

    def test_pick_priority_row_ignores_other_users_rows(self):
        import core.llm_policy_store as llm_policy_store

        rows = [
            {"scope": "user", "user_id": "teacher-a", "scene_key": "pipeline", "model": "a-model", "reasoning_effort": None},
        ]
        result = llm_policy_store._pick_priority_row(
            rows, scene_key="pipeline", feature="pipeline", user_id="teacher-b"
        )
        assert result is None


# ===========================================================================
# Phase 3: validate_model_for_scene の fail-closed（正本の単一の真実源）
# ===========================================================================


class TestValidateModelForSceneFailClosed:
    """core.llm_policy.validate_model_for_scene（§11-2）が routes/llm_models.py と
    Phase 3 の各エンドポイントの唯一の検証経路であることのガードレール。"""

    def test_catalog_unset_plus_model_is_rejected(self, monkeypatch):
        monkeypatch.setattr(llm_policy, "load_catalog", lambda: None)
        reason = llm_policy.validate_model_for_scene(llm_policy.SCENE_COURSE_BUILDER, "gpt-5.2")
        assert reason is not None

    def test_unknown_scene_key_is_rejected_even_with_catalog(self, monkeypatch):
        catalog = {"models": [{"id": "gpt-5.2", "provider": "openai", "capabilities": ["text"]}]}
        monkeypatch.setattr(llm_policy, "load_catalog", lambda: catalog)
        reason = llm_policy.validate_model_for_scene("not_a_real_scene", "gpt-5.2")
        assert reason is not None

    def test_routes_llm_models_has_no_duplicated_validation_logic(self):
        """routes/llm_models.py が catalog_models() を直接ループする独自検証ロジックを
        再実装していない（正本への委譲のみ）ことの静的確認。"""
        src = (BACKEND / "api" / "routes" / "llm_models.py").read_text(encoding="utf-8")
        assert "valid_ids = {entry.get" not in src, (
            "routes/llm_models.py は独自の model 検証ループを再実装せず "
            "llm_policy.validate_model_for_scene に委譲すること"
        )


# ===========================================================================
# Phase 3: 学習者向け routes のレスポンスに model キーを足していない（M9）
# ===========================================================================


class TestNoModelFieldInLearnerFacingResponses:
    def test_learning_chat_response_schema_has_no_model_field(self):
        from schemas import LearningChatResponse

        assert "model" not in LearningChatResponse.model_fields

    def test_check_question_response_schema_has_no_model_field(self):
        from schemas import LearningCheckQuestionResponse

        assert "model" not in LearningCheckQuestionResponse.model_fields

    def test_course_update_response_schema_has_no_model_or_llm_models_field(self):
        """LearningCourseDetail（コース設定 PUT のレスポンス）は llm_models も
        model も学生に見せる語彙を持たない。"""
        from schemas import LearningCourseDetail

        assert "model" not in LearningCourseDetail.model_fields
        assert "llm_models" not in LearningCourseDetail.model_fields
