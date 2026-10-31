"""IK-0384: 学習者向けコース DTO にコース生成の内部記録を出さない（KO10 / 数値非表示）。

``GET /api/learning/courses/{id}`` は ``course_content_status`` をそのまま返しており、
件数（equations / components / mapping_topics …）・document_id・draft_errors・
uncovered_sections(_dropped)（PDF の柱・図軸ラベルの断片を含む）が学習者に届いていた
（ペルソナ通し受講 第 8 周）。学習画面（app.js）はこの dict を読まない。学習者向けには
状態の語（``status``）だけを残す。教員向けの経路（PUT の応答・原稿スタジオ）は射影しない。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.learning as learning_mod  # noqa: E402

_INTERNAL_STATUS = {
    "status": "completed",
    "counts": {"equations": 489, "components": 30, "mapping_topics": 55},
    "equations": 489,
    "components": 30,
    "document_ids": ["0b6e7a5c-1111-2222-3333-444455556666"],
    "draft_errors": ["topic t4: invalid"],
    "uncovered_sections": ["Draft version June 2, 2026 Typeset using LATEX"],
    "uncovered_sections_dropped": ["40'", "RSFR(z)/RSFR,0"],
    "extra": {"units_note": "…"},
}


def _course(status):
    return {
        "id": "c-1",
        "title": "宇宙物理",
        "topics": [{"id": "t0", "title": "問題設定", "chapter_index": 0}],
        "sources": [],
        "course_content_status": status,
    }


def _walk_numbers(obj):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_numbers(v)


class TestProjection:
    def test_only_status_word_survives(self):
        out = learning_mod._project_topics_for_learner(_course(dict(_INTERNAL_STATUS)))
        assert out["course_content_status"] == {"status": "completed"}

    def test_missing_status_projects_to_empty(self):
        out = learning_mod._project_topics_for_learner(_course({"equations": 3}))
        assert out["course_content_status"] == {}

    def test_saved_data_is_not_mutated(self):
        data = _course(dict(_INTERNAL_STATUS))
        learning_mod._project_topics_for_learner(data)
        assert data["course_content_status"]["equations"] == 489


class TestLearnerCourseEndpoint:
    def test_get_course_has_no_counts_ids_or_build_internals(self, monkeypatch):
        monkeypatch.setattr(
            learning_mod, "get_course_data", lambda uid, cid: _course(dict(_INTERNAL_STATUS))
        )
        monkeypatch.setattr(
            learning_mod, "get_personal_layer",
            lambda uid, cid: {"misconceptions_by_topic": {}, "chat_anchors": {}},
        )
        resp = learning_mod.get_course("c-1", current_user={"id": "u-1"})
        status = resp.master_course.model_dump()["course_content_status"]
        assert status == {"status": "completed"}
        assert list(_walk_numbers(status)) == []
        for key in ("document_ids", "draft_errors", "uncovered_sections",
                    "uncovered_sections_dropped", "counts", "extra"):
            assert key not in status

    def test_app_js_does_not_read_course_content_status(self):
        """学習画面は course_content_status を読まない（射影で失う表示が無いことの確認）。"""
        js = (ROOT / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
        assert "course_content_status" not in js
