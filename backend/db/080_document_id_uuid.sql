-- Migration 080: 知識オブジェクト層 M3 — document_id を UUID + FK CASCADE に統一する
--
-- 設計正本: docs/features/knowledge_objects_design.md §4.3 / §8（不変条項 KO9 は §2）。
-- 親: docs/architecture/knowledge_structure_review_2026-09-12.md P1-7（S-8 の孤児滞留）。
-- このファイルが DDL の正本。適用は `backend/core/migrations.py` のランナーが起動時に
-- 行う（冪等・毎起動・番号順に再実行）。
--
-- 何をするか（対象は下の VALUES に並べた14の (テーブル, 列)。各列について順に）:
--   ① material_id 形（`documents.source_path`）で書かれた行を `documents.id::text` に正規化する。
--   ② `documents` に対応行の無い行を DELETE する（下記「唯一の破壊的ステップ」）。
--   ③ `ALTER COLUMN <col> TYPE uuid USING NULLIF(<col>, '')::uuid` + `DROP DEFAULT`。
--      `NOT NULL DEFAULT ''` だった表（theory_claims / theory_components / epistemic_ledger /
--      counterfactual_sessions / reconstruction_items）に空文字の行が残っている場合は
--      NOT NULL を外して NULL にする（**行は消さない** — 意味は「どの document にも
--      紐づいていない」であって孤児ではない）。
--   ④ `<table>_document_fk`: `REFERENCES documents(id) ON DELETE CASCADE` を張る。
--
-- ★ 唯一の破壊的ステップ（設計書 §8.3）★
--   ② の DELETE は本 Phase で**唯一**行を消す処理である。消すのは `documents` に対応行の
--   無い（= 教材が既に物理削除済みの）行だけで、理由は次の3つ:
--     - 全ての読み取り経路が `documents` 行の存在を前提にする権限ゲート
--       （`_ensure_document_viewable` / `resolve_document_access` 等）の内側にあり、
--       アプリからは**到達不能**である（見ることも直すこともできない）。
--     - export にも載らない（export は document / course スコープで引く）。
--     - 開発 DB 実測で `document_analysis_runs` の 17MB / 18MB を占める（S-8）。
--   material_id 形で書かれた行は削除ではなく ① で UUID へ正規化する（消さない）。
--   掃除件数は表ごとに RAISE NOTICE で出す（黙って消さない）。
--
-- 設計上の要点:
--   - 078 の live ビュー（theory_claims_live / theory_components_live）は `document_id` に
--     依存するため、`ALTER COLUMN ... TYPE` は "cannot alter type of a column used by a view"
--     で失敗する。したがって **DROP VIEW → ALTER → CREATE OR REPLACE VIEW（078 と同一定義）**
--     の順で行う。2回目以降の起動では列が既に uuid なのでガードが何もしない。
--   - 既存の UNIQUE 制約（theory_component_graphs(course_id, document_id) /
--     document_figures(document_id, figure_key) / document_embeddings(document_id,
--     embedding_type, source_version) / section_assembly_status(course_id, document_id,
--     section_id) / element_identity_links(..., instance_document_id, ...)）と 078 の
--     部分一意索引（(document_id, stable_key) WHERE superseded_at IS NULL）は、列の型変更に
--     自動で追随する（索引は ALTER COLUMN ... TYPE で再構築される）。ここで張り直さない。
--   - `documents.active_analysis_run_id → document_analysis_runs(id)`（019・NO ACTION）と、
--     本 migration が張る `document_analysis_runs.document_id → documents(id) ON DELETE CASCADE`
--     は相互参照になるが、`DELETE FROM documents` 1文で解消される（NO ACTION の参照チェックは
--     文末に行われ、そのときには参照元の documents 行も一緒に消えている）。
--   - SQL 本文は psql で読める plain SQL で書く（`%` は 1 個。psycopg2 向けの二重化は
--     ランナー core/migrations.py::escape_percent_for_driver が行う。2026-09-19）。動的 SQL は
--     `format('%I', ...)` でも `quote_ident()` + `||` でもよい。
--   - 14 の (テーブル, 列) は 2 つの DO ブロックで**同じ順・同じ並び**に書く（型変更と FK を
--     別ガードにしてあるのは、型が既に uuid の DB でも FK だけを後から張れるようにするため）。

-- ============================================================================
-- 1. 正規化 → 孤児掃除 → 型変更（live ビューを一旦落として行う）
-- ============================================================================

DO $$
DECLARE
    rec        RECORD;
    q_tbl      TEXT;
    q_col      TEXT;
    normalized INTEGER;
    orphaned   INTEGER;
    blanked    INTEGER;
    swap_views BOOLEAN := FALSE;
