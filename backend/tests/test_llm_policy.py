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

**フルスイート限定の実行位相 flake の遮断（2026-08-16）**: この共有 lru_cache
（``maxsize=1``）は core/postgres.py など worker デーモンスレッド側とも同一実体で、
先行テストが起動した worker スレッドが **env パッチ前の環境**で ``Settings()`` を
構築中に本モジュールのテストが ``setenv → cache_clear()`` を済ませると、スレッド側の
計算結果（旧 env 由来）が**クリア後のキャッシュへ格納**され、テストが stale な
``llm_fast_model`` 等を読んで落ちる（単独実行では再現しない）。そのため本モジュールは
autouse フィクスチャ ``_uncached_settings`` で llm_policy に非キャッシュの
``get_settings.__wrapped__`` を注入し、共有キャッシュへの依存自体を断つ
（llm_policy の解決は常に「現在の os.environ」を読む）。期待値側の直接参照も
:func:`_fresh_settings` を使い、同じ理由で共有キャッシュを経由しない。
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


def _fresh_settings():
    """共有 lru_cache を経由せず、現在の os.environ から Settings を構築する。

    期待値の算出にキャッシュ済み ``_real_get_settings()`` を使うと、worker デーモン
    スレッドとの in-flight 競合（モジュール docstring 参照）で stale な値を掴み得る。
    """
    return _real_get_settings.__wrapped__()


@pytest.fixture(autouse=True)
def _uncached_settings(monkeypatch):
    """llm_policy のモデル解決を共有 settings キャッシュから切り離す（flake 遮断）。

    llm_policy は ``from core.config import get_settings`` の by-name 参照を保持する
    ため、モジュール属性の差し替えで本モジュール内のテストだけが非キャッシュ読みに
    なる（worker スレッド側・他モジュールのキャッシュ利用には影響しない）。
    """
    monkeypatch.setattr(llm_policy, "get_settings", _real_get_settings.__wrapped__)


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
            # 論文ディスカバリーの関連度ランキング（Phase 3）も実体は embedding
            # 呼び出しで、モデル選択の対象外（M5）。
            "discovery:ranking",
            # VA層のアンカー埋め込み（atlas_vector_anchoring_design.md VA5）。
            # embedding: プレフィックスで scene なし（M5）。
            "embedding:atlas_anchors",
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

        settings = _fresh_settings()
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
        settings = _fresh_settings()
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


# ===========================================================================
# 7. 代表 feature（レビュー指摘 m1）— scene の「現在のモデル」表示に使う feature
# ===========================================================================


class TestRepresentativeFeature:
    def test_every_scene_declares_a_representative_that_belongs_to_it(self):
        """新 scene 追加時に宣言漏れ（= features[0] への暗黙フォールバック）を検出する。"""
        for scene_key, entry in llm_policy.SCENES.items():
            features = tuple(entry["features"])
            declared = llm_policy._SCENE_REPRESENTATIVE_FEATURE.get(scene_key)  # noqa: SLF001
            assert declared is not None, f"scene {scene_key!r} has no representative feature"
            assert declared in features, (
                f"representative {declared!r} is not a member of scene {scene_key!r}"
            )
            assert llm_policy.representative_feature_for_scene(scene_key) == declared

    def test_feature_level_scene_key_returns_itself(self):
        assert (
            llm_policy.representative_feature_for_scene("pipeline:claim_qualification")
            == "pipeline:claim_qualification"
        )

    def test_deliberation_representative_is_the_chat_feature(self, monkeypatch):
        """旧実装は KNOWN_FEATURES 順の先頭 ``deliberation:cross_corpus``（embedding 用・
        env マッピングなし）を代表にしていたため、実際は fast tier で動く対話に
        analysis tier のモデル名を表示していた（m1）。"""
        monkeypatch.delenv("DELIBERATION_LLM_MODEL", raising=False)
        monkeypatch.setenv("LLM_FAST_MODEL", "fast-model-m1")
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "analysis-model-m1")
        _real_get_settings.cache_clear()
        # llm_policy_store の実時間 TTL キャッシュ（20秒）が、直前に走った別テストの
        # 同一 scene_key 解決を保持していると env 由来の期待値と食い違い、フルスイートの
        # 実行位相によって flake する。env を差し替えたら必ず無効化する。
        from core import llm_policy_store

        llm_policy_store.invalidate()

        feature = llm_policy.representative_feature_for_scene(llm_policy.SCENE_DELIBERATION)
        assert feature == "deliberation:chat"
        # 表示される実効モデルが、実際の対話（deliberation:chat）の解決結果と一致する
        assert llm_policy.resolve_scene_model(feature).model == "fast-model-m1"
        assert llm_policy.resolve_scene_model("deliberation:chat").model == "fast-model-m1"

    def test_assistant_representative_is_the_copilot_feature(self, monkeypatch):
        monkeypatch.delenv("ASSISTANT_LLM_MODEL", raising=False)
        monkeypatch.setenv("LLM_FAST_MODEL", "fast-model-m1")
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "analysis-model-m1")
        _real_get_settings.cache_clear()

        feature = llm_policy.representative_feature_for_scene(llm_policy.SCENE_ASSISTANT)
        assert feature == "admin:assistant"
        assert llm_policy.resolve_scene_model(feature).model == "fast-model-m1"

    def test_pipeline_representative_keeps_the_generic_stage_default(self, monkeypatch):
        """汎用ステージ（大多数）は analysis tier。ステージ別 env を持つ
        contextual_explanation（fast）を代表にしてはいけない。"""
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "analysis-model-m1")
        monkeypatch.setenv("LLM_FAST_MODEL", "fast-model-m1")
        _real_get_settings.cache_clear()

        feature = llm_policy.representative_feature_for_scene(llm_policy.SCENE_PIPELINE)
        assert feature == "pipeline"
        assert llm_policy.resolve_scene_model(feature).model == "analysis-model-m1"

    def test_lecture_studio_representative_resolves_to_fast_tier(self, monkeypatch):
        monkeypatch.setenv("LLM_FAST_MODEL", "fast-model-m1")
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "analysis-model-m1")
        _real_get_settings.cache_clear()

        feature = llm_policy.representative_feature_for_scene(llm_policy.SCENE_LECTURE_STUDIO)
        assert feature == "admin:lecture_rewrite"
        assert llm_policy.resolve_scene_model(feature).model == "fast-model-m1"


