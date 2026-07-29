"""LLM モデル選択（M層）Phase 4 のテスト — ステージ別（pipeline:<stage>）指定 UI 用 API。

正本: docs/features/llm_model_selection_design.md §6.1「▸ ステージ別に指定する（詳細）」・
§6.6「▸ ステージ別の指定（N件）」・§10 Phase 4。

``tests/test_llm_model_policy_api.py`` と同型の流儀: DB・TestClient は使わず、route
ハンドラをプレーン関数として直接呼び出す（``Depends`` は関数シグネチャの型注釈でしか
ないため、``current_user=`` を直接渡せる）。外部サービス（PostgreSQL / OpenAI）への
実接続は行わない。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_SRC_DIR = BACKEND.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

_HAS_FASTAPI = True
try:
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)

from core.config import get_settings as _real_get_settings  # noqa: E402
import core.llm_policy as llm_policy  # noqa: E402
from core.document_pipeline.orchestrator import LLM_STAGE_NAMES, PIPELINE_STAGES  # noqa: E402


def _collect_forbidden_keys(obj, path: str = "") -> list[str]:
    """レスポンスを再帰的に走査し、金額 (M8) を匂わせるキー・値を収集する。"""
    offending: list[str] = []
    forbidden_key_substrings = ("cost_usd", "price")
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_lower = str(key).lower()
            if any(sub in key_lower for sub in forbidden_key_substrings):
                offending.append(f"{path}.{key}" if path else str(key))
            offending.extend(_collect_forbidden_keys(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            offending.extend(_collect_forbidden_keys(item, f"{path}[{i}]"))
    return offending


def _collect_tier_name_leaks(obj) -> list[str]:
    """``source`` / ``source_label`` / ``label`` の値に tier 名が現れないことを確認する(M3)。"""
    offending: list[str] = []
    tier_words = ("fast", "standard", "deep", "analysis")

    def _walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("source_label", "label") and isinstance(value, str):
                    lowered = value.lower()
                    for word in tier_words:
                        if word in lowered:
                            offending.append(f"{path}.{key}={value!r}")
                _walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    _walk(obj, "")
    return offending


@pytest.fixture(autouse=True)
def _reset_policy_state():
    yield
    llm_policy.reset_policy_backend_for_tests()
    _real_get_settings.cache_clear()


class _FakeBackend:
    """``core/llm_policy.py`` の PolicyBackend Protocol を満たすテスト用 fake。

    ``tests/test_llm_policy.py::_FakeBackend`` と同型（feature/scene_key の区別はせず、
    user_id の有無だけで system/user 行を切り替える単純化版）。
    """

    def __init__(self, *, system=None, user=None):
        self._system = system
        self._user = user

    def lookup(self, scene_key, feature, user_id):
        if user_id is not None:
            return self._user
        return self._system


# ===========================================================================
# 1. core/llm_policy.py::PIPELINE_STAGE_LABELS — orchestrator との相互整合
# ===========================================================================


class TestPipelineStageLabelsCoverage:
    def test_label_keys_match_orchestrator_llm_stage_names(self):
        """ステージが増減したのに一方だけ更新し忘れたら落ちる構造テスト。"""
        assert set(llm_policy.PIPELINE_STAGE_LABELS.keys()) == set(LLM_STAGE_NAMES)

    def test_every_label_is_non_empty_and_not_the_stage_name_itself(self):
        for stage, label in llm_policy.PIPELINE_STAGE_LABELS.items():
            assert isinstance(label, str) and label.strip()
            assert label != stage, f"stage {stage!r} has no real Japanese label"

    def test_pipeline_stage_label_helper_falls_back_to_stage_name(self):
        assert llm_policy.pipeline_stage_label("not_a_real_stage") == "not_a_real_stage"
        assert llm_policy.pipeline_stage_label("paper_skeleton") == "論文骨格の仮説化"


# ===========================================================================
# 2. GET /api/admin/llm-models/pipeline-stages
# ===========================================================================


@_skip_no_fastapi
class TestPipelineStagesEndpoint:
    def test_requires_teacher_not_system_admin(self):
        import routes.llm_models as llm_models_routes

        route = next(
            r
            for r in llm_models_routes.router.routes
            if r.path.endswith("/pipeline-stages") and "GET" in (getattr(r, "methods", set()) or set())
        )
        dep_names = {dep.call.__name__ for dep in route.dependant.dependencies if dep.call}
        assert "_require_teacher" in dep_names
        assert "_require_system_admin" not in dep_names

    def test_stages_follow_pipeline_stages_order_and_llm_only(self):
        import routes.llm_models as llm_models_routes

        result = llm_models_routes.get_pipeline_stages(
            current_user={"id": "teacher-1", "role": "TEACHER"}
        )
        stage_names = [s["stage"] for s in result["stages"]]

        expected = [s for s in PIPELINE_STAGES if s in LLM_STAGE_NAMES]
        assert stage_names == expected
        # 非 LLM ステージ（document_structure 等）は含まれない。
        assert "document_structure" not in stage_names
        assert "grobid_parse" not in stage_names
        assert len(stage_names) == len(LLM_STAGE_NAMES)

    def test_each_stage_has_feature_label_vision_and_effective(self):
        import routes.llm_models as llm_models_routes

        result = llm_models_routes.get_pipeline_stages(
            current_user={"id": "teacher-1", "role": "TEACHER"}
        )
        for entry in result["stages"]:
            assert entry["feature"] == f"pipeline:{entry['stage']}"
            assert entry["label"] == llm_policy.pipeline_stage_label(entry["stage"])
            assert isinstance(entry["vision"], bool)
            assert "effective" in entry
            assert set(entry["effective"].keys()) == {"model", "source", "source_label"}

    def test_only_apparatus_semantics_is_vision(self):
        import routes.llm_models as llm_models_routes

        result = llm_models_routes.get_pipeline_stages(
            current_user={"id": "teacher-1", "role": "TEACHER"}
        )
        vision_stages = {s["stage"] for s in result["stages"] if s["vision"]}
        assert vision_stages == {"apparatus_semantics"}

    def test_effective_reflects_authenticated_users_policy(self, monkeypatch):
        import routes.llm_models as llm_models_routes

        backend = _FakeBackend(
            system=llm_policy.PolicyRow(model="system-model", scope="system"),
            user=llm_policy.PolicyRow(model="user-model", scope="user"),
        )
        llm_policy.set_policy_backend(backend)

        result_a = llm_models_routes.get_pipeline_stages(
            current_user={"id": "teacher-a", "role": "TEACHER"}
        )
        claim_qual_a = next(s for s in result_a["stages"] if s["stage"] == "claim_qualification")
        assert claim_qual_a["effective"]["model"] == "user-model"
        assert claim_qual_a["effective"]["source"] == llm_policy.SOURCE_USER_POLICY

    def test_catalog_available_flag_reflects_load_catalog(self, monkeypatch):
        import routes.llm_models as llm_models_routes

        monkeypatch.setattr(llm_models_routes.llm_policy, "load_catalog", lambda: None)
        result = llm_models_routes.get_pipeline_stages(
            current_user={"id": "teacher-1", "role": "TEACHER"}
        )
        assert result["catalog_available"] is False

        monkeypatch.setattr(
            llm_models_routes.llm_policy,
            "load_catalog",
            lambda: {"models": [{"id": "gpt-5.2", "provider": "openai", "capabilities": ["text"]}]},
        )
        result2 = llm_models_routes.get_pipeline_stages(
            current_user={"id": "teacher-1", "role": "TEACHER"}
        )
        assert result2["catalog_available"] is True

    def test_response_has_no_cost_usd_or_tier_labels(self):
        import routes.llm_models as llm_models_routes

        result = llm_models_routes.get_pipeline_stages(
            current_user={"id": "teacher-1", "role": "TEACHER"}
        )
        assert _collect_forbidden_keys(result) == []
        assert _collect_tier_name_leaks(result) == []


# ===========================================================================
# 3. GET /policies — is_feature_level / label（システム既定一覧のステージ別対応）
# ===========================================================================


class _FakeSession:
    def close(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


@_skip_no_fastapi
class TestPoliciesListFeatureLevelRows:
    def test_scene_level_row_is_not_feature_level(self, monkeypatch):
        import routes.llm_models as llm_models_routes

        monkeypatch.setattr(llm_models_routes, "get_session", lambda: _FakeSession())
        monkeypatch.setattr(
            llm_models_routes.llm_policy_store,
            "list_system_policies",
            lambda session: [{"scene_key": "pipeline", "model": "gpt-5.2"}],
        )
        result = llm_models_routes.list_system_policies(
            current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"}
        )
        row = result["policies"][0]
        assert row["is_feature_level"] is False
        assert row["label"] == llm_policy.SCENES[llm_policy.SCENE_PIPELINE]["label"]

    def test_stage_feature_row_is_feature_level_with_stage_label(self, monkeypatch):
        import routes.llm_models as llm_models_routes

        monkeypatch.setattr(llm_models_routes, "get_session", lambda: _FakeSession())
        monkeypatch.setattr(
            llm_models_routes.llm_policy_store,
            "list_system_policies",
            lambda session: [{"scene_key": "pipeline:claim_qualification", "model": "gpt-5.4-mini"}],
        )
        result = llm_models_routes.list_system_policies(
            current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"}
        )
        row = result["policies"][0]
        assert row["is_feature_level"] is True
        assert row["label"] == llm_policy.pipeline_stage_label("claim_qualification")
        assert row["label"] == "主張の採否・原子化"

    def test_unrecognized_feature_key_falls_back_to_key_itself(self, monkeypatch):
        import routes.llm_models as llm_models_routes

        monkeypatch.setattr(llm_models_routes, "get_session", lambda: _FakeSession())
        monkeypatch.setattr(
            llm_models_routes.llm_policy_store,
            "list_system_policies",
            lambda session: [{"scene_key": "pipeline:not_a_real_stage", "model": "gpt-5.2"}],
        )
        result = llm_models_routes.list_system_policies(
            current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"}
        )
        row = result["policies"][0]
        assert row["is_feature_level"] is True
        assert row["label"] == "not_a_real_stage"


# ===========================================================================
# 4. PUT /policies/pipeline:apparatus_semantics — vision capability の fail-closed 検証
# ===========================================================================


_FAKE_CATALOG = {
    "models": [
        {"id": "gpt-5.2", "provider": "openai", "capabilities": ["text", "structured", "vision"],
         "default_effort": "medium", "cost_hint": "high", "note": ""},
        {"id": "gpt-5.4-mini", "provider": "openai", "capabilities": ["text", "structured"],
         "default_effort": "medium", "cost_hint": "medium", "note": ""},
    ]
}


def _fake_catalog_models(catalog=_FAKE_CATALOG):
    def _impl(*, capability=None):
        result = []
        for entry in catalog["models"]:
            if capability is not None and capability not in entry.get("capabilities", []):
                continue
            result.append(entry)
        return result

    return _impl


@_skip_no_fastapi
class TestStageFeatureVisionValidation:
    def test_non_vision_model_rejected_for_apparatus_semantics_feature_key(self, monkeypatch):
        import routes.llm_models as llm_models_routes
        from fastapi import HTTPException

        monkeypatch.setattr(llm_models_routes.llm_policy, "load_catalog", lambda: _FAKE_CATALOG)
        monkeypatch.setattr(llm_models_routes.llm_policy, "catalog_models", _fake_catalog_models())

        with pytest.raises(HTTPException) as exc_info:
            llm_models_routes.put_system_policy(
                "pipeline:apparatus_semantics",
                llm_models_routes.PolicyUpsertRequest(model="gpt-5.4-mini"),
                current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"},
            )
        assert exc_info.value.status_code == 422

    def test_vision_model_accepted_for_apparatus_semantics_feature_key(self, monkeypatch):
        import routes.llm_models as llm_models_routes

        monkeypatch.setattr(llm_models_routes.llm_policy, "load_catalog", lambda: _FAKE_CATALOG)
        monkeypatch.setattr(llm_models_routes.llm_policy, "catalog_models", _fake_catalog_models())
        monkeypatch.setattr(llm_models_routes, "get_session", lambda: _FakeSession())
        monkeypatch.setattr(
            llm_models_routes.llm_policy_store, "get_policy", lambda session, **kw: None
        )
        monkeypatch.setattr(
            llm_models_routes.llm_policy_store,
            "upsert_policy",
            lambda session, **kw: {"scene_key": kw["scene_key"], "model": kw["model"]},
        )
        monkeypatch.setattr(
            llm_models_routes.services, "record_review_event", lambda *a, **kw: None
        )

        result = llm_models_routes.put_system_policy(
            "pipeline:apparatus_semantics",
            llm_models_routes.PolicyUpsertRequest(model="gpt-5.2"),
            current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"},
        )
        assert result["model"] == "gpt-5.2"

    def test_unknown_stage_feature_key_is_rejected(self, monkeypatch):
        import routes.llm_models as llm_models_routes
        from fastapi import HTTPException

        monkeypatch.setattr(llm_models_routes.llm_policy, "load_catalog", lambda: _FAKE_CATALOG)
        monkeypatch.setattr(llm_models_routes.llm_policy, "catalog_models", _fake_catalog_models())

        with pytest.raises(HTTPException) as exc_info:
            llm_models_routes.put_system_policy(
                "pipeline:not_a_real_stage",
                llm_models_routes.PolicyUpsertRequest(model="gpt-5.2"),
                current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"},
            )
        assert exc_info.value.status_code == 422
