-- ============================================================
-- Migration 036: 再構成ループ（Reconstruction Loop, R層）
-- ============================================================
--
-- 学習者に理論の再構成（予測・言い直し）をさせ、A層が生成した精密な構造
-- （theory_claims / SymbolRegistry）を「答えキー（ground truth）」として
-- 構造照合し、ズレを事実として返す閉ループ。
--
-- 設計原則（docs/features/reconstruction_loop_design.md）:
--   - A層非改変: R層は theory_claims を「読む側」。A層コードは変更しない。
--   - 判定は構造（非LLM・同期）、権威は出典リビール、点数は出さない（P7）。
--   - item は LLM が自動オーサリングし、教員確定なしで配信する（status='auto'=candidate 相当）。
--     教員は事後の監査役（confirmed / retired への遷移）。
--   - 情報を落とさない（P4）: item は削除せず auto → flagged → retired の状態遷移。
--     学習者の成果物・自己確認・異議も行削除しない。
--   - 監査: 全状態変更を theory_review_events に記録。entity_type は
--     'reconstruction_item' / 'reconstruction_response' をコード側の正本語彙で管理する。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。
-- ============================================================

-- 再構成課題（item）: claim から LLM が自動オーサリングする出題。
CREATE TABLE IF NOT EXISTS reconstruction_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id UUID NOT NULL REFERENCES theory_claims(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL DEFAULT '',
    elicit_mode TEXT NOT NULL DEFAULT 'predict',      -- predict | restate | symbol
    prompt TEXT NOT NULL,
    response_space JSONB NOT NULL DEFAULT '[]',       -- 選択肢（predict のみ）
    expected JSONB NOT NULL DEFAULT '{}',             -- 想定解（選択肢 ID・型）
    claim_fields_used JSONB NOT NULL DEFAULT '[]',    -- provenance
    author TEXT NOT NULL DEFAULT 'llm',               -- llm | teacher | system（記号葉プローブ=非LLM 決定論生成）
    author_confidence REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'auto'               -- auto | flagged | retired | confirmed
        CHECK (status IN ('auto', 'flagged', 'retired', 'confirmed')),
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_recon_items_claim ON reconstruction_items(claim_id);
CREATE INDEX IF NOT EXISTS idx_recon_items_status ON reconstruction_items(status);
CREATE INDEX IF NOT EXISTS idx_recon_items_document ON reconstruction_items(document_id);

-- 学習者の産出物（一級の成果物・改訂履歴つき）。
CREATE TABLE IF NOT EXISTS learner_reconstructions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id TEXT NOT NULL DEFAULT '',
    item_id UUID NOT NULL REFERENCES reconstruction_items(id) ON DELETE CASCADE,
    claim_id UUID NOT NULL,                           -- 非正規化（集計用）
    response JSONB NOT NULL DEFAULT '{}',
    machine_verdict TEXT NOT NULL DEFAULT 'na'        -- match | mismatch | na
        CHECK (machine_verdict IN ('match', 'mismatch', 'na')),
    self_check TEXT                                   -- agreed | disagreed | verdict_wrong | NULL
        CHECK (self_check IS NULL OR self_check IN ('agreed', 'disagreed', 'verdict_wrong')),
    descended_to_symbol BOOLEAN NOT NULL DEFAULT FALSE,
    revision_of UUID REFERENCES learner_reconstructions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_learner_recon_user ON learner_reconstructions(user_id);
CREATE INDEX IF NOT EXISTS idx_learner_recon_item ON learner_reconstructions(item_id);
CREATE INDEX IF NOT EXISTS idx_learner_recon_claim ON learner_reconstructions(claim_id);

-- item 健全性は専用カウンタテーブルを持たず集計ビューで出す（DX-2 と同じ方針）。
-- 疑わしさランク自体はアプリ側（core/reconstruction/health.py）で計算し SQL に埋め込まない。
-- n_users は k-匿名の母数（D層 naive_signal と同じく「人数」で数える。応答行数ではない）。
CREATE OR REPLACE VIEW reconstruction_item_health AS
SELECT
    i.id AS item_id,
    i.claim_id,
    i.status,
    i.author_confidence,
    COUNT(r.id)                                                    AS n_responses,
    COUNT(*) FILTER (WHERE r.machine_verdict='mismatch')           AS n_mismatch,
    COUNT(*) FILTER (WHERE r.self_check='verdict_wrong')           AS n_verdict_dissent,
    COUNT(*) FILTER (WHERE r.machine_verdict='mismatch'
                       AND r.self_check='agreed')                  AS n_verdict_self_disagree,
    COUNT(*) FILTER (WHERE r.descended_to_symbol)                  AS n_descend,
    COUNT(DISTINCT r.user_id)                                      AS n_users
FROM reconstruction_items i
LEFT JOIN learner_reconstructions r ON r.item_id = i.id
GROUP BY i.id;
