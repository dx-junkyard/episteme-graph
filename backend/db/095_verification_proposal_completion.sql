-- Migration 095: D層 / SL層 — 検証提案の完了の帰属（IK-1121・第 25 周 第 10 波）
--
-- 提案の［完了を記録］は状態を completed にするだけで、誰が・いつ・何を確かめて完了としたかを
-- 表示の欄に持たなかった（監査 theory_review_events には actor があるのに台帳の画面へ届かない）。
-- 取り下げた疑義には「取り下げ: 名前（日時）」が出る（IK-1060）のと非対称だった。
--
--   completed_by     完了を記録した教員（users。アカウントの墓標化で SET NULL）
--   completed_at     完了を記録した時刻
--   completion_note  完了の根拠（何を確かめたか・任意。空は「記帳なし」と事実で書く）
--
-- 行は削除しない（P4）。完了しても検証状態（epistemic_ledger.verification_status）は変えない
-- （人が別の操作で記帳する — SL2）。既存の完了行は監査の記録から 1 回だけ埋める（冪等）。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。

ALTER TABLE verification_proposals
    ADD COLUMN IF NOT EXISTS completed_by UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE verification_proposals
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE verification_proposals
    ADD COLUMN IF NOT EXISTS completion_note TEXT NOT NULL DEFAULT '';

-- 既存の完了行: 監査（entity_type='verification_proposal'・new_status='completed'）の最新 1 件から
-- actor と時刻を写す。completed_at が埋まっている行は触らない（毎起動の再実行で上書きしない）。
UPDATE verification_proposals vp
SET completed_by = ev.changed_by,
    completed_at = ev.created_at
FROM (
    SELECT DISTINCT ON (entity_id) entity_id, changed_by, created_at
    FROM theory_review_events
    WHERE entity_type = 'verification_proposal' AND new_status = 'completed'
    ORDER BY entity_id, created_at DESC
) ev
WHERE ev.entity_id = vp.id::text
  AND vp.status = 'completed'
  AND vp.completed_at IS NULL;
