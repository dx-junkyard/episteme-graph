"""LLM モデル選択（M層）API のテスト — routes/llm_models.py。

正本: docs/features/llm_model_selection_design.md §7・§11。

``tests/test_llm_usage_api.py`` と同型の流儀: DB・TestClient は使わず、route ハンドラを
プレーン関数として直接呼び出す（``Depends`` は関数シグネチャの型注釈でしかないため、
``current_user=`` を直接渡せる）。ロール要求（``_require_teacher`` /
``_require_system_admin``）自体は route の ``dependant.dependencies`` を走査して検証する。

外部サービス（PostgreSQL / OpenAI）への実接続は行わない。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_HAS_FASTAPI = True
try:
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)


def _collect_forbidden_keys(obj, path: str = "") -> list[str]:
    """レスポンスを再帰的に走査し、金額 (M8) / tier 名 (M3) を匂わせるキー・値を収集する。"""
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
    """文字列値に tier 名 (fast/standard/deep/analysis) が現れないことを再帰確認する（M3）。

    scene_key / model id 自体には現れうる語（例: "learning_chat" 等）があるため、
    ``source`` / ``source_label`` / ``label`` の値のみを対象にする。
    """
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


# ===========================================================================
# 1. ルート登録（メソッド・権限依存）
# ===========================================================================


@_skip_no_fastapi
class TestRouteRegistration:
    def _dep_names(self, route) -> set[str]:
        return {dep.call.__name__ for dep in route.dependant.dependencies if dep.call}

    def _route(self, suffix: str, method: str):
        import routes.llm_models as llm_models_routes

        for r in llm_models_routes.router.routes:
            methods = getattr(r, "methods", set()) or set()
            if r.path.endswith(suffix) and method in methods:
                return r
        raise AssertionError(f"route not found: {method} *{suffix}")

    def test_catalog_requires_teacher_only(self):
        route = self._route("/catalog", "GET")
        deps = self._dep_names(route)
        assert "_require_teacher" in deps
        assert "_require_system_admin" not in deps

    def test_get_policies_requires_system_admin_only(self):
        route = self._route("/policies", "GET")
        deps = self._dep_names(route)
        assert "_require_system_admin" in deps
        assert "_require_teacher" not in deps

    def test_put_policies_requires_system_admin_only(self):
        route = self._route("/policies/{scene_key}", "PUT")
        deps = self._dep_names(route)
        assert "_require_system_admin" in deps
        assert "_require_teacher" not in deps

    def test_delete_policies_requires_system_admin_only(self):
        route = self._route("/policies/{scene_key}", "DELETE")
        deps = self._dep_names(route)
        assert "_require_system_admin" in deps
        assert "_require_teacher" not in deps

    def test_put_my_policies_requires_teacher(self):
        route = self._route("/my-policies/{scene_key}", "PUT")
        deps = self._dep_names(route)
        assert "_require_teacher" in deps

    def test_delete_my_policies_requires_teacher(self):
        route = self._route("/my-policies/{scene_key}", "DELETE")
        deps = self._dep_names(route)
        assert "_require_teacher" in deps

    def test_my_policies_request_model_has_no_user_id_field(self):
        """本人以外の user_id を指定できない構造保証（body に user_id フィールドが無い）。"""
        import routes.llm_models as llm_models_routes

        assert "user_id" not in llm_models_routes.PolicyUpsertRequest.model_fields


# ===========================================================================
# 2. GET /catalog
# ===========================================================================


_FAKE_CATALOG = {
    "models": [
        {"id": "gpt-5.2", "provider": "openai", "capabilities": ["text", "structured", "vision"],
         "default_effort": "medium", "cost_hint": "high", "note": ""},
        {"id": "gpt-5.4-mini", "provider": "openai", "capabilities": ["text", "structured"],
         "default_effort": "medium", "cost_hint": "medium", "note": ""},
        {"id": "gpt-4o", "provider": "openai", "capabilities": ["text", "structured", "vision"],
         "default_effort": None, "cost_hint": "medium", "note": ""},
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
class TestCatalogHandler:
    def test_catalog_available_lists_all_scenes_with_options(self, monkeypatch):
        import routes.llm_models as llm_models_routes

        monkeypatch.setattr(llm_models_routes.llm_policy, "load_catalog", lambda: _FAKE_CATALOG)
        monkeypatch.setattr(llm_models_routes.llm_policy, "catalog_models", _fake_catalog_models())

        result = llm_models_routes.get_catalog(scene=None, current_user={"id": "teacher-1", "role": "TEACHER"})

        assert result["catalog_available"] is True
        scene_keys = {s["scene_key"] for s in result["scenes"]}
        assert scene_keys == set(llm_models_routes.llm_policy.SCENES.keys())
        for scene in result["scenes"]:
            assert scene["options"], f"scene {scene['scene_key']} has no options"
            assert "effective" in scene and "model" in scene["effective"]

    def test_catalog_unavailable_falls_back_to_single_current_option(self, monkeypatch):
        import routes.llm_models as llm_models_routes

        monkeypatch.setattr(llm_models_routes.llm_policy, "load_catalog", lambda: None)

        result = llm_models_routes.get_catalog(
            scene="learning_chat", current_user={"id": "teacher-1", "role": "TEACHER"}
        )

        assert result["catalog_available"] is False
        scene = result["scenes"][0]
        assert len(scene["options"]) == 1
        assert scene["options"][0]["is_current"] is True
        assert scene["options"][0]["id"] == scene["effective"]["model"]

    def test_pipeline_vision_scene_options_are_vision_only(self, monkeypatch):
        import routes.llm_models as llm_models_routes

        monkeypatch.setattr(llm_models_routes.llm_policy, "load_catalog", lambda: _FAKE_CATALOG)
        monkeypatch.setattr(llm_models_routes.llm_policy, "catalog_models", _fake_catalog_models())

        result = llm_models_routes.get_catalog(
            scene=llm_models_routes.llm_policy.SCENE_PIPELINE_VISION,
            current_user={"id": "teacher-1", "role": "TEACHER"},
        )
        scene = result["scenes"][0]
        option_ids = {o["id"] for o in scene["options"]}
        assert option_ids == {"gpt-5.2", "gpt-4o"}  # gpt-5.4-mini はvision非対応なので除外

    def test_unknown_scene_param_is_422(self, monkeypatch):
        import routes.llm_models as llm_models_routes
        from fastapi import HTTPException

        monkeypatch.setattr(llm_models_routes.llm_policy, "load_catalog", lambda: _FAKE_CATALOG)

        with pytest.raises(HTTPException) as exc_info:
            llm_models_routes.get_catalog(
                scene="does_not_exist", current_user={"id": "teacher-1", "role": "TEACHER"}
            )
        assert exc_info.value.status_code == 422

    def test_catalog_response_has_no_cost_usd_or_tier_labels(self, monkeypatch):
        import routes.llm_models as llm_models_routes

        monkeypatch.setattr(llm_models_routes.llm_policy, "load_catalog", lambda: _FAKE_CATALOG)
        monkeypatch.setattr(llm_models_routes.llm_policy, "catalog_models", _fake_catalog_models())

        result = llm_models_routes.get_catalog(scene=None, current_user={"id": "teacher-1", "role": "TEACHER"})

        assert _collect_forbidden_keys(result) == []
        assert _collect_tier_name_leaks(result) == []


# ===========================================================================
# 3. GET /policies（SYSTEM_ADMIN）
# ===========================================================================


class _FakeSession:
    def __init__(self):
        self.closed = False
        self.committed = False
        self.rolled_back = False

    def close(self):
        self.closed = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


@_skip_no_fastapi
class TestGetPoliciesHandler:
    def test_lists_system_rows_and_env_facts(self, monkeypatch):
        import routes.llm_models as llm_models_routes

        monkeypatch.setattr(llm_models_routes, "get_session", lambda: _FakeSession())
        monkeypatch.setattr(
            llm_models_routes.llm_policy_store, "list_system_policies", lambda session: [{"scene_key": "pipeline", "model": "x"}]
        )

        result = llm_models_routes.list_system_policies(current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"})

        # Phase 4: scene-level 行は is_feature_level=False + scene label が付与される。
        assert result["policies"] == [
            {
                "scene_key": "pipeline",
                "model": "x",
                "is_feature_level": False,
                "label": "教材の解析",
            }
        ]
        scene_keys = {s["scene_key"] for s in result["scenes"]}
        assert scene_keys == set(llm_models_routes.llm_policy.SCENES.keys())
        for scene in result["scenes"]:
            assert "current" in scene and "model" in scene["current"]


# ===========================================================================
# 4. PUT/DELETE /policies/{scene_key}（SYSTEM_ADMIN, fail-closed 検証）
# ===========================================================================


@_skip_no_fastapi
class TestPutSystemPolicy:
    def _setup(self, monkeypatch, *, catalog=_FAKE_CATALOG):
        import routes.llm_models as llm_models_routes

        monkeypatch.setattr(llm_models_routes.llm_policy, "load_catalog", lambda: catalog)
        monkeypatch.setattr(llm_models_routes.llm_policy, "catalog_models", _fake_catalog_models(catalog) if catalog else (lambda **k: []))
        monkeypatch.setattr(llm_models_routes, "get_session", lambda: _FakeSession())
        return llm_models_routes

    def test_unknown_scene_key_is_422(self, monkeypatch):
        llm_models_routes = self._setup(monkeypatch)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            llm_models_routes.put_system_policy(
                "not_a_real_scene",
                llm_models_routes.PolicyUpsertRequest(model="gpt-5.2"),
                current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"},
            )
        assert exc_info.value.status_code == 422

    def test_model_not_in_catalog_is_422(self, monkeypatch):
        llm_models_routes = self._setup(monkeypatch)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            llm_models_routes.put_system_policy(
                "learning_chat",
                llm_models_routes.PolicyUpsertRequest(model="totally-fake-model"),
                current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"},
            )
        assert exc_info.value.status_code == 422

    def test_catalog_missing_is_422(self, monkeypatch):
        llm_models_routes = self._setup(monkeypatch, catalog=None)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            llm_models_routes.put_system_policy(
                "learning_chat",
                llm_models_routes.PolicyUpsertRequest(model="gpt-5.2"),
                current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"},
            )
        assert exc_info.value.status_code == 422

    def test_vision_scene_rejects_non_vision_model(self, monkeypatch):
        llm_models_routes = self._setup(monkeypatch)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            llm_models_routes.put_system_policy(
                llm_models_routes.llm_policy.SCENE_PIPELINE_VISION,
                llm_models_routes.PolicyUpsertRequest(model="gpt-5.4-mini"),  # non-vision
                current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"},
            )
        assert exc_info.value.status_code == 422

    def test_happy_path_upserts_and_audits(self, monkeypatch):
        llm_models_routes = self._setup(monkeypatch)
        captured_upsert = {}
        captured_audit = {}

        monkeypatch.setattr(llm_models_routes.llm_policy_store, "get_policy", lambda session, **kw: None)

        def fake_upsert(session, **kwargs):
            captured_upsert.update(kwargs)
            return {"scene_key": kwargs["scene_key"], "model": kwargs["model"]}

        monkeypatch.setattr(llm_models_routes.llm_policy_store, "upsert_policy", fake_upsert)
        monkeypatch.setattr(
            llm_models_routes.services,
            "record_review_event",
            lambda entity_type, entity_id, old, new, user_id, metadata=None: captured_audit.update(
                {"entity_type": entity_type, "entity_id": entity_id, "old": old, "new": new, "user_id": user_id}
            ),
        )

        result = llm_models_routes.put_system_policy(
            "learning_chat",
            llm_models_routes.PolicyUpsertRequest(model="gpt-5.2", note="test"),
            current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"},
        )

        assert result["model"] == "gpt-5.2"
        assert captured_upsert["scope"] == "system"
        assert captured_upsert["user_id"] is None
        assert captured_upsert["scene_key"] == "learning_chat"
        assert captured_audit["entity_type"] == llm_models_routes.AUDIT_ENTITY_LLM_MODEL_POLICY
        assert captured_audit["entity_id"] == "learning_chat"
        assert captured_audit["new"] == "gpt-5.2"
        assert captured_audit["user_id"] == "admin-1"

    def test_delete_missing_is_404(self, monkeypatch):
        llm_models_routes = self._setup(monkeypatch)
        from fastapi import HTTPException

        monkeypatch.setattr(llm_models_routes.llm_policy_store, "get_policy", lambda session, **kw: None)

        with pytest.raises(HTTPException) as exc_info:
            llm_models_routes.delete_system_policy(
                "learning_chat", current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"}
            )
        assert exc_info.value.status_code == 404

    def test_delete_happy_path_audits(self, monkeypatch):
        llm_models_routes = self._setup(monkeypatch)
        captured_audit = {}

        monkeypatch.setattr(
            llm_models_routes.llm_policy_store, "get_policy",
            lambda session, **kw: {"model": "gpt-5.2"},
        )
        monkeypatch.setattr(llm_models_routes.llm_policy_store, "delete_policy", lambda session, **kw: True)
        monkeypatch.setattr(
            llm_models_routes.services,
            "record_review_event",
            lambda entity_type, entity_id, old, new, user_id, metadata=None: captured_audit.update(
                {"entity_type": entity_type, "old": old, "new": new}
            ),
        )

        result = llm_models_routes.delete_system_policy(
            "learning_chat", current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"}
        )
        assert result == {"deleted": True}
        assert captured_audit["entity_type"] == llm_models_routes.AUDIT_ENTITY_LLM_MODEL_POLICY
        assert captured_audit["old"] == "gpt-5.2"
        assert captured_audit["new"] == ""


# ===========================================================================
# 5. PUT/DELETE /my-policies/{scene_key}（TEACHER・本人固定）
# ===========================================================================


@_skip_no_fastapi
class TestMyPolicies:
    def _setup(self, monkeypatch):
        import routes.llm_models as llm_models_routes

        monkeypatch.setattr(llm_models_routes.llm_policy, "load_catalog", lambda: _FAKE_CATALOG)
        monkeypatch.setattr(llm_models_routes.llm_policy, "catalog_models", _fake_catalog_models())
        monkeypatch.setattr(llm_models_routes, "get_session", lambda: _FakeSession())
        monkeypatch.setattr(llm_models_routes.llm_policy_store, "get_policy", lambda session, **kw: None)
        return llm_models_routes

    def test_put_my_policy_always_uses_authenticated_user_id(self, monkeypatch):
        llm_models_routes = self._setup(monkeypatch)
        captured = {}

        def fake_upsert(session, **kwargs):
            captured.update(kwargs)
            return {"scene_key": kwargs["scene_key"], "model": kwargs["model"]}

        monkeypatch.setattr(llm_models_routes.llm_policy_store, "upsert_policy", fake_upsert)
        monkeypatch.setattr(llm_models_routes.services, "record_review_event", lambda *a, **k: None)

        llm_models_routes.put_my_policy(
            "learning_chat",
            llm_models_routes.PolicyUpsertRequest(model="gpt-5.2"),
            current_user={"id": "teacher-b", "role": "TEACHER"},
        )

        assert captured["scope"] == "user"
        assert captured["user_id"] == "teacher-b"  # body に user_id が無いのでトークンの id が使われる

    def test_delete_my_policy_scopes_to_authenticated_user(self, monkeypatch):
        llm_models_routes = self._setup(monkeypatch)
        captured = {}

        monkeypatch.setattr(
            llm_models_routes.llm_policy_store, "get_policy",
            lambda session, **kw: {"model": "gpt-5.2"} if kw.get("user_id") == "teacher-b" else None,
        )

        def fake_delete(session, **kwargs):
            captured.update(kwargs)
            return True

        monkeypatch.setattr(llm_models_routes.llm_policy_store, "delete_policy", fake_delete)
        monkeypatch.setattr(llm_models_routes.services, "record_review_event", lambda *a, **k: None)

        result = llm_models_routes.delete_my_policy(
            "learning_chat", current_user={"id": "teacher-b", "role": "TEACHER"}
        )

        assert result == {"deleted": True}
        assert captured["scope"] == "user"
        assert captured["user_id"] == "teacher-b"

    def test_delete_my_policy_missing_is_404(self, monkeypatch):
        llm_models_routes = self._setup(monkeypatch)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            llm_models_routes.delete_my_policy(
                "learning_chat", current_user={"id": "teacher-b", "role": "TEACHER"}
            )
        assert exc_info.value.status_code == 404
