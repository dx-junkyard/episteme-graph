"""core/status/projector.py::project_course_status の音声 readiness 判定（Tier2-11）。

調査レポート（docs/architecture/consolidation_survey_2026-07.md 提案11）が指摘した
「音声準備完了の判定が二重実装かつ粒度不一致」の回帰確認。

- 旧実装: chunk 単位のみ（``lecture_audio_cache`` に1行でもあれば readiness）。
  スライドが複数あるチャンクで一部スライドしか音声が無くても "generated" と
  誤判定しうる（言語不一致チェックも無かった）。
- 新実装: ``core.lecture.compute_material_audio_readiness()``（スライド単位 + 言語一致）
  を ``api/routes/lecture.py::get_topic_audio_status`` と共通で呼ぶ。本テストは
  projector 側がスライド単位判定に切り替わったことを確認する
  （chunk 単位なら GENERATED と誤判定するケースが正しく PARTIAL になること）。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from core.status import schema
from core.status.projector import project_course_status


def _result(fetchall=None, fetchone=None, mappings_fetchone=None):
    result = MagicMock()
    if fetchall is not None:
        result.fetchall.return_value = fetchall
    if fetchone is not None:
        result.fetchone.return_value = fetchone
    if mappings_fetchone is not None:
        result.mappings.return_value.fetchone.return_value = mappings_fetchone
    return result


def _course_row(data: dict) -> dict:
    return {"id": "c1", "data": data, "is_published": False, "updated_at": None}


class TestAudioStatusSlideLevelRegression:
    """chunk 単位なら見逃す「一部スライドのみ未生成」を PARTIAL として検出できること。"""

    def test_multi_slide_chunk_with_partial_audio_is_partial_not_generated(self):
        data = {"sources": [{"material_id": "m1"}]}
        course_result = _result(mappings_fetchone=_course_row(data))

        # script_status 用クエリ: 1チャンク・spoken_text 生成済み
        script_result = _result(fetchall=[("chunk-1", "spoken script")])

        # compute_material_audio_readiness 用クエリ: 1チャンクが2スライドに分割される。
        display = "Slide A\n===\nSlide B"
        spoken = "Speak A\n===\nSpeak B"
        audio_chunk_result = _result(fetchall=[("c0", display, display, spoken, [], "ja")])
        # スライド0のみキャッシュあり（スライド1は未生成） -> 旧 chunk 単位判定なら
        # 「chunk-id が1件でもキャッシュにある」= audio_chunks(1) == total_chunks(1) => GENERATED
        # と誤判定していた箇所。
        audio_rows_result = _result(fetchall=[("c0", 0)])

        shared_result = _result(fetchone=None)

        session = MagicMock()
        session.execute.side_effect = [
            course_result, script_result, audio_chunk_result, audio_rows_result, shared_result,
        ]

        status = project_course_status(session, "c1")

        assert status.audio_status == schema.AUDIO_STATUS_PARTIAL

    def test_all_slides_ready_and_language_matched_is_generated(self):
        data = {"sources": [{"material_id": "m1"}]}
        course_result = _result(mappings_fetchone=_course_row(data))
        script_result = _result(fetchall=[("chunk-1", "spoken script")])

        audio_chunk_result = _result(fetchall=[("c0", "text", "text", "spoken", [], "ja")])
        audio_rows_result = _result(fetchall=[("c0", 0)])
        shared_result = _result(fetchone=None)

        session = MagicMock()
        session.execute.side_effect = [
            course_result, script_result, audio_chunk_result, audio_rows_result, shared_result,
        ]

        status = project_course_status(session, "c1")

        assert status.audio_status == schema.AUDIO_STATUS_GENERATED

    def test_stale_language_audio_not_counted_generated(self):
        """キャッシュはあるが spoken_language がコース設定と不一致 -> readiness に数えない。"""
        data = {
            "sources": [{"material_id": "m1"}],
            "lecture_studio_settings": {"lecture_language": "en"},
        }
        course_result = _result(mappings_fetchone=_course_row(data))
        script_result = _result(fetchall=[("chunk-1", "spoken script")])

        audio_chunk_result = _result(fetchall=[("c0", "text", "text", "spoken", [], "ja")])
        audio_rows_result = _result(fetchall=[("c0", 0)])
        shared_result = _result(fetchone=None)

        session = MagicMock()
        session.execute.side_effect = [
            course_result, script_result, audio_chunk_result, audio_rows_result, shared_result,
        ]

        status = project_course_status(session, "c1")

        assert status.audio_status == schema.AUDIO_STATUS_NONE

    def test_no_material_ids_is_none_without_audio_queries(self):
        data = {"sources": []}
        course_result = _result(mappings_fetchone=_course_row(data))
        shared_result = _result(fetchone=None)

        session = MagicMock()
        # material_ids が空なので script クエリも compute_material_audio_readiness の
        # クエリも発行されない（_fetch_course → _course_shared の2回のみ）。
        session.execute.side_effect = [course_result, shared_result]

        status = project_course_status(session, "c1")

        assert status.audio_status == schema.AUDIO_STATUS_NONE
        assert status.script_status == schema.SCRIPT_STATUS_DRAFT
        assert session.execute.call_count == 2
