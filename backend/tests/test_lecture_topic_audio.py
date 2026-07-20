"""トピック教材ベースのレクチャー音声（topic_lecture_audio_cache）の回帰テスト。

方針: レクチャー表示を非レクチャー教材表示（student_material 最優先）に揃え、各スライドの
読み上げ原稿（spoken_script）に対して TTS を生成・キャッシュする。キャッシュキーは
``(course_id, topic_id, slide_index, voice)`` で、チャンク音声（lecture_audio_cache・
chunks への FK あり）とは別テーブル。

FastAPI/DB を起動せず検証できるよう、純関数はソースから抽出して exec し、配線・
マイグレーションはソース文字列で検証する（既存 test_lecture_audio_availability.py と同型）。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LECTURE_PY = ROOT / "backend" / "api" / "routes" / "lecture.py"
CORE_LECTURE_PY = ROOT / "backend" / "core" / "lecture.py"
SCRIPTS_PY = ROOT / "backend" / "api" / "routes" / "lecture_studio" / "scripts.py"
TOPICS_PY = ROOT / "backend" / "api" / "routes" / "lecture_studio" / "topics.py"
MIGRATION = ROOT / "backend" / "db" / "047_topic_lecture_audio.sql"


def _extract_func(source: str, name: str) -> str:
    start = source.index(f"def {name}")
    end = source.index("\ndef ", start + 10)
    return source[start:end]


class TestParseTopicRef:
    def setup_method(self):
        src = LECTURE_PY.read_text(encoding="utf-8")
        ns: dict = {}
        exec(_extract_func(src, "_parse_topic_ref"), ns)  # noqa: S102 - テスト専用の純関数抽出
        self.fn = ns["_parse_topic_ref"]

    def test_parses_topic_prefixed_chunk_id(self):
        assert self.fn("topic:abc-123") == "abc-123"

    def test_returns_none_for_uuid_chunk_id(self):
        assert self.fn("550e8400-e29b-41d4-a716-446655440000") is None

    def test_returns_none_for_empty_or_non_str(self):
        assert self.fn("") is None
        assert self.fn(None) is None


class TestMigration047:
    def test_migration_file_exists(self):
        assert MIGRATION.exists(), "047_topic_lecture_audio.sql が存在しない"

    def test_creates_topic_audio_table_idempotently(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS topic_lecture_audio_cache" in sql
        # キャッシュキー（表示スライドと音声スライドの対応の要）
        assert "UNIQUE(course_id, topic_id, slide_index, voice)" in sql

    def test_topic_audio_table_has_no_chunks_fk(self):
        # トピックは JSON 上のキーでありテーブル行ではないため、chunks への FK を張らない
        # （FK があるとトピック音声を格納できない。これが独立テーブルにした理由）。
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "REFERENCES chunks" not in sql


class TestLearningServesTopicAudio:
    def test_generate_tts_serves_topic_cache(self):
        src = LECTURE_PY.read_text(encoding="utf-8")
        body = _extract_func(src, "generate_tts")
        assert "_parse_topic_ref(chunk_id)" in body
        assert "_get_topic_audio_cache(course_id, topic_ref, body.voice, body.slide_index)" in body

    def test_topic_segment_sets_has_audio_from_topic_cache(self):
        src = LECTURE_PY.read_text(encoding="utf-8")
        body = _extract_func(src, "_build_topic_draft_segment")
        assert "_get_topic_slide_audio_map(course_id, topic_id)" in body
        # 音声キャッシュのあるスライドだけ has_audio=True（無ければタイマー送り）
        assert 'has_audio=sd["slide_index"] in topic_audio_map' in body


class TestStudioGeneratesTopicAudio:
    def test_worker_generates_topic_audio_before_completion(self):
        src = SCRIPTS_PY.read_text(encoding="utf-8")
        body = _extract_func(src, "_batch_audio_worker")
        idx_topic = body.index("_generate_course_topic_audio(course_id, course_data, course_language)")
        idx_completed = body.index('update_background_task(task_id, "completed"')
        # タスク完了マークの前にトピック音声を生成する（完了＝再生可能を保証）
        assert idx_topic < idx_completed

    def test_topic_audio_generator_writes_to_topic_table(self):
        src = SCRIPTS_PY.read_text(encoding="utf-8")
        body = _extract_func(src, "_generate_course_topic_audio")
        assert "INSERT INTO topic_lecture_audio_cache" in body
        assert "generate_tts_audio(slide_spoken, language=course_language)" in body


class TestTopicAudioInvalidatedOnEdit:
    def test_topic_draft_save_deletes_topic_audio(self):
        src = TOPICS_PY.read_text(encoding="utf-8")
        body = _extract_func(src, "save_lecture_studio_course_topic")
        assert "DELETE FROM topic_lecture_audio_cache" in body


class TestSharedSlideBuilderForConsistency:
    """表示・音声・readiness の3者が同じ `build_topic_slides`（core/lecture.py の正本）を
    通ること（slide_index 一致の要）。N18 で正本が routes から core へ移設された。"""

    def test_learner_segment_uses_shared_builder(self):
        # Phase 4 図のコース流通 (§7.2): 学習者向け表示は figures_by_id も供給するため
        # 呼び出し形が `build_topic_slides(topic, figures_by_id=figures_by_id)` に変わった
        # （readiness/音声生成側は figures_by_id 無しの `build_topic_slides(topic)` のまま —
        # _display_length が未解決 embed も 200字換算するため slide_index は一致し続ける）。
        src = LECTURE_PY.read_text(encoding="utf-8")
        body = _extract_func(src, "_build_topic_draft_segment")
        assert "build_topic_slides(" in body
        assert "topic, figures_by_id=figures_by_id" in body

    def test_readiness_uses_shared_builder(self):
        src = CORE_LECTURE_PY.read_text(encoding="utf-8")
        assert "build_topic_slides(topic)" in _extract_func(src, "compute_topic_audio_readiness")

    def test_course_readiness_uses_shared_builder(self):
        # コース全体の合算 readiness（状態投影が使う正本）も同じビルダーを通ること。
        src = CORE_LECTURE_PY.read_text(encoding="utf-8")
        assert "build_topic_slides(topic)" in _extract_func(src, "compute_course_audio_readiness")

    def test_audio_generator_uses_shared_builder(self):
        src = SCRIPTS_PY.read_text(encoding="utf-8")
        body = _extract_func(src, "_generate_course_topic_audio")
        # 音声生成は自前で split せず、共有ビルダーの slide_index をそのまま使う
        assert "build_topic_slides(topic)" in body

    def test_shared_builder_uses_auto_pagination(self):
        src = CORE_LECTURE_PY.read_text(encoding="utf-8")
        assert "auto_paginate_slides(" in _extract_func(src, "build_topic_slides")
