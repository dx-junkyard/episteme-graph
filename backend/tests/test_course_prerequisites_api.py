"""``POST /api/admin/course-builder/prerequisite-check`` のテスト（P2-4）。

正本: ``docs/features/learning_units_design.md`` §6.4。

観点:
  1. TEACHER 以上のゲート（未認証・STUDENT は通らない）
  2. DB に触れない・LLM を呼ばない（ルーターのソース構造 + セッション未使用で確認）
  3. トピックゼロの下書きは ``available: false`` / ``facts: []``
  4. 下書きの topic id 組み立てが admin.js の ``approveCourse()`` と同じ規則
  5. 返すのは事実文だけ（数値キー・件数を返さない）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入（full API は Docker 内で）")

_PATH = "/api/admin/course-builder/prerequisite-check"
_UID_TEACHER = "22222222-2222-2222-2222-222222222222"
_UID_STUDENT = "33333333-3333-3333-3333-333333333333"

_ROUTE_SRC = (BACKEND / "api" / "routes" / "course_prerequisites.py").read_text(encoding="utf-8")


def _headers(role: str = "TEACHER", sub: str = _UID_TEACHER):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    return {"Authorization": "Bearer " + jwt.encode(payload, "test-secret-key", algorithm="HS256")}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app)


def _draft(chapters):
    return {"chapters": chapters}


# ---------------------------------------------------------------------------
# 1. 権限
# ---------------------------------------------------------------------------


class TestAuthorization:
    def test_anonymous_is_rejected(self, client):
        res = client.post(_PATH, json=_draft([]))
        assert res.status_code in (401, 403)

    def test_student_is_rejected(self, client):
        res = client.post(_PATH, json=_draft([]), headers=_headers(role="STUDENT", sub=_UID_STUDENT))
        assert res.status_code == 403

    def test_teacher_is_allowed(self, client):
        res = client.post(_PATH, json=_draft([]), headers=_headers())
        assert res.status_code == 200

    def test_route_requires_teacher(self):
        assert "_require_teacher" in _ROUTE_SRC


# ---------------------------------------------------------------------------
# 2. 応答（事実文だけ・数値なし）
# ---------------------------------------------------------------------------


class TestResponse:
    def test_empty_draft_is_not_available(self, client):
        res = client.post(_PATH, json=_draft([]), headers=_headers())
        assert res.json() == {"available": False, "facts": []}

    def test_chapters_without_topics_are_not_available(self, client):
        res = client.post(_PATH, json=_draft([{"title": "第1章", "topics": []}]), headers=_headers())
        assert res.json() == {"available": False, "facts": []}

    def test_healthy_draft_is_available_with_no_facts(self, client):
        body = _draft([
            {"title": "第1章", "topics": [
                {"title": "波動関数", "prerequisites": []},
                {"title": "期待値", "prerequisites": ["波動関数"]},
            ]},
        ])
        res = client.post(_PATH, json=body, headers=_headers())
        assert res.json() == {"available": True, "facts": []}

    def test_cycle_is_reported_as_a_fact(self, client):
        body = _draft([
            {"title": "第1章", "topics": [
                {"title": "A", "prerequisites": ["B"]},
                {"title": "B", "prerequisites": ["A"]},
            ]},
        ])
        payload = client.post(_PATH, json=body, headers=_headers()).json()
        assert payload["available"] is True
        assert payload["facts"] == ["「A」は「B」を前提とし、「B」は「A」を前提としています。"]

    def test_forward_reference_across_chapters(self, client):
        body = _draft([
            {"title": "第1章", "topics": [{"title": "A", "prerequisites": ["B"]}]},
            {"title": "第2章", "topics": [{"title": "B"}]},
        ])
        payload = client.post(_PATH, json=body, headers=_headers()).json()
        assert payload["facts"] == ["「A」は、後の章にある「B」を前提にしています。"]

    def test_plain_string_topics_are_accepted(self, client):
        body = _draft([{"title": "第1章", "topics": ["A", "B"]}])
        payload = client.post(_PATH, json=body, headers=_headers()).json()
        assert payload == {"available": True, "facts": []}

    def test_string_chapters_do_not_break_the_check(self, client):
        body = {"chapters": ["第1章", {"title": "第2章", "topics": [{"title": "A", "prerequisites": ["未登録"]}]}]}
        payload = client.post(_PATH, json=body, headers=_headers()).json()
        assert payload["facts"] == [
            "「A」の前提「未登録」に対応するトピックがこのコースにありません。"
        ]

    def test_flat_topics_are_accepted(self, client):
        body = {"topics": [{"title": "A", "prerequisites": ["B"]}, {"title": "B", "prerequisites": ["A"]}]}
        payload = client.post(_PATH, json=body, headers=_headers()).json()
        assert len(payload["facts"]) == 1

    def test_response_has_no_numeric_fields(self, client):
        body = _draft([
            {"title": "第1章", "topics": [
                {"title": "A", "prerequisites": ["B"]},
                {"title": "B", "prerequisites": ["A"]},
            ]},
        ])
        payload = client.post(_PATH, json=body, headers=_headers()).json()
        assert set(payload) == {"available", "facts"}
        assert all(isinstance(f, str) for f in payload["facts"])


# ---------------------------------------------------------------------------
# 3. topic id の組み立て（admin.js approveCourse() と同じ規則）
# ---------------------------------------------------------------------------


class TestDraftTopicAssembly:
    def test_ids_are_sequential_across_chapters(self):
        from routes.course_prerequisites import PrerequisiteCheckRequest, _draft_topics

        body = PrerequisiteCheckRequest.model_validate(_draft([
            {"title": "第1章", "topics": ["A", "B"]},
            {"title": "第2章", "topics": ["C"]},
        ]))
        topics = _draft_topics(body)
        assert [(t["id"], t["chapter_index"], t["title"]) for t in topics] == [
            ("t0", 0, "A"), ("t1", 0, "B"), ("t2", 1, "C"),
        ]

    def test_string_chapter_still_advances_the_chapter_index(self):
        from routes.course_prerequisites import PrerequisiteCheckRequest, _draft_topics

        body = PrerequisiteCheckRequest.model_validate(
            {"chapters": ["第1章", {"title": "第2章", "topics": ["A"]}]}
        )
        topics = _draft_topics(body)
        assert topics[0]["chapter_index"] == 1


# ---------------------------------------------------------------------------
# 4. ガードレール（DB 非変更・LLM 0 回）
# ---------------------------------------------------------------------------


class TestGuardrails:
    def test_route_does_not_touch_db_or_llm(self):
        for token in ("get_session", "sa_text", "generate_text", "INSERT", "UPDATE", "DELETE"):
            assert token not in _ROUTE_SRC, f"{token} がルーターに現れる（DB 非変更・LLM 0 回）"

    def test_route_delegates_to_core(self):
        assert "from core.course_prerequisites import analyze_prerequisite_order" in _ROUTE_SRC

    def test_router_is_registered_under_admin_prefix(self):
        main_src = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")
        assert (
            'app.include_router(course_prerequisites_routes.router, prefix="/api/admin")'
            in main_src
        )
