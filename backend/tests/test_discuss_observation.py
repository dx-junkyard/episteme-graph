"""discuss 観測基盤（Observation Layer for Phase 3 Gate, migration 060）のテスト。

正本: ``docs/features/discuss_observation_design.md``（DO1〜DO6）。
``test_discuss_opening.py`` / ``test_llm_usage_guardrails.py`` / ``test_reconstruction_guardrails.py``
と同じ流儀（構造検査 + fake データによる純粋関数の実行可能単体テスト。実 DB・実 LLM・
TestClient は使わない）で以下を検証する:

- ``core/discuss/observation.py`` が FastAPI / routes / services を import しない
- 仮名化（``pseudonymize_user_id``）が安定・非可逆・ユーザーごとに異なること
- ダンプの行射影（``project_llm_usage_row`` / ``project_trace_row`` / ``project_ui_event_row``）が
  本文キー（text/paraphrase/evidence_quote/message 等）を一切出力しないこと（DO1）
- 200,000 行キャップで ``truncated`` が正直に立つこと
- イベント語彙・payload キーホワイトリストの配線（未知イベント422・余剰キー除去）
- 参考目安（``compute_criteria``）が全達成/未達を正しく判定し、自動ゲートのコードが無いこと（DO5）
- 削除 API が無いこと（DO4）・SYSTEM_ADMIN ゲートの配線・監査記帳の配線
- migration 060（``discuss_metric_events``）が期待する DDL 構造を持つこと
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
SRC = ROOT / "src"
for _p in (str(BACKEND), str(BACKEND / "api"), str(SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.discuss import observation  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

_CORE_DISCUSS_DIR = BACKEND / "core" / "discuss"
_OBSERVATION_SRC = (BACKEND / "core" / "discuss" / "observation.py").read_text(encoding="utf-8")
_ROUTE_SRC = (BACKEND / "api" / "routes" / "discuss_observation.py").read_text(encoding="utf-8")
_MIGRATION_SQL = read_migration_sql(BACKEND, 60)

_HAS_FASTAPI = True
try:
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)


# ---------------------------------------------------------------------------
# レイヤリング（core/discuss は FastAPI / routes / services を import しない）
# ---------------------------------------------------------------------------


class TestLayering:
    def test_no_fastapi_import(self):
        assert_module_tree_does_not_import(_CORE_DISCUSS_DIR, ["fastapi"])

    def test_no_routes_or_services_import(self):
        assert_module_tree_does_not_import(_CORE_DISCUSS_DIR, ["routes", "services"])


# ---------------------------------------------------------------------------
# 仮名化（DO2）
# ---------------------------------------------------------------------------


class TestPseudonymization:
    def test_stable_for_same_input(self):
        a = observation.pseudonymize_user_id("11111111-1111-1111-1111-111111111111")
        b = observation.pseudonymize_user_id("11111111-1111-1111-1111-111111111111")
        assert a == b

    def test_different_users_get_different_pseudonyms(self):
        a = observation.pseudonymize_user_id("11111111-1111-1111-1111-111111111111")
        b = observation.pseudonymize_user_id("22222222-2222-2222-2222-222222222222")
        assert a != b

    def test_raw_uuid_does_not_appear_in_output(self):
        raw = "33333333-3333-3333-3333-333333333333"
        pseudonym = observation.pseudonymize_user_id(raw)
        assert raw not in pseudonym

    def test_output_is_hex_sha256_length(self):
        pseudonym = observation.pseudonymize_user_id("user-x")
        assert len(pseudonym) == 64
        int(pseudonym, 16)  # raises ValueError if not hex

    def test_empty_or_none_user_id_does_not_raise(self):
        assert observation.pseudonymize_user_id("") != ""
        assert observation.pseudonymize_user_id(None) != ""  # type: ignore[arg-type]

    def test_key_uses_settings_jwt_secret_not_os_environ(self):
        """鍵の由来が get_settings().jwt_secret であること（開発ルール1: os.environ 直書き禁止）。

        コメント/docstring 中の語彙言及で誤検出しないよう、関数本体だけを検査する。
        """
        body = extract_function_source(_OBSERVATION_SRC, "pseudonymize_user_id")
        assert "get_settings()" in body
        assert "jwt_secret" in body
        # 実際の直書きアクセス構文のみを禁止語彙とする（docstring 中の語彙言及とは
        # 区別する — 例えば「os.environ 直書き禁止」という注記自体は許容する）。
        assert "os.environ[" not in body
        assert "os.environ.get(" not in body
        assert "os.getenv(" not in body


# ---------------------------------------------------------------------------
# ダンプ行射影 — 本文非漏洩（DO1）
# ---------------------------------------------------------------------------


class TestDumpProjectionDoesNotLeakFreeText:
    """fake row に text/paraphrase/evidence_quote/message 等の本文キーを仕込んでも、
    射影後の出力にそれらのキー・値が一切現れないこと（ホワイトリスト方式の裏付け）。
    """

    _LEAK_PROBE = "ここには絶対に出てはいけない学習者の発話本文です"

    def test_llm_usage_row_projection_has_no_free_text(self):
        row = {
            "occurred_at": datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc),
            "feature": "learning:chat_discuss",
            "operation": "chat",
            "model": "gpt-4o",
            "usage_source": "reported",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "user_id": "11111111-1111-1111-1111-111111111111",
            "course_id": "course-1",
            "text": self._LEAK_PROBE,
            "message": self._LEAK_PROBE,
        }
        out = observation.project_llm_usage_row(row)
        assert self._LEAK_PROBE not in str(out)
        assert "text" not in out
        assert "message" not in out
        assert out["user_pseudonym"] != row["user_id"]
        assert "user_id" not in out

    def test_trace_row_projection_has_no_free_text(self):
        row = {
            "created_at": datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc),
            "user_id": "22222222-2222-2222-2222-222222222222",
            "course_id": "course-1",
            "kind": "question",
            "status": "open",
            "overall_tier": "source",
            "content_grounding": "course_material",
            "discuss_scope": "course_sources",
            "tension_hint": True,
            "structure_anchor_present": False,
            "map_excluded": False,
            "text": self._LEAK_PROBE,
            "paraphrase": self._LEAK_PROBE,
            "evidence_quote": self._LEAK_PROBE,
            "context_label": self._LEAK_PROBE,
        }
        out = observation.project_trace_row(row)
        assert self._LEAK_PROBE not in str(out)
        for forbidden_key in ("text", "paraphrase", "evidence_quote", "context_label"):
            assert forbidden_key not in out
        assert out == {
            "created_at": "2026-07-20T03:00:00+00:00",
            "user_pseudonym": observation.pseudonymize_user_id(row["user_id"]),
            "course_id": "course-1",
            "kind": "question",
            "status": "open",
            "overall_tier": "source",
            "content_grounding": "course_material",
            "discuss_scope": "course_sources",
            "tension_hint": True,
            "structure_anchor_present": False,
            "map_excluded": False,
        }

    def test_trace_row_projection_handles_postgres_text_booleans(self):
        """payload->>'x' は Postgres では TEXT ('true'/'false') で返る。文字列表現も
        正しく bool に変換されること。"""
        row = {
            "created_at": None,
            "user_id": "u1",
            "course_id": "",
            "kind": "misconception",
            "status": "open",
            "overall_tier": None,
            "content_grounding": None,
            "discuss_scope": None,
            "tension_hint": "true",
            "structure_anchor_present": "false",
            "map_excluded": "true",
        }
        out = observation.project_trace_row(row)
        assert out["tension_hint"] is True
        assert out["structure_anchor_present"] is False
        assert out["map_excluded"] is True

    def test_ui_event_row_projection_has_no_free_text_and_keeps_whitelisted_payload(self):
        row = {
            "created_at": datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc),
            "user_id": "33333333-3333-3333-3333-333333333333",
            "course_id": "course-2",
            "event": "landing_shown",
            "payload": {"reason": "topic_switch"},
            "text": self._LEAK_PROBE,
        }
        out = observation.project_ui_event_row(row)
        assert self._LEAK_PROBE not in str(out)
        assert "text" not in out
        assert out["payload"] == {"reason": "topic_switch"}


# ---------------------------------------------------------------------------
# 行キャップ（200,000行）で truncated が正直に立つ
# ---------------------------------------------------------------------------


class _FakeMappingResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _stmt, _params=None):
        return _FakeMappingResult(self._rows)


def _fake_llm_usage_row(user_id: str) -> dict:
    return {
        "occurred_at": datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc),
        "feature": "learning:chat_discuss",
        "operation": "chat",
        "model": "gpt-4o",
        "usage_source": "reported",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "user_id": user_id,
        "course_id": "course-1",
    }


class TestRowCapTruncation:
    def test_llm_usage_fetch_marks_truncated_when_over_cap(self, monkeypatch):
        monkeypatch.setattr(observation, "_MAX_DUMP_ROWS", 2)
        # 実クエリは LIMIT :limit(=_MAX_DUMP_ROWS+1) を発行するため、fake セッションは
        # その上限まで返す想定で3行だけ用意する（キャップ2に対し1行超過）。
        rows = [_fake_llm_usage_row(f"user-{i}") for i in range(3)]
        session = _FakeSession(rows)

        projected, truncated = observation._fetch_llm_usage_chat_rows(session)

        assert truncated is True
        assert len(projected) == 2

    def test_llm_usage_fetch_not_truncated_when_under_cap(self, monkeypatch):
        monkeypatch.setattr(observation, "_MAX_DUMP_ROWS", 5)
        rows = [_fake_llm_usage_row(f"user-{i}") for i in range(3)]
        session = _FakeSession(rows)

        projected, truncated = observation._fetch_llm_usage_chat_rows(session)

        assert truncated is False
        assert len(projected) == 3


# ---------------------------------------------------------------------------
# イベント語彙・payload キーホワイトリスト
# ---------------------------------------------------------------------------


class TestMetricEventVocabAndPayloadWhitelist:
    _EXPECTED_VOCAB = {
        "discussion_entered",
        "opening_shown",
        "opening_starter_clicked",
        "opening_backbone_clicked",
        "branch_deep_clicked",
        "branch_wide_clicked",
        "scope_switched",
        "landing_shown",
        "landing_confirmed",
        "landing_dismissed",
        "landing_skipped",
        "landing_probe_clicked",
        "landing_continue_clicked",
    }

    def test_vocab_matches_design_doc_13_events(self):
        assert observation.METRIC_EVENT_VOCAB == self._EXPECTED_VOCAB
        assert len(observation.METRIC_EVENT_VOCAB) == 13

    def test_sanitize_event_payload_keeps_only_whitelisted_keys(self):
        out = observation.sanitize_event_payload(
            {"scope": "course_sources", "reason": "topic_switch", "kind": "tension", "text": "leak", "extra": 1}
        )
        assert out == {"scope": "course_sources", "reason": "topic_switch", "kind": "tension"}

    def test_sanitize_event_payload_handles_non_dict_input(self):
        assert observation.sanitize_event_payload(None) == {}
        assert observation.sanitize_event_payload("not-a-dict") == {}  # type: ignore[arg-type]

    def test_sanitize_event_payload_empty_dict_stays_empty(self):
        assert observation.sanitize_event_payload({}) == {}

    def test_route_validates_against_vocab_and_raises_422(self):
        """未知イベント422の配線: ルートが observation.METRIC_EVENT_VOCAB を参照し、
        含まれない場合に 422 を送出する構造になっていること。"""
        assert "observation.METRIC_EVENT_VOCAB" in _ROUTE_SRC
        body = extract_function_source(_ROUTE_SRC, "record_discuss_metric_events")
        assert "not in observation.METRIC_EVENT_VOCAB" in body
        assert "422" in body

    def test_route_enforces_max_20_events_per_request(self):
        body = extract_function_source(_ROUTE_SRC, "record_discuss_metric_events")
        assert "_MAX_METRIC_EVENTS_PER_REQUEST" in body
        assert "_MAX_METRIC_EVENTS_PER_REQUEST = 20" in _ROUTE_SRC


# ---------------------------------------------------------------------------
# 参考目安（compute_criteria） — 全達成/未達・自動ゲートにしない（DO5）
# ---------------------------------------------------------------------------


class TestObservationCriteria:
    def test_five_named_criteria_exist_with_documented_targets(self):
        keys = [c.key for c in observation.OBSERVATION_CRITERIA]
        assert keys == [
            "distinct_discuss_users",
            "discuss_turns",
            "observation_span_days",
            "landing_shown",
            "grounding_recorded_turns",
        ]
        targets = {c.key: c.target for c in observation.OBSERVATION_CRITERIA}
        assert targets == {
            "distinct_discuss_users": 5,
            "discuss_turns": 200,
            "observation_span_days": 14,
            "landing_shown": 30,
            "grounding_recorded_turns": 100,
        }

    def test_all_criteria_met_yields_ready_for_analysis_true(self):
        usage = {
            "learning:chat_discuss": {
                "count": 250,
                "distinct_users": 8,
                "first_at": "2026-07-01T00:00:00+00:00",
                "last_at": "2026-07-20T00:00:00+00:00",
            }
        }
        traces = {"grounding_recorded": 120}
        ui_events = {"by_event": {"landing_shown": 40}}

        criteria, ready = observation.compute_criteria(usage, traces, ui_events)

        assert ready is True
        assert all(c["met"] for c in criteria)
        by_key = {c["key"]: c for c in criteria}
        assert by_key["discuss_turns"]["current"] == 250
        assert by_key["distinct_discuss_users"]["current"] == 8
        assert by_key["observation_span_days"]["current"] == 19

    def test_not_all_criteria_met_yields_ready_for_analysis_false(self):
        usage = {
            "learning:chat_discuss": {
                "count": 10,
                "distinct_users": 1,
                "first_at": "2026-07-19T00:00:00+00:00",
                "last_at": "2026-07-20T00:00:00+00:00",
            }
        }
        traces = {"grounding_recorded": 2}
        ui_events = {"by_event": {"landing_shown": 0}}

        criteria, ready = observation.compute_criteria(usage, traces, ui_events)

        assert ready is False
        assert not all(c["met"] for c in criteria)

    def test_missing_feature_or_empty_dicts_default_to_zero_not_crash(self):
        criteria, ready = observation.compute_criteria({}, {}, {})
        assert ready is False
        assert all(c["current"] == 0 for c in criteria)

    def test_compute_criteria_is_read_only_no_db_or_mutation_side_effects(self):
        """DO5: 参考目安は判定結果を返すだけで、閾値到達で何かを自動的に変更しない
        （読み取り専用の静的検査 — session/INSERT/UPDATE を一切含まない）。"""
        body = extract_function_source(_OBSERVATION_SRC, "compute_criteria")
        assert_source_forbids(body, ["session", "INSERT INTO", "UPDATE ", "DELETE FROM"], context="compute_criteria")

    def test_build_observation_status_does_not_write_to_db(self):
        """観測状況の組み立て関数自体も読み取り専用であること。"""
        body = extract_function_source(_OBSERVATION_SRC, "build_observation_status")
        assert_source_forbids(body, ["INSERT INTO", "UPDATE ", "DELETE FROM"], context="build_observation_status")


# ---------------------------------------------------------------------------
# 継続送信近似（daily_summary の followup_turns_approx, 課題#4）
# ---------------------------------------------------------------------------


class TestFollowupTurnsApprox:
    def test_events_within_window_count_as_followup(self):
        events = [
            ("user-a", datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc)),
            ("user-a", datetime(2026, 7, 20, 3, 10, tzinfo=timezone.utc)),  # 10分後 → followup
        ]
        counts = observation.count_followup_turns_by_day(events)
        assert counts == {"2026-07-20": 1}

    def test_events_outside_window_do_not_count(self):
        events = [
            ("user-a", datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc)),
            ("user-a", datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc)),  # 60分後 → 対象外
        ]
        counts = observation.count_followup_turns_by_day(events)
        assert counts == {}

    def test_different_users_do_not_cross_contaminate(self):
        events = [
            ("user-a", datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc)),
            ("user-b", datetime(2026, 7, 20, 3, 5, tzinfo=timezone.utc)),
        ]
        counts = observation.count_followup_turns_by_day(events)
        assert counts == {}

    def test_unordered_input_is_handled_via_internal_sort(self):
        events = [
            ("user-a", datetime(2026, 7, 20, 3, 10, tzinfo=timezone.utc)),
            ("user-a", datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc)),
        ]
        counts = observation.count_followup_turns_by_day(events)
        assert counts == {"2026-07-20": 1}

    def test_readme_documents_approximation_caveat(self):
        """README.md の生成元テンプレートに「近似」であることの注意書きがあること（課題#4）。"""
        assert "近似" in observation._README_TEMPLATE
        assert "生成プロンプト応答率" in observation._README_TEMPLATE


