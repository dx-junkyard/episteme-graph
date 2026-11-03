-- Migration 089: 検証シミュレーション層 v1.1「主語は学習者」— 所有・共有・予想の列追加
--
-- 設計正本: docs/features/simulation_bridge_design.md §14（SB11〜SB15・§14.2 データモデル）。
-- 語彙の正本: backend/core/simulation_bridge/schema.py（WIRING_ORIGINS に 'learner' を追加）。
-- このファイルが DDL の正本。適用は backend/core/migrations.py のランナーが起動時に行う
-- （冪等・毎起動・番号順に全ファイルを再実行）。
--
-- 何をするか（列追加のみ・既存行の意味は変えない）:
--   1. simulation_programs / instrument_wirings / simulation_runs に
--      owner_user_id UUID（所有者。users FK なし・NULL = v1 の旧行）と course_id TEXT
--      （学習文脈。任意）を足す（SB12）。
--   2. simulation_runs に shared_at TIMESTAMPTZ（本人の明示共有。NULL = 本人のみ・SB13）と
--      prediction JSONB（実行前の予想 [{symbol, value}]・SB14）を足す。
--   3. instrument_wirings.origin の CHECK に 'learner' を足す（teacher / learner / llm_candidate）。
--   4. knowledge_equations_live ビューを作る（KO5。078 は基表だけを作り live ビューが無かった。
--      SB層の式の読み手 core/simulation_bridge/service.py::equation_rows がこのビューを読む —
--      ビューが無いと読みが UndefinedTable で落ち、教員の画面の一覧が縮退していた）。
--
-- 設計上の要点:
--   - owner_user_id は users(id) を参照しない（088 の created_by と同じ判断。
--     account_lifecycle の PURGE / RETAIN 宣言の対象にしない）。
--   - 行を消さない（SB7）。本ファイルにも DELETE 文は無い。
--   - SQL 本文は psql で読める plain SQL で書く（パーセント記号は 1 個）。

-- ============================================================================
-- 1. 所有者と学習文脈（SB12）
-- ============================================================================

ALTER TABLE simulation_programs ADD COLUMN IF NOT EXISTS owner_user_id UUID;
ALTER TABLE simulation_programs ADD COLUMN IF NOT EXISTS course_id TEXT;
ALTER TABLE instrument_wirings ADD COLUMN IF NOT EXISTS owner_user_id UUID;
ALTER TABLE instrument_wirings ADD COLUMN IF NOT EXISTS course_id TEXT;
ALTER TABLE simulation_runs ADD COLUMN IF NOT EXISTS owner_user_id UUID;
ALTER TABLE simulation_runs ADD COLUMN IF NOT EXISTS course_id TEXT;

-- ============================================================================
-- 2. 共有と予想（SB13 / SB14）
-- ============================================================================

ALTER TABLE simulation_runs ADD COLUMN IF NOT EXISTS shared_at TIMESTAMPTZ;
ALTER TABLE simulation_runs ADD COLUMN IF NOT EXISTS prediction JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_simulation_programs_owner
    ON simulation_programs(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_instrument_wirings_owner
    ON instrument_wirings(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_simulation_runs_owner
    ON simulation_runs(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_simulation_runs_shared
    ON simulation_runs(shared_at)
    WHERE shared_at IS NOT NULL;

-- ============================================================================
-- 3. instrument_wirings.origin に 'learner'
-- ============================================================================
-- 088 の CREATE TABLE IF NOT EXISTS は既存表には何もしないので、ここで張り直す。
-- DROP ガードは「定義に learner が無いときだけ」（毎起動に往復しない）。

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'instrument_wirings_origin_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%learner%'
    ) THEN
        ALTER TABLE instrument_wirings
            DROP CONSTRAINT instrument_wirings_origin_check;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'instrument_wirings_origin_check'
    ) THEN
        ALTER TABLE instrument_wirings
            ADD CONSTRAINT instrument_wirings_origin_check
            CHECK (origin IN ('teacher', 'learner', 'llm_candidate'));
    END IF;
END $$;

-- ============================================================================
-- 4. knowledge_equations_live（KO5）
-- ============================================================================
-- 082 の knowledge_symbols_live と同じ形。基表 knowledge_equations を SELECT してよいのは
-- 書き手（persistence / 削除経路）だけで、読み手はこのビューを読む。

CREATE OR REPLACE VIEW knowledge_equations_live AS
    SELECT * FROM knowledge_equations WHERE superseded_at IS NULL;
