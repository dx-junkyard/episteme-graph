-- ============================================================
-- M層（LLM モデル選択）— llm_model_policies (migration 061)
-- ============================================================
--
-- 正本ドキュメント: docs/features/llm_model_selection_design.md §4。
-- 「場面（scene）→ 実モデル名」の教員別・システム既定の上書きを保存する。
-- scene_key 列には scene キー（例: 'pipeline'）と feature キー（例:
-- 'pipeline:claim_qualification'）の両方が入りうる（§2 のステージ別上書き）。
--
-- scope='system' 行は scene_key 単位で一意、scope='user' 行は
-- (user_id, scene_key) 単位で一意（教員ごとの個人設定。他教員には影響しない）。
--
-- 削除 API は行削除（「既定に戻す」= restore）として実装する。行は append-only
-- ではなく、変更・削除は theory_review_events（entity_type='llm_model_policy'）
-- に監査記録する（アプリ側の責務）。

CREATE TABLE IF NOT EXISTS llm_model_policies (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    scope            TEXT NOT NULL CHECK (scope IN ('system', 'user')),
    user_id          UUID REFERENCES users(id) ON DELETE CASCADE,  -- scope='user' のみ非NULL
    scene_key        TEXT NOT NULL,          -- 'pipeline' / 'pipeline:claim_qualification' 等
    model            TEXT NOT NULL,
    reasoning_effort TEXT,                   -- NULL = カタログ既定
    updated_by       UUID,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    note             TEXT NOT NULL DEFAULT '',
    CHECK ((scope = 'user') = (user_id IS NOT NULL))
);

-- system 行は scene_key 単位で一意
CREATE UNIQUE INDEX IF NOT EXISTS llm_model_policies_system_uq
    ON llm_model_policies (scene_key) WHERE scope = 'system';

-- user 行は (user_id, scene_key) 単位で一意
CREATE UNIQUE INDEX IF NOT EXISTS llm_model_policies_user_uq
    ON llm_model_policies (user_id, scene_key) WHERE scope = 'user';

CREATE INDEX IF NOT EXISTS idx_llm_model_policies_user_id
    ON llm_model_policies (user_id) WHERE scope = 'user';