# ===========================================================================
# 8. 原稿スタジオの fallback tier（レビュー指摘 J3 の Phase 0 不変性）
# ===========================================================================


class TestLectureFeatureFallbackTier:
    @pytest.mark.parametrize("feature", ["admin:lecture_rewrite", "admin:lecture_generate"])
    def test_lecture_features_fall_back_to_fast_tier(self, feature, monkeypatch):
        """policy 行も env も無い環境では従来（``get_llm_params("fast")``）と同じモデル。"""
        monkeypatch.setenv("LLM_FAST_MODEL", "fast-model-j3")
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "analysis-model-j3")
        _real_get_settings.cache_clear()
        resolved = llm_policy.resolve_scene_model(feature)
        assert resolved.model == "fast-model-j3"
        assert resolved.source == llm_policy.SOURCE_TIER_DEFAULT

    def test_system_policy_on_the_scene_applies_to_lecture_generation(self, monkeypatch):
        monkeypatch.setenv("LLM_FAST_MODEL", "fast-model-j3")
        _real_get_settings.cache_clear()
        llm_policy.set_policy_backend(
            _FakeBackend(system=llm_policy.PolicyRow(model="system-model-j3", scope="system"))
        )
        resolved = llm_policy.resolve_scene_model("admin:lecture_generate")
        assert resolved.model == "system-model-j3"
        assert resolved.source == llm_policy.SOURCE_SYSTEM_POLICY


# ===========================================================================
# 9. capability の fail-closed（レビュー指摘 m2）
# ===========================================================================


_VISION_CATALOG = {
    "models": [
        {"id": "vision-model", "provider": "openai", "capabilities": ["text", "structured", "vision"]},
        {"id": "text-only-model", "provider": "openai", "capabilities": ["text", "structured"]},
    ]
}


