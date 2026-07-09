-- Migration 024: 分野の地図 — 状態導出キャッシュ atlas_overlay_cache (Issue E)
--
-- 3層モデル(§9.1)の C: コーパス層。骨格(カートリッジ同梱・年1配布)の上に、
-- 論文取り込み・承認/解釈追加・行間確定のイベントで動く状態を差分バッチで重ねる。
-- 実行時処理は「このキャッシュの読み出し + 個人層(interest_traces)の合成」のみで、
-- リアルタイム LLM 生成はしない。状態判定ロジックはサーバ側(core/atlas_state.py)に
-- 一箇所隔離し、フロントへ複製しない。
--
-- 適用: 本 SQL は正本リファレンス。実際の適用は backend/api/main.py の _run_migrations() 内。
--
-- 設計上の確定:
-- - 状態はすべて既存データ(theory_components / theory_claims /
--   theory_component_graphs / component_explanations / component_endorsements /
--   document_analysis_runs)からの近似で導出する。将来 D-1 台帳へ置き換わる前提で、
--   導出規則は core/atlas_state.py に隔離する(このテーブルは導出結果のみを持つ)。
-- - 骨格には状態を焼き込まない(§9.2)。キャッシュは (cartridge_id, skeleton_version)
--   単位で持ち、骨格改版時は旧版の行が自然に参照されなくなる。
-- - 検証行・承認行は台帳由来の事実のみのテンプレート合成。評価語を含めない
--   (core/atlas_state.py の EVALUATIVE_VOCABULARY で静的チェック)。
-- - entry_type:
--     region = 領域の被覆状態 (fog / lit)
--     node   = 骨格概念・配置済みコーパス概念・導出ステップの状態
--     chain  = L3 導出チェーンの並び (evidence JSONB に chain: [node_id...])
--     meta   = コーパス署名(差分検知)・更新時刻

CREATE TABLE IF NOT EXISTS atlas_overlay_cache (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cartridge_id     TEXT NOT NULL,
    skeleton_version TEXT NOT NULL,
    entry_type       TEXT NOT NULL CHECK (entry_type IN ('region', 'node', 'chain', 'meta')),
    entry_id         TEXT NOT NULL,
    -- node の所属領域 (E-2 の配置結果。骨格代表概念は骨格の領域)
    region_id        TEXT NOT NULL DEFAULT '',
    label            TEXT NOT NULL DEFAULT '',
    -- fog / gap / assumed / contested / verified / unknown (region は fog / lit)
    status           TEXT NOT NULL DEFAULT '',
    -- derived = コーパス由来 / seed = 骨格 seed_status の弱い初期表示 (コーパス状態が付いたら上書き)
    status_source    TEXT NOT NULL DEFAULT 'derived',
    verify_line      TEXT NOT NULL DEFAULT '',
    endorse_line     TEXT NOT NULL DEFAULT '',
    -- 詳細パネルのアクション表示規則 (§6.2: 霧・行間では learn/evid 非表示)
    learn_enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    evid_enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    -- 正規化座標 {x, y} (領域は {x, y, w, h})
    layout           JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- 配置の来歴 {method: skeleton|binding|embedding, distance, unplaced}
    placement        JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- 台帳由来の事実 {evidence_refs[], endorsements[], counts...} (詳細パネル用)
    evidence         JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(cartridge_id, skeleton_version, entry_type, entry_id)
);
CREATE INDEX IF NOT EXISTS idx_atlas_overlay_cache_key
    ON atlas_overlay_cache(cartridge_id, skeleton_version);

-- 差分更新の契機管理。イベント側は行を1つ立てるだけ(冪等)で、
-- 更新本体(refresh)が処理後に消す。更新遅延の目標: イベント後数分以内。
CREATE TABLE IF NOT EXISTS atlas_overlay_dirty (
    cartridge_id TEXT PRIMARY KEY,
    reason       TEXT NOT NULL DEFAULT '',
    marked_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
