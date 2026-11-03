-- Migration 097: 検証提案の到達可能性に「本文・文献の読み直しで確かめられる」を足す（IK-1148・第 25 周 第 11 波）
--
-- 到達可能性の語彙（SL3 の人間専用語彙・正本は core/doubt/schema.py の REACHABILITY_LEVELS）は
-- 覆る条件（SL-1）の観測の語だけで、定義の疑義から作った「本文の式の前後と付録を読み直して
-- 確かめる」提案に当てはまる語が無く、教員は「現在の観測で検証可能」を選ぶしかなかった。
-- 値 'by_document'（本文・文献の読み直しで確かめられる）を additive に足す。既存の値と既存行の意味は
-- 変えない（行は書き換えない）。
--
-- 067 は verification_proposals.reachability を名前なしの CHECK（自動名
-- verification_proposals_reachability_check）で作る。ADD COLUMN IF NOT EXISTS なので 067 の再実行は
-- 既存列の CHECK を作り直さない。ここで旧 CHECK を外し、名前付きの CHECK に置き換える（冪等）。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。

DO $$
DECLARE
    con RECORD;
BEGIN
    FOR con IN
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = 'verification_proposals'
          AND c.contype = 'c'
          AND pg_get_constraintdef(c.oid) LIKE '%reachability%'
          AND c.conname <> 'verification_proposals_reachability_vocab_check'
    LOOP
        EXECUTE format('ALTER TABLE verification_proposals DROP CONSTRAINT %I', con.conname);
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = 'verification_proposals'
          AND c.conname = 'verification_proposals_reachability_vocab_check'
    ) THEN
        ALTER TABLE verification_proposals
            ADD CONSTRAINT verification_proposals_reachability_vocab_check
            CHECK (reachability IN ('reachable','next_generation','by_document','unreachable','unassessed'));
    END IF;
END $$;
