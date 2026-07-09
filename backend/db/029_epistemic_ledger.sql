-- Migration 029: D層（Doubt Layer）— 認識的地位台帳 epistemic_ledger (D1-1)
--
-- 「合意の強さ」と「検証の強さ」をデータ構造のレベルで混同不可能にする台帳。
-- 設計原則（docs/features/doubt_layer_issues.md §0）:
--   - A層非改変: D層は A層成果物（theory_claims / theory_component_graphs 等）を読む側。
--   - 検証は単一ブール値にせず **スコープの配列**（verification_scopes JSONB）で持つ。
--     スコープ 0 件（空欄）は異常ではなく正常状態（=発見）。
--   - `directly_verified` は人間の記帳専用。バックフィル（ledger_builder）は生成しない。
--     昇格はスコープ 1 件以上のときのみ許可（全称検証の構造的禁止, D1-3）。
--   - LLM スコープ候補は scope_candidates 列に status='candidate' で保持し、
--     教員確定まで verification_scopes 本体に入らない（AIに疑わせない）。
--   - load_score / confidence の生数値は API/UI に出さない（段階ラベルのみ）。
--   - 監査: 全書き込みを theory_review_events（entity_type='ledger'）に記録。
--     ※ theory_review_events.entity_type に CHECK 制約はないため語彙拡張は
--       'ledger' / 'assumption' / 'challenge' / 'verification_proposal' /
--       'counterfactual_session' をコード側の正本語彙（core/doubt/schema.py）で管理する。
--
-- verification_scopes の各要素:
--   {"scope_id": "...", "condition": "", "domain": "", "precision": "", "system": "",
--    "evidence_ids": [], "recorded_by": "<user_id>", "reason": "", "recorded_at": "..."}
--
-- 実際の適用は backend/api/main.py の _run_migrations()（Migration 029）で行う。
-- このファイルは正本スキーマのリファレンス。

CREATE TABLE IF NOT EXISTS epistemic_ledger (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id            TEXT NOT NULL,
    target_type          TEXT NOT NULL
                             CHECK (target_type IN ('claim', 'assumption', 'equation', 'component')),
    document_id          TEXT NOT NULL DEFAULT '',
    course_id            TEXT NOT NULL DEFAULT '',
    verification_status  TEXT NOT NULL DEFAULT 'unknown'
                             CHECK (verification_status IN (
                                 'directly_verified', 'indirectly_supported',
                                 'untested', 'refuted', 'unknown'
                             )),
    verification_scopes  JSONB NOT NULL DEFAULT '[]'::jsonb,
    scope_candidates     JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- LLM スコープ候補 worker の冪等性マーカー（tension の analyzed_at と同型）
    scope_candidates_analyzed_at TIMESTAMPTZ,
    consensus_explicit   JSONB NOT NULL DEFAULT '{}'::jsonb,
    consensus_behavioral INTEGER NOT NULL DEFAULT 0,
    load_score           DOUBLE PRECISION,
    load_computed_at     TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(target_id, target_type)
);

CREATE INDEX IF NOT EXISTS idx_epistemic_ledger_target
    ON epistemic_ledger(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_epistemic_ledger_course
    ON epistemic_ledger(course_id);
CREATE INDEX IF NOT EXISTS idx_epistemic_ledger_document
    ON epistemic_ledger(document_id);
-- 空欄（スコープ未記帳）の抽出用: サマリ・open-assumptions 編纂で使う
CREATE INDEX IF NOT EXISTS idx_epistemic_ledger_unscoped
    ON epistemic_ledger(course_id)
    WHERE verification_scopes = '[]'::jsonb;
