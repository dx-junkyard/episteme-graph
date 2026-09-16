-- Migration 079: 知識オブジェクト層 M2 — artifact を「1 run × 1 ステージ = 1 行」にする
--
-- 設計正本: docs/features/knowledge_objects_design.md §4.2 / §6（不変条項 KO6）。
-- 親: docs/architecture/knowledge_structure_review_2026-09-12.md P1-8（S-9 の単調増加）。
-- このファイルが DDL の正本。適用は `backend/core/migrations.py` のランナーが起動時に
-- 行う（冪等・毎起動・番号順に再実行）。
--
-- 何をするか:
--   1. document_analysis_artifacts を作る（run_id × stage が主キー）。
--   2. 既存の `document_analysis_runs.stage_outputs->'_artifacts'` blob を**1回だけ**
--      行へ移送し、blob をキーごと取り除く。
--
-- 設計上の要点:
--   - artifact は**不変の生成ログ**であって知識の正本ではない（KO6）。知識の正本は
--     078 で作った知識行の側にある。
--   - **GIN を張らない**。1 payload が数 MB になるため、索引の維持コストが検索の
--     利益を上回る（親文書 P1-8 の「GIN」はこの理由で見送り）。payload を条件に
--     探す用途は知識行の側で満たす。
--   - 移送は自己収束する: 1 回目で `stage_outputs` から `_artifacts` が消えるので、
--     2 回目以降は `WHERE stage_outputs ? '_artifacts'` が 0 行になり何もしない。
--   - 行削除 API は作らない。run 行が消えれば FK CASCADE で一緒に消える。

CREATE TABLE IF NOT EXISTS document_analysis_artifacts (
    run_id     UUID NOT NULL REFERENCES document_analysis_runs(id) ON DELETE CASCADE,
    -- パイプラインの stage 名、または revision の artifact キー。
    stage      TEXT NOT NULL,
    payload    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, stage)
);

-- run 単位の読み出し（`document_run_artifacts()` が {stage: payload} を組み立てる経路）。
CREATE INDEX IF NOT EXISTS idx_document_analysis_artifacts_run
    ON document_analysis_artifacts (run_id);

-- ----------------------------------------------------------------------------
-- 既存 blob の 1 回限りの移送
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    moved   INTEGER := 0;
    cleared INTEGER := 0;
BEGIN
    INSERT INTO document_analysis_artifacts (run_id, stage, payload)
    SELECT r.id, e.key, e.value
      FROM document_analysis_runs r
      CROSS JOIN LATERAL jsonb_each(r.stage_outputs -> '_artifacts') AS e(key, value)
     WHERE r.stage_outputs ? '_artifacts'
       AND jsonb_typeof(r.stage_outputs -> '_artifacts') = 'object'
    ON CONFLICT (run_id, stage) DO NOTHING;
    GET DIAGNOSTICS moved = ROW_COUNT;

    -- 剥がすのは**表へ移せた object 形の blob だけ**。``_artifacts`` が object でない
    -- （配列・文字列・null 等の壊れた値）run は INSERT の対象外なので、ここで剥がすと
    -- 中身がどこにも残らず消える。形が想定外の blob は run 行に置いたまま残す。
    UPDATE document_analysis_runs
       SET stage_outputs = stage_outputs - '_artifacts'
     WHERE stage_outputs ? '_artifacts'
       AND jsonb_typeof(stage_outputs -> '_artifacts') = 'object';
    GET DIAGNOSTICS cleared = ROW_COUNT;

    IF moved > 0 OR cleared > 0 THEN
        RAISE NOTICE 'migration 079: moved %% artifact row(s) out of %% run blob(s)', moved, cleared;
    END IF;
END $$;