class TestDailySummaryMerge:
    def test_merges_four_daily_dicts_into_rows(self):
        rows = observation.build_daily_summary_rows(
            usage_by_day={"2026-07-20": {"discuss_turns": 10, "distinct_users": 3}},
            grounding_by_day={"2026-07-20": {"course_material": 6, "model_generated": 4}},
            landing_by_day={"2026-07-20": 2},
            followup_by_day={"2026-07-20": 1},
        )
        assert rows == [
            {
                "day": "2026-07-20",
                "discuss_turns": 10,
                "distinct_users": 3,
                "grounding_distribution": {"course_material": 6, "model_generated": 4},
                "landing_events": 2,
                "followup_turns_approx": 1,
            }
        ]

    def test_days_present_in_only_one_source_are_zero_filled(self):
        rows = observation.build_daily_summary_rows(
            usage_by_day={},
            grounding_by_day={},
            landing_by_day={"2026-07-21": 5},
            followup_by_day={},
        )
        assert rows == [
            {
                "day": "2026-07-21",
                "discuss_turns": 0,
                "distinct_users": 0,
                "grounding_distribution": {},
                "landing_events": 5,
                "followup_turns_approx": 0,
            }
        ]

    def test_no_input_yields_no_rows(self):
        assert observation.build_daily_summary_rows({}, {}, {}, {}) == []


