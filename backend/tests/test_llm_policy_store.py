"""core/llm_policy_store.py（M層 Phase 1 の DB ポリシーバックエンド）のテスト。

正本: docs/features/llm_model_selection_design.md §3・§4・§11。

DB には接続しない。``DbPolicyBackend`` はセッションを自前で開閉するため、
``core.postgres.get_session`` 相当の呼び出し口（``llm_policy_store.get_session``）を
fake session に差し替えて検証する（``tests/test_llm_usage_recorder.py`` /
``tests/test_llm_usage_api.py`` と同型の fake-session 流儀。実 PostgreSQL 接続なし）。

観点:
  1. ``_pick_priority_row`` の優先順位（user+feature > user+scene > system+feature >
     system+scene）とユーザー分離（設計書 §11-9）
  2. ``DbPolicyBackend.lookup`` の fail-open（DB 例外時は None を返し、例外を漏らさない）
  3. TTL キャッシュ（同一キーの再呼び出しで session.execute が再実行されない）
  4. ``seed_env_policies`` の冪等性（既存行は上書きしない・2回目は0件挿入）
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

_backend_dir = str(Path(__file__).resolve().parents[1])
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import core.llm_policy as llm_policy  # noqa: E402
import core.llm_policy_store as llm_policy_store  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cache():
    llm_policy_store.invalidate()
    yield
    llm_policy_store.invalidate()


# ===========================================================================
# 1. 優先順位付け（_pick_priority_row）
# ===========================================================================


class TestPickPriorityRow:
    def _row(self, *, scope, scene_key, model, user_id=None, reasoning_effort=None):
        return {
            "scope": scope,
            "user_id": user_id,
            "scene_key": scene_key,
            "model": model,
            "reasoning_effort": reasoning_effort,
        }

    def test_user_feature_beats_everything(self):
        rows = [
            self._row(scope="user", scene_key="pipeline", model="user-scene-model", user_id="u1"),
            self._row(scope="user", scene_key="pipeline:claim_qualification", model="user-feature-model", user_id="u1"),
            self._row(scope="system", scene_key="pipeline:claim_qualification", model="system-feature-model"),
            self._row(scope="system", scene_key="pipeline", model="system-scene-model"),
        ]
        result = llm_policy_store._pick_priority_row(
            rows, scene_key="pipeline", feature="pipeline:claim_qualification", user_id="u1"
        )
        assert result.model == "user-feature-model"
        assert result.scope == "user"

    def test_user_scene_beats_system_when_no_user_feature_row(self):
        rows = [
            self._row(scope="user", scene_key="pipeline", model="user-scene-model", user_id="u1"),
            self._row(scope="system", scene_key="pipeline:claim_qualification", model="system-feature-model"),
            self._row(scope="system", scene_key="pipeline", model="system-scene-model"),
        ]
        result = llm_policy_store._pick_priority_row(
            rows, scene_key="pipeline", feature="pipeline:claim_qualification", user_id="u1"
        )
        assert result.model == "user-scene-model"
        assert result.scope == "user"

    def test_system_feature_beats_system_scene(self):
        rows = [
            self._row(scope="system", scene_key="pipeline:claim_qualification", model="system-feature-model"),
            self._row(scope="system", scene_key="pipeline", model="system-scene-model"),
        ]
        result = llm_policy_store._pick_priority_row(
            rows, scene_key="pipeline", feature="pipeline:claim_qualification", user_id=None
        )
        assert result.model == "system-feature-model"
        assert result.scope == "system"

    def test_no_rows_returns_none(self):
        result = llm_policy_store._pick_priority_row(
            [], scene_key="pipeline", feature="pipeline:claim_qualification", user_id="u1"
        )
        assert result is None

    def test_user_isolation_other_users_row_is_ignored(self):
        """教員 A の user 行は教員 B の解決に一切影響しない（設計書 §11-9）。"""
        rows = [
            self._row(scope="user", scene_key="pipeline", model="a-model", user_id="user-a"),
            self._row(scope="system", scene_key="pipeline", model="system-model"),
        ]
        result = llm_policy_store._pick_priority_row(
            rows, scene_key="pipeline", feature="pipeline", user_id="user-b"
        )
        assert result.model == "system-model"
        assert result.scope == "system"

    def test_none_user_id_only_considers_system_rows(self):
        rows = [
            self._row(scope="user", scene_key="pipeline", model="a-model", user_id="user-a"),
            self._row(scope="system", scene_key="pipeline", model="system-model"),
        ]
        result = llm_policy_store._pick_priority_row(
            rows, scene_key="pipeline", feature="pipeline", user_id=None
        )
        assert result.model == "system-model"


# ===========================================================================
# 2. DbPolicyBackend.lookup — fail-open + TTL キャッシュ
# ===========================================================================


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeLookupSession:
    def __init__(self, rows):
        self._rows = rows
        self.call_count = 0
        self.closed = False

    def execute(self, stmt, params):
        self.call_count += 1
        return _FakeMappingsResult(self._rows)

    def close(self):
        self.closed = True


class _RaisingSession:
    def execute(self, stmt, params):
        raise RuntimeError("db unavailable")

    def close(self):
        pass


class TestDbPolicyBackendLookup:
    def test_returns_policy_row_from_rows(self, monkeypatch):
        rows = [
            {
                "scope": "system",
                "user_id": None,
                "scene_key": "pipeline",
                "model": "system-model",
                "reasoning_effort": "medium",
            }
        ]
        fake_session = _FakeLookupSession(rows)
        monkeypatch.setattr(llm_policy_store, "get_session", lambda: fake_session)

        backend = llm_policy_store.DbPolicyBackend()
        result = backend.lookup("pipeline", "pipeline:claim_qualification", None)

        assert result is not None
        assert result.model == "system-model"
        assert result.scope == "system"
        assert fake_session.closed is True

    def test_fail_open_returns_none_on_exception(self, monkeypatch):
        monkeypatch.setattr(llm_policy_store, "get_session", lambda: _RaisingSession())

        backend = llm_policy_store.DbPolicyBackend()
        result = backend.lookup("pipeline", "pipeline:claim_qualification", None)

        assert result is None

    def test_cache_avoids_repeated_session_calls(self, monkeypatch):
        rows = [
            {
                "scope": "system",
                "user_id": None,
                "scene_key": "pipeline",
                "model": "system-model",
                "reasoning_effort": None,
            }
        ]
        fake_session_holder = {"session": _FakeLookupSession(rows)}
        monkeypatch.setattr(llm_policy_store, "get_session", lambda: fake_session_holder["session"])

        backend = llm_policy_store.DbPolicyBackend()
        first = backend.lookup("pipeline", "pipeline", None)
        second = backend.lookup("pipeline", "pipeline", None)

        assert first == second
        assert fake_session_holder["session"].call_count == 1

    def test_negative_cache_is_also_cached(self, monkeypatch):
        fake_session = _FakeLookupSession([])
        monkeypatch.setattr(llm_policy_store, "get_session", lambda: fake_session)

        backend = llm_policy_store.DbPolicyBackend()
        first = backend.lookup("atlas", "admin:atlas_skeleton", None)
        second = backend.lookup("atlas", "admin:atlas_skeleton", None)

        assert first is None
        assert second is None
        assert fake_session.call_count == 1

    def test_end_to_end_via_resolve_scene_model(self, monkeypatch):
        """resolve_scene_model() 経由でも DbPolicyBackend が正しく効くことを確認する
        （Phase 0 の NullPolicyBackend を差し替えるだけで挙動が変わることの実地検証）。
        """
        rows = [
            {
                "scope": "user",
                "user_id": "teacher-a",
                "scene_key": "learning:tension",
                "model": "user-picked-model",
                "reasoning_effort": None,
            }
        ]
        fake_session = _FakeLookupSession(rows)
        monkeypatch.setattr(llm_policy_store, "get_session", lambda: fake_session)
        llm_policy.set_policy_backend(llm_policy_store.DbPolicyBackend())
        try:
            from core.llm_usage.context import usage_context

            with usage_context("learning:tension", user_id="teacher-a"):
                resolved = llm_policy.resolve_scene_model("learning:tension")
            assert resolved.model == "user-picked-model"
            assert resolved.source == llm_policy.SOURCE_USER_POLICY
        finally:
            llm_policy.reset_policy_backend_for_tests()


# ===========================================================================
# 3. seed_env_policies — 冪等性
# ===========================================================================


class _FakeSeedResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeDeleteResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _FakeSeedSession:
    """``_seed_one`` の INSERT ... ON CONFLICT DO NOTHING RETURNING id と、
    ``_delete_legacy_env_seed_rows`` の DELETE を模倣する。

    実 SQL は解釈せず、呼び出しパラメータだけで「初回は挿入、2回目以降は None
    （何もしない）」と「旧シード行の削除」を再現する。
    """

    def __init__(self, *, preexisting: dict[str, tuple[str, str]] | None = None):
        # scene_key -> (model, note)。preexisting は「旧実装が既に書いた行」を模す。
        self.rows: dict[str, tuple[str, str]] = dict(preexisting or {})
        self.insert_calls = 0
        self.deleted: list[str] = []

    @property
    def existing(self) -> set[str]:
        return set(self.rows)

    def execute(self, stmt, params):
        if "scene_key" in params and "model" in params and "note" in params:
            self.insert_calls += 1
            scene_key = params["scene_key"]
            if scene_key in self.rows:
                return _FakeSeedResult(None)
            self.rows[scene_key] = (params["model"], params["note"])
            return _FakeSeedResult({"id": "fake-id"})
        if "keys" in params and "note" in params and "model" in params:
            removed = 0
            for key in params["keys"]:
                row = self.rows.get(key)
                if row is not None and row == (params["model"], params["note"]):
                    del self.rows[key]
                    self.deleted.append(key)
                    removed += 1
            return _FakeDeleteResult(removed)
        raise AssertionError(f"unexpected query in fake seed session: {params!r}")


def _fake_settings(**overrides):
    attrs = {attr: "" for attr, _tier in llm_policy._FEATURE_ENV_SETTINGS.values()}  # noqa: SLF001
    attrs.setdefault("llm_provider", "openai")
    attrs.update(overrides)
    return types.SimpleNamespace(**attrs)


@pytest.fixture
def clean_direct_env(monkeypatch):
    """``_FEATURE_DIRECT_ENV`` 由来の env を明示的に空にする（環境差の排除）。"""
    for _feature, (env_name, _tier) in llm_policy._FEATURE_DIRECT_ENV.items():  # noqa: SLF001
        monkeypatch.delenv(env_name, raising=False)


class TestSeedEnvPoliciesIdempotency:
    def test_seeds_use_ui_editable_keys_and_are_idempotent(self, monkeypatch, clean_direct_env):
        """レビュー指摘 J2: シードは **UI が編集するキー**（scene キー）で書く。

        旧実装は feature キー（``pipeline:apparatus_semantics`` 等）で書いていたため、
        ``_pick_priority_row`` の system+feature > system+scene により運用タブの
        scene 行が恒久的にシャドウされていた。
        """
        fake_settings = _fake_settings(
            tension_llm_model="custom-tension-model",
            apparatus_llm_model="custom-vision-model",
        )
        monkeypatch.setattr(llm_policy, "get_settings", lambda: fake_settings)
        monkeypatch.setenv("CTXEXPL_LLM_MODEL", "custom-ctx-model")

        expected_keys = {
            # 代表 feature が env を持つ scene は scene キーで1行
            llm_policy.SCENE_LEARNING_BACKGROUND,  # TENSION_LLM_MODEL
            llm_policy.SCENE_PIPELINE_VISION,      # APPARATUS_LLM_MODEL
            # _FEATURE_DIRECT_ENV は従来どおり feature キー（運用タブのステージ別で編集可）
            "pipeline:contextual_explanation",
        }

        session = _FakeSeedSession()
        first_count = llm_policy_store.seed_env_policies(session)
        assert session.existing == expected_keys
        assert first_count == len(expected_keys)
        # feature キーでは書かない（J2 の恒久シャドウを作らない）
        assert "pipeline:apparatus_semantics" not in session.existing
        assert "learning:tension" not in session.existing

        second_count = llm_policy_store.seed_env_policies(session)
        assert second_count == 0
        # 2回目も同じ scene_key に対して INSERT 文自体は発行される（ON CONFLICT で吸収）が
        # 既存行は絶対に上書きしない（挿入は起きない）ことを existing 集合の不変で確認する。
        assert session.existing == expected_keys

    def test_empty_env_yields_zero_inserts(self, monkeypatch, clean_direct_env):
        fake_settings = _fake_settings()
        monkeypatch.setattr(llm_policy, "get_settings", lambda: fake_settings)

        session = _FakeSeedSession()
        assert llm_policy_store.seed_env_policies(session) == 0
        assert session.existing == set()

    def test_scene_with_multiple_env_vars_keeps_feature_keys_for_the_others(
        self, monkeypatch, clean_direct_env
    ):
        """1 scene に別々の env を持つ feature が居る場合、代表以外は feature キーで残す。

        学習の裏方（TENSION / ANCHOR / RECON）は env が feature 単位に分かれているので、
        値を落とさないために feature キーでシードする（運用タブの「ステージ別の指定」で
        変更・解除できる）。
        """
        fake_settings = _fake_settings(
            tension_llm_model="t-model",
            anchor_llm_model="a-model",
            recon_llm_model="r-model",
        )
        monkeypatch.setattr(llm_policy, "get_settings", lambda: fake_settings)

        session = _FakeSeedSession()
        llm_policy_store.seed_env_policies(session)

        assert session.rows[llm_policy.SCENE_LEARNING_BACKGROUND][0] == "t-model"
        assert session.rows["learning:structure_anchor"][0] == "a-model"
        assert session.rows["admin:reconstruction_authoring"][0] == "r-model"

    def test_legacy_feature_seed_rows_are_migrated_to_the_scene_key(
        self, monkeypatch, clean_direct_env
    ):
        """旧シード行（同じ env・同じ値・同じ note）は削除して scene キーへ移行する。"""
        fake_settings = _fake_settings(apparatus_llm_model="gpt-4o")
        monkeypatch.setattr(llm_policy, "get_settings", lambda: fake_settings)

        note = "seeded from env: APPARATUS_LLM_MODEL"
        session = _FakeSeedSession(
            preexisting={
                "pipeline:apparatus_semantics": ("gpt-4o", note),
                "deliberation:figure_reanalysis": ("gpt-4o", note),
            }
        )
        llm_policy_store.seed_env_policies(session)

        assert llm_policy.SCENE_PIPELINE_VISION in session.rows
        assert set(session.deleted) == {
            "pipeline:apparatus_semantics",
            "deliberation:figure_reanalysis",
        }

    def test_human_edited_feature_rows_are_not_deleted(self, monkeypatch, clean_direct_env):
        """人が運用タブで編集した行（note が違う / model が env と違う）は削除しない（P4）。"""
        fake_settings = _fake_settings(apparatus_llm_model="gpt-4o")
        monkeypatch.setattr(llm_policy, "get_settings", lambda: fake_settings)

        session = _FakeSeedSession(
            preexisting={
                # note がシード由来だが model が env と違う（= UI で変更済み）
                "pipeline:apparatus_semantics": ("gpt-5.2", "seeded from env: APPARATUS_LLM_MODEL"),
                # note が空（= UI の upsert で書かれた行）
                "deliberation:figure_reanalysis": ("gpt-4o", ""),
            }
        )
        llm_policy_store.seed_env_policies(session)

        assert session.deleted == []
        assert "pipeline:apparatus_semantics" in session.rows
        assert "deliberation:figure_reanalysis" in session.rows

    def test_read_only_scene_is_never_seeded(self, monkeypatch, clean_direct_env):
        """J5: learning_voice は読み取り専用なのでポリシー行を作らない。"""
        fake_settings = _fake_settings()
        monkeypatch.setattr(llm_policy, "get_settings", lambda: fake_settings)

        session = _FakeSeedSession()
        llm_policy_store.seed_env_policies(session)
        assert llm_policy.SCENE_LEARNING_VOICE not in session.existing


class TestSeededKeyIsResolvedForItsFeatures:
    """J2 の回帰テスト: scene キーのシード行が、その scene の feature 解決に効く。"""

    def test_scene_row_applies_to_the_vision_feature(self):
        rows = [
            {
                "scope": "system",
                "user_id": None,
                "scene_key": llm_policy.SCENE_PIPELINE_VISION,
                "model": "gpt-5.2",
                "reasoning_effort": None,
            }
        ]
        result = llm_policy_store._pick_priority_row(
            rows,
            scene_key=llm_policy.SCENE_PIPELINE_VISION,
            feature="pipeline:apparatus_semantics",
            user_id=None,
        )
        assert result is not None and result.model == "gpt-5.2"
