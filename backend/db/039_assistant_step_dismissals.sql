-- ============================================================
-- Migration 039: ガイダンス層（G層）— Next Steps 却下台帳
-- ============================================================
--
-- 背景:
--   Next Steps（状態から導出する To-Do、backend/core/admin_assistant/next_steps.py）は
--   本体を保存しない（毎回サーバ状態から投影する, G1）。保存するのは「却下した」という
--   意図だけ（G5/P4）。assistant_actions（migration 034）と同型で、却下は行削除ではなく
--   revoked カラムの状態遷移として保持し、復元（「非表示にした項目」からの復元）は
--   revoked=TRUE への更新で行う。
--
--   §8 の「操作アシスタント初回ログイン cue」の一度きりフラグも専用テーブルを増やさず、
--   本テーブルの step_key='cue:first_login' 行（revoked=FALSE）で代用する。
--
-- 正本設計書: docs/features/guidance_layer_design.md §5 / §8
-- 実際の適用は backend/api/main.py の _run_migrations() が起動時に行う（同 DDL を実行）。
-- このファイルは正本スキーマのリファレンス。全文 IF NOT EXISTS で冪等。
-- ============================================================

CREATE TABLE IF NOT EXISTS assistant_step_dismissals (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    step_key     TEXT NOT NULL,          -- "{rule_id}:{target_id}"（cue は "cue:first_login"）
    dismissed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked      BOOLEAN NOT NULL DEFAULT FALSE,  -- 復元は行削除でなく revoked（G5/P4）
    UNIQUE (user_id, step_key)
);
CREATE INDEX IF NOT EXISTS idx_assistant_step_dismissals_user
    ON assistant_step_dismissals(user_id, revoked);
