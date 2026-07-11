-- Migration 040: レクチャースライド同期 + 音声言語切替 Phase 1 — スライド基盤
-- 正本: docs/features/lecture_slide_sync_design.md (§2, §5-3)
--
-- 変更概要:
--   1. lecture_audio_cache をスライド単位のキャッシュキーに拡張
--        slide_index : スライド番号（既定 0 = 後方互換。マーカーなしチャンクはそのまま有効）
--        language    : 生成時の読み上げ言語（既定 'ja'）
--      UNIQUE(chunk_id, voice) → UNIQUE(chunk_id, slide_index, voice) に張り替え。
--   2. chunks に spoken_language を追加（原稿の生成言語。NULL は 'ja' とみなす）
--
-- すべて IF NOT EXISTS / DO ブロックの存在チェックで冪等。

-- ----------------------------------------------------------------------------
-- lecture_audio_cache: スライド単位キャッシュキーへの拡張
-- ----------------------------------------------------------------------------
ALTER TABLE lecture_audio_cache
    ADD COLUMN IF NOT EXISTS slide_index INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS language    TEXT NOT NULL DEFAULT 'ja';

-- 旧 UNIQUE(chunk_id, voice)（自動命名: lecture_audio_cache_chunk_id_voice_key）を撤去し、
-- UNIQUE(chunk_id, slide_index, voice) に張り替える。既存行は slide_index=0 のまま
-- 有効に残るため後方互換。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'lecture_audio_cache_chunk_id_voice_key'
    ) THEN
        ALTER TABLE lecture_audio_cache
            DROP CONSTRAINT lecture_audio_cache_chunk_id_voice_key;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'lecture_audio_cache_chunk_slide_voice_key'
    ) THEN
        ALTER TABLE lecture_audio_cache
            ADD CONSTRAINT lecture_audio_cache_chunk_slide_voice_key
            UNIQUE (chunk_id, slide_index, voice);
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- chunks: 原稿の生成言語
-- ----------------------------------------------------------------------------
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS spoken_language TEXT;
