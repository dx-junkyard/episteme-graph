-- Migration 093: 同一性リンク（element_identity_links）の確定を取り消す遷移（IK-0953）
--
-- 課題: IK-0953（第 25 周 面 A 第 4 周）。migration 048 以来、同一性リンクの状態は
--   candidate → confirmed / rejected の一方向だけで、確定（confirmed）から先へ動く遷移が
--   無かった。粒度の違う対を誤って確定すると、学生の「共通部品として」に誤りが届き続け、
--   戻す手続が存在しなかった。
-- 正本の設計: docs/features/concept_registry_design.md「第 4 波の追補（2026-10-25）」。
-- このファイルが DDL の正本。適用は backend/core/migrations.py のランナーが起動時に行う
-- （冪等・毎起動・番号順に全ファイルを再実行）。
--
-- 何をするか:
--   1. status の CHECK に 'revoked'（教員が確定を取り消した）を足す。
--   2. 取り消しの記録の列 revoked_by / revoked_at / revoke_reason を足す（確定者 decided_by は
--      上書きしない — 誰が確定し、誰が取り消したかを両方残す）。
--
-- 設計上の要点:
--   - 行を消さない（P4 / W4）。本ファイルに DELETE 文は無い。取り消しは status 遷移で表す。
--   - 確定リンクの読み手（学生の共通部品・ライブラリの結び付き・旅）は status = 'confirmed'
--     だけを読むので、取り消した行は追加の変更なしに消える。
--   - revoked_by に users への FK は張らない（account_lifecycle の purge 網羅表の対象を増やさない。
--     帰属は監査 theory_review_events にも残る）。
--   - SQL 本文は psql で読める plain SQL（`%` は 1 個）。

-- 1. status の CHECK（048 はカラム直付けの無名 CHECK = 自動命名 element_identity_links_status_check）
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'element_identity_links_status_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%revoked%'
    ) THEN
        ALTER TABLE element_identity_links
            DROP CONSTRAINT element_identity_links_status_check;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'element_identity_links_status_check'
    ) THEN
        ALTER TABLE element_identity_links
            ADD CONSTRAINT element_identity_links_status_check
            CHECK (status IN ('candidate', 'confirmed', 'rejected', 'revoked'));
    END IF;
END $$;

-- 2. 取り消しの記録
ALTER TABLE element_identity_links ADD COLUMN IF NOT EXISTS revoked_by UUID;
ALTER TABLE element_identity_links ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;
ALTER TABLE element_identity_links ADD COLUMN IF NOT EXISTS revoke_reason TEXT NOT NULL DEFAULT '';

-- 3. 否定の判断（見送り・取り消し）を候補に戻す記録（IK-1062・第 25 周 面 A 第 8 波）。
--    rejected / revoked → candidate の遷移は教員の操作だけ（理由必須）。元の判断の列
--    （decided_by / decided_at / revoked_* / revoke_reason）は上書きしない — 誰が見送り・
--    取り消し、誰が何のために候補に戻したかを両方残す（監査 identity_link.restored にも残る）。
--    restored_from は戻す前の状態（'rejected' / 'revoked'）。FK は張らない（093 と同じ理由）。
ALTER TABLE element_identity_links ADD COLUMN IF NOT EXISTS restored_by UUID;
ALTER TABLE element_identity_links ADD COLUMN IF NOT EXISTS restored_at TIMESTAMPTZ;
ALTER TABLE element_identity_links ADD COLUMN IF NOT EXISTS restore_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE element_identity_links ADD COLUMN IF NOT EXISTS restored_from TEXT NOT NULL DEFAULT '';
