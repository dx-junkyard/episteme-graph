-- Migration 083: 知識の転用層 Phase 4 — D層・C層の表現語彙（列追加のみ）
--
-- 設計正本: docs/features/knowledge_transfer_design.md §8（P4-5）。不変条項は同 §2
-- （KT2 確定は人間 / KT5 情報を落とさない / KT7 数値・内部 ID を学習者に見せない）。
-- 親: docs/architecture/knowledge_structure_review_2026-09-12.md Phase 4（X-5 / X-6 / X-7）。
-- 前提: migration 021（component_citations）/ 029（epistemic_ledger）/ 031（challenges）/
--       067（SL層の列追加。本ファイルはその作法をそのまま継承する）。
--
-- 何をするか（新テーブルなし・3 表への additive な列追加のみ）:
--   1. challenges.challenge_mode  — 疑義の**向き**（X-6 / Pollock の undercut）。
--      direct  = 主張そのものへ（既存行の意味 = DEFAULT）
--      undercut = 主張と根拠のつながりへ
--      既存行は DEFAULT 'direct' で意味不変（既存の疑義はすべて主張そのものへの疑義）。
--   2. challenges.target_element_ref — グラフ上の位置（任意）。
--      {element_type, element_id, document_id}。空 '{}' = 位置の記録なし。
--   3. epistemic_ledger.evidence_lines — 根拠の線（X-5 / SEPIO）。**人間の記帳専用**で
--      worker / ledger_builder は書かない（SL3 と同型の分離。ガードレールがソース検査）。
--      support_paths の計算結果は記帳しない（PN-2: 導出物を記帳に混ぜない）。
--   4. component_citations.citation_intent — 引用の意図（X-7 / CiTO の最小語彙）。
--      NULL 可 = 記録なし。**既存行を推測で埋めない**（KT5）。
--
-- 設計上の要点:
--   - 語彙の正本はコード側: core/doubt/schema.py::CHALLENGE_MODES /
--     EVIDENCE_LINE_KINDS、core/schema.py::CITATION_INTENTS。DB の CHECK と
--     コードの列挙が一致することは backend/tests/test_doubt_citation_vocab_migration.py
--     が固定する。
--   - **行を消さない**（KT5）。本ファイルに DELETE 文は無い。evidence_lines の訂正は
--     PATCH（配列の当該要素の書き換え）で、削除 API は作らない。
--   - CHECK は名前付き制約 + DO $$ ガードで足す（列が既に存在する再起動でも
--     ADD COLUMN IF NOT EXISTS が丸ごとスキップされて CHECK だけ付き損ねる事故を防ぐ。
--     062 / 064 と同じ作法）。
--   - theory_claims / theory_components には触れないので、Phase 1 の live ビュー
--     （theory_claims_live / theory_components_live）の再作成は不要。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う
-- （冪等・毎起動・番号順に再実行）。

-- ---------------------------------------------------------------------------
-- 1. challenges — 疑義の向き（challenge_mode）と対象要素（target_element_ref）
-- ---------------------------------------------------------------------------

ALTER TABLE challenges
    ADD COLUMN IF NOT EXISTS challenge_mode TEXT NOT NULL DEFAULT 'direct';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'challenges_challenge_mode_check'
    ) THEN
        ALTER TABLE challenges
            ADD CONSTRAINT challenges_challenge_mode_check
            CHECK (challenge_mode IN ('direct', 'undercut'));
    END IF;
END $$;

ALTER TABLE challenges
    ADD COLUMN IF NOT EXISTS target_element_ref JSONB NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- 2. epistemic_ledger — 根拠の線（人間の記帳専用）
-- ---------------------------------------------------------------------------
--
-- evidence_lines の各要素:
--   {"line_id": "...", "line_kind": "observation|derivation|external_reference|consistency",
--    "evidence_ids": [], "claim_ids": [], "equation_ids": [],
--    "recorded_by": "<user_id>", "reason": "", "recorded_at": "..."}
-- verification_scopes（どこで確かめられたか）/ falsification_conditions（何が起これば
-- 覆るか）とは別の軸（どの経路で支えられているか）であり、既存列には混ぜない。

ALTER TABLE epistemic_ledger
    ADD COLUMN IF NOT EXISTS evidence_lines JSONB NOT NULL DEFAULT '[]'::jsonb;

-- ---------------------------------------------------------------------------
-- 3. component_citations — 引用の意図（NULL 可）
-- ---------------------------------------------------------------------------

ALTER TABLE component_citations
    ADD COLUMN IF NOT EXISTS citation_intent TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'component_citations_citation_intent_check'
    ) THEN
        ALTER TABLE component_citations
            ADD CONSTRAINT component_citations_citation_intent_check
            CHECK (citation_intent IS NULL OR citation_intent IN (
                'uses_as_evidence', 'extends', 'qualifies',
                'contrasts_with', 'cites_for_background'
            ));
    END IF;
END $$;
