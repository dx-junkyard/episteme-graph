-- Migration 019: Iterative-improvement pipeline — analysis run version management (#402)
--
-- 変更概要:
--   1. document_analysis_runs に版管理カラムを追加
--        run_type            : 'initial' | 'revision'
--        base_run_id         : revision の比較元 Run（採用済み active run）
--        parent_revision_id  : 再修正時の直前候補 Run
--        revision_status     : preparing | auditing | proposed | accepted | rejected | superseded
--        created_by          : Run を作成したユーザー
--   2. documents に active_analysis_run_id を追加（採用済み Run へのポインタ）
--   3. 既存 document へのバックフィル: document ごとの最新 completed Run を暫定 active にする
--      （failed Run は active にしない）
--
-- すべて IF NOT EXISTS / DO ブロックの存在チェックで冪等。

-- ----------------------------------------------------------------------------
-- document_analysis_runs: 版管理カラム
-- ----------------------------------------------------------------------------
ALTER TABLE document_analysis_runs
    ADD COLUMN IF NOT EXISTS run_type           TEXT NOT NULL DEFAULT 'initial',
    ADD COLUMN IF NOT EXISTS base_run_id        UUID,
    ADD COLUMN IF NOT EXISTS parent_revision_id UUID,
    ADD COLUMN IF NOT EXISTS revision_status    TEXT,
    ADD COLUMN IF NOT EXISTS created_by         UUID;

-- run_type の値域
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'document_analysis_runs_run_type_chk'
    ) THEN
        ALTER TABLE document_analysis_runs
            ADD CONSTRAINT document_analysis_runs_run_type_chk
            CHECK (run_type IN ('initial', 'revision'));
    END IF;
END $$;

-- revision_status の値域（initial run では NULL）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'document_analysis_runs_revision_status_chk'
    ) THEN
        ALTER TABLE document_analysis_runs
            ADD CONSTRAINT document_analysis_runs_revision_status_chk
            CHECK (
                revision_status IS NULL
                OR revision_status IN (
                    'preparing', 'auditing', 'proposed',
                    'accepted', 'rejected', 'superseded'
                )
            );
    END IF;
END $$;

-- revision run は必ず base_run_id を持つ
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'document_analysis_runs_base_run_required_chk'
    ) THEN
        ALTER TABLE document_analysis_runs
            ADD CONSTRAINT document_analysis_runs_base_run_required_chk
            CHECK (run_type <> 'revision' OR base_run_id IS NOT NULL);
    END IF;
END $$;

-- self-FK: base_run_id / parent_revision_id
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'document_analysis_runs_base_run_fk'
    ) THEN
        ALTER TABLE document_analysis_runs
            ADD CONSTRAINT document_analysis_runs_base_run_fk
            FOREIGN KEY (base_run_id) REFERENCES document_analysis_runs(id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'document_analysis_runs_parent_revision_fk'
    ) THEN
        ALTER TABLE document_analysis_runs
            ADD CONSTRAINT document_analysis_runs_parent_revision_fk
            FOREIGN KEY (parent_revision_id) REFERENCES document_analysis_runs(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_document_analysis_runs_run_type ON document_analysis_runs(run_type);
CREATE INDEX IF NOT EXISTS idx_document_analysis_runs_base     ON document_analysis_runs(base_run_id);

-- ----------------------------------------------------------------------------
-- documents: active_analysis_run_id（採用済み Run）
-- ----------------------------------------------------------------------------
ALTER TABLE documents ADD COLUMN IF NOT EXISTS active_analysis_run_id UUID;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'documents_active_analysis_run_fk'
    ) THEN
        ALTER TABLE documents
            ADD CONSTRAINT documents_active_analysis_run_fk
            FOREIGN KEY (active_analysis_run_id) REFERENCES document_analysis_runs(id);
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- Backfill: document ごとの最新 completed Run を暫定 active にする。
--   - failed Run は active にしない
--   - 既に active が設定済みの document は変更しない
--   - document_analysis_runs.document_id は TEXT、documents.id は UUID のため text 比較
-- ----------------------------------------------------------------------------
UPDATE documents d
SET active_analysis_run_id = sub.id
FROM (
    SELECT DISTINCT ON (document_id)
        document_id,
        id
    FROM document_analysis_runs
    WHERE status = 'completed'
    ORDER BY document_id,
             completed_at DESC NULLS LAST,
             created_at DESC,
             id DESC
) sub
WHERE d.id::text = sub.document_id
  AND d.active_analysis_run_id IS NULL;
