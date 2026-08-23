-- Migration 067: 賭け金の台帳（Stakes Ledger, SL層）— 理解サイクル Phase 3
-- 正本: docs/features/stakes_ledger_design.md §3.1（SL-1 反証条件レジストリ / SL-2 観測の
-- 反実仮想 / SL-4 晴れ間昇格ゲート の DB 部分）
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う
-- （冪等・毎起動・番号順に再実行）。すべて IF NOT EXISTS で冪等。
--
-- 設計上の要点:
--   1. SL-1（反証条件レジストリ）は scope_candidates（migration 029）と完全同型:
--      falsification_conditions（確定 = 人間の記帳専用）/ falsification_candidates
--      （LLM 候補・status='candidate' で教員確定まで本体に入らない）/
--      falsification_analyzed_at（worker の冪等マーカー）。
--      falsification_analyzed_at の部分インデックスは最初から付ける
--      （既存 scope_candidates_analyzed_at の index 欠落と同じ穴を掘らない。
--      既存側の是正は本 migration のスコープ外 — 設計書 §14-1）。
--   2. SL10（既存意味論の非改変）: verification_scopes / verification_status の意味は
--      不変。反証条件は双対の別列であり、既存列には混ぜない。
--   3. SL-4（晴れ間昇格ゲート, SL8）用に verification_proposals へ course_id（複製用）/
--      reachability（人間専用語彙・既定 unassessed）/ external_check（コーパス外文献確認の
--      記帳・必須は API 層で強制）/ external_checked_by を追加する。
--   4. SL-2（観測の反実仮想）用に counterfactual_sessions へ toggled_observations を追加。
--      既存列 toggled_assumption_ids の意味は不変（両方空のときのみ 422 に緩和するのは
--      API 層の責務）。course_id の index は既存 GET の絞り込み列の既知欠落を同時に是正
--      （最終状態の是正であり意味変更ではない）。

-- (a) SL-1 反証条件レジストリ（epistemic_ledger への相乗り）
ALTER TABLE epistemic_ledger
    ADD COLUMN IF NOT EXISTS falsification_conditions JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE epistemic_ledger
    ADD COLUMN IF NOT EXISTS falsification_candidates JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE epistemic_ledger
    ADD COLUMN IF NOT EXISTS falsification_analyzed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_epistemic_ledger_falsification_pending
    ON epistemic_ledger(course_id) WHERE falsification_analyzed_at IS NULL;

-- (b) SL-4 晴れ間昇格ゲート（verification_proposals の拡張）
ALTER TABLE verification_proposals
    ADD COLUMN IF NOT EXISTS course_id TEXT NOT NULL DEFAULT '';
ALTER TABLE verification_proposals
    ADD COLUMN IF NOT EXISTS reachability TEXT NOT NULL DEFAULT 'unassessed'
        CHECK (reachability IN ('reachable','next_generation','unreachable','unassessed'));
ALTER TABLE verification_proposals
    ADD COLUMN IF NOT EXISTS external_check TEXT NOT NULL DEFAULT '';
ALTER TABLE verification_proposals
    ADD COLUMN IF NOT EXISTS external_checked_by UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_verification_proposals_course ON verification_proposals(course_id);

-- (c) SL-2 観測の反実仮想（counterfactual_sessions の拡張）
ALTER TABLE counterfactual_sessions
    ADD COLUMN IF NOT EXISTS toggled_observations JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_counterfactual_sessions_course ON counterfactual_sessions(course_id);
