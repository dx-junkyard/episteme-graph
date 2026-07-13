-- Migration 011: コース原本と学習状態（State）の完全分離 (Issue #133)
--
-- 従来、受講（Enroll）時に learning_courses を丸ごとクローンしていたが、以下の
-- 技術的負債を生んでいたため、「1つの不変なマスターコース」に対し「ユーザーの
-- 学習状態（差分データ）」を learning_states で紐づける構造に刷新する。
--   1. マスター更新がクローンに反映されない（陳腐化）
--   2. マスターが削除されてもクローンが残る（ゴーストデータ）
--   3. UI 操作等で同一マスターから複数クローンが生成される（増殖バグ）
--
-- このファイルが正本。適用は backend/core/migrations.py のランナーが
-- 起動時に行う（冪等・毎起動再実行）。
--
-- NOTE: 旧版はここで `BEGIN; ... COMMIT;` を張り、無条件の
--       `DELETE FROM learning_courses WHERE cloned_from IS NOT NULL` 等でクローンを
--       ハードリセットしていたが、cloned_from カラム削除後に再実行すると
--       「column cloned_from does not exist」でエラーになり、冪等な毎起動再実行と
--       両立しない。ランナーがファイル単位でトランザクションを張るため BEGIN/COMMIT も
--       このファイルには置かない。そのため、クローン後始末は
--       information_schema でカラム存在を確認するガード付き DO $$ に一本化する
--       （main.py 側では Migration 015 セクション末尾に置かれているが、内容としては
--       本 Issue #133 のものであるため、ファイルはこの 011 に置く）。

-- 1. 新しい学習状態（State）テーブルの作成
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

-- 2. 既存の cloned_from カラムがあれば、クローンコースとその履歴をハードリセット後にカラム廃止
--    （cloned_from が既に存在しない DB では no-op。再実行しても安全。）
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'learning_courses' AND column_name = 'cloned_from'
    ) THEN
        DELETE FROM learning_chat_history
        WHERE course_id IN (
            SELECT id FROM learning_courses WHERE cloned_from IS NOT NULL
        );
        DELETE FROM learning_courses WHERE cloned_from IS NOT NULL;
        ALTER TABLE learning_courses DROP COLUMN cloned_from;
    END IF;
END $$;
