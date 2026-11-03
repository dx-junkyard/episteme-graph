-- Migration 092: D層（Doubt Layer）— 認識的地位台帳の行キーに論文を含める（IK-0899）
--
-- 課題: IK-0899（第 25 周 面 B 第 2 周）。migration 029 以来、台帳 epistemic_ledger の行キーは
--   UNIQUE(target_id, target_type) で、対象の ID が全体で一意である前提に立っていた。理論操作
--   グラフのノード ID（theory_op_0001 …）と式の agent ID（TeX ラベル由来）は論文の中でしか
--   一意でないため、論文をまたいで 1 行を共有し、ある論文で記帳したスコープ・検証状態が
--   別の論文の同名ノードにも出ていた。
-- 正本の設計: docs/features/stakes_ledger_design.md「台帳の行キーに論文を含める（IK-0899）」。
-- このファイルが DDL の正本。適用は backend/core/migrations.py のランナーが起動時に行う
-- （冪等・毎起動・番号順に全ファイルを再実行）。
--
-- 何をするか:
--   1. 列 attribution_check_needed（BOOLEAN NOT NULL DEFAULT FALSE）を足す。
--   2. 旧キー（target_id, target_type）の一意制約がまだあるとき**だけ**、次を 1 回行う:
--      - 論文の中でしか一意でない ID（component / equation で UUID 形でないもの）の行のうち、
--        人の記帳（検証スコープ・覆る条件・根拠の線・人間専用の検証状態）が入っている行に
--        attribution_check_needed = TRUE を立てる。記帳時にどの論文を開いていたかは行にも
--        監査にも残っていないので、**自動で振り分けない**（行は現在の document_id のまま残す）。
--        画面はこの印を見て「どの論文の記帳か確かめてください」の事実文を出す。
--      - 旧キーの一意制約を外す。
--   3. 新キー UNIQUE NULLS NOT DISTINCT (document_id, target_type, target_id) を張る
--      （前提の行は複数の論文にまたがるため document_id が NULL のことがある。NULL どうしも
--      同じキーとみなす）。既存の行はどれも旧キーで一意だったので、新キーでも一意のまま。
--
-- 設計上の要点:
--   - 行を消さない・行を複製しない。本ファイルに DELETE 文は無い。
--   - 記帳の入っていない衝突行は、ledger_builder の再実行（再解析）で論文ごとに作り直される
--     （新しい ON CONFLICT は新キーで当たる）。
--   - SQL 本文は psql で読める plain SQL で書く。

ALTER TABLE epistemic_ledger
    ADD COLUMN IF NOT EXISTS attribution_check_needed BOOLEAN NOT NULL DEFAULT FALSE;

DO $$
DECLARE
    old_key TEXT;
BEGIN
    -- 旧キー: (target_id, target_type) のちょうど 2 列に掛かる一意制約。
    FOR old_key IN
        SELECT con.conname
          FROM pg_constraint con
          JOIN pg_class rel ON rel.oid = con.conrelid
         WHERE rel.relname = 'epistemic_ledger'
           AND con.contype = 'u'
           AND array_length(con.conkey, 1) = 2
           AND (
               SELECT array_agg(att.attname::text ORDER BY att.attname::text)
                 FROM pg_attribute att
                WHERE att.attrelid = rel.oid
                  AND att.attnum = ANY(con.conkey)
           ) = ARRAY['target_id', 'target_type']
    LOOP
        -- 旧キーが残っている DB でだけ 1 回走る（新キーへ移った後は再実行されない）。
        UPDATE epistemic_ledger
           SET attribution_check_needed = TRUE
         WHERE target_type IN ('component', 'equation')
           AND target_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           AND (
               verification_scopes <> '[]'::jsonb
               OR falsification_conditions <> '[]'::jsonb
               OR evidence_lines <> '[]'::jsonb
               OR verification_status IN ('directly_verified', 'refuted')
           );
        EXECUTE 'ALTER TABLE epistemic_ledger DROP CONSTRAINT ' || quote_ident(old_key);
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'epistemic_ledger_document_target_key'
    ) THEN
        ALTER TABLE epistemic_ledger
            ADD CONSTRAINT epistemic_ledger_document_target_key
            UNIQUE NULLS NOT DISTINCT (document_id, target_type, target_id);
    END IF;
END $$;
