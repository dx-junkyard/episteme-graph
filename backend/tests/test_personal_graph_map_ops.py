"""個人知識ネットワーク — 「地図には反映しない/地図に戻す」操作のテスト。

対象仕様: /Users/Shared/issues/episteme_graph_personal_knowledge_network_ux_proposal.md §6
「本人が地図を訂正できるUX」のうち、痕跡そのものを dismiss せず表示だけを除外/復帰する
``payload.map_excluded`` フラグ操作（``services.set_trace_map_exclusion`` /
``services.get_trace_map_exclusion_flags`` + ``routes/learning.py`` の
``/traces/{trace_id}/map-exclude`` / ``/traces/{trace_id}/map-restore`` エンドポイント、
および interest-traces 一覧への ``map_excluded`` フィールド付与）。

既存の tension/anchor API テスト（test_tension_api_and_aggregation.py /
test_anchor_api_and_aggregation.py）と同型: services.py の重い外部依存importを避けるため
SQL/ソースの静的検証を軸に、加えて ``_pg_session`` を差し替えるフェイクセッションで
分岐ロジック（本人以外/対象外kind→None、entity_type選択、excludedのトグル、監査記帳）を
純粋にユニットテストする（test_prerequisite_routing.py と同型の手法。DB 不要）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

from tests.guardrail_helpers import (  # noqa: E402
    assert_source_forbids,
    extract_function_source,
)

SERVICES = ROOT / "backend" / "api" / "services.py"
LEARNING = ROOT / "backend" / "api" / "routes" / "learning.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _service_body(fn_name: str) -> str:
    return extract_function_source(_read(SERVICES), fn_name)


def _route_body(fn_name: str) -> str:
    return extract_function_source(_read(LEARNING), fn_name)


# ---------------------------------------------------------------------------
# フェイクセッション（test_prerequisite_routing.py と同型: 実SQLは解釈せず、
# あらかじめ用意した1つの結果を返すだけ。分岐ロジックの検証に専念する）。
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row or []


class _FakeSession:
    def __init__(self, row):
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


class _BoomSession(_FakeSession):
    """execute() が例外を投げる異常系用。"""

    def execute(self, *_a, **_kw):
        raise RuntimeError("boom")


# ===========================================================================
# services.set_trace_map_exclusion / get_trace_map_exclusion_flags — SQL の形
# ===========================================================================


class TestServiceSqlContract:
    def test_set_exclusion_is_owner_scoped(self):
        body = _service_body("set_trace_map_exclusion")
        assert "user_id = CAST(:uid AS uuid)" in body

    def test_set_exclusion_targets_tension_and_question_only(self):
        body = _service_body("set_trace_map_exclusion")
        assert "kind IN ('tension', 'question')" in body

    def test_set_exclusion_does_not_touch_status_column(self):
        """dismiss（候補の当落判定）とは独立: status 列は変更しない。"""
        body = _service_body("set_trace_map_exclusion")
        update_block = body.split("UPDATE interest_traces")[1].split("WHERE")[0]
        assert_source_forbids(
            update_block, ["SET status", "status ="],
            context="set_trace_map_exclusion UPDATE clause",
        )

    def test_set_exclusion_writes_payload_map_excluded_key_only(self):
        body = _service_body("set_trace_map_exclusion")
        assert "'{map_excluded}'" in body
        assert "RETURNING kind" in body

    def test_set_exclusion_does_not_delete_row(self):
        """P4: 情報を落とさない。行削除しない。"""
        body = _service_body("set_trace_map_exclusion")
        assert "DELETE" not in body.upper()

    def test_exclusion_flags_reader_is_owner_scoped_and_kind_filtered(self):
        body = _service_body("get_trace_map_exclusion_flags")
        assert "user_id = CAST(:uid AS uuid)" in body
        assert "kind IN ('tension', 'question')" in body
        assert "map_excluded" in body

    def test_exclusion_flags_reader_does_not_mutate(self):
        """読み取り専用: UPDATE/DELETE を含まない。"""
        body = _service_body("get_trace_map_exclusion_flags")
        assert_source_forbids(
            body, ["UPDATE interest_traces", "DELETE"],
            context="get_trace_map_exclusion_flags",
        )


class TestAuditUsesCatalogEntities:
    """監査 entity_type は core/schema.py の既存カタログ定数のみを使う（新語彙を作らない）。"""

    def test_uses_tension_and_structure_anchor_catalog_constants(self):
        body = _service_body("set_trace_map_exclusion")
        assert "AUDIT_ENTITY_TENSION" in body
        assert "AUDIT_ENTITY_STRUCTURE_ANCHOR" in body

    def test_records_review_event(self):
        body = _service_body("set_trace_map_exclusion")
        assert "record_review_event(" in body

    def test_catalog_constants_are_existing_vocabulary(self):
        from core.schema import AUDIT_ENTITY_STRUCTURE_ANCHOR, AUDIT_ENTITY_TENSION, AUDIT_ENTITY_TYPES

        assert AUDIT_ENTITY_TENSION in AUDIT_ENTITY_TYPES
        assert AUDIT_ENTITY_STRUCTURE_ANCHOR in AUDIT_ENTITY_TYPES


# ===========================================================================
# services.set_trace_map_exclusion — フェイクセッションでの分岐ロジック検証
# ===========================================================================


class TestSetTraceMapExclusionBehavior:
    def test_tension_row_excludes_and_records_tension_entity(self, monkeypatch):
        from api import services
        from core.schema import AUDIT_ENTITY_TENSION

        fake = _FakeSession(("tension",))
        monkeypatch.setattr(services, "_pg_session", lambda: fake)
        recorded = []
        monkeypatch.setattr(services, "record_review_event", lambda *a, **kw: recorded.append(a))

        result = services.set_trace_map_exclusion("user-1", "trace-1", True)

        assert result == {"trace_id": "trace-1", "map_excluded": True}
        assert fake.committed and not fake.rolled_back
        assert recorded, "record_review_event が呼ばれていない"
        assert recorded[0][0] == AUDIT_ENTITY_TENSION

    def test_question_row_excludes_and_records_structure_anchor_entity(self, monkeypatch):
        """kind='question' の行でも動く。"""
        from api import services
        from core.schema import AUDIT_ENTITY_STRUCTURE_ANCHOR

        fake = _FakeSession(("question",))
        monkeypatch.setattr(services, "_pg_session", lambda: fake)
        recorded = []
        monkeypatch.setattr(services, "record_review_event", lambda *a, **kw: recorded.append(a))

        result = services.set_trace_map_exclusion("user-1", "trace-2", True)

        assert result == {"trace_id": "trace-2", "map_excluded": True}
        assert recorded[0][0] == AUDIT_ENTITY_STRUCTURE_ANCHOR

    def test_restore_sets_excluded_false_and_passes_param(self, monkeypatch):
        """地図に戻す: excluded=False で呼び、SQL パラメータにも False が渡る。"""
        from api import services

        fake = _FakeSession(("tension",))
        monkeypatch.setattr(services, "_pg_session", lambda: fake)
        monkeypatch.setattr(services, "record_review_event", lambda *a, **kw: None)

        result = services.set_trace_map_exclusion("user-1", "trace-1", False)

        assert result == {"trace_id": "trace-1", "map_excluded": False}
        assert fake.calls[0][1]["excluded"] is False

    def test_other_users_row_or_missing_trace_returns_none(self, monkeypatch):
        """他人の行・存在しない trace_id は WHERE 句で除外され行が無くなる
        （fetchone()=None を模擬）。None のときは監査記帳を呼ばない。"""
        from api import services

        fake = _FakeSession(None)
        monkeypatch.setattr(services, "_pg_session", lambda: fake)
        recorded = []
        monkeypatch.setattr(services, "record_review_event", lambda *a, **kw: recorded.append(a))

        result = services.set_trace_map_exclusion("user-1", "trace-missing", True)

        assert result is None
        assert not recorded

    def test_db_error_rolls_back_and_returns_none(self, monkeypatch):
        from api import services

        fake = _BoomSession(None)
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        result = services.set_trace_map_exclusion("user-1", "trace-1", True)

        assert result is None
        assert fake.rolled_back
        assert fake.closed


class TestGetTraceMapExclusionFlags:
    def test_returns_dict_of_excluded_trace_ids(self, monkeypatch):
        from api import services

        fake = _FakeSession([("trace-1",), ("trace-2",)])
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        flags = services.get_trace_map_exclusion_flags("user-1", "course-1")

        assert flags == {"trace-1": True, "trace-2": True}

    def test_returns_empty_dict_when_none_excluded(self, monkeypatch):
        from api import services

        fake = _FakeSession([])
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        assert services.get_trace_map_exclusion_flags("user-1", "course-1") == {}

    def test_returns_empty_dict_on_db_error(self, monkeypatch):
        from api import services

        monkeypatch.setattr(services, "_pg_session", lambda: _BoomSession(None))

        assert services.get_trace_map_exclusion_flags("user-1", "course-1") == {}


# ===========================================================================
# routes/learning.py — エンドポイント契約
# ===========================================================================


class TestRoutesExist:
    def test_map_exclude_and_restore_routes_registered(self):
        source = _read(LEARNING)
        assert '"/traces/{trace_id}/map-exclude"' in source
        assert '"/traces/{trace_id}/map-restore"' in source

    def test_routes_are_post(self):
        source = _read(LEARNING)
        assert '@router.post("/traces/{trace_id}/map-exclude")' in source
        assert '@router.post("/traces/{trace_id}/map-restore")' in source


class TestRoutesAreOwnerScoped:
    def test_routes_use_current_user_id_only(self):
        """本人のみ: current_user["id"] を使い、任意の user_id 引数を受けない。"""
        for fn in ("map_exclude_trace_route", "map_restore_trace_route"):
            body = _route_body(fn)
            assert 'current_user["id"]' in body
            assert "user_id: str" not in body


class TestRouteBehaviorViaFakeService:
    """ルート関数を直接呼び出し、404 マッピングと引数の受け渡しを検証する。"""

    def test_map_exclude_route_calls_service_with_true(self, monkeypatch):
        from api.routes import learning

        captured = {}

        def _fake(uid, tid, excluded):
            captured["args"] = (uid, tid, excluded)
            return {"trace_id": tid, "map_excluded": excluded}

        monkeypatch.setattr(learning, "set_trace_map_exclusion", _fake)
        result = learning.map_exclude_trace_route("trace-1", current_user={"id": "user-1"})

        assert captured["args"] == ("user-1", "trace-1", True)
        assert result == {"ok": True, "trace_id": "trace-1", "map_excluded": True}

    def test_map_restore_route_calls_service_with_false(self, monkeypatch):
        from api.routes import learning

        captured = {}

        def _fake(uid, tid, excluded):
            captured["args"] = (uid, tid, excluded)
            return {"trace_id": tid, "map_excluded": excluded}

        monkeypatch.setattr(learning, "set_trace_map_exclusion", _fake)
        result = learning.map_restore_trace_route("trace-1", current_user={"id": "user-1"})

        assert captured["args"] == ("user-1", "trace-1", False)
        assert result == {"ok": True, "trace_id": "trace-1", "map_excluded": False}

    def test_map_exclude_route_404_when_not_found(self, monkeypatch):
        from fastapi import HTTPException
        from api.routes import learning

        monkeypatch.setattr(learning, "set_trace_map_exclusion", lambda *a, **kw: None)

        with pytest.raises(HTTPException) as exc_info:
            learning.map_exclude_trace_route("trace-missing", current_user={"id": "user-1"})
        assert exc_info.value.status_code == 404

    def test_map_restore_route_404_when_not_found(self, monkeypatch):
        from fastapi import HTTPException
        from api.routes import learning

        monkeypatch.setattr(learning, "set_trace_map_exclusion", lambda *a, **kw: None)

        with pytest.raises(HTTPException) as exc_info:
            learning.map_restore_trace_route("trace-missing", current_user={"id": "user-1"})
        assert exc_info.value.status_code == 404


# ===========================================================================
# interest-traces 一覧への map_excluded フィールド付与
# ===========================================================================


class TestInterestTracesListEnrichment:
    def test_route_enriches_traces_with_map_excluded_field(self, monkeypatch):
        from api.routes import learning

        monkeypatch.setattr(
            learning, "get_interest_traces",
            lambda uid, cid, tid: {
                "traces": [{"id": "t1"}, {"id": "t2"}],
                "course_id": cid, "topic_id": tid,
            },
        )
        monkeypatch.setattr(
            learning, "get_trace_map_exclusion_flags",
            lambda uid, cid: {"t1": True},
        )

        result = learning.get_interest_traces_route(
            "course-1", topic_id=None, current_user={"id": "user-1"},
        )

        by_id = {t["id"]: t for t in result["traces"]}
        assert by_id["t1"]["map_excluded"] is True
        assert by_id["t2"]["map_excluded"] is False

    def test_route_passes_current_user_and_course_id_to_flags_lookup(self, monkeypatch):
        from api.routes import learning

        monkeypatch.setattr(
            learning, "get_interest_traces",
            lambda uid, cid, tid: {"traces": [], "course_id": cid, "topic_id": tid},
        )
        captured = {}

        def _fake_flags(uid, cid):
            captured["args"] = (uid, cid)
            return {}

        monkeypatch.setattr(learning, "get_trace_map_exclusion_flags", _fake_flags)

        learning.get_interest_traces_route("course-9", topic_id=None, current_user={"id": "user-9"})

        assert captured["args"] == ("user-9", "course-9")

    def test_existing_get_interest_traces_function_is_unmodified(self):
        """services.get_interest_traces 自体は改変しない（既存関数は非改変。
        map_excluded の付与はルート側の後処理のみで行う）。"""
        body = _service_body("get_interest_traces")
        assert "map_excluded" not in body

    def test_route_uses_new_helper_rather_than_reimplementing_query(self):
        body = _route_body("get_interest_traces_route")
        assert "get_trace_map_exclusion_flags(" in body
