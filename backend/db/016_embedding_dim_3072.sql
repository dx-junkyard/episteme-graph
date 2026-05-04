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
-- 冪等: 既に 3072 次元であれば ALTER は no-op（PostgreSQL が型キャストエラーを
--       出さないが、同一型へのキャストは警告なしに成功する）

-- ----------------------------------------------------------------------------
-- chunks: embedding 次元数を 3072 に変更
-- ----------------------------------------------------------------------------

-- 既存の HNSW インデックスを先に削除（型変更には DROP が必要）
DROP INDEX IF EXISTS idx_chunks_embedding;

-- カラムを vector(3072) に変更
-- 既存データが 768 次元の場合は次元不一致エラーになるので NULL にリセットする
ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(3072)
    USING NULL;

-- HNSW インデックスを halfvec(3072) で再作成
-- halfvec を使うことで 3072 次元でも HNSW インデックスが作成可能
-- (pgvector の vector 型は HNSW 上限 2000 次元だが、halfvec は 16000 次元まで対応)
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks
    USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);

-- ----------------------------------------------------------------------------
-- document_embeddings: embedding 次元数を 3072 に変更
-- ----------------------------------------------------------------------------

DROP INDEX IF EXISTS idx_document_embeddings_embedding;

ALTER TABLE document_embeddings ALTER COLUMN embedding TYPE vector(3072)
    USING NULL;
