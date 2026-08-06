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
            "formulas": [], "figures": [], "has_audio": False, "duration_ms": 0,
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

    def test_auto_paginate_defaults_to_off(self, client):
        """既定は従来どおり（マーカー分割のみ）。既存の呼び出しは不変。"""
        long_text = "\n\n".join(["段落" + str(i) + "。" + "あ" * 300 for i in range(4)])
        r = client.post(
            "/api/admin/lecture-studio/preview-split",
            headers=_headers("TEACHER"),
            json={"display_text": long_text, "spoken_text": None, "formulas": []},
        )
        data = r.json()
        assert len(data["slides"]) == 1
        assert data["auto_paginated"] is False
        assert data["spoken_degraded"] is False


class TestPreviewSplitAutoPaginate:
    """教材図スタジオ §7.5: 受講画面と同じ自動ページ分割を反映するオプション。

    ``core.lecture.auto_paginate_slides``（``_build_topic_slides`` が使う正本）を
    そのまま通すことを確認する（クライアント側に分割ロジックを再実装しないため）。
    """

    def _post(self, client, **body):
        payload = {"display_text": "", "spoken_text": None, "formulas": []}
        payload.update(body)
        return client.post(
            "/api/admin/lecture-studio/preview-split",
            headers=_headers("TEACHER"),
            json=payload,
        )

    def test_long_material_without_markers_is_paginated(self, client):
        from core.lecture import auto_paginate_slides

        display = "\n\n".join(["段落" + str(i) + "。" + "あ" * 300 for i in range(4)])
        spoken = "\n\n".join(["よみ" + str(i) + "。" + "い" * 300 for i in range(4)])
        expected, _mismatch = auto_paginate_slides(display, spoken, [])

        r = self._post(client, display_text=display, spoken_text=spoken, auto_paginate=True)
        data = r.json()

        assert len(data["slides"]) == len(expected) > 1
        assert [s["display_text"] for s in data["slides"]] == [s["display_text"] for s in expected]
        assert data["auto_paginated"] is True
        assert data["display_segment_count"] == len(expected)
        assert data["spoken_degraded"] is False

    def test_figure_embeds_count_toward_the_page_boundary(self, client):
        """図1個=200字換算のため、図を入れるとページ境界が動く（§7.5）。"""
        base = "\n\n".join(["段落" + str(i) + "。" + "あ" * 120 for i in range(4)])
        with_figures = "\n\n".join(
            ["段落" + str(i) + "。" + "あ" * 120 + "\n\n![[figure:fig-" + str(i) + "]]" for i in range(4)]
        )
        plain = self._post(client, display_text=base, auto_paginate=True).json()
        figured = self._post(client, display_text=with_figures, auto_paginate=True).json()
        assert len(figured["slides"]) > len(plain["slides"])

    def test_spoken_degradation_is_reported(self, client):
        """読み上げが同数ページに割れないと spoken は空（タイマー送り）に縮退する。"""
        display = "\n\n".join(["段落" + str(i) + "。" + "あ" * 300 for i in range(4)])
        r = self._post(
            client, display_text=display, spoken_text="ひとかたまりの読み上げ原稿", auto_paginate=True,
        )
        data = r.json()
        assert len(data["slides"]) > 1
        assert data["spoken_degraded"] is True
        assert data["spoken_segment_count"] == 0
        assert all(not (s["spoken_text"] or "") for s in data["slides"])

    def test_explicit_markers_take_precedence(self, client):
        r = self._post(
            client,
            display_text="Slide A\n===\nSlide B",
            spoken_text="Speak A\n===\nSpeak B",
            auto_paginate=True,
        )
        data = r.json()
        assert len(data["slides"]) == 2
        assert data["auto_paginated"] is False
        assert data["spoken_degraded"] is False
        assert data["display_segment_count"] == 2
        assert data["spoken_segment_count"] == 2

    def test_marker_mismatch_still_reports_the_collapse(self, client):
        """マーカー分割数の不一致は既存どおり「1枚に統合」+ 縮退前の区切り数を返す。"""
        r = self._post(
            client,
            display_text="Slide A\n===\nSlide B",
            spoken_text="Speak only one block",
            auto_paginate=True,
        )
        data = r.json()
        assert data["mismatch"] is True
        assert len(data["slides"]) == 1
        assert data["display_segment_count"] == 2
        assert data["spoken_segment_count"] == 1
        assert data["auto_paginated"] is False

    def test_short_material_is_a_single_slide(self, client):
        r = self._post(client, display_text="短い本文", spoken_text="読み上げ", auto_paginate=True)
        data = r.json()
        assert len(data["slides"]) == 1
        assert data["auto_paginated"] is False
        assert data["spoken_degraded"] is False
        assert data["display_segment_count"] == 1
        assert data["spoken_segment_count"] == 1

    def test_auto_paginate_does_not_touch_the_database(self, client, monkeypatch):
        import core.postgres as postgres_mod

        def _boom(*_a, **_k):
            raise AssertionError("preview-split must not touch the database")

        monkeypatch.setattr(postgres_mod, "get_session", _boom)
        display = "\n\n".join(["段落" + str(i) + "。" + "あ" * 300 for i in range(4)])
        assert self._post(client, display_text=display, auto_paginate=True).status_code == 200

    def test_student_is_still_forbidden(self, client):
        r = client.post(
            "/api/admin/lecture-studio/preview-split",
            headers=_headers("STUDENT"),
            json={"display_text": "A", "auto_paginate": True},
        )
        assert r.status_code == 403


class TestPreviewSplitDatabaseIsolation:
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
