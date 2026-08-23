"""わたしの記録 API（routes/my_records.py）のテスト — TestClient 不使用・DB 非接続。

test_understanding_cycle_api.py と同型の手法（ルート関数を直接呼ぶ + ソース静的検査）。
対象仕様: docs/features/trace_registry_sovereignty_ledger_design.md §3.3。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_MY_RECORDS_SRC = (BACKEND / "api" / "routes" / "my_records.py").read_text(encoding="utf-8")
_MAIN_SRC = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")


# ===========================================================================
# ソース静的検査（ルーター形状・読み取り専用・監査記帳なし）
# ===========================================================================


class TestRouterShape:
    def test_me_router_prefix_is_api_me(self):
        assert 'me_router = APIRouter(prefix="/api/me"' in _MY_RECORDS_SRC

    def test_exactly_two_get_endpoints(self):
        assert '@me_router.get("/records")' in _MY_RECORDS_SRC
        assert '@me_router.get("/records/export")' in _MY_RECORDS_SRC
        assert _MY_RECORDS_SRC.count("@me_router.") == 2  # GET 2本のみ（TR4）

    def test_export_is_an_attachment_download(self):
        assert "Content-Disposition" in _MY_RECORDS_SRC
        assert "attachment" in _MY_RECORDS_SRC
        assert 'media_type="application/json"' in _MY_RECORDS_SRC

    def test_no_audit_recording_for_learner_export(self):
        """学習者本人の持ち出しは監査記帳しない（意図的 — 観察面を広げない）。"""
        assert "record_review_event" not in _MY_RECORDS_SRC

    def test_current_user_is_the_only_subject(self):
        assert 'current_user["id"]' in _MY_RECORDS_SRC
        assert "{user_id}" not in _MY_RECORDS_SRC

    def test_main_registers_the_router(self):
        assert "from routes import my_records as my_records_routes" in _MAIN_SRC
        assert "app.include_router(my_records_routes.me_router)" in _MAIN_SRC


# ===========================================================================
# ルート関数の直接呼び出し（DB は monkeypatch で遮断）
# ===========================================================================


class TestGetMyRecords:
    def test_overview_is_built_for_the_current_user(self, monkeypatch):
        from routes import my_records

        seen = {}

        def fake_fetch(user_id, limit=500):
            seen["user_id"] = user_id
            return (
                [{
                    "id": "t-1", "kind": "question", "status": "open",
                    "course_id": "c-1", "topic_id": None,
                    "payload": {"text": "q", "context_label": ""},
                    "created_at": "2026-08-15T10:00:00+00:00",
                }],
                False,
            )

        monkeypatch.setattr(my_records, "fetch_ledger_rows", fake_fetch)
        monkeypatch.setattr(
            my_records, "fetch_course_labels", lambda rows: {"c-1": "コース"}
        )

        result = my_records.get_my_records(current_user={"id": "u-1"})
        assert seen["user_id"] == "u-1"
        assert result["provenance_note"]
        assert result["truncated"] is False
        question_system = next(
            s for s in result["systems"] if s["kind"] == "question"
        )
        assert question_system["items"][0]["course_label"] == "コース"

    def test_truncated_is_passed_through(self, monkeypatch):
        from routes import my_records

        monkeypatch.setattr(
            my_records, "fetch_ledger_rows", lambda user_id, limit=500: ([], True)
        )
        monkeypatch.setattr(my_records, "fetch_course_labels", lambda rows: {})
        result = my_records.get_my_records(current_user={"id": "u-1"})
        assert result["truncated"] is True


class TestExportMyRecords:
    def _export(self, monkeypatch, truncated=False):
        from routes import my_records

        rows = [{
            "id": "t-1", "kind": "tension", "status": "dismissed",
            "course_id": "c-1", "topic_id": "topic-1",
            "payload": {"text": "本文", "confidence": 0.9},
            "created_at": "2026-08-15T10:00:00+00:00",
        }]
        monkeypatch.setattr(
            my_records,
            "fetch_ledger_rows",
            lambda user_id, limit=500: (rows, truncated),
        )
        return my_records.export_my_records(current_user={"id": "u-1"})

    def test_response_is_an_attachment_with_dated_filename(self, monkeypatch):
        response = self._export(monkeypatch)
        disposition = response.headers["content-disposition"]
        assert disposition.startswith('attachment; filename="my-records-')
        assert disposition.endswith('.json"')
        assert response.media_type == "application/json"

    def test_body_is_full_export_without_user_id(self, monkeypatch):
        response = self._export(monkeypatch)
        body = response.body.decode("utf-8")
        assert "user_id" not in body
        payload = json.loads(body)
        assert payload["schema_version"] == 1
        assert payload["records"][0]["payload"] == {"text": "本文", "confidence": 0.9}
        assert payload["records"][0]["status"] == "dismissed"  # 保持したまま持ち出す（P4）
        assert "truncated" not in payload  # 非到達時はキー自体を出さない

    def test_export_truncation_is_passed_through(self, monkeypatch):
        """読み出し上限到達（fetch の truncated）が export DTO に正直に届く（TR5）。"""
        response = self._export(monkeypatch, truncated=True)
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["truncated"] is True
