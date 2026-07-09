-- Migration 034: 横断ユーティリティ層（Admin Copilot）— 操作代行の戻す台帳 assistant_actions
--
-- 管理画面 統合 AI アシスタント（Admin Copilot）が代行した各操作を 1 行ずつ記録し、
-- 確定前（ローカル L1）・確定後（永続 L2）いずれの取り消しも成立させる。
--
-- 設計原則（docs/features/admin_assistant_design.md §2 / §6 / §7）:
--   - P3 情報を落とさない: apply 前に before_snapshot を保持し、取り消しは行削除でなく
--     status 遷移（applied → reverted、reverted_at を立てる）。
--   - P2 破壊的操作は確認: reversible=FALSE（削除・公開・freeze・アカウント作成）は
--     戻す UI で無効化。capability registry 側で必ず confirm=TRUE を伴う。
--   - P5 監査: apply / revert / confirm は既存 theory_review_events
--     （entity_type='assistant_action'）にも記録する（本テーブルは操作台帳、
--     theory_review_events は監査ログ）。専用の監査テーブルは増やさない。
--
-- 実際の適用は backend/api/main.py の _run_migrations()（Migration 034）で行う。
-- このファイルは正本スキーマのリファレンス。

CREATE TABLE IF NOT EXISTS assistant_actions (
    id              TEXT PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id      TEXT,                                  -- Copilot 会話セッション（任意）
    capability_id   TEXT NOT NULL,                         -- capabilities.py の id
    screen          TEXT NOT NULL,                         -- 実行時のタブ
    target_type     TEXT NOT NULL,                         -- 'course'|'material'|'chunk'|...
    target_id       TEXT,
    args            JSONB NOT NULL DEFAULT '{}'::jsonb,     -- 指示・パラメータ
    before_snapshot JSONB,                                 -- 変更前（P3）
    after_snapshot  JSONB,
    reversible      BOOLEAN NOT NULL DEFAULT TRUE,
    revert_spec     JSONB,                                 -- revert に必要な情報
    status          TEXT NOT NULL DEFAULT 'applied'
                        CHECK (status IN ('applied', 'reverted', 'failed', 'confirm_pending')),
    reverted_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_assistant_actions_user   ON assistant_actions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_assistant_actions_target ON assistant_actions(target_type, target_id);
