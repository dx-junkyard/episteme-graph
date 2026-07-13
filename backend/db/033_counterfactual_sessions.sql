-- Migration 033: D層 — 反実仮想セッション counterfactual_sessions (D3-3)
--
-- 前提を仮に「偽」に倒したときの崩壊 / 生存 / 判定不能（indeterminate）の 3 区分
-- スナップショットを保存する。伝播計算は core/doubt/counterfactual.py（非LLM・
-- 決定論的）。**「外した後に何が再構築できるか」は計算しない**（人間の創造の場所）。
--
-- 設計原則:
--   - 判定不能は崩壊側に倒さず indeterminate で保持（情報を落とさない）。
--   - 共有範囲は既存 Visibility 語彙（private | group | public）を流用。
--     group 共有は group_id のメンバーのみ閲覧可（既存 RBAC / Group 可視性と整合）。
--   - notes は本人の言葉。モード退出で通常表示に完全復帰（UI 側, D3-4）。
--   - 監査: theory_review_events（entity_type='counterfactual_session'）。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。

CREATE TABLE IF NOT EXISTS counterfactual_sessions (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id              TEXT NOT NULL DEFAULT '',
    document_id            TEXT NOT NULL DEFAULT '',
    toggled_assumption_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    collapsed_subgraph     JSONB NOT NULL DEFAULT '{}'::jsonb,
    surviving_subgraph     JSONB NOT NULL DEFAULT '{}'::jsonb,
    indeterminate_subgraph JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes                  TEXT NOT NULL DEFAULT '',
    shared_scope           TEXT NOT NULL DEFAULT 'private'
                               CHECK (shared_scope IN ('private', 'group', 'public')),
    group_id               UUID REFERENCES groups(id) ON DELETE SET NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_counterfactual_sessions_owner
    ON counterfactual_sessions(owner_id);
CREATE INDEX IF NOT EXISTS idx_counterfactual_sessions_shared
    ON counterfactual_sessions(shared_scope) WHERE shared_scope <> 'private';