class TestCapabilityFailClosed:
    def test_required_capability_for_feature_covers_vision_features(self):
        assert llm_policy.required_capability_for_feature("deliberation:vision") == "vision"
        assert llm_policy.required_capability_for_feature("deliberation:figure_reanalysis") == "vision"
        assert llm_policy.required_capability_for_feature("pipeline:apparatus_semantics") == "vision"
        assert llm_policy.required_capability_for_feature("deliberation:chat") is None
        assert llm_policy.required_capability_for_feature("learning:chat") is None

    def test_non_vision_scene_policy_is_skipped_for_vision_feature(self, monkeypatch):
        """scene ``deliberation`` の既定に非 vision モデルが入っていても、画像付き
        コール（``deliberation:vision``）は env / tier 既定へ落ちる（M5）。"""
        monkeypatch.setattr(llm_policy, "load_catalog", lambda: _VISION_CATALOG)
        monkeypatch.delenv("DELIBERATION_LLM_MODEL", raising=False)
        monkeypatch.setenv("LLM_FAST_MODEL", "fast-model-m2")
        _real_get_settings.cache_clear()
        llm_policy.set_policy_backend(
            _FakeBackend(system=llm_policy.PolicyRow(model="text-only-model", scope="system"))
        )

        # text 経路はそのまま適用される
        assert llm_policy.resolve_scene_model("deliberation:chat").model == "text-only-model"
        # vision 経路は capability 不足でスキップ → tier 既定
        vision_resolved = llm_policy.resolve_scene_model("deliberation:vision")
        assert vision_resolved.model == "fast-model-m2"
        assert vision_resolved.source == llm_policy.SOURCE_TIER_DEFAULT

    def test_vision_capable_policy_is_applied_to_vision_feature(self, monkeypatch):
        monkeypatch.setattr(llm_policy, "load_catalog", lambda: _VISION_CATALOG)
        _real_get_settings.cache_clear()
        llm_policy.set_policy_backend(
            _FakeBackend(system=llm_policy.PolicyRow(model="vision-model", scope="system"))
        )
        resolved = llm_policy.resolve_scene_model("deliberation:vision")
        assert resolved.model == "vision-model"
        assert resolved.source == llm_policy.SOURCE_SYSTEM_POLICY

    def test_user_policy_lacking_capability_falls_through_to_system_policy(self, monkeypatch):
        monkeypatch.setattr(llm_policy, "load_catalog", lambda: _VISION_CATALOG)
        _real_get_settings.cache_clear()
        llm_policy.set_policy_backend(
            _FakeBackend(
                system=llm_policy.PolicyRow(model="vision-model", scope="system"),
                user=llm_policy.PolicyRow(model="text-only-model", scope="user"),
            )
        )
        with usage_context("deliberation:vision", user_id="teacher-1"):
            resolved = llm_policy.resolve_scene_model("deliberation:vision")
        assert resolved.model == "vision-model"
        assert resolved.source == llm_policy.SOURCE_SYSTEM_POLICY

    def test_catalog_unavailable_is_fail_open_for_existing_rows(self, monkeypatch):
        """カタログが読めない環境では capability 判定不能 → 既存のポリシー行を無効化しない。"""
        monkeypatch.setattr(llm_policy, "load_catalog", lambda: None)
        _real_get_settings.cache_clear()
        llm_policy.set_policy_backend(
            _FakeBackend(system=llm_policy.PolicyRow(model="text-only-model", scope="system"))
        )
        assert llm_policy.resolve_scene_model("deliberation:vision").model == "text-only-model"

    def test_explicit_call_argument_is_never_overridden_by_capability(self, monkeypatch):
        monkeypatch.setattr(llm_policy, "load_catalog", lambda: _VISION_CATALOG)
        _real_get_settings.cache_clear()
        resolved = llm_policy.resolve_scene_model("deliberation:vision", requested="text-only-model")
        assert resolved.model == "text-only-model"
        assert resolved.source == llm_policy.SOURCE_CALL_ARGUMENT


# ===========================================================================
# 10. 読み取り専用 scene（レビュー指摘 J5）— 音声は policy 経路を通らない
# ===========================================================================


