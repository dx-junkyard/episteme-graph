-- Migration 030: D層 — 暗黙前提ノード assumption_nodes (D2-2 / D3-5 / D2-4)
--
-- 暗黙前提マイニングの受け皿。出所は 4 経路:
--   mined_gap       経路A: 導出の隙間の反転（inferred / review_required 補完の反復）
--   mined_corpus    経路B: コーパス横断の監査（依存されているが被主張・被引用がない）
--   naive_aggregate 部品6: 素朴な問いの k-匿名集約から教員が引き上げた場合
--   manual          教員の直接明示化
--
-- 設計原則:
--   - マイニング出力は常に status='candidate'。confirmed / dismissed への遷移は
--     教員 API（D2-4）のみ = 「確定は人間」。
--   - dismissed は行削除ではなく保持（P4）。
--   - cluster_key（検出クラスタの構造キー）の一意性で、confirmed / dismissed 済み
--     前提の再候補化を防ぐ（D3-5 受け入れ条件）。
--   - LLM 正規化（原子化された前提文の生成）に 2 回失敗しても検出クラスタは
--     candidate のまま保持し、created_from.normalization_failed=true を付ける（P4）。
--   - 確定時に epistemic_ledger へ assumption の台帳行を自動生成
--     （既定 untested・スコープ空欄, D2-4）。
--
-- 実際の適用は backend/api/main.py の _run_migrations()（Migration 030）で行う。
-- このファイルは正本スキーマのリファレンス。

CREATE TABLE IF NOT EXISTS assumption_nodes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    statement           TEXT NOT NULL,
    origin              TEXT NOT NULL DEFAULT 'mined_gap'
                            CHECK (origin IN ('mined_gap', 'mined_corpus', 'naive_aggregate', 'manual')),
    status              TEXT NOT NULL DEFAULT 'candidate'
                            CHECK (status IN ('candidate', 'confirmed', 'operationalized', 'dismissed')),
    cluster_key         TEXT NOT NULL DEFAULT '',
    created_from        JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_quote      TEXT NOT NULL DEFAULT '',
    reason              TEXT NOT NULL DEFAULT '',
    confidence          REAL NOT NULL DEFAULT 0.0,
    course_id           TEXT NOT NULL DEFAULT '',
    document_ids        JSONB NOT NULL DEFAULT '[]'::jsonb,
    confirmed_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    confirmed_at        TIMESTAMPTZ,
    operationalized_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    dismissed_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    created_by          UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_assumption_nodes_status ON assumption_nodes(status);
CREATE INDEX IF NOT EXISTS idx_assumption_nodes_course ON assumption_nodes(course_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_assumption_nodes_cluster
    ON assumption_nodes(cluster_key) WHERE cluster_key <> '';