# ---------------------------------------------------------------------------
# 削除 API 不在（DO4）
# ---------------------------------------------------------------------------


class TestNoDeleteAPI:
    def test_no_delete_route_decorator_in_source(self):
        assert_source_forbids(
            _ROUTE_SRC,
            ["@admin_router.delete", "@learning_router.delete"],
            context="routes/discuss_observation.py",
        )

    def test_no_delete_sql_against_metric_events_table(self):
        assert_source_forbids(
            _OBSERVATION_SRC, ["DELETE FROM discuss_metric_events"], context="observation.py"
        )

    @_skip_no_fastapi
    def test_all_registered_routes_have_no_delete_method(self):
        import routes.discuss_observation as discuss_observation_routes

        for router in (discuss_observation_routes.admin_router, discuss_observation_routes.learning_router):
            for route in router.routes:
                methods = getattr(route, "methods", set()) or set()
                assert "DELETE" not in methods, f"{route.path} unexpectedly allows DELETE"


# ---------------------------------------------------------------------------
# SYSTEM_ADMIN ゲートの配線
# ---------------------------------------------------------------------------


@_skip_no_fastapi
class TestPermissionFailClosed:
    def test_observation_status_requires_system_admin(self):
        import routes.discuss_observation as discuss_observation_routes

        route = next(
            r for r in discuss_observation_routes.admin_router.routes if r.path.endswith("/observation-status")
        )
        dep_names = {dep.call.__name__ for dep in route.dependant.dependencies if dep.call}
        assert "_require_system_admin" in dep_names

    def test_observation_dump_requires_system_admin(self):
        import routes.discuss_observation as discuss_observation_routes

        route = next(
            r for r in discuss_observation_routes.admin_router.routes if r.path.endswith("/observation-dump")
        )
        dep_names = {dep.call.__name__ for dep in route.dependant.dependencies if dep.call}
        assert "_require_system_admin" in dep_names

    def test_metric_events_requires_authentication(self):
        import routes.discuss_observation as discuss_observation_routes

        route = next(
            r for r in discuss_observation_routes.learning_router.routes if r.path.endswith("/discuss/metric-events")
        )
        dep_names = {dep.call.__name__ for dep in route.dependant.dependencies if dep.call}
        assert "_get_current_user" in dep_names