class TestVoiceSceneFacts:
    def test_learning_voice_is_read_only(self):
        assert llm_policy.is_read_only_scene(llm_policy.SCENE_LEARNING_VOICE) is True
        assert llm_policy.read_only_scene_reason(llm_policy.SCENE_LEARNING_VOICE)
        for scene_key in llm_policy.SCENES:
            if scene_key == llm_policy.SCENE_LEARNING_VOICE:
                continue
            assert llm_policy.is_read_only_scene(scene_key) is False
            assert llm_policy.read_only_scene_reason(scene_key) is None

    def test_openai_facts_show_transcribe_setting_and_tts_model(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_TRANSCRIBE_MODEL", "whisper-x")
        _real_get_settings.cache_clear()
        facts = llm_policy.voice_model_facts()
        assert [f["label"] for f in facts] == ["音声認識", "読み上げ"]
        assert facts[0]["model"] == "whisper-x"
        assert facts[1]["model"] == "tts-1"
        display = llm_policy.voice_display_model()
        assert "whisper-x" in display and "tts-1" in display

    def test_tts_model_literal_matches_core_tts_source(self):
        """``core/tts.py`` のハードコード値とのずれを検出する（表示の正直さ）。"""
        tts_src = (Path(__file__).resolve().parents[1] / "core" / "tts.py").read_text(encoding="utf-8")
        assert f'model="{llm_policy._OPENAI_TTS_MODEL}"' in tts_src  # noqa: SLF001

    def test_non_openai_provider_is_honest_about_stt(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "google")
        _real_get_settings.cache_clear()
        facts = llm_policy.voice_model_facts()
        assert facts[0]["model"] == llm_policy._VOICE_UNSUPPORTED_LABEL  # noqa: SLF001
        assert facts[1]["model"] == "google-tts"

    def test_voice_facts_do_not_leak_tier_names(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        _real_get_settings.cache_clear()
        text = llm_policy.voice_display_model().lower()
        for tier in ("fast", "standard", "deep", "analysis"):
            assert tier not in text


# ===========================================================================
# 11. iter_env_seeds（レビュー指摘 J2）— シードの書き込みキーの正本
# ===========================================================================


class TestIterEnvSeeds:
    def _seed_map(self):
        return {seed.scene_key: seed for seed in llm_policy.iter_env_seeds()}

    def test_apparatus_env_seeds_the_scene_key_not_the_feature_key(self, monkeypatch):
        monkeypatch.setenv("APPARATUS_LLM_MODEL", "gpt-4o")
        _real_get_settings.cache_clear()
        seeds = self._seed_map()
        assert llm_policy.SCENE_PIPELINE_VISION in seeds
        assert "pipeline:apparatus_semantics" not in seeds
        seed = seeds[llm_policy.SCENE_PIPELINE_VISION]
        assert seed.model == "gpt-4o"
        assert seed.env_label == "APPARATUS_LLM_MODEL"
        # 旧実装が書いた feature キーは移行対象として列挙される
        assert set(seed.legacy_scene_keys) == {
            "pipeline:apparatus_semantics",
            "deliberation:figure_reanalysis",
        }

    def test_direct_env_features_keep_feature_keys(self, monkeypatch):
        monkeypatch.setenv("CTXEXPL_LLM_MODEL", "ctx-model")
        _real_get_settings.cache_clear()
        seeds = self._seed_map()
        assert seeds["pipeline:contextual_explanation"].model == "ctx-model"
        assert seeds["pipeline:contextual_explanation"].legacy_scene_keys == ()

    def test_empty_env_values_are_not_seeded(self, monkeypatch):
        monkeypatch.delenv("TENSION_LLM_MODEL", raising=False)
        _real_get_settings.cache_clear()
        assert llm_policy.SCENE_LEARNING_BACKGROUND not in self._seed_map()

    def test_read_only_scene_is_not_seeded(self, monkeypatch):
        monkeypatch.setenv("LLM_TRANSCRIBE_MODEL", "whisper-1")
        _real_get_settings.cache_clear()
        assert llm_policy.SCENE_LEARNING_VOICE not in self._seed_map()

    def test_seed_keys_are_all_writable_scene_or_feature_keys(self, monkeypatch):
        monkeypatch.setenv("APPARATUS_LLM_MODEL", "gpt-4o")
        monkeypatch.setenv("TENSION_LLM_MODEL", "t-model")
        monkeypatch.setenv("ANCHOR_LLM_MODEL", "a-model")
        _real_get_settings.cache_clear()
        for seed in llm_policy.iter_env_seeds():
            assert llm_policy.is_known_scene_key(seed.scene_key), seed.scene_key
            assert not llm_policy.is_read_only_scene(seed.scene_key)


# ===========================================================================
# 12. 共有 settings キャッシュ競合への免疫（フルスイート flake の回帰固定）
# ===========================================================================


class TestSettingsCacheRaceImmunity:
    def test_resolution_ignores_stale_shared_settings_cache(self, monkeypatch):
        """worker デーモンスレッドの in-flight ``Settings()`` 構築が ``cache_clear()``
        後に stale な結果を共有キャッシュへ格納しても、本モジュールのモデル解決が
        影響を受けないこと（モジュール docstring の flake の決定論的再現）。

        手順: ①stale な env でキャッシュを温める ②env を差し替えるが cache_clear
        **しない**（= スレッドがクリア後に旧 env の結果を格納した状態の再現）
        ③解決結果は現在の env（fresh 値）を読むこと。``_uncached_settings``
        フィクスチャを外すとこのテストは stale 値を返して落ちる。
        """
        monkeypatch.delenv("DELIBERATION_LLM_MODEL", raising=False)
        # ① 共有キャッシュに stale な Settings を格納
        monkeypatch.setenv("LLM_FAST_MODEL", "stale-fast-model")
        _real_get_settings.cache_clear()
        assert _real_get_settings().llm_fast_model == "stale-fast-model"
        # ② env は更新するが共有キャッシュはあえて温存（レース後の状態を再現）
        monkeypatch.setenv("LLM_FAST_MODEL", "fresh-fast-model")
        assert _real_get_settings().llm_fast_model == "stale-fast-model"  # 前提の確認
        # ③ llm_policy の解決は共有キャッシュを経由せず現在の env を読む
        resolved = llm_policy.resolve_scene_model("deliberation:chat")
        assert resolved.model == "fresh-fast-model"
        assert resolved.source == llm_policy.SOURCE_TIER_DEFAULT
