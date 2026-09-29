"""IK-0375: 解説がまだ無いトピックで論文の本文をそのまま出すとき、その事実を1文添える。

ペルソナ通し受講で、公開直後のコースを開いた学習者 4 人が全員「論文の表紙
（雑誌名・著者・所属）」だけを見て、コースが壊れていると結論した。原因は
``get_topic_material`` が ``student_material`` 未生成のトピックで PDF 由来チャンクへ
黙ってフォールバックすること。是正後の契約:

1. 解説が無く本文を返すときは ``preparation_notice`` に事実文を1つ付ける
   - コースの解説生成が走っている／順番待ち（pending / queued / processing）→ 準備中
   - それ以外（記録なし・完了・パイプライン待ち・失敗）→ 生成されていません
2. 解説があるときは付けない（None）
3. 返すチャンクは変えない（表紙チャンクを落とすのは別件）
4. 本文を返さない（chunks 空）ときは「そのまま表示しています」が偽になるので付けない
5. 事実文に数字を入れない
"""

from __future__ import annotations

import os
import re
import sys
from unittest.mock import patch

import pytest

_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from core import label_vocab  # noqa: E402
from core.course_data import (  # noqa: E402
    COURSE_CONTENT_PREPARING_STATUSES,
    course_content_is_preparing,
    course_content_state,
)

_RAW_HEADER_CHUNK = {
    "id": "chunk-0",
    "text": "Journal of Cosmology 12 (2026)\nA. Author, B. Author\nUniversity of X",
    "chunk_index": 0,
    "formulas": [],
    "chapter": None,
    "section": None,
    "material_id": "mat-1",
    "graph_mentions": [],
}


def _course(status: dict | None, *, with_material: bool = False) -> dict:
    topic = {"id": "topic-1", "title": "導入"}
    if with_material:
        topic["student_material"] = {"source_text": "解説の本文。"}
    data = {"topics": [topic], "sources": [{"material_id": "mat-1"}]}
    if status is not None:
        data["course_content_status"] = status
    return data


def _call(course: dict, chunks: list[dict]):
    from api.routes.learning import get_topic_material

    with patch("api.routes.learning.get_course_data", return_value=course), \
         patch("api.routes.learning.get_course_chunks_ordered", return_value=chunks), \
         patch("api.routes.learning._load_course_figures_by_id", return_value={}):
        return get_topic_material("c1", "topic-1", {"id": "u1"})


class TestAccessor:
    def test_state_reads_status_only(self):
        assert course_content_state({"course_content_status": {"status": " processing "}}) == "processing"

    @pytest.mark.parametrize("data", [None, {}, {"course_content_status": None},
                                      {"course_content_status": "processing"},
                                      {"course_content_status": {}}])
    def test_missing_is_empty_not_completed(self, data):
        assert course_content_state(data) == ""
        assert course_content_is_preparing(data) is False

    def test_preparing_statuses(self):
        assert COURSE_CONTENT_PREPARING_STATUSES == {"pending", "queued", "processing"}
        for status in ("completed", "waiting_for_pipeline", "failed"):
            assert course_content_is_preparing({"course_content_status": {"status": status}}) is False


class TestPreparationNotice:
    @pytest.mark.parametrize("status", ["pending", "queued", "processing"])
    def test_preparing_course_gets_preparing_notice(self, status):
        resp = _call(_course({"status": status}), [_RAW_HEADER_CHUNK])
        assert resp.preparation_notice == label_vocab.MATERIAL_PREPARING_NOTICE

    @pytest.mark.parametrize("status", [None, {}, {"status": "completed"},
                                        {"status": "waiting_for_pipeline"},
                                        {"status": "failed"}])
    def test_other_states_get_not_generated_notice(self, status):
        resp = _call(_course(status), [_RAW_HEADER_CHUNK])
        assert resp.preparation_notice == label_vocab.MATERIAL_NOT_GENERATED_NOTICE

    def test_chunks_are_unchanged_including_the_header(self):
        resp = _call(_course({"status": "processing"}), [_RAW_HEADER_CHUNK])
        assert [c.id for c in resp.chunks] == ["chunk-0"]
        assert resp.chunks[0].text == _RAW_HEADER_CHUNK["text"]

    def test_no_notice_when_no_chunks_are_returned(self):
        resp = _call(_course({"status": "processing"}), [])
        assert resp.chunks == []
        assert resp.preparation_notice is None

    def test_no_notice_when_the_explanation_exists(self):
        resp = _call(_course({"status": "processing"}, with_material=True), [_RAW_HEADER_CHUNK])
        assert resp.chunks and resp.chunks[0].id == "topic:topic-1"
        assert resp.preparation_notice is None

    def test_notice_is_serialized_in_the_dto(self):
        resp = _call(_course({"status": "pending"}), [_RAW_HEADER_CHUNK])
        assert resp.model_dump()["preparation_notice"] == label_vocab.MATERIAL_PREPARING_NOTICE


class TestWording:
    @pytest.mark.parametrize("text", [label_vocab.MATERIAL_PREPARING_NOTICE,
                                      label_vocab.MATERIAL_NOT_GENERATED_NOTICE])
    def test_no_numbers(self, text):
        assert not re.search(r"[0-9０-９]", text)

    def test_the_two_sentences_differ(self):
        assert label_vocab.MATERIAL_PREPARING_NOTICE != label_vocab.MATERIAL_NOT_GENERATED_NOTICE
