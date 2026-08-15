"""帰還の扉（return_door_design.md Phase 2）— API のテスト。

対象: routes/cycle.py の GET return-door / GET todays-words。
test_understanding_cycle_api.py と同型（ルート関数を直接呼ぶ・monkeypatch）。
ガードレール性のある検査（GET のみ・受講ゲート・user ロールフィルタの構造固定・
経過日数語彙の不在）もここに置く。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

from tests.guardrail_helpers import extract_function_source  # noqa: E402

_CYCLE_ROUTE_SRC = (ROOT / "backend" / "api" / "routes" / "cycle.py").read_text(encoding="utf-8")
_QUERIES_SRC = (ROOT / "backend" / "core" / "cycle" / "queries.py").read_text(encoding="utf-8")
_DERIVE_SRC = (ROOT / "backend" / "core" / "cycle" / "derive.py").read_text(encoding="utf-8")


# ===========================================================================
# 1. ルート存在・GET のみ（読み取り専用）
# ===========================================================================


class TestRoutesExistAndAreGetOnly:
    def _route(self, path_suffix: str):
        from api.routes import cycle

        for route in cycle.learning_router.routes:
            if getattr(route, "path", "").endswith(path_suffix):
                return route
        return None

    def test_return_door_route_exists_and_is_get_only(self):
        route = self._route("/cycle/return-door")
        assert route is not None, "return-door ルートが登録されていない"
        assert route.path == "/api/learning/courses/{course_id}/cycle/return-door"
        assert set(route.methods) == {"GET"}

    def test_todays_words_route_exists_and_is_get_only(self):
        route = self._route("/cycle/todays-words")
        assert route is not None, "todays-words ルートが登録されていない"
        assert route.path == "/api/learning/courses/{course_id}/cycle/todays-words"
        assert set(route.methods) == {"GET"}

    def test_no_write_decorators_for_return_door_endpoints(self):
        """扉・トレイは読み取り専用（POST/PUT/PATCH/DELETE を持たない）。"""
        for suffix in ("cycle/return-door", "cycle/todays-words"):
            for verb in ("post", "put", "patch", "delete"):
                assert f'@learning_router.{verb}("/courses/{{course_id}}/{suffix}"' not in (
                    _CYCLE_ROUTE_SRC
                ), f"{suffix} に書き込みメソッド {verb} が生えている"


# ===========================================================================
# 2. 受講ゲート（get_accessible_course_data — 本人のみ・fail-closed）
# ===========================================================================


class TestEnrollmentGate:
    def test_return_door_404_when_course_not_accessible(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: None)

        with pytest.raises(HTTPException) as exc_info:
            cycle.get_cycle_return_door_route("course-x", current_user={"id": "u1"})
        assert exc_info.value.status_code == 404

    def test_todays_words_404_when_course_not_accessible(self, monkeypatch):
        from fastapi import HTTPException

        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: None)

        with pytest.raises(HTTPException) as exc_info:
            cycle.get_cycle_todays_words_route("course-x", current_user={"id": "u1"})
        assert exc_info.value.status_code == 404

    def test_route_functions_use_current_user_id_only(self):
        for fn in ("get_cycle_return_door_route", "get_cycle_todays_words_route"):
            body = extract_function_source(_CYCLE_ROUTE_SRC, fn)
            assert 'current_user["id"]' in body
            assert "get_accessible_course_data" in body


# ===========================================================================
# 3. return-door の応答（3部品の合成・fail-open）
# ===========================================================================


class TestReturnDoorRoute:
    def test_returns_built_door(self, monkeypatch):
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(
            cycle, "fetch_active_leave_note",
            lambda uid, cid: {"id": "n1", "text": "書き置き", "created_at": "a"},
        )
        monkeypatch.setattr(
            cycle, "fetch_active_carryover",
            lambda uid, cid: {"id": "c1", "text": "問い", "created_at": "b"},
        )
        monkeypatch.setattr(
            cycle, "fetch_last_owned_tension",
            lambda uid, cid: {"id": "t1", "status": "articulated", "text": "引っかかり",
                              "created_at": "c"},
        )

        result = cycle.get_cycle_return_door_route("course-1", current_user={"id": "u1"})

        assert result["empty"] is False
        assert result["leave_note"]["text"] == "書き置き"
        assert result["carryover"]["text"] == "問い"
        assert result["last_tension"]["text"] == "引っかかり"

    def test_all_absent_returns_empty_true(self, monkeypatch):
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(cycle, "fetch_active_leave_note", lambda uid, cid: None)
        monkeypatch.setattr(cycle, "fetch_active_carryover", lambda uid, cid: None)
        monkeypatch.setattr(cycle, "fetch_last_owned_tension", lambda uid, cid: None)

        result = cycle.get_cycle_return_door_route("course-1", current_user={"id": "u1"})

        assert result == {"empty": True}

    def test_fetch_failure_is_fail_open_empty(self, monkeypatch):
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})

        def _boom(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(cycle, "fetch_active_leave_note", _boom)

        result = cycle.get_cycle_return_door_route("course-1", current_user={"id": "u1"})

        assert result == {"empty": True}


# ===========================================================================
# 4. todays-words の応答・user ロールフィルタの構造固定（RD1）
# ===========================================================================


class TestTodaysWordsRoute:
    def test_returns_built_words(self, monkeypatch):
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(
            cycle, "fetch_todays_user_words",
            lambda uid, cid: [
                {"role": "user", "text": "今日の発話", "topic_id": "t1", "created_at": "a"},
            ],
        )

        result = cycle.get_cycle_todays_words_route("course-1", current_user={"id": "u1"})

        assert result == {
            "words": [{"text": "今日の発話", "topic_id": "t1", "created_at": "a"}],
            "truncated": False,
        }

    def test_fetch_failure_is_fail_open_empty_tray(self, monkeypatch):
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})

        def _boom(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(cycle, "fetch_todays_user_words", _boom)

        result = cycle.get_cycle_todays_words_route("course-1", current_user={"id": "u1"})

        assert result == {"words": [], "truncated": False}

    def test_sql_role_filter_is_fixed_to_user_literal(self):
        """SQL の role フィルタに 'user' リテラルがあり、'assistant' を選ぶ経路が無い。"""
        body = extract_function_source(_QUERIES_SRC, "fetch_todays_user_words")
        assert "m.msg->>'role' = 'user'" in body, (
            "fetch_todays_user_words の SQL から user ロールフィルタが消えている（RD1）"
        )
        assert "'assistant'" not in body, (
            "fetch_todays_user_words が assistant ロールに言及している（RD1 違反の疑い）"
        )

    def test_derive_re_checks_user_role(self):
        """二重防御: derive 側にも 'user' 再検査があり、assistant 発話は返らない。"""
        body = extract_function_source(_DERIVE_SRC, "build_todays_words")
        assert '"user"' in body or "'user'" in body

        from core.cycle.derive import build_todays_words

        rows = [
            {"role": "assistant", "text": "AI回答", "topic_id": "t1", "created_at": "a"},
            {"role": "user", "text": "本人発話", "topic_id": "t1", "created_at": "a"},
        ]
        result = build_todays_words(rows)
        assert "AI回答" not in str(result)
        assert result["words"][0]["text"] == "本人発話"

    def test_day_filter_is_rolling_24h_window_tz_independent(self):
        """「当日」判定は直近24時間窓 × 行 updated_at の近似（TZ 非依存）。
        CURRENT_DATE（DB タイムゾーンの暦日）に戻すと JST 学習者の朝の発話が
        同日昼に消える — fetch_landing_candidates と同型の相対窓を固定する。
        近似であることを docstring で正直に宣言していること。"""
        body = extract_function_source(_QUERIES_SRC, "fetch_todays_user_words")
        assert "updated_at >= now() - interval '24 hours'" in body
        # SQL 述語としての CURRENT_DATE 比較の再侵入を禁止する
        # （docstring 内の「なぜ使わないか」説明での言及は許容）。
        assert "updated_at >= CURRENT_DATE" not in body, (
            "当日判定が DB タイムゾーン依存の CURRENT_DATE に戻っている"
        )
        assert "近似" in body

    def test_blank_words_are_excluded_in_sql_for_accurate_truncation(self):
        """空白のみの発話は SQL 段階（btrim）で除外する — limit+1 方式の truncated
        判定が空行に食われて不正確にならないため（derive 側のスキップは二重防御）。"""
        body = extract_function_source(_QUERIES_SRC, "fetch_todays_user_words")
        assert "btrim" in body
        assert "<> ''" in body


# ===========================================================================
# 5. 経過日数語彙の不在（RD2: 「14日ぶりですね」等を作らない）
# ===========================================================================


class TestNoElapsedDaysLanguage:
    _FORBIDDEN = ("ぶりです", "日ぶり", "ぶりですね")

    def test_no_elapsed_days_wording_in_backend_sources(self):
        for src, name in (
            (_CYCLE_ROUTE_SRC, "routes/cycle.py"),
            (_QUERIES_SRC, "core/cycle/queries.py"),
            (_DERIVE_SRC, "core/cycle/derive.py"),
        ):
            for phrase in self._FORBIDDEN:
                assert phrase not in src, f"{name} に経過日数語彙 {phrase!r} がある（RD2）"

    def test_return_door_dto_has_no_day_arithmetic(self):
        """扉 DTO の組み立てに日数計算（timedelta / days）を持ち込まない。"""
        body = extract_function_source(_DERIVE_SRC, "build_return_door")
        for token in ("timedelta", ".days", "day_count"):
            assert token not in body, f"build_return_door が日数計算 {token!r} を含む（RD2/RD5）"

    def test_no_count_shaped_output_keys(self, monkeypatch):
        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"id": cid})
        monkeypatch.setattr(
            cycle, "fetch_active_leave_note",
            lambda uid, cid: {"id": "n1", "text": "書き置き", "created_at": "a"},
        )
        monkeypatch.setattr(cycle, "fetch_active_carryover", lambda uid, cid: None)
        monkeypatch.setattr(cycle, "fetch_last_owned_tension", lambda uid, cid: None)

        result = cycle.get_cycle_return_door_route("course-1", current_user={"id": "u1"})

        blob = str(result)
        for pat in (r"\d+\s*件", r"\d+\s*日"):
            assert not re.search(pat, blob), f"count/day-shaped text {pat!r} in {blob}"
