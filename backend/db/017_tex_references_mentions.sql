-- ============================================================
-- Migration 017: TeX references/citations as graph mentions
-- ============================================================
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。
--
-- backend/db/012_graph_suggestions.sql は現在 chunk_graph_mentions.element_type の
-- CHECK を最初から6値（'reference' / 'citation' を含む）で作るため、フレッシュ DB では
-- 本ファイルは no-op になる。本ファイルは「012.sql の旧バージョン（4値のみ）で
-- 既に作成済みの既存 DB」を治癒するガード付き migration として残す。
-- 制約名は PostgreSQL の自動命名 (chunk_graph_mentions_element_type_check) を用いる
-- （012.sql / main.py とも同名でこの制約を扱っており、旧バージョンの本ファイルも
-- 同名を DROP 対象にしていたため、考慮すべき別名は無い）。
--
-- 冪等性: 制約が既に6値（'reference' を含む）であれば何もしない。制約が存在しない
-- 場合は素直に ADD する。DROP→ADD の往復は「4値のまま」のときだけ発火する。

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chunk_graph_mentions_element_type_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%%reference%%'
    ) THEN
        ALTER TABLE chunk_graph_mentions
            DROP CONSTRAINT chunk_graph_mentions_element_type_check;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chunk_graph_mentions_element_type_check'
    ) THEN
        ALTER TABLE chunk_graph_mentions
            ADD CONSTRAINT chunk_graph_mentions_element_type_check
            CHECK (element_type IN (
                'concept',
                'relationship',
                'formula',
                'keyword',
                'reference',
                'citation'
            ));
    END IF;
END $$;
