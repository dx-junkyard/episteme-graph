-- 007: arxiv_id カラムを chunks テーブルから削除し、material_id に統一 (Issue #70)
--
-- 既存の arxiv_id の値を material_id にコピーした後、カラムとインデックスを削除する。

-- 1. arxiv_id に値があり material_id が NULL のレコードをマイグレーション
UPDATE chunks
SET material_id = arxiv_id
WHERE arxiv_id IS NOT NULL
  AND (material_id IS NULL OR material_id = '');

-- 2. インデックスを削除
DROP INDEX IF EXISTS idx_chunks_arxiv;

-- 3. カラムを削除
ALTER TABLE chunks DROP COLUMN IF EXISTS arxiv_id;
