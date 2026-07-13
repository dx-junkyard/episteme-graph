-- Migration 006: Interactive Lecture Mode (Issue #66)
-- chunks テーブルに spoken_text / formulas カラムを追加
-- lecture_audio_cache テーブルを新規作成
--
-- このファイルが正本。適用は backend/core/migrations.py のランナーが
-- 起動時に行う（冪等・毎起動再実行）。
--
-- NOTE: chunks.display_text の追加は 008_display_text.sql の担当。
--       lecture_audio_cache.slide_index / language 等の拡張は 040_lecture_slides.sql の担当。
--       このファイルには含めない（main.py の実行順ではこの間に割り込むが、ファイル分割は変えない）。

-- 1. chunks に音声読み上げ用テキストと数式メタデータを追加
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS spoken_text TEXT;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS formulas JSONB DEFAULT '[]'::jsonb;

-- 2. TTS 音声キャッシュテーブル
CREATE TABLE IF NOT EXISTS lecture_audio_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    voice TEXT NOT NULL DEFAULT 'alloy',
    audio_data BYTEA NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    word_timestamps JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(chunk_id, voice)
);

CREATE INDEX IF NOT EXISTS idx_lecture_audio_cache_chunk_id ON lecture_audio_cache(chunk_id);
