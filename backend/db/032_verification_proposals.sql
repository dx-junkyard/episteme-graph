-- Migration 032: D層 — 検証提案 verification_proposals (D3-2)
--
-- 疑義から「この実験・この計算で検証可能」への昇格経路（操作化型の完成形）。
-- 昇格時に元 challenge の status を 'led_to_verification' に遷移させる（双方向参照）。
--
-- 未検証合意リスト（Open Assumptions List）は本テーブルではなく epistemic_ledger の
-- 投影（高負荷 × 低検証の自動編纂・編集不可）。並び順・スコア・「ノーベル賞候補」的
-- 演出は禁止（§8-5）。
--
-- 監査: theory_review_events（entity_type='verification_proposal'）。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。

CREATE TABLE IF NOT EXISTS verification_proposals (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    challenge_id UUID NOT NULL REFERENCES challenges(id) ON DELETE CASCADE,
    proposal     TEXT NOT NULL CHECK (proposal <> ''),
    proposer_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'proposed'
                     CHECK (status IN ('proposed', 'in_progress', 'completed', 'withdrawn')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_verification_proposals_challenge
    ON verification_proposals(challenge_id);
