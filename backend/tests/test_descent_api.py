"""構造の降下路（Phase 3）— API（routes/descent.py）のテスト。

対象: GET /api/learning/courses/{course_id}/descent/ladder ・
GET .../descent/backstage-path。test_return_door_api.py と同型
（ルート関数を直接呼ぶ・monkeypatch・ソース静的検査。DB 不要）。

- learning_router の prefix と GET 2本の存在（書き込みメソッドなし）
- 受講ゲート（get_accessible_course_data — 本人のみ・fail-closed 404）
- element_type の 422 検証（equation / component / claim 以外は拒否・ゲート前に弾く）
- fail-closed 縮退（導出例外を 500 にせず available:false / 宣言+空 steps へ丸める）
- main.py への import + include_router 登録
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

from tests.guardrail_helpers import extract_function_source  # noqa: E402

_ROUTE_SRC = (ROOT / "backend" / "api" / "routes" / "descent.py").read_text(encoding="utf-8")
_MAIN_SRC = (ROOT / "backend" / "api" / "main.py").read_text(encoding="utf-8")


# ===========================================================================
# 1. ルート存在・GET のみ（読み取り専用の2本 — SD3/SD5）
# ===========================================================================


class TestRoutesExistAndAreGetOnly:
    def _route(self, path_suffix: str):
        from api.routes import descent

        for route in descent.learning_router.routes:
            if getattr(route, "path", "").endswith(path_suffix):
                return route
        return None

    def test_router_prefix_is_api_learning(self):
        from api.routes import descent

        assert descent.learning_router.prefix == "/api/learning"

    def test_ladder_route_exists_and_is_get_only(self):
        route = self._route("/descent/ladder")
        assert route is not None, "ladder ルートが登録されていない"
        assert route.path == "/api/learning/courses/{course_id}/descent/ladder"
        assert set(route.methods) == {"GET"}

    def test_backstage_path_route_exists_and_is_get_only(self):
        route = self._route("/descent/backstage-path")
        assert route is not None, "backstage-path ルートが登録されていない"
        assert route.path == "/api/learning/courses/{course_id}/descent/backstage-path"
        assert set(route.methods) == {"GET"}

    def test_router_has_exactly_two_descent_routes(self):
        from api.routes import descent

        descent_paths = [
            getattr(r, "path", "") for r in descent.learning_router.routes
            if "/descent/" in getattr(r, "path", "")
        ]
        assert len(descent_paths) == 2


# ===========================================================================
# 2. element_type の 422 検証（equation / component / claim 以外は拒否）
# ===========================================================================


class TestElementTypeValidation:
    def test_supported_vocabulary_is_exactly_three_types(self):
        from api.routes import descent

        assert set(descent.SUPPORTED_ELEMENT_TYPES) == {
            "equation", "component", "claim",
        }

    @pytest.mark.parametrize("bad_type", ["figure", "derivation", "evidence", "", "EQUATION"])
    def test_ladder_rejects_unknown_element_type_with_422(self, monkeypatch, bad_type):
        from fastapi import HTTPException

        from api.routes import descent

        gate_calls: list[str] = []
        monkeypatch.setattr(
            descent, "get_accessible_course_data",
            lambda uid, cid: gate_calls.append(cid) or {"id": cid},
        )

        with pytest.raises(HTTPException) as exc_info:
            descent.get_descent_ladder(
                "course-1", element_type=bad_type, element_id="x",
                current_user={"id": "u1"},
            )
        assert exc_info.value.status_code == 422
        # 不正値はコース解決より前に弾く（無駄な DB 読みをしない）。
        assert gate_calls == []

    @pytest.mark.parametrize("bad_type", ["figure", "derivation", ""])
    def test_backstage_path_rejects_unknown_element_type_with_422(
        self, monkeypatch, bad_type
    ):
        from fastapi import HTTPException

        from api.routes import descent

        monkeypatch.setattr(
            descent, "get_accessible_course_data", lambda uid, cid: {"id": cid}
        )

        with pytest.raises(HTTPException) as exc_info:
            descent.get_descent_backstage_path(
                "course-1", element_type=bad_type, element_id="x",
                current_user={"id": "u1"},
            )
        assert exc_info.value.status_code == 422


# ===========================================================================
# 3. 受講ゲート（get_accessible_course_data — 本人のみ・fail-closed）
# ===========================================================================


class TestEnrollmentGate:
    def test_ladder_404_when_course_not_accessible(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import descent

        monkeypatch.setattr(descent, "get_accessible_course_data", lambda uid, cid: None)

        with pytest.raises(HTTPException) as exc_info:
            descent.get_descent_ladder(
                "course-x", element_type="equation", element_id="eq_1",
                current_user={"id": "u1"},
            )
        assert exc_info.value.status_code == 404

    def test_backstage_path_404_when_course_not_accessible(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import descent

        monkeypatch.setattr(descent, "get_accessible_course_data", lambda uid, cid: None)

        with pytest.raises(HTTPException) as exc_info:
            descent.get_descent_backstage_path(
                "course-x", element_type="claim", element_id="cl_1",
                current_user={"id": "u1"},
            )
        assert exc_info.value.status_code == 404

    def test_gate_receives_current_user_id_and_course_id(self, monkeypatch):
        from api.routes import descent

        seen: list[tuple[str, str]] = []

        def _gate(uid, cid):
            seen.append((uid, cid))
            return {"id": cid}

        monkeypatch.setattr(descent, "get_accessible_course_data", _gate)
        monkeypatch.setattr(descent, "build_ladder", lambda cd, cid, et, eid: {"available": False})

        descent.get_descent_ladder(
            "course-1", element_type="equation", element_id="eq_1",
            current_user={"id": "user-9"},
        )
        assert seen == [("user-9", "course-1")]

    def test_route_source_uses_gate_via_require_course(self):
        body = extract_function_source(_ROUTE_SRC, "_require_course")
        assert "get_accessible_course_data" in body
        for fn in ("get_descent_ladder", "get_descent_backstage_path"):
            fn_body = extract_function_source(_ROUTE_SRC, fn)
            assert "_require_course(" in fn_body
            assert 'current_user["id"]' in fn_body


# ===========================================================================
# 4. 応答のパススルーと fail-closed 縮退（500 にしない）
# ===========================================================================


class TestResponsesAndFailClosed:
    def _gate_ok(self, monkeypatch):
        from api.routes import descent

        monkeypatch.setattr(
            descent, "get_accessible_course_data", lambda uid, cid: {"id": cid}
        )
        return descent

    def test_ladder_returns_engine_result(self, monkeypatch):
        descent = self._gate_ok(monkeypatch)
        sentinel = {"available": True, "rungs": [{"kind": "recall_prompt", "text": "q"}]}
        captured: list[tuple] = []

        def _build(course_data, course_id, element_type, element_id):
            captured.append((course_data, course_id, element_type, element_id))
            return sentinel

        monkeypatch.setattr(descent, "build_ladder", _build)

        result = descent.get_descent_ladder(
            "course-1", element_type="equation", element_id="eq_1",
            current_user={"id": "u1"},
        )
        assert result is sentinel
        # ゲートが返した course_data がそのままエンジンへ渡る（版ビュー・受講スコープ）。
        assert captured == [({"id": "course-1"}, "course-1", "equation", "eq_1")]

    def test_ladder_engine_failure_degrades_to_available_false(self, monkeypatch):
        descent = self._gate_ok(monkeypatch)

        def _boom(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(descent, "build_ladder", _boom)

        result = descent.get_descent_ladder(
            "course-1", element_type="equation", element_id="eq_1",
            current_user={"id": "u1"},
        )
        assert result == {"available": False}

    def test_backstage_path_returns_engine_result(self, monkeypatch):
        descent = self._gate_ok(monkeypatch)
        sentinel = {"declaration": descent.BACKSTAGE_DECLARATION, "steps": [{"kind": "notation_patterns", "items": []}]}
        monkeypatch.setattr(descent, "build_backstage_path", lambda *a: sentinel)

        result = descent.get_descent_backstage_path(
            "course-1", element_type="component", element_id="cmp-1",
            current_user={"id": "u1"},
        )
        assert result is sentinel

    def test_backstage_failure_degrades_to_declaration_and_empty_steps(self, monkeypatch):
        descent = self._gate_ok(monkeypatch)

        def _boom(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(descent, "build_backstage_path", _boom)

        result = descent.get_descent_backstage_path(
            "course-1", element_type="claim", element_id="cl-1",
            current_user={"id": "u1"},
        )
        assert result == {"declaration": descent.BACKSTAGE_DECLARATION, "steps": []}

    def test_route_source_has_fail_closed_structure(self):
        """例外を 500 に漏らさない構造（except Exception → 縮退 DTO）の静的固定。"""
        ladder_body = extract_function_source(_ROUTE_SRC, "get_descent_ladder")
        assert "except Exception" in ladder_body
        assert '{"available": False}' in ladder_body
        backstage_body = extract_function_source(_ROUTE_SRC, "get_descent_backstage_path")
        assert "except Exception" in backstage_body
        assert '"declaration": BACKSTAGE_DECLARATION' in backstage_body
        assert '"steps": []' in backstage_body


# ===========================================================================
# 5. main.py への登録（直接 include — Tier 3-17c の平置き規約）
# ===========================================================================


class TestMainRegistration:
    def test_main_imports_descent_routes(self):
        assert "from routes import descent as descent_routes" in _MAIN_SRC

    def test_main_includes_learning_router(self):
        assert "app.include_router(descent_routes.learning_router)" in _MAIN_SRC
