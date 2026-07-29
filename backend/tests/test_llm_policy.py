"""LLM モデル選択（M層, core/llm_policy.py）Phase 0 のテスト。

正本: docs/features/llm_model_selection_design.md。Phase 0 のスコープは
「決定ロジックの一点集約」のみ（DB ポリシー・UI は後続 Phase）— このテストは
その不変条件（§11-6: 環境変数だけの環境では従来と完全に同じモデルが選ばれる）を
機械的に固定する。

パターンは ``tests/test_course_builder_infra.py`` の
「``core.config.get_settings`` を直接 import して ``cache_clear()`` する」流儀に倣う
（conftest.py の autouse フィクスチャは ``core.config.get_settings`` という *属性* を
モンキーパッチするだけで、既に ``from core.config import get_settings`` 済みの
モジュール（core/llm_policy.py 含む）が保持する参照までは差し替わらない —
ただし ``cache_clear()`` は同一の関数オブジェクトに対して働くため、
``monkeypatch.setenv`` → ``_real_get_settings.cache_clear()`` の順で呼べば
llm_policy 側の解決も実環境の値を正しく反映する）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_backend_dir = str(Path(__file__).resolve().parents[1])
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from core.config import get_settings as _real_get_settings  # noqa: E402
import core.llm_policy as llm_policy  # noqa: E402
from core.llm_usage.context import usage_context  # noqa: E402
from core.llm_usage.schema import KNOWN_FEATURES, UNATTRIBUTED  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_policy_state():
    """各テスト後に PolicyBackend を Null 実装へ戻し、settings キャッシュも掃除する。"""
    yield
    llm_policy.reset_policy_backend_for_tests()
    _real_get_settings.cache_clear()


# ===========================================================================
# 1. scene_for_feature — KNOWN_FEATURES 全件の解決
# ===========================================================================


class TestSceneForFeature:
    def test_all_known_features_resolve_except_embedding_and_unattributed(self):
        unmapped = {f for f in KNOWN_FEATURES if llm_policy.scene_for_feature(f) is None}
        allowed_none = {
            UNATTRIBUTED,
            "embedding:chunks",
            "embedding:library_search",
            "admin:help_kb_embed",
        }
        assert unmapped == allowed_none

    def test_pipeline_vision_is_apparatus_semantics_only(self):
        assert llm_policy.scene_for_feature("pipeline:apparatus_semantics") == "pipeline.vision"
        assert llm_policy.scene_for_feature("pipeline:paper_skeleton") == "pipeline"
        assert llm_policy.scene_for_feature("pipeline") == "pipeline"

    def test_learning_chat_grouping(self):
        for feature in (
            "learning:chat",
            "learning:chat_casual",
            "learning:chat_discuss",
            "learning:understanding_check",
            "learning:help_usage",
        ):
            assert llm_policy.scene_for_feature(feature) == "learning_chat"

    def test_learning_voice_and_background(self):
        assert llm_policy.scene_for_feature("learning:voice_stt") == "learning_voice"
        assert llm_policy.scene_for_feature("learning:voice_tts") == "learning_voice"
        for feature in ("learning:tension", "learning:structure_anchor", "admin:reconstruction_authoring"):
            assert llm_policy.scene_for_feature(feature) == "learning_background"

    def test_unknown_feature_returns_none(self):
        assert llm_policy.scene_for_feature("totally:unknown_feature") is None
        assert llm_policy.scene_for_feature("") is None

    def test_scenes_catalog_is_consistent_with_scene_for_feature(self):
        for scene_key, entry in llm_policy.SCENES.items():
            for feature in entry["features"]:
                assert llm_policy.scene_for_feature(feature) == scene_key


# ===========================================================================
# 2. model_override — 実行時オーバーライド contextvar
# ===========================================================================


class TestModelOverride:
    def test_no_override_by_default(self):
        assert llm_policy.current_model_override() is None

    def test_override_nesting_inner_wins_then_restores_outer(self):
        assert llm_policy.current_model_override() is None
        with llm_policy.model_override("model-a", reasoning_effort="high"):
            outer = llm_policy.current_model_override()
            assert outer is not None
            assert outer.model == "model-a"
            assert outer.reasoning_effort == "high"
            assert outer.source == llm_policy.SOURCE_RUN_OVERRIDE

            with llm_policy.model_override("model-b", source=llm_policy.SOURCE_COURSE_OVERRIDE):
                inner = llm_policy.current_model_override()
                assert inner.model == "model-b"
                assert inner.source == llm_policy.SOURCE_COURSE_OVERRIDE

            restored = llm_policy.current_model_override()
            assert restored.model == "model-a"
        assert llm_policy.current_model_override() is None

    def test_resolve_scene_model_prefers_override_over_env(self, monkeypatch):
        monkeypatch.setenv("LEARNING_CHAT_LLM_MODEL", "env-model")
        _real_get_settings.cache_clear()
        with llm_policy.model_override("override-model"):
            resolved = llm_policy.resolve_scene_model("learning:chat")
        assert resolved.model == "override-model"
        assert resolved.source == llm_policy.SOURCE_RUN_OVERRIDE

    def test_requested_argument_wins_over_everything(self, monkeypatch):
        monkeypatch.setenv("LEARNING_CHAT_LLM_MODEL", "env-model")
        _real_get_settings.cache_clear()
        with llm_policy.model_override("override-model"):
            resolved = llm_policy.resolve_scene_model("learning:chat", requested="explicit")
        assert resolved.model == "explicit"
        assert resolved.source == llm_policy.SOURCE_CALL_ARGUMENT


# ===========================================================================
# 3. PolicyBackend の優先順位（user > system > env > tier）
# ===========================================================================


class _FakeBackend:
    def __init__(self, *, system=None, user=None):
        self._system = system
        self._user = user

    def lookup(self, scene_key, feature, user_id):
        if user_id is not None:
            return self._user
        return self._system


class TestPolicyBackendPrecedence:
    def test_system_policy_used_when_no_user_id(self, monkeypatch):
        monkeypatch.setenv("LEARNING_CHAT_LLM_MODEL", "env-model")
        _real_get_settings.cache_clear()
        backend = _FakeBackend(
            system=llm_policy.PolicyRow(model="system-model", scope="system"),
        )
        llm_policy.set_policy_backend(backend)
        resolved = llm_policy.resolve_scene_model("learning:chat")
        assert resolved.model == "system-model"
        assert resolved.source == llm_policy.SOURCE_SYSTEM_POLICY

    def test_user_policy_wins_over_system_policy(self, monkeypatch):
        monkeypatch.setenv("LEARNING_CHAT_LLM_MODEL", "env-model")
        _real_get_settings.cache_clear()
        backend = _FakeBackend(
            system=llm_policy.PolicyRow(model="system-model", scope="system"),
            user=llm_policy.PolicyRow(model="user-model", scope="user", reasoning_effort="high"),
        )
        llm_policy.set_policy_backend(backend)
        with usage_context("learning:chat", user_id="teacher-1"):
            resolved = llm_policy.resolve_scene_model("learning:chat")
        assert resolved.model == "user-model"
        assert resolved.source == llm_policy.SOURCE_USER_POLICY
        assert resolved.reasoning_effort == "high"

    def test_null_policy_backend_is_default(self):
        assert isinstance(llm_policy.get_policy_backend(), llm_policy.NullPolicyBackend)
        assert llm_policy.get_policy_backend().lookup("learning_chat", "learning:chat", "u1") is None


# ===========================================================================
# 4. 挙動不変（Phase 0）— 代表的 feature で従来ロジックと一致すること
# ===========================================================================


class TestPhase0BehaviorParity:
    def test_generic_pipeline_feature_falls_back_to_analysis_tier(self, monkeypatch):
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "analysis-model-x")
        _real_get_settings.cache_clear()
        resolved = llm_policy.resolve_scene_model("pipeline:paper_skeleton")
        assert resolved.model == "analysis-model-x"
        assert resolved.source == llm_policy.SOURCE_TIER_DEFAULT

    def test_learning_tension_unset_falls_back_to_fast_tier(self, monkeypatch):
        monkeypatch.delenv("TENSION_LLM_MODEL", raising=False)
        monkeypatch.setenv("LLM_FAST_MODEL", "fast-model-x")
        _real_get_settings.cache_clear()
        resolved = llm_policy.resolve_scene_model("learning:tension")
        assert resolved.model == "fast-model-x"
        assert resolved.source == llm_policy.SOURCE_TIER_DEFAULT

    def test_learning_tension_env_set_is_honored(self, monkeypatch):
        monkeypatch.setenv("TENSION_LLM_MODEL", "tension-custom")
        _real_get_settings.cache_clear()
        resolved = llm_policy.resolve_scene_model("learning:tension")
        assert resolved.model == "tension-custom"
        assert resolved.source == llm_policy.SOURCE_ENV

        settings = _real_get_settings()
        assert resolved.model == (settings.tension_llm_model or settings.llm_fast_model)

    def test_learning_chat_unset_falls_back_to_analysis(self, monkeypatch):
        monkeypatch.delenv("LEARNING_CHAT_LLM_MODEL", raising=False)
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "analysis-model-y")
        _real_get_settings.cache_clear()
        resolved = llm_policy.resolve_scene_model("learning:chat")
        assert resolved.model == "analysis-model-y"
        assert resolved.source == llm_policy.SOURCE_TIER_DEFAULT

    def test_learning_chat_env_set_is_honored(self, monkeypatch):
        monkeypatch.setenv("LEARNING_CHAT_LLM_MODEL", "learning-chat-custom")
        _real_get_settings.cache_clear()
        resolved = llm_policy.resolve_scene_model("learning:chat")
        assert resolved.model == "learning-chat-custom"
        assert resolved.source == llm_policy.SOURCE_ENV

    def test_apparatus_semantics_uses_apparatus_setting(self, monkeypatch):
        # APPARATUS_LLM_MODEL has a non-empty Settings default ("gpt-4o"), so the
        # env step always wins for this feature (mirrors the old hardcoded
        # ``getattr(settings, "apparatus_llm_model", "gpt-4o")`` fallback exactly).
        monkeypatch.delenv("APPARATUS_LLM_MODEL", raising=False)
        _real_get_settings.cache_clear()
        settings = _real_get_settings()
        resolved = llm_policy.resolve_scene_model("pipeline:apparatus_semantics")
        assert resolved.model == settings.apparatus_llm_model
        assert resolved.source == llm_policy.SOURCE_ENV

        monkeypatch.setenv("APPARATUS_LLM_MODEL", "vision-custom")
        _real_get_settings.cache_clear()
        resolved2 = llm_policy.resolve_scene_model("pipeline:apparatus_semantics")
        assert resolved2.model == "vision-custom"

    def test_contextual_explanation_reads_ctxexpl_env_directly(self, monkeypatch):
        monkeypatch.delenv("CTXEXPL_LLM_MODEL", raising=False)
        monkeypatch.setenv("LLM_FAST_MODEL", "fast-model-z")
        _real_get_settings.cache_clear()
        resolved = llm_policy.resolve_scene_model("pipeline:contextual_explanation")
        assert resolved.model == "fast-model-z"
        assert resolved.source == llm_policy.SOURCE_TIER_DEFAULT

        monkeypatch.setenv("CTXEXPL_LLM_MODEL", "ctxexpl-custom")
        resolved2 = llm_policy.resolve_scene_model("pipeline:contextual_explanation")
        assert resolved2.model == "ctxexpl-custom"
        assert resolved2.source == llm_policy.SOURCE_ENV


# ===========================================================================
# 5. reasoning_effort 合成
# ===========================================================================


class TestEffortForCall:
    def test_explicit_effort_always_wins(self):
        resolved = llm_policy.ResolvedModel(
            model="m", source=llm_policy.SOURCE_USER_POLICY, source_label="x", reasoning_effort="high"
        )
        assert llm_policy.effort_for_call(resolved, requested_effort="low") == "low"

    def test_policy_source_injects_effort_when_unrequested(self):
        resolved = llm_policy.ResolvedModel(
            model="m", source=llm_policy.SOURCE_USER_POLICY, source_label="x", reasoning_effort="high"
        )
        assert llm_policy.effort_for_call(resolved, requested_effort=None) == "high"

    def test_env_source_never_injects_effort(self):
        resolved = llm_policy.ResolvedModel(
            model="m", source=llm_policy.SOURCE_ENV, source_label="x", reasoning_effort="high"
        )
        assert llm_policy.effort_for_call(resolved, requested_effort=None) is None

    def test_tier_default_source_never_injects_effort(self):
        resolved = llm_policy.ResolvedModel(
            model="m", source=llm_policy.SOURCE_TIER_DEFAULT, source_label="x", reasoning_effort="high"
        )
        assert llm_policy.effort_for_call(resolved, requested_effort=None) is None


# ===========================================================================
# 6. resolve_for_setting — 既存 resolve_model の委譲先
# ===========================================================================


class TestResolveForSetting:
    def test_known_key_matches_scene_resolution(self, monkeypatch):
        monkeypatch.setenv("COURSE_BUILDER_LLM_MODEL", "cb-custom")
        _real_get_settings.cache_clear()
        assert llm_policy.resolve_for_setting("course_builder_llm_model", fallback="analysis") == "cb-custom"

    def test_known_key_falls_back_to_analysis_tier_when_unset(self, monkeypatch):
        monkeypatch.delenv("COURSE_BUILDER_LLM_MODEL", raising=False)
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "analysis-model-w")
        _real_get_settings.cache_clear()
        resolved = llm_policy.resolve_for_setting("course_builder_llm_model", fallback="analysis")
        assert resolved == "analysis-model-w"

    def test_unknown_key_uses_legacy_getattr_logic(self, monkeypatch):
        monkeypatch.setenv("LLM_FAST_MODEL", "fast-model-legacy")
        _real_get_settings.cache_clear()
        resolved = llm_policy.resolve_for_setting("not_a_real_setting_key", fallback="fast")
        assert resolved == "fast-model-legacy"

    def test_invalid_fallback_tier_raises(self):
        with pytest.raises(ValueError):
            llm_policy.resolve_for_setting("course_builder_llm_model", fallback="bogus")

    def test_llm_worker_client_delegates_to_policy(self, monkeypatch):
        from core.llm_worker.client import resolve_model

        monkeypatch.setenv("TENSION_LLM_MODEL", "tension-via-client")
        _real_get_settings.cache_clear()
        assert resolve_model("tension_llm_model", fallback="fast") == "tension-via-client"
        assert resolve_model("tension_llm_model", fallback="fast") == llm_policy.resolve_for_setting(
            "tension_llm_model", fallback="fast"
        )


# ===========================================================================
# 7. モデルカタログ
# ===========================================================================


class TestCatalog:
    def test_no_path_falls_back_to_bundled_catalog(self, monkeypatch):
        """パス未設定なら同梱カタログを使う（2026-07-28 の実機不整合の回帰ガード）。

        既定を「カタログ無し」にすると、選択肢ゼロ = 各画面の [変更] が押せない状態が
        既定になってしまう（設計書 §5「既定同梱」に反する）。
        """
        monkeypatch.delenv("LLM_MODEL_CATALOG_PATH", raising=False)
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        _real_get_settings.cache_clear()
        catalog = llm_policy.load_catalog()
        assert catalog is not None, "パス未設定時は同梱カタログにフォールバックすること"
        assert llm_policy.catalog_models(), "同梱カタログから選択肢が1件以上得られること"

    def test_bundled_catalog_path_resolves_from_module_location(self):
        """同梱パスは cwd 非依存（docker の /app/config/ でも解決できる形）であること。"""
        assert llm_policy._BUNDLED_CATALOG_PATH.is_file()
        assert llm_policy._BUNDLED_CATALOG_PATH.name == "llm_models.json"
        assert llm_policy._BUNDLED_CATALOG_PATH.parent.name == "config"

    def test_blank_path_env_also_falls_back(self, monkeypatch):
        """docker-compose の ``${LLM_MODEL_CATALOG_PATH:-}`` が空文字を注入しても同梱を使う。"""
        monkeypatch.setenv("LLM_MODEL_CATALOG_PATH", "")
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        _real_get_settings.cache_clear()
        assert llm_policy.load_catalog() is not None

    def test_valid_catalog_filters_by_provider_and_capability(self, monkeypatch, tmp_path):
        catalog_path = tmp_path / "models.json"
        catalog_path.write_text(
            json.dumps(
                {
                    "models": [
                        {"id": "gpt-5.2", "provider": "openai", "capabilities": ["text", "structured", "vision"]},
                        {"id": "gpt-5.4-nano", "provider": "openai", "capabilities": ["text", "structured"]},
                        {"id": "gemini-x", "provider": "gemini", "capabilities": ["text"]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("LLM_MODEL_CATALOG_PATH", str(catalog_path))
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        _real_get_settings.cache_clear()

        catalog = llm_policy.load_catalog()
        assert catalog is not None and len(catalog["models"]) == 3

        openai_models = llm_policy.catalog_models()
        assert {m["id"] for m in openai_models} == {"gpt-5.2", "gpt-5.4-nano"}

        vision_models = llm_policy.catalog_models(capability="vision")
        assert [m["id"] for m in vision_models] == ["gpt-5.2"]

    def test_broken_json_returns_none(self, monkeypatch, tmp_path):
        bad_path = tmp_path / "bad.json"
        bad_path.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setenv("LLM_MODEL_CATALOG_PATH", str(bad_path))
        _real_get_settings.cache_clear()
        assert llm_policy.load_catalog() is None
        assert llm_policy.catalog_models() == []

    def test_missing_file_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LLM_MODEL_CATALOG_PATH", str(tmp_path / "does_not_exist.json"))
        _real_get_settings.cache_clear()
        assert llm_policy.load_catalog() is None

    def test_non_dict_or_missing_models_key_returns_none(self, monkeypatch, tmp_path):
        path = tmp_path / "no_models.json"
        path.write_text(json.dumps({"not_models": []}), encoding="utf-8")
        monkeypatch.setenv("LLM_MODEL_CATALOG_PATH", str(path))
        _real_get_settings.cache_clear()
        assert llm_policy.load_catalog() is None

    def test_shipped_catalog_is_loadable_and_openai_only(self, monkeypatch):
        shipped = Path(__file__).resolve().parents[1] / "config" / "llm_models.json"
        assert shipped.is_file()
        monkeypatch.setenv("LLM_MODEL_CATALOG_PATH", str(shipped))
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        _real_get_settings.cache_clear()
        models = llm_policy.catalog_models()
        ids = {m["id"] for m in models}
        assert {"gpt-5.2", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-4o"} <= ids
        assert all(m.get("provider") == "openai" for m in models)
