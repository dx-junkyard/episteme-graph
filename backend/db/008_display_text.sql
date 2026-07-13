-- Migration 008: lecture display_text column (Issue #88)
--
-- このファイルが正本。適用は backend/core/migrations.py のランナーが
-- 起動時に行う（冪等・毎起動再実行）。
--
-- NOTE: main.py 本体では本カラムの ADD COLUMN は Migration 006 のインラインブロック内に
--       混在しているが、実質的には本 Issue #88 (Migration 008) の内容のため、
--       ファイル分割はこのまま維持する。
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS display_text TEXT;
