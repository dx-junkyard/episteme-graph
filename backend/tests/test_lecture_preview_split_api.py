"""POST /api/admin/lecture-studio/preview-split の API テスト（Tier2-11）。

調査レポート（docs/architecture/consolidation_survey_2026-07.md 提案11）が指摘した
「スライド分割ロジックが Python/JS に並行実装」の解消策: admin.js の `lsSplitSlides`
ローカル実装を廃止し、本エンドポイント（``core.lecture.split_slides`` をそのまま
呼ぶだけ）に一本化する。DB を一切変更しない純粋な変換エンドポイントのため、
TestClient で直接検証できる（test_next_steps_guardrails.py の Group B と同型）。
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

_UID_TEACHER = "22222222-2222-2222-2222-222222222222"


def _headers(role: str, sub: str = _UID_TEACHER):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    return {"Authorization": "Bearer " + jwt.encode(payload, "test-secret-key", algorithm="HS256")}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app)


class TestPreviewSplitEndpointAuth:
    """教員ゲートの fail-closed 確認。"""

    def test_student_forbidden(self, client):
        r = client.post(
            "/api/admin/lecture-studio/preview-split",
            headers=_headers("STUDENT"),
            json={"display_text": "A", "spoken_text": "B", "formulas": []},
        )
        assert r.status_code == 403

    def test_unauthenticated_forbidden(self, client):
        r = client.post(
            "/api/admin/lecture-studio/preview-split",
            json={"display_text": "A", "spoken_text": "B"},
        )
        assert r.status_code in (401, 403)

    def test_teacher_allowed(self, client):
        r = client.post(
            "/api/admin/lecture-studio/preview-split",
            headers=_headers("TEACHER"),
            json={"display_text": "Hello", "spoken_text": "Speak", "formulas": []},
        )
        assert r.status_code == 200

    def test_system_admin_allowed(self, client):
        """_require_teacher は TEACHER と SYSTEM_ADMIN の両方を許可する。"""
        r = client.post(
            "/api/admin/lecture-studio/preview-split",
            headers=_headers("SYSTEM_ADMIN"),
            json={"display_text": "Hello", "spoken_text": "Speak", "formulas": []},
        )
        assert r.status_code == 200

    def test_invalid_body_returns_422(self, client):
        r = client.post(
            "/api/admin/lecture-studio/preview-split",
            headers=_headers("TEACHER"),
            json={"display_text": "Hello", "formulas": "not-a-list"},
        )
        assert r.status_code == 422

    def test_empty_body_uses_defaults(self, client):
        """全フィールドがデフォルト値を持つため、空ボディでも 200 で返る。"""
        r = client.post(
            "/api/admin/lecture-studio/preview-split",
            headers=_headers("TEACHER"),
            json={},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["slides"] == [{
            "slide_index": 0, "display_text": "", "spoken_text": None,
            "formulas": [], "has_audio": False, "duration_ms": 0,
        }]


class TestPreviewSplitEndpointBehavior:
    """core.lecture.split_slides と同一の分割ロジックがそのまま返ること。"""

    def test_no_marker_returns_single_slide(self, client):
        r = client.post(
            "/api/admin/lecture-studio/preview-split",
            headers=_headers("TEACHER"),
            json={"display_text": "Hello", "spoken_text": "Speak", "formulas": []},
        )
        data = r.json()
        assert data["mismatch"] is False
        assert len(data["slides"]) == 1
        assert data["slides"][0]["display_text"] == "Hello"
        assert data["slides"][0]["spoken_text"] == "Speak"
        assert data["display_segment_count"] == 1
        assert data["spoken_segment_count"] == 1

    def test_marker_splits_into_multiple_slides(self, client):
        r = client.post(
            "/api/admin/lecture-studio/preview-split",
            headers=_headers("TEACHER"),
            json={
                "display_text": "Slide A\n===\nSlide B",
                "spoken_text": "Speak A\n===\nSpeak B",
                "formulas": [],
            },
        )
        data = r.json()
        assert data["mismatch"] is False
        assert len(data["slides"]) == 2
        assert data["slides"][0]["display_text"] == "Slide A"
        assert data["slides"][0]["spoken_text"] == "Speak A"
        assert data["slides"][1]["display_text"] == "Slide B"
        assert data["slides"][1]["spoken_text"] == "Speak B"

    def test_segment_count_mismatch_collapses_to_single_slide(self, client):
        r = client.post(
            "/api/admin/lecture-studio/preview-split",
            headers=_headers("TEACHER"),
            json={
                "display_text": "Slide A\n===\nSlide B",
                "spoken_text": "Speak only one block",
                "formulas": [],
            },
        )
        data = r.json()
        assert data["mismatch"] is True
        assert len(data["slides"]) == 1
        # 縮退前のセグメント数はクライアントの整合インジケータ表示専用に返す。
        assert data["display_segment_count"] == 2
        assert data["spoken_segment_count"] == 1

    def test_formulas_are_assigned_to_referencing_slide(self, client):
        r = client.post(
            "/api/admin/lecture-studio/preview-split",
            headers=_headers("TEACHER"),
            json={
                "display_text": "Energy: [[FORMULA_0]]\n===\nSlide B",
                "spoken_text": "A\n===\nB",
                "formulas": [
                    {"id": "[[FORMULA_0]]", "latex": "E=mc^2", "spoken": "E equals m c squared", "is_display": False},
                ],
            },
        )
        data = r.json()
        assert data["mismatch"] is False
        assert len(data["slides"][0]["formulas"]) == 1
        assert data["slides"][0]["formulas"][0]["latex"] == "E=mc^2"
        assert data["slides"][1]["formulas"] == []

    def test_does_not_touch_database(self, client, monkeypatch):
        """DB 非変更エンドポイントであることの回帰確認。"""
        import core.postgres as postgres_mod

        def _boom(*_a, **_k):
            raise AssertionError("preview-split must not touch the database")

        monkeypatch.setattr(postgres_mod, "get_session", _boom)

        r = client.post(
            "/api/admin/lecture-studio/preview-split",
            headers=_headers("TEACHER"),
            json={"display_text": "Hello", "spoken_text": None, "formulas": []},
        )
        assert r.status_code == 200
