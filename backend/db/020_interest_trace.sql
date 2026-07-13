-- Migration 020: 学習者体験レイヤー(B層) — 関心痕跡 InterestTraceStore (Stage 3 / Phase 1)
--
-- 学習者の問い・寄り道・誤答を「資産」として安価に永続化する（LLM 不使用）。
-- 設計原則:
--   - プライバシー: 個人を特定する追加情報は持たせない。教員可視は集団集計を前提
--     とし、個人追跡を作ってから集計をかぶせる順序は禁止（仕様書 §1 / §6-5）。
--   - 既存 learning_states.personal_graph.chat_anchors は温存。新規痕跡はこの表に書き、
--     chat_anchors への二重書きは禁止（last-write 競合回避。仕様書 §3.1）。
--   - kind は分類前の生記録を既定 'raw'。Stage 4 の TraceAnalyzer が interest_kind を後付け。
--
-- このファイルが正本。適用は backend/core/migrations.py のランナーが起動時に行う
-- （冪等・毎起動再実行）。

CREATE TABLE IF NOT EXISTS interest_traces (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,
    course_id     TEXT NOT NULL,
    topic_id      TEXT,
    kind          TEXT NOT NULL DEFAULT 'raw',   -- raw | question | detour | misconception | (分類後: interest_kind)
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb, -- 質問本文・アンカー・復帰位置・context_label など
    weight        REAL NOT NULL DEFAULT 1.0,      -- DecayPolicy が更新 (Stage 4)
    status        TEXT NOT NULL DEFAULT 'open',   -- open | revisited | resolved
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    analyzed_at   TIMESTAMPTZ                      -- TraceAnalyzer 処理済み時刻 (Stage 4)
);

CREATE INDEX IF NOT EXISTS idx_interest_traces_user_course
    ON interest_traces(user_id, course_id);
CREATE INDEX IF NOT EXISTS idx_interest_traces_status
    ON interest_traces(status);