# ---------------------------------------------------------------------------
# 監査記帳の配線
# ---------------------------------------------------------------------------


class TestAuditWiring:
    def test_audit_entity_constant_registered_in_catalog(self):
        from core.schema import AUDIT_ENTITY_DISCUSS_OBSERVATION, AUDIT_ENTITY_TYPES

        assert AUDIT_ENTITY_DISCUSS_OBSERVATION == "discuss_observation"
        assert AUDIT_ENTITY_DISCUSS_OBSERVATION in AUDIT_ENTITY_TYPES

    def test_dump_route_calls_record_review_event_with_catalog_constant(self):
        body = extract_function_source(_ROUTE_SRC, "get_discuss_observation_dump")
        assert "record_review_event(" in body
        assert "AUDIT_ENTITY_DISCUSS_OBSERVATION" in body

    def test_route_imports_record_review_event_from_services(self):
        assert "from services import record_review_event" in _ROUTE_SRC


# ---------------------------------------------------------------------------
# migration 060（discuss_metric_events）の DDL 構造
# ---------------------------------------------------------------------------


class TestMigrationSql:
    def test_create_table_if_not_exists(self):
        assert "CREATE TABLE IF NOT EXISTS discuss_metric_events" in _MIGRATION_SQL

    def test_columns_present(self):
        for col in ("id", "user_id", "course_id", "event", "payload", "created_at"):
            assert col in _MIGRATION_SQL

    def test_indexes_present(self):
        assert "CREATE INDEX IF NOT EXISTS idx_discuss_metric_events_event" in _MIGRATION_SQL
        assert "CREATE INDEX IF NOT EXISTS idx_discuss_metric_events_user" in _MIGRATION_SQL

    def test_no_foreign_key_to_users(self):
        """FK を意図的に張らない（計測をユーザー削除と独立に残す、atlas_cue_events と同じ思想）。"""
        assert "REFERENCES" not in _MIGRATION_SQL
