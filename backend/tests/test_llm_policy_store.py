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

import os
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


class _FakeSeedSession:
    """``_seed_one`` の INSERT ... ON CONFLICT DO NOTHING RETURNING id を模倣する。

    実 SQL は解釈せず、呼び出しパラメータ（scene_key の既出集合）だけで
    「初回は挿入、2回目以降は None（何もしない）」を再現する。
    """

    def __init__(self):
        self.existing: set[str] = set()
        self.insert_calls = 0

    def execute(self, stmt, params):
        if "scene_key" in params and "model" in params and "note" in params:
            self.insert_calls += 1
            scene_key = params["scene_key"]
            if scene_key in self.existing:
                return _FakeSeedResult(None)
            self.existing.add(scene_key)
            return _FakeSeedResult({"id": "fake-id"})
        raise AssertionError(f"unexpected query in fake seed session: {params!r}")


class TestSeedEnvPoliciesIdempotency:
    def _fake_settings(self, **overrides):
        attrs = {attr: "" for attr, _tier in llm_policy._FEATURE_ENV_SETTINGS.values()}  # noqa: SLF001
        attrs.update(overrides)
        return types.SimpleNamespace(**attrs)

    def test_seeds_only_nonempty_env_values_and_is_idempotent(self, monkeypatch):
        fake_settings = self._fake_settings(
            tension_llm_model="custom-tension-model",
            apparatus_llm_model="custom-vision-model",
        )
        monkeypatch.setattr(llm_policy_store, "_settings", lambda: fake_settings)
        monkeypatch.setenv("CTXEXPL_LLM_MODEL", "custom-ctx-model")

        expected_features = {
            feature
            for feature, (attr, _tier) in llm_policy._FEATURE_ENV_SETTINGS.items()  # noqa: SLF001
            if getattr(fake_settings, attr, "")
        }
        expected_features |= {
            feature
            for feature, (env_name, _tier) in llm_policy._FEATURE_DIRECT_ENV.items()  # noqa: SLF001
            if (os.getenv(env_name) or "")
        }
        assert expected_features == {
            "learning:tension",
            "pipeline:apparatus_semantics",
            "deliberation:figure_reanalysis",
            "pipeline:contextual_explanation",
        }

        session = _FakeSeedSession()
        first_count = llm_policy_store.seed_env_policies(session)
        assert first_count == len(expected_features)
        assert session.existing == expected_features

        second_count = llm_policy_store.seed_env_policies(session)
        assert second_count == 0
        # 2回目も同じ scene_key に対して INSERT 文自体は発行される（ON CONFLICT で吸収）が
        # 既存行は絶対に上書きしない（挿入は起きない）ことを existing 集合の不変で確認する。
        assert session.existing == expected_features

    def test_empty_env_yields_zero_inserts(self, monkeypatch):
        fake_settings = self._fake_settings()
        monkeypatch.setattr(llm_policy_store, "_settings", lambda: fake_settings)
        monkeypatch.delenv("CTXEXPL_LLM_MODEL", raising=False)

        session = _FakeSeedSession()
        assert llm_policy_store.seed_env_policies(session) == 0
        assert session.existing == set()
