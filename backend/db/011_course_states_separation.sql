-- Migration 011: コース原本と学習状態（State）の完全分離 (Issue #133)
--
-- 従来、受講（Enroll）時に learning_courses を丸ごとクローンしていたが、以下の
-- 技術的負債を生んでいたため、「1つの不変なマスターコース」に対し「ユーザーの
-- 学習状態（差分データ）」を learning_states で紐づける構造に刷新する。
--   1. マスター更新がクローンに反映されない（陳腐化）
--   2. マスターが削除されてもクローンが残る（ゴーストデータ）
--   3. UI 操作等で同一マスターから複数クローンが生成される（増殖バグ）
--
-- ※ 既存のクローンデータ（学習状態）はすべてハードリセット（削除）する。

BEGIN;

-- 1. 既存のクローンに紐づくチャット履歴をすべて削除
DELETE FROM learning_chat_history
WHERE course_id IN (SELECT id FROM learning_courses WHERE cloned_from IS NOT NULL);

-- 2. 既存のクローンコース（学習状態の残骸）をすべて削除（マスターのみが残る）
DELETE FROM learning_courses
WHERE cloned_from IS NOT NULL;

-- 3. 新しい学習状態（State）テーブルの作成
CREATE TABLE IF NOT EXISTS learning_states (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
    progress_data JSONB NOT NULL DEFAULT '{}'::jsonb,   -- 進捗やテストのスコアなど
    personal_graph JSONB NOT NULL DEFAULT '{}'::jsonb,  -- AIが見つけた誤解や個別メモの差分
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, course_id)  -- 二重受講（増殖バグ）をDBレベルで完全ブロック
);

CREATE INDEX IF NOT EXISTS idx_learning_states_user ON learning_states(user_id);
CREATE INDEX IF NOT EXISTS idx_learning_states_course ON learning_states(course_id);

-- 4. 役割を終えた旧カラムの削除
ALTER TABLE learning_courses DROP COLUMN IF EXISTS cloned_from;

COMMIT;
