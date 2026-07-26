-- Migration 060: discuss 観測基盤（Observation Layer for Phase 3 Gate）— UI イベント計測
--
-- discuss モード（「論文と話す」）の Phase 3（v2）着手判断を実測ゲートで行うための
-- 観測基盤の一部。開幕画面・着地画面・分岐チップなど、フロントの UI 操作を内部計測する
-- テーブルを新設する。数値はユーザーに一切見せない（DO3。atlas_cue_events, migration 026
-- と同じ規律）。
--
-- FK は意図的に張らない（計測はユーザー削除と独立に残す。atlas_cue_events と同じ思想）。
-- 削除 API は作らない（DO4・append-only）。
--
-- event 語彙（サーバ側ホワイトリスト。未知イベントは 422）・payload キーホワイトリスト
-- （scope/reason/kind のみ、本文混入の構造的防止 — DO1）の正本は
-- backend/core/discuss/observation.py（METRIC_EVENT_VOCAB）。
--
-- 正本: docs/features/discuss_observation_design.md §2-2。
-- 適用: このファイルが正本。backend/core/migrations.py のランナーが起動時に行う
--   （冪等・毎起動再実行）。

CREATE TABLE IF NOT EXISTS discuss_metric_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL,
    course_id   TEXT NOT NULL DEFAULT '',
    event       TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_discuss_metric_events_event
    ON discuss_metric_events(event, created_at);
CREATE INDEX IF NOT EXISTS idx_discuss_metric_events_user
    ON discuss_metric_events(user_id);
