-- Migration 022: TensionMiningAgent (B層) — 違和感（tension）候補のインデックス
--
-- 新テーブル・列追加はゼロ。interest_traces を kind='tension' で拡張利用する
-- （設計書 §3: 語彙拡張は services.py の _INTEREST_KINDS / _TRACE_STATUSES）。
-- 設計原則:
--   - P1: LLM 出力は常に status='candidate'。学習者本人の confirm なしに確定しない。
--   - P3: 候補・確定とも既定で本人のみ可視。教員へは k-匿名化した集約のみ。
--   - P4: dismiss は削除でなく status='dismissed' で保持する。
--
-- 実際の適用は backend/api/main.py の _run_migrations()（Migration 022）で行う。
-- このファイルは正本スキーマのリファレンス。

CREATE INDEX IF NOT EXISTS idx_interest_traces_kind_status
    ON interest_traces(kind, status);

-- ダイジェスト取得用（本人×未確定候補）
CREATE INDEX IF NOT EXISTS idx_interest_traces_candidate
    ON interest_traces(user_id, course_id)
    WHERE kind = 'tension' AND status = 'candidate';
