-- Migration 023: 分野の地図 — 修正報告フロー (Issue D)
--
-- 骨格(モデル生成の地形)への修正報告を帰属つきで記録し、既存の C層教員レビュー
-- 導線(review 語彙 + theory_review_events 監査 + 管理画面のレビュー表示)へ流す。
-- 新規のキュー機構は作らない(このテーブルは報告レコードの正本のみ)。
--
-- 適用: 本 SQL は正本リファレンス。実際の適用は backend/api/main.py の _run_migrations() 内。
--
-- 設計上の確定:
-- - 送信は帰属つき(匿名不可): reporter_id NOT NULL + API 側も要認証
-- - どの版への指摘かを固定する: skeleton_version NOT NULL
-- - Stage 4 の challenge(型: 地図修正)昇格を妨げない: kind 列(現状 'map_correction' のみ)
-- - 採用は骨格次版へ反映され applied_version が刻印される(changelog[].credits に帰属)
-- - 旧版への pending 報告は新版へ引き継ぎ、id_migrations に従って node_id を付け替える

CREATE TABLE IF NOT EXISTS atlas_correction_reports (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind             TEXT NOT NULL DEFAULT 'map_correction',
    cartridge_id     TEXT NOT NULL DEFAULT '',
    -- どの版の骨格への指摘か(自動添付・不変)
    skeleton_version TEXT NOT NULL,
    -- 対象: node_id または region_id のどちらかが必須
    node_id          TEXT NOT NULL DEFAULT '',
    region_id        TEXT NOT NULL DEFAULT '',
    level            INTEGER NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 3),
    -- 報告時点の表示ラベル(骨格改版後もレビュー画面で読めるように保持)
    node_label       TEXT NOT NULL DEFAULT '',
    report_text      TEXT NOT NULL,
    -- 帰属(匿名不可)。学術的クレジットの回路
    reporter_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status           TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'accepted', 'declined', 'merged')),
    -- レビュー処理(見送りは理由必須。API 側で強制)
    resolution_note  TEXT NOT NULL DEFAULT '',
    resolved_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    resolved_at      TIMESTAMPTZ,
    -- 重複統合: 統合先の報告
    merged_into      UUID REFERENCES atlas_correction_reports(id) ON DELETE SET NULL,
    -- 採用が反映された骨格の版(凍結時に刻印。'' = 未反映)
    applied_version  TEXT NOT NULL DEFAULT '',
    -- 報告者本人への結果通知の既読管理(NULL = 未読)
    notified_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (node_id <> '' OR region_id <> '')
);
CREATE INDEX IF NOT EXISTS idx_atlas_reports_cartridge_status
    ON atlas_correction_reports(cartridge_id, status);
CREATE INDEX IF NOT EXISTS idx_atlas_reports_reporter
    ON atlas_correction_reports(reporter_id);
-- 通知(未読の処理結果)の取得用
CREATE INDEX IF NOT EXISTS idx_atlas_reports_unnotified
    ON atlas_correction_reports(reporter_id)
    WHERE resolved_at IS NOT NULL AND notified_at IS NULL;
