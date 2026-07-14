-- Migration 047: トピック教材ベースのレクチャー音声キャッシュ
-- 正本: docs/features/lecture_slide_sync_design.md（トピック教材レクチャーへの拡張）
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。
--
-- 背景:
--   レクチャー受講の表示を、受講画面の非レクチャー教材表示（get_topic_material =
--   topics[].student_material 最優先）に揃える。これに伴い読み上げ音声も
--   「トピックの読み上げ原稿（spoken_script）をスライド単位に分割した音声」を再生する。
--   既存の lecture_audio_cache は chunk_id が chunks(id) への FK を持ち、
--   トピック（JSON 上のキー、テーブル行ではない）を格納できないため、トピック用の
--   音声キャッシュを独立テーブルとして持つ（FK なし・course_id/topic_id はテキストキー）。
--
--   キャッシュキーは (course_id, topic_id, slide_index, voice)。チャンク用の
--   lecture_audio_cache とは別管理で、既存のチャンク音声・readiness(#491) には影響しない。

CREATE TABLE IF NOT EXISTS topic_lecture_audio_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    slide_index INTEGER NOT NULL DEFAULT 0,
    voice TEXT NOT NULL DEFAULT 'alloy',
    language TEXT NOT NULL DEFAULT 'ja',
    audio_data BYTEA NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    word_timestamps JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(course_id, topic_id, slide_index, voice)
);

CREATE INDEX IF NOT EXISTS idx_topic_lecture_audio_course_topic
    ON topic_lecture_audio_cache(course_id, topic_id);
