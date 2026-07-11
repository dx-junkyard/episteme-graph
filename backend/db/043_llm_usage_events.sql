-- Migration 043: LLM トークン使用量推計（U層, Usage Metering）
-- 正本: docs/features/llm_usage_metering_design.md (§5)
--
-- 変更概要:
--   llm_usage_events を新設する。実測（プロバイダ報告値）を正本、決定論的推計を
--   フォールバックとする使用量台帳。append-only（行削除 API は作らない, U6）。
--   FK を意図的に張らない（テレメトリ行が本体の削除を妨げたり、本体削除でカスケード
--   消失したりしないため。帰属 ID は文字列/UUID として残すのみ）。
--   コスト（金額）列は持たない。単価は変動するため集計時に価格表（U7）で都度換算する。
--
-- すべて IF NOT EXISTS / CREATE OR REPLACE で冪等。

CREATE TABLE IF NOT EXISTS llm_usage_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN
        ('chat','structured','embedding','vision','transcribe','tts')),
    feature TEXT NOT NULL DEFAULT 'unattributed',
    usage_source TEXT NOT NULL CHECK (usage_source IN
        ('reported','estimated_tokenizer','estimated_heuristic')),
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    cached_tokens INTEGER,
    reasoning_tokens INTEGER,
    input_characters INTEGER,
    output_characters INTEGER,
    image_count INTEGER NOT NULL DEFAULT 0,
    audio_bytes BIGINT,
    tts_characters INTEGER,
    duration_ms INTEGER,
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error_type TEXT,
    user_id UUID,
    document_id UUID,
    course_id TEXT,
    run_id UUID,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_events_occurred
    ON llm_usage_events (occurred_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_events_feature
    ON llm_usage_events (feature, occurred_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_events_model
    ON llm_usage_events (model, occurred_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_events_document
    ON llm_usage_events (document_id) WHERE document_id IS NOT NULL;

-- 日次集計ビュー（day × feature × model × usage_source の SUM）。
-- 専用カウンタテーブルは作らない（DX-2 と同じ立場）。
CREATE OR REPLACE VIEW llm_usage_daily AS
SELECT
    date_trunc('day', occurred_at) AS day,
    feature,
    model,
    usage_source,
    COUNT(*) AS calls,
    SUM(prompt_tokens) AS sum_prompt_tokens,
    SUM(completion_tokens) AS sum_completion_tokens,
    SUM(total_tokens) AS sum_total_tokens,
    SUM(cached_tokens) AS sum_cached_tokens,
    SUM(reasoning_tokens) AS sum_reasoning_tokens,
    SUM(image_count) AS sum_image_count,
    SUM(audio_bytes) AS sum_audio_bytes,
    SUM(tts_characters) AS sum_tts_characters
FROM llm_usage_events
GROUP BY date_trunc('day', occurred_at), feature, model, usage_source;
