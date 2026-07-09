-- Migration 025: Structure-Anchored Questions (B層) — 構造帰属のインデックス（列追加ゼロ）
-- interest_traces.payload.structure_anchor の拡張のみ（新テーブル・新列なし。P4:
-- 質問原文 text は残したまま追加する）。帰属候補は attribution_source='llm_candidate'
-- で保存され、学習者本人の confirm/dismiss でのみ確定・棄却する（P1）。
-- 行の status は変更しない（問い自体は確定済み。候補なのは帰属だけ）。

-- ダイジェスト取得（本人の llm_candidate を新しい順に）用
CREATE INDEX IF NOT EXISTS idx_interest_traces_anchor_candidate
    ON interest_traces(user_id, course_id)
    WHERE kind = 'question'
      AND (payload->'structure_anchor'->>'attribution_source') = 'llm_candidate';

-- worker の未帰属フェッチ（structure_anchor 無し・anchor_analyzed_at 無し）用
CREATE INDEX IF NOT EXISTS idx_interest_traces_anchor_pending
    ON interest_traces(user_id, course_id)
    WHERE kind = 'question'
      AND (payload->'structure_anchor') IS NULL
      AND (payload->>'anchor_analyzed_at') IS NULL;
