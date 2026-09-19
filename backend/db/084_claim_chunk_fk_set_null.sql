-- Migration 084: 再解析で claim 行が消えないようにする（theory_claims.chunk_id の FK）
--
-- 設計正本: docs/features/knowledge_objects_design.md §8.3（破壊ステップの一覧）/ KO3
-- （「再解析は DELETE ではなく superseded_at の刻印で表現する」）。
-- このファイルが DDL の正本。適用は `backend/core/migrations.py` のランナーが起動時に
-- 行う（冪等・毎起動・番号順に再実行）。
--
-- ★ 直した穴 ★
--   `persist_source_chunks` は再解析のたびに
--   `DELETE FROM chunks WHERE document_id = …` を発行していた（chunks は RAG 素材なので
--   作り直してよい、という前提）。ところが migration 013 の
--   `theory_claims.chunk_id UUID REFERENCES chunks(id) ON DELETE CASCADE` により、
--   この DELETE は **theory_claims の live 行を物理削除していた**。
--   教員の review_status も、そこにぶら下がる W層注釈・D層台帳の参照も一緒に消える。
--   KO3（行を消さない）と真っ向から矛盾するため、FK を **ON DELETE SET NULL** に張り替える。
--   claim が指していたチャンクが無くなったことは「chunk_id が NULL になった」という
--   状態で正直に残り、claim 行そのものは残る（次の同期で新しいチャンクに結び直る）。
--
--   併せて `chunks(document_id, chunk_index)` に一意索引を張る。書き手側
--   （`persist_source_chunks`）は本 migration 以降、DELETE → 全件 INSERT ではなく
--   chunk_index キーの upsert で chunk UUID を安定させる（`interest_traces` の
--   チャンクアンカーや `lecture_audio_cache` の chunk 参照が再解析で切れない）。
--   ※ 書き手は索引の存在に依存しない（SELECT → UPDATE / INSERT で自前に突合する）ので、
--     既存の重複で索引を作れない DB でも動作は変わらない。
--
-- 設計上の要点:
--   - **行を消さない**。本ファイルに DELETE 文は無い。
--   - FK 名は 013 の inline 定義（自動命名 `theory_claims_chunk_id_fkey`）だが、
--     環境によって名前が違い得るので **pg_constraint から動的に引く**。
--     既に SET NULL（confdeltype = 'n'）なら何もしない（毎起動の DROP ↔ ADD を作らない）。
--   - SQL 本文は psql で読める plain SQL で書く（`%` は 1 個）。psycopg2 向けの二重化は
--     ランナー core/migrations.py::escape_percent_for_driver が 1 箇所で行う（2026-09-19）。
-- ============================================================================
-- 1. theory_claims.chunk_id: ON DELETE CASCADE → ON DELETE SET NULL
-- ============================================================================

DO $$
DECLARE
    fk_name TEXT;
    fk_delete_action "char";
BEGIN
    SELECT c.conname, c.confdeltype
      INTO fk_name, fk_delete_action
      FROM pg_constraint c
      JOIN pg_attribute a
        ON a.attrelid = c.conrelid
       AND a.attnum = c.conkey[1]
     WHERE c.conrelid = 'theory_claims'::regclass
       AND c.contype = 'f'
       AND c.confrelid = 'chunks'::regclass
       AND a.attname = 'chunk_id'
       AND array_length(c.conkey, 1) = 1
     LIMIT 1;

    IF fk_name IS NULL THEN
        -- FK が無い環境（将来 FK を外した場合など）では何もしない。
        RAISE NOTICE 'migration 084: theory_claims.chunk_id has no FK to chunks; nothing to do';
    ELSIF fk_delete_action = 'n' THEN
        -- 既に SET NULL。2回目以降の起動はここに来る。
        NULL;
    ELSE
        EXECUTE format('ALTER TABLE theory_claims DROP CONSTRAINT %I', fk_name);
        ALTER TABLE theory_claims
            ADD CONSTRAINT theory_claims_chunk_fk
            FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE SET NULL;
        RAISE NOTICE 'migration 084: theory_claims.chunk_id FK switched to ON DELETE SET NULL';
    END IF;
END $$;

-- ============================================================================
-- 2. chunks(document_id, chunk_index) の一意索引（chunk UUID を安定させる upsert の下地）
-- ============================================================================
-- 既存の重複があると索引は作れない。その場合は**消さずに**件数だけ NOTICE で報告する
-- （重複チャンクは RAG 検索で二重に当たるだけで、勝手に消してよい行ではない）。
-- 書き手は索引の有無に依存しないため、作れなくても再解析は upsert のまま動く。

DO $$
DECLARE
    duplicates INTEGER;
BEGIN
    IF to_regclass('public.uq_chunks_document_chunk_index') IS NOT NULL THEN
        RETURN;
    END IF;

    SELECT count(*) INTO duplicates
      FROM (
            SELECT document_id, chunk_index
              FROM chunks
             GROUP BY document_id, chunk_index
            HAVING count(*) > 1
           ) AS d;

    IF duplicates > 0 THEN
        RAISE NOTICE 'migration 084: skipped uq_chunks_document_chunk_index (% duplicate (document_id, chunk_index) group(s) present)', duplicates;
    ELSE
        CREATE UNIQUE INDEX IF NOT EXISTS uq_chunks_document_chunk_index
            ON chunks (document_id, chunk_index);
    END IF;
END $$;