BEGIN
    -- 078 の live ビューは theory_claims / theory_components の document_id に依存するため、
    -- 型変更の前に落とす（まだ TEXT の列が残っているときだけ）。
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name IN ('theory_claims', 'theory_components')
           AND column_name = 'document_id'
           AND data_type = 'text'
    ) INTO swap_views;

    IF swap_views THEN
        DROP VIEW IF EXISTS theory_claims_live;
        DROP VIEW IF EXISTS theory_components_live;
    END IF;

    FOR rec IN
        SELECT * FROM (VALUES
            ('theory_claims',           'document_id'),
            ('theory_components',       'document_id'),
            ('theory_component_links',  'document_id'),
            ('theory_component_graphs', 'document_id'),
            ('document_analysis_runs',  'document_id'),
            ('document_embeddings',     'document_id'),
            ('document_figures',        'document_id'),
            ('epistemic_ledger',        'document_id'),
            ('counterfactual_sessions', 'document_id'),
            ('reconstruction_items',    'document_id'),
            ('section_assembly_status', 'document_id'),
            -- W層のポリモーフィック2表は nullable のまま（scope='domain' 行は NULL）。
            ('deliberation_sessions',   'document_id'),
            ('element_annotations',     'document_id'),
            ('element_identity_links',  'instance_document_id')
        ) AS t(tbl, col)
    LOOP
        q_tbl := quote_ident(rec.tbl);
        q_col := quote_ident(rec.col);

        -- まだ TEXT の列だけを触る（2回目以降の起動では何もしない）。
        CONTINUE WHEN NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = current_schema()
               AND table_name = rec.tbl
               AND column_name = rec.col
               AND data_type = 'text'
        );

        -- ① material_id 形（documents.source_path）で書かれた行を UUID 形へ正規化する。
        EXECUTE 'UPDATE ' || q_tbl || ' AS t SET ' || q_col || ' = d.id::text'
             || '  FROM documents d WHERE t.' || q_col || ' = d.source_path';
        GET DIAGNOSTICS normalized = ROW_COUNT;

        -- ② documents に対応行の無い行を消す（唯一の破壊的ステップ）。空文字の行はここでは
        --    消さず ③ で NULL にする（意味は「未紐づけ」であって孤児ではない）。
        EXECUTE 'DELETE FROM ' || q_tbl || ' AS t'
             || ' WHERE t.' || q_col || ' IS NOT NULL AND t.' || q_col || ' <> '''''
             || '   AND NOT EXISTS ('
             || '     SELECT 1 FROM documents d WHERE d.id::text = t.' || q_col || ')';
        GET DIAGNOSTICS orphaned = ROW_COUNT;

        -- ③ 型変更。DEFAULT '' は uuid へキャストできないので先に落とす。
        EXECUTE 'SELECT count(*) FROM ' || q_tbl || ' WHERE ' || q_col || ' = ''''' INTO blanked;
        IF blanked > 0 THEN
            EXECUTE 'ALTER TABLE ' || q_tbl || ' ALTER COLUMN ' || q_col || ' DROP NOT NULL';
        END IF;
        EXECUTE 'ALTER TABLE ' || q_tbl || ' ALTER COLUMN ' || q_col || ' DROP DEFAULT';
        EXECUTE 'ALTER TABLE ' || q_tbl || ' ALTER COLUMN ' || q_col || ' TYPE uuid'
             || ' USING NULLIF(' || q_col || ', '''')::uuid';

        IF normalized > 0 OR orphaned > 0 OR blanked > 0 THEN
            RAISE NOTICE
                'migration 080: % — normalized % material_id-form row(s), '
                'deleted % orphan row(s), nulled % blank row(s)',
                rec.tbl, normalized, orphaned, blanked;
        END IF;
    END LOOP;

    -- 078 と同一定義でビューを作り直す（SELECT * は作成時の列で固定されるため、ここで
    -- 作り直すことで uuid になった document_id がそのまま live ビューに現れる）。
    IF swap_views THEN
        CREATE OR REPLACE VIEW theory_claims_live AS
            SELECT * FROM theory_claims WHERE superseded_at IS NULL;

        CREATE OR REPLACE VIEW theory_components_live AS
            SELECT * FROM theory_components WHERE superseded_at IS NULL;
    END IF;
END $$;

-- ============================================================================
-- 2. FK（documents への ON DELETE CASCADE）
-- ============================================================================
-- これで教材の物理削除は `DELETE FROM documents` に集約され、孤児が新たに生まれない
-- （削除経路の正本は core/versioning/deletion.py::_purge_document。設計書 §8.1）。

DO $$
DECLARE
    rec RECORD;
    fk  TEXT;
BEGIN
    FOR rec IN
        SELECT * FROM (VALUES
            ('theory_claims',           'document_id'),
            ('theory_components',       'document_id'),
            ('theory_component_links',  'document_id'),
            ('theory_component_graphs', 'document_id'),
            ('document_analysis_runs',  'document_id'),
            ('document_embeddings',     'document_id'),
            ('document_figures',        'document_id'),
            ('epistemic_ledger',        'document_id'),
            ('counterfactual_sessions', 'document_id'),
            ('reconstruction_items',    'document_id'),
            ('section_assembly_status', 'document_id'),
            ('deliberation_sessions',   'document_id'),
            ('element_annotations',     'document_id'),
            ('element_identity_links',  'instance_document_id')
        ) AS t(tbl, col)
    LOOP
        fk := rec.tbl || '_document_fk';

        CONTINUE WHEN NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = current_schema()
               AND table_name = rec.tbl
               AND column_name = rec.col
               AND data_type = 'uuid'
        );
        CONTINUE WHEN EXISTS (SELECT 1 FROM pg_constraint WHERE conname = fk);

        EXECUTE 'ALTER TABLE ' || quote_ident(rec.tbl)
             || ' ADD CONSTRAINT ' || quote_ident(fk)
             || ' FOREIGN KEY (' || quote_ident(rec.col) || ')'
             || ' REFERENCES documents(id) ON DELETE CASCADE';
        RAISE NOTICE 'migration 080: added % on %', fk, rec.tbl;
    END LOOP;
END $$;
