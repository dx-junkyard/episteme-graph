"""N18: 音声 readiness へのトピック教材経路の合算 + 音声生成タスクの正直な完了サマリ。

調査文書 `docs/architecture/vision_ux_gap_survey_2026-07-17.md` §2-P7 / §5-7 の回帰確認。

- 旧実装: コース単位の音声 readiness（``core/status/projector.py``）はチャンク経路
  （``compute_material_audio_readiness``）しか見ておらず、トピック教材経路
  （migration 047、``lecture_uses_topic_material`` が真のトピック）の
  「読み上げ原稿（draft）の充足」「``topic_lecture_audio_cache`` の生成済みスライド」が
  反映されなかった。原稿未充足でも音声生成タスクは completed 表示で、トピック音声は
  静かに0件生成になった。
- 新実装: ``core.lecture.compute_course_audio_readiness()``（チャンク + トピック合算）が
  唯一の正本で、projector がこれを呼ぶ。音声生成バッチ（``_batch_audio_worker``）は
  トピックも生成対象1件として generated/skipped/errors/total_chunks に合算し、内訳
  （topic_targets / topic_skipped_no_script 等）と事実文 message を result_data に含める
  （フロントはカウントから完了メッセージを組み立てるため、フロント変更なしで伝わる）。
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

# api/ ディレクトリを sys.path に追加して schemas 等をインポート可能にする
_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

import api.routes.lecture_studio.scripts  # noqa: F401 - patch 対象モジュールを解決可能にする

from core.lecture import compute_course_audio_readiness
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


# ---------------------------------------------------------------------------
# 1. core.lecture.compute_course_audio_readiness — 合算の正本
# ---------------------------------------------------------------------------


class TestComputeCourseAudioReadinessTopicMerge:
    def test_topic_without_spoken_script_is_counted_but_never_ready(self):
        """原稿未充足（student_material のみ・読み上げ原稿なし）のトピックは
        total_slides に数えられ ready にならない（draft 未充足が readiness に反映される）。"""
        data = {
            "sources": [],
            "topics": [{"id": "t1", "student_material": {"source_text": "表示のみの教材"}}],
        }
        # sources が空なのでチャンク経路のクエリは発行されず、トピックキャッシュの
        # 1クエリだけが実行される。
        topic_cache_result = _result(fetchall=[])
        session = MagicMock()
        session.execute.side_effect = [topic_cache_result]

        result = compute_course_audio_readiness(session, "c1", data, "ja")

        assert result["topic_total_topics"] == 1
        assert result["topic_total_slides"] == 1
        assert result["topic_speakable_slides"] == 0
        assert result["topics_missing_script"] == 1
        assert result["total_slides"] == 1
        assert result["ready_slides"] == 0

    def test_topic_with_script_but_no_audio_is_not_ready(self):
        """原稿充足 + 音声未生成 → total に数え ready 0（音声生成で ready になる）。"""
        data = {
            "sources": [],
            "topics": [{"id": "t1", "spoken_script": "読み上げ原稿あり"}],
        }
        topic_cache_result = _result(fetchall=[])
        session = MagicMock()
        session.execute.side_effect = [topic_cache_result]

        result = compute_course_audio_readiness(session, "c1", data, "ja")

        assert result["topic_speakable_slides"] == 1
        assert result["topics_missing_script"] == 0
        assert result["total_slides"] == 1
        assert result["ready_slides"] == 0

    def test_topic_with_script_and_cached_audio_is_ready(self):
        """原稿充足 + 言語一致のトピック音声キャッシュあり → ready_slides == total_slides。"""
        data = {
            "sources": [],
            "topics": [{
                "id": "t1",
                "student_material": {"source_text": "Show A\n===\nShow B"},
                "spoken_script": "Speak A\n===\nSpeak B",
            }],
        }
        topic_cache_result = _result(fetchall=[("t1", 0, "ja"), ("t1", 1, "ja")])
        session = MagicMock()
        session.execute.side_effect = [topic_cache_result]

        result = compute_course_audio_readiness(session, "c1", data, "ja")

        assert result["total_slides"] == 2
        assert result["ready_slides"] == 2
        assert result["topic_ready_slides"] == 2
        assert result["stale_language"] is False

    def test_topic_cached_audio_in_other_language_is_stale_not_ready(self):
        data = {
            "sources": [],
            "topics": [{"id": "t1", "spoken_script": "原稿"}],
        }
        topic_cache_result = _result(fetchall=[("t1", 0, "en")])
        session = MagicMock()
        session.execute.side_effect = [topic_cache_result]

        result = compute_course_audio_readiness(session, "c1", data, "ja")

        assert result["ready_slides"] == 0
        assert result["stale_language"] is True

    def test_merges_chunk_path_and_topic_path(self):
        """チャンク経路のスライドとトピック経路のスライドが合算されること。"""
        data = {
            "sources": [{"material_id": "m1"}],
            "topics": [{"id": "t1", "spoken_script": "トピック原稿"}],
        }
        # チャンク経路: 1チャンク1スライド・音声キャッシュあり(ja)
        chunk_result = _result(fetchall=[("c0", "text", "text", "spoken", [], "ja")])
        audio_result = _result(fetchall=[("c0", 0)])
        # トピック経路: キャッシュなし
        topic_cache_result = _result(fetchall=[])
        session = MagicMock()
        session.execute.side_effect = [chunk_result, audio_result, topic_cache_result]

        result = compute_course_audio_readiness(session, "c1", data, "ja")

        assert result["chunk_total_slides"] == 1
        assert result["chunk_ready_slides"] == 1
        assert result["topic_total_slides"] == 1
        assert result["topic_ready_slides"] == 0
        assert result["total_slides"] == 2
        assert result["ready_slides"] == 1

    def test_no_topic_material_topics_issues_no_topic_query(self):
        """トピック教材経路のトピックが無ければ topic_lecture_audio_cache を引かない
        （既存のチャンク経路のみのコースで挙動・クエリ数が変わらないこと）。"""
        data = {
            "sources": [{"material_id": "m1"}],
            "topics": [{"id": "t1", "material_chunk_ids": ["c0"]}],  # 教材本文なし
        }
        chunk_result = _result(fetchall=[("c0", "text", "text", "spoken", [], "ja")])
        audio_result = _result(fetchall=[("c0", 0)])
        session = MagicMock()
        session.execute.side_effect = [chunk_result, audio_result]

        result = compute_course_audio_readiness(session, "c1", data, "ja")

        assert session.execute.call_count == 2
        assert result["topic_total_topics"] == 0
        assert result["total_slides"] == 1
        assert result["ready_slides"] == 1


# ---------------------------------------------------------------------------
# 2. projector — audio_status がトピック draft 充足を反映すること
# ---------------------------------------------------------------------------


def _course_row(data: dict) -> dict:
    return {"id": "c1", "data": data, "is_published": False, "updated_at": None}


class TestProjectorAudioStatusIncludesTopicPath:
    def test_unfulfilled_topic_draft_prevents_generated(self):
        """チャンク音声が全て揃っていても、原稿未充足のトピック教材トピックが残っていれば
        audio_status は generated にならない（旧実装は generated に化けた: N18）。"""
        data = {
            "sources": [{"material_id": "m1"}],
            "topics": [{"id": "t1", "student_material": {"source_text": "表示のみ"}}],
        }
        course_result = _result(mappings_fetchone=_course_row(data))
        script_result = _result(fetchall=[("chunk-1", "spoken script")])
        # チャンク経路: 1スライド・キャッシュ済み(ja)
        chunk_result = _result(fetchall=[("c0", "text", "text", "spoken", [], "ja")])
        audio_result = _result(fetchall=[("c0", 0)])
        # トピック経路: キャッシュなし
        topic_cache_result = _result(fetchall=[])
        shared_result = _result(fetchone=None)

        session = MagicMock()
        session.execute.side_effect = [
            course_result, script_result, chunk_result, audio_result,
            topic_cache_result, shared_result,
        ]

        status = project_course_status(session, "c1")

        assert status.audio_status == schema.AUDIO_STATUS_PARTIAL

    def test_topic_only_course_with_cached_audio_is_generated(self):
        """チャンクを持たないトピック教材コースでも、トピック音声が揃えば generated
        （旧実装はチャンク経路しか見ないため常に none だった）。"""
        data = {
            "sources": [],
            "topics": [{"id": "t1", "spoken_script": "原稿"}],
        }
        course_result = _result(mappings_fetchone=_course_row(data))
        topic_cache_result = _result(fetchall=[("t1", 0, "ja")])
        shared_result = _result(fetchone=None)

        session = MagicMock()
        session.execute.side_effect = [course_result, topic_cache_result, shared_result]

        status = project_course_status(session, "c1")

        assert status.audio_status == schema.AUDIO_STATUS_GENERATED

    def test_topic_only_course_without_audio_is_none(self):
        data = {
            "sources": [],
            "topics": [{"id": "t1", "spoken_script": "原稿"}],
        }
        course_result = _result(mappings_fetchone=_course_row(data))
        topic_cache_result = _result(fetchall=[])
        shared_result = _result(fetchone=None)

        session = MagicMock()
        session.execute.side_effect = [course_result, topic_cache_result, shared_result]

        status = project_course_status(session, "c1")

        assert status.audio_status == schema.AUDIO_STATUS_NONE


# ---------------------------------------------------------------------------
# 3. _batch_audio_worker — 正直な完了サマリ（対象トピック n / 生成 m / スキップ w + 理由）
# ---------------------------------------------------------------------------


class TestBatchAudioWorkerHonestSummary:
    @patch("api.routes.lecture_studio.scripts.time.sleep", return_value=None)
    @patch("api.routes.lecture_studio.scripts.generate_tts_audio")
    @patch("api.routes.lecture_studio.scripts._pg_session")
    @patch("api.routes.lecture_studio.scripts.update_background_task")
    def test_topic_without_script_reported_as_skipped_with_reason(
        self, mock_update, mock_pg, mock_tts, mock_sleep,
    ):
        """原稿未充足トピックは「静かに0件生成で完了」にならず、スキップ件数 + 理由
        （原稿未生成）として completed の result_data / message に現れる。"""
        from api.routes.lecture_studio import _batch_audio_worker

        course_data = {
            "topics": [{"id": "t1", "student_material": {"source_text": "表示のみの教材"}}],
        }

        _batch_audio_worker("task-1", "course-1", [], "ja", course_data)

        mock_tts.assert_not_called()
        completed_calls = [c for c in mock_update.call_args_list if c.args[1] == "completed"]
        assert completed_calls
        rd = completed_calls[-1].kwargs["result_data"]
        assert rd["total_chunks"] == 1  # トピックも生成対象1件として総数に入る
        assert rd["generated"] == 0
        assert rd["skipped"] == 1
        assert rd["topic_targets"] == 1
        assert rd["topic_skipped"] == 1
        assert rd["topic_skipped_no_script"] == 1
        assert "対象トピック 1 件" in rd["message"]
        assert "原稿未生成" in rd["message"]

    @patch("api.routes.lecture_studio.scripts.time.sleep", return_value=None)
    @patch("api.routes.lecture_studio.scripts.generate_tts_audio")
    @patch("api.routes.lecture_studio.scripts._pg_session")
    @patch("api.routes.lecture_studio.scripts.update_background_task")
    def test_topic_with_script_counts_as_generated(
        self, mock_update, mock_pg, mock_tts, mock_sleep,
    ):
        from api.routes.lecture_studio import _batch_audio_worker

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None  # 未キャッシュ
        mock_pg.return_value = mock_session
        mock_tts.return_value = b"FAKE_AUDIO"

        course_data = {
            "topics": [{"id": "t1", "spoken_script": "読み上げ原稿"}],
        }

        _batch_audio_worker("task-1", "course-1", [], "ja", course_data)

        assert mock_tts.call_count == 1
        completed_calls = [c for c in mock_update.call_args_list if c.args[1] == "completed"]
        rd = completed_calls[-1].kwargs["result_data"]
        assert rd["generated"] == 1
        assert rd["topic_generated"] == 1
        assert rd["topic_skipped_no_script"] == 0
        assert "生成 1 件" in rd["message"]

    @patch("api.routes.lecture_studio.scripts.time.sleep", return_value=None)
    @patch("api.routes.lecture_studio.scripts.generate_tts_audio")
    @patch("api.routes.lecture_studio.scripts._pg_session")
    @patch("api.routes.lecture_studio.scripts.update_background_task")
    def test_chunk_and_topic_counts_are_merged(
        self, mock_update, mock_pg, mock_tts, mock_sleep,
    ):
        """チャンク1件生成 + トピック1件原稿未生成 → generated=1 / skipped=1 / 全2件。
        フロントは completed 時にこのカウントから完了メッセージを組み立てるため、
        フロント変更なしで正直な件数が表示される。"""
        from api.routes.lecture_studio import _batch_audio_worker

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None
        mock_pg.return_value = mock_session
        mock_tts.return_value = b"FAKE_AUDIO"

        chunks = [{
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "display_text": "Slide A",
            "text": "Slide A",
            "spoken_text": "Speak A",
            "formulas": [],
            "spoken_language": "ja",
        }]
        course_data = {
            "topics": [{"id": "t1", "student_material": {"source_text": "表示のみ"}}],
        }

        _batch_audio_worker("task-1", "course-1", chunks, "ja", course_data)

        completed_calls = [c for c in mock_update.call_args_list if c.args[1] == "completed"]
        rd = completed_calls[-1].kwargs["result_data"]
        assert rd["total_chunks"] == 2
        assert rd["generated"] == 1
        assert rd["skipped"] == 1
        assert rd["chunk_targets"] == 1
        assert rd["topic_targets"] == 1

    @patch("api.routes.lecture_studio.scripts.generate_tts_audio")
    @patch("api.routes.lecture_studio.scripts._pg_session")
    @patch("api.routes.lecture_studio.scripts.update_background_task")
    def test_no_course_data_keeps_chunk_only_behavior(
        self, mock_update, mock_pg, mock_tts,
    ):
        """course_data が無い呼び出し（後方互換）ではトピック合算が発生しない。"""
        from api.routes.lecture_studio import _batch_audio_worker

        chunks = [{
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "display_text": "text",
            "text": "text",
            "spoken_text": "",
            "formulas": [],
        }]

        _batch_audio_worker("task-1", "course-1", chunks)

        completed_calls = [c for c in mock_update.call_args_list if c.args[1] == "completed"]
        rd = completed_calls[-1].kwargs["result_data"]
        assert rd["total_chunks"] == 1
        assert rd["topic_targets"] == 0
        assert rd["skipped"] == 1


# ---------------------------------------------------------------------------
# 4. batch_generate_audio — 開始レスポンスの総数もトピックを含む（(全N件) 表示の整合）
# ---------------------------------------------------------------------------


class TestGenerateAudioStartResponseIncludesTopics:
    @patch("api.routes.lecture_studio.scripts.threading.Thread")
    @patch("api.routes.lecture_studio.scripts.create_background_task")
    @patch("api.routes.lecture_studio.scripts._get_course_chunks")
    @patch("api.routes.lecture_studio.scripts.get_editable_course_data")
    def test_total_includes_topic_targets(
        self, mock_course, mock_chunks, mock_create, mock_thread,
    ):
        from api.routes.lecture_studio import batch_generate_audio
        from schemas import LectureAudioGenerateRequest

        mock_course.return_value = {
            "lecture_studio_settings": {"lecture_language": "ja"},
            "topics": [{"id": "t1", "spoken_script": "原稿"}],
        }
        mock_chunks.return_value = [
            {"id": "c1", "spoken_language": "ja", "stored_spoken_text": "s1"},
        ]
        mock_thread.return_value = MagicMock()

        resp = batch_generate_audio(
            "course-1", LectureAudioGenerateRequest(language="ja"), {"id": "u1", "role": "TEACHER"},
        )

        assert resp.total_chunks == 2  # チャンク1 + トピック1
