-- Migration 016: chunks.embedding の次元数を 768 → 3072 に変更
--
-- 変更理由:
--   LLM_EMBEDDING_MODEL=text-embedding-3-large は 3072 次元のベクトルを返すが、
--   init.sql で定義された chunks.embedding は vector(768) のままだったため、
--   INSERTが "expected 768 dimensions, not 3072" エラーで失敗していた。
--
-- 変更概要:
--   1. chunks テーブルの embedding カラムを vector(768) → vector(3072) に ALTER
--   2. 既存の HNSW インデックスを halfvec(3072) で再作成
--   3. document_embeddings テーブルの embedding カラムも同様に変更
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。
--
-- 冪等性・再実行安全性（重要）: ALTER COLUMN ... TYPE vector(3072) USING NULL は
-- 既存の embedding を破棄する破壊的操作のため、無条件に毎回実行してはならない
-- （このランナーは全ファイルを毎起動再実行するため、ガード無しだと起動のたびに
-- 全 embedding が NULL にリセットされてしまう）。pg_attribute / format_type で
-- 実際のカラム型を調べ、既に vector(3072) であれば何もしない DO $$ ブロックで包む
-- （main.py の Migration 016 ブロックと同一ロジック）。

-- ----------------------------------------------------------------------------
-- chunks: embedding 次元数を 3072 に変更（既に 3072 なら no-op）
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    col_type TEXT;
BEGIN
    SELECT format_type(atttypid, atttypmod) INTO col_type
    FROM pg_attribute
    WHERE attrelid = 'chunks'::regclass
      AND attname = 'embedding'
      AND attnum > 0;

    IF col_type IS NOT NULL AND col_type != 'vector(3072)' THEN
        -- 型変更には HNSW インデックスの先行 DROP が必要
        DROP INDEX IF EXISTS idx_chunks_embedding;
        -- 既存データが 768 次元の場合は次元不一致エラーになるので NULL にリセットする
        ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(3072) USING NULL;
        -- halfvec を使うことで 3072 次元でも HNSW インデックスが作成可能
        -- (pgvector の vector 型は HNSW 上限 2000 次元だが、halfvec は 16000 次元まで対応)
        CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks
            USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- document_embeddings: embedding 次元数を 3072 に変更（既に 3072 なら no-op）
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    col_type TEXT;
BEGIN
    SELECT format_type(atttypid, atttypmod) INTO col_type
    FROM pg_attribute
    WHERE attrelid = 'document_embeddings'::regclass
      AND attname = 'embedding'
      AND attnum > 0;

    IF col_type IS NOT NULL AND col_type != 'vector(3072)' THEN
        ALTER TABLE document_embeddings ALTER COLUMN embedding TYPE vector(3072) USING NULL;
    END IF;
END $$;
