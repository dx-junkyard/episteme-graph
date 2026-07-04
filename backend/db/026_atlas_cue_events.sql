-- Migration 026: 分野の地図 — 見晴らしの導線の内部計測と初回自動表示フラグ (Issue F)
--
-- 導線別の開封率・オーバーレイ滞在・「ここから学ぶ」到達を内部計測する
-- (Stage 2 ゲート判断の材料)。数値はユーザーに一切見せない (§1.2-4: 踏破率・
-- スコア・バッジ禁止はこの計測の表示にも適用する)。
--
-- 初回ログイン時の自動表示 (導線4) の「一度きり」フラグは、このテーブルの
-- (user_id, cue='first_login', event='opened') 行の存在で表す。専用フラグ列や
-- 設定テーブルは作らない (再ログイン・別端末を含め永続)。
--
-- 適用: 本 SQL は正本リファレンス。実際の適用は backend/api/main.py の _run_migrations() 内。
--
-- cue   = minimap / topbar / topic_complete / chapter_end / detour_return / first_login
-- event = shown (導線カード表示) / opened (オーバーレイを開いた) /
--         dwell (閉じた時点の滞在。payload.duration_ms) /
--         learn_reached (そのオーバーレイから「ここから学ぶ」に到達)

CREATE TABLE IF NOT EXISTS atlas_cue_events (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cue        TEXT NOT NULL,
    event      TEXT NOT NULL,
    payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_atlas_cue_events_user
    ON atlas_cue_events(user_id, cue, event);
