"""帰還の扉（return_door_design.md Phase 2）— 純関数・services のテスト。

対象仕様: docs/features/return_door_design.md §2.1/§2.2（RD1〜RD5）。
test_understanding_cycle_core.py / test_understanding_cycle_api.py と同型の手法
（純関数は fake rows・services はフェイクセッション/monkeypatch）で DB なしに検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

from core.cycle.derive import build_return_door, build_todays_words  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
)

_DERIVE_SRC = (ROOT / "backend" / "core" / "cycle" / "derive.py").read_text(encoding="utf-8")
_QUERIES_SRC = (ROOT / "backend" / "core" / "cycle" / "queries.py").read_text(encoding="utf-8")
_SERVICES_SRC = (ROOT / "backend" / "api" / "services.py").read_text(encoding="utf-8")


# ===========================================================================
# フェイクセッション（test_understanding_cycle_api.py と同型）
# ===========================================================================


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row or []


class _FakeSession:
    def __init__(self, row=None):
        self._row = row
        self.calls: list[tuple[str, dict]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        return _FakeResult(self._row)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


# ===========================================================================
# 1. build_return_door — 3部品 null 分岐（RD3: 書かなければ何も出ない）
# ===========================================================================


class TestBuildReturnDoorNullBranches:
    def test_all_none_returns_empty(self):
        assert build_return_door(None, None, None) == {"empty": True}

    def test_blank_texts_count_as_absent(self):
        result = build_return_door(
            {"id": "n1", "text": "   ", "created_at": "2026-08-15T00:00:00+00:00"},
            {"id": "c1", "text": "", "created_at": "2026-08-15T00:00:00+00:00"},
            {"id": "t1", "text": "  ", "created_at": "2026-08-15T00:00:00+00:00"},
        )
        assert result == {"empty": True}

    def test_leave_note_only(self):
        result = build_return_door(
            {"id": "n1", "text": "続きはこの式から", "created_at": "2026-08-15T00:00:00+00:00"},
            None,
            None,
        )
        assert result["empty"] is False
        assert result["leave_note"] == {
            "text": "続きはこの式から",
            "created_at": "2026-08-15T00:00:00+00:00",
        }
        assert result["carryover"] is None
        assert result["last_tension"] is None

    def test_carryover_only(self):
        result = build_return_door(
            None,
            {"id": "c1", "text": "なぜ線形化できるのか", "created_at": "2026-08-14T00:00:00+00:00"},
            None,
        )
        assert result["carryover"] == {
            "trace_id": "c1",
            "text": "なぜ線形化できるのか",
            "created_at": "2026-08-14T00:00:00+00:00",
        }
        assert result["leave_note"] is None
        assert result["last_tension"] is None

    def test_last_tension_only(self):
        result = build_return_door(
            None,
            None,
            {"id": "t1", "status": "articulated", "text": "定義がしっくりこない",
             "created_at": "2026-08-13T00:00:00+00:00"},
        )
        assert result["last_tension"] == {
            "text": "定義がしっくりこない",
            "created_at": "2026-08-13T00:00:00+00:00",
        }

    def test_all_three_present_fixed_keys(self):
        result = build_return_door(
            {"id": "n1", "text": "書き置き", "created_at": "a"},
            {"id": "c1", "text": "問い", "created_at": "b"},
            {"id": "t1", "text": "引っかかり", "created_at": "c"},
        )
        assert set(result.keys()) == {"empty", "leave_note", "carryover", "last_tension"}


# ===========================================================================
# 2. build_return_door — 逐語性（RD1: 本文は本人の text 逐語のみ・切らない）
# ===========================================================================


class TestBuildReturnDoorVerbatim:
    def test_long_text_is_not_truncated_or_rewritten(self):
        long_text = "これは本人が書いた長い書き置きの本文。" * 40
        result = build_return_door(
            {"id": "n1", "text": long_text, "created_at": "2026-08-15T00:00:00+00:00"},
            {"id": "c1", "text": long_text, "created_at": "2026-08-15T00:00:00+00:00"},
            {"id": "t1", "text": long_text, "created_at": "2026-08-15T00:00:00+00:00"},
        )
        assert result["leave_note"]["text"] == long_text
        assert result["carryover"]["text"] == long_text
        assert result["last_tension"]["text"] == long_text
        assert "…" not in result["leave_note"]["text"]

    def test_build_return_door_source_never_calls_excerpt(self):
        """RD1 の構造固定: 逐語のみ — 切り詰め（_excerpt）を呼ばない。"""
        body = extract_function_source(_DERIVE_SRC, "build_return_door")
        assert "_excerpt(" not in body

    def test_no_elapsed_day_keys_or_wording(self):
        """RD2/RD5: 経過日数キー・件数キー・「〜ぶり」文言を含めない。"""
        result = build_return_door(
            {"id": "n1", "text": "書き置き", "created_at": "a"},
            {"id": "c1", "text": "問い", "created_at": "b"},
            {"id": "t1", "text": "引っかかり", "created_at": "c"},
        )
        blob = str(result)
        for forbidden in ("days", "elapsed", "count", "日ぶり", "ぶりです"):
            assert forbidden not in blob, f"forbidden key/wording {forbidden!r} in {blob}"


# ===========================================================================
# 3. build_todays_words — user ロール限定・逐語・truncated（RD1/§2.2）
# ===========================================================================


class TestBuildTodaysWords:
    def test_empty_rows(self):
        assert build_todays_words([]) == {"words": [], "truncated": False}

    def test_assistant_rows_are_excluded_even_if_present(self):
        """二重防御: SQL が user に絞っていても derive 側でも assistant を弾く。"""
        rows = [
            {"role": "assistant", "text": "AIの回答文", "topic_id": "t1", "created_at": "a"},
            {"role": "user", "text": "わたしの質問", "topic_id": "t1", "created_at": "a"},
        ]
        result = build_todays_words(rows)
        assert result["words"] == [
            {"text": "わたしの質問", "topic_id": "t1", "created_at": "a"}
        ]
        assert "AIの回答文" not in str(result)

    def test_text_is_verbatim_full_length(self):
        long_text = "とても長い発話。" * 100  # 800字 — 200字で切らない（逐語性優先）
        rows = [{"role": "user", "text": long_text, "topic_id": "t1", "created_at": "a"}]
        result = build_todays_words(rows)
        assert result["words"][0]["text"] == long_text
        assert "…" not in result["words"][0]["text"]

    def test_limit_and_truncated_flag(self):
        rows = [
            {"role": "user", "text": f"発話{i}", "topic_id": "t1", "created_at": "a"}
            for i in range(31)
        ]
        result = build_todays_words(rows, limit=30)
        assert len(result["words"]) == 30
        assert result["truncated"] is True

    def test_no_truncated_flag_at_exact_limit(self):
        rows = [
            {"role": "user", "text": f"発話{i}", "topic_id": "t1", "created_at": "a"}
            for i in range(30)
        ]
        result = build_todays_words(rows, limit=30)
        assert len(result["words"]) == 30
        assert result["truncated"] is False

    def test_blank_text_rows_are_skipped(self):
        rows = [
            {"role": "user", "text": "   ", "topic_id": "t1", "created_at": "a"},
            {"role": "user", "text": "本文あり", "topic_id": "t1", "created_at": "a"},
        ]
        result = build_todays_words(rows)
        assert [w["text"] for w in result["words"]] == ["本文あり"]


# ===========================================================================
# 4. leave_note の supersede 規約（record_cycle_intention）
# ===========================================================================


class TestLeaveNoteSupersede:
    def test_leave_note_supersedes_previous_leave_note_before_insert(self, monkeypatch):
        from api import services

        order = []
        monkeypatch.setattr(
            services, "_supersede_active_carryover",
            lambda uid, cid, role="carryover_question": order.append(("supersede", role)),
        )
        monkeypatch.setattr(
            services, "record_interest_trace",
            lambda *a, **kw: order.append(("insert",)) or "trace-ln",
        )

        result = services.record_cycle_intention("u1", "c1", "leave_note", "未来の自分へ")

        assert result == {"trace_id": "trace-ln"}
        assert order[0] == ("supersede", "leave_note")
        assert order[1] == ("insert",)

    def test_carryover_path_is_unchanged_two_positional_args(self, monkeypatch):
        """既存 carryover 挙動は完全不変（role kwarg を渡さない2引数呼び）。"""
        from api import services

        order = []
        # role kwarg を受けない strict な2引数 lambda — kwarg を渡すと TypeError になる。
        monkeypatch.setattr(
            services, "_supersede_active_carryover",
            lambda uid, cid: order.append(("supersede", uid, cid)),
        )
        monkeypatch.setattr(services, "record_interest_trace", lambda *a, **kw: "trace-c")

        result = services.record_cycle_intention("u1", "c1", "carryover_question", "次の問い")

        assert result == {"trace_id": "trace-c"}
        assert order == [("supersede", "u1", "c1")]

    def test_leave_note_empty_text_returns_none(self):
        from api import services

        assert services.record_cycle_intention("u1", "c1", "leave_note", "   ") is None

    def test_leave_note_empty_text_with_prediction_still_none(self):
        """text 空の許容は opening_motive 限定（leave_note には広げない）。"""
        from api import services

        assert services.record_cycle_intention(
            "u1", "c1", "leave_note", "", prediction={"text": "x"},
        ) is None

    def test_leave_note_payload_role_and_status(self, monkeypatch):
        from api import services

        captured = {}

        def _fake_record(*a, **kw):
            captured["kw"] = kw
            return "trace-ln2"

        monkeypatch.setattr(services, "record_interest_trace", _fake_record)
        monkeypatch.setattr(services, "_supersede_active_carryover", lambda *a, **kw: None)

        services.record_cycle_intention("u1", "c1", "leave_note", "書き置き本文")

        kw = captured["kw"]
        assert kw["kind"] == "intention"
        assert kw["extra_payload"]["role"] == "leave_note"
        assert kw["status"] == "open"

    def test_supersede_helper_is_role_scoped_sql(self, monkeypatch):
        """UPDATE が role パラメータでスコープされ、leave_note 指定時は
        carryover 行に触れない（:role バインドの実値検査）。"""
        from api import services

        fake = _FakeSession()
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        services._supersede_active_carryover("u1", "c1", role="leave_note")

        stmt, params = fake.calls[0]
        assert "payload->>'role' = :role" in stmt
        assert params["role"] == "leave_note"
        assert fake.committed

    def test_supersede_helper_default_role_is_carryover(self, monkeypatch):
        from api import services

        fake = _FakeSession()
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        services._supersede_active_carryover("u1", "c1")

        _stmt, params = fake.calls[0]
        assert params["role"] == "carryover_question"

    def test_supersede_helper_does_not_delete(self):
        body = extract_function_source(_SERVICES_SRC, "_supersede_active_carryover")
        assert_source_forbids(body, ["DELETE"], context="_supersede_active_carryover")
        assert "SET status = 'superseded'" in body

    def test_leave_note_is_registered_role(self):
        from core.cycle.schema import INTENTION_ROLES, ROLE_LEAVE_NOTE

        assert ROLE_LEAVE_NOTE == "leave_note"
        assert "leave_note" in INTENTION_ROLES


# ===========================================================================
# 5. queries — TENSION_OWNED_STATUSES の正本参照・非LLM（RD4）
# ===========================================================================


class TestQueriesStructure:
    def test_last_owned_tension_uses_constant_not_literals(self):
        body = extract_function_source(_QUERIES_SRC, "fetch_last_owned_tension")
        assert "TENSION_OWNED_STATUSES" in body
        # 語彙のリテラル再掲をしない（正本は core.tension.schema）。
        for literal in ("'articulated'", "'connected'", "'abstracted'"):
            assert literal not in body, f"status literal {literal} re-declared in query"

    def test_queries_import_owned_statuses_from_tension_schema(self):
        assert "from core.tension.schema import TENSION_OWNED_STATUSES" in _QUERIES_SRC

    def test_derive_and_queries_do_not_import_llm_or_fastapi(self):
        """RD4: 扉の合成・トレイの列挙は非LLM・決定論の読み時導出。"""
        for src, name in ((_DERIVE_SRC, "derive.py"), (_QUERIES_SRC, "queries.py")):
            assert_source_does_not_import(
                src,
                ["core.llm", "openai", "core.llm_worker", "fastapi"],
                context=f"core/cycle/{name}",
            )

    def test_leave_note_query_is_open_status_scoped(self):
        body = extract_function_source(_QUERIES_SRC, "fetch_active_leave_note")
        assert "payload->>'role' = 'leave_note'" in body
        assert "status = 'open'" in body
        assert "LIMIT 1" in body
