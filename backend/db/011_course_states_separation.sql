-- ============================================================
-- Migration 011: コース原本と学習状態の完全分離 (Issue #133)
-- ============================================================
-- 既存のクローンデータ（学習状態の残骸）はすべてハードリセット（削除）し、
-- 「1つの不変なマスターコース」対「無数のユーザーの学習状態（差分データ）」
-- アーキテクチャへ移行する。

BEGIN;

-- 1. 既存のクローンに紐づくチャット履歴をすべて削除
DELETE FROM learning_chat_history
WHERE course_id IN (SELECT id FROM learning_courses WHERE cloned_from IS NOT NULL);

-- 2. 既存のクローンコース（学習状態の残骸）をすべて削除（マスターのみが残る）
DELETE FROM learning_courses
WHERE cloned_from IS NOT NULL;

-- 3. 新しい学習状態（State）テーブルの作成
CREATE TABLE learning_states (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
    progress_data JSONB DEFAULT '{}'::jsonb,  -- 進捗やテストのスコアなど
    personal_graph JSONB DEFAULT '{}'::jsonb, -- AIが見つけた誤解や個別メモの差分
    enrolled_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (user_id, course_id) -- 二重受講（増殖バグ）をDBレベルで完全ブロック
);

CREATE INDEX idx_learning_states_user ON learning_states(user_id);
CREATE INDEX idx_learning_states_course ON learning_states(course_id);

-- 4. 役割を終えた旧カラムの削除
ALTER TABLE learning_courses DROP COLUMN cloned_from;

COMMIT;
