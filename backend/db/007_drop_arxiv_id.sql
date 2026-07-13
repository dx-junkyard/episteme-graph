-- 007: arxiv_id カラムを chunks テーブルから削除し、material_id に統一 (Issue #70)
--
-- このファイルが正本。適用は backend/core/migrations.py のランナーが
-- 起動時に行う（冪等・毎起動再実行）。
--
-- NOTE: 「arxiv_id の値を material_id にコピーする」UPDATE は既存DBからの移行用だったため
--       本番適用時 (main.py) には含まれていない。クリーンな DB には arxiv_id カラムが
--       存在しないため実行不要であり、かつ arxiv_id カラム削除後の再実行時に
--       「column arxiv_id does not exist」でエラーになるため、このファイルにも含めない。

-- 1. インデックスを削除
DROP INDEX IF EXISTS idx_chunks_arxiv;

-- 2. カラムを削除
ALTER TABLE chunks DROP COLUMN IF EXISTS arxiv_id;
