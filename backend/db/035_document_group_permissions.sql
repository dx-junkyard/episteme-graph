-- ============================================================
-- Migration 035: ドキュメント（教材・パイプライン成果）× グループ 権限マッピング
-- ============================================================
--
-- 背景:
--   これまで PDF 解析パイプラインの成果（theory_components / theory_claims /
--   theory_component_graphs / document_analysis_runs）は、そのドキュメントを参照する
--   コース（course_group_permissions）経由でしか他者に共有できなかった。
--   コースを作らずに「解析成果そのもの」を指定グループへ共有したい、という要求に応える。
--
-- 設計:
--   course_group_permissions（migration 010）の完全な移植版。documents と groups の
--   多対多マッピングを permission 付きで表現する。成果テーブルはすべて document_id 由来
--   なので、ドキュメント単位で権限を持たせ、成果はそれを継承する（成果テーブルに列を足さない）。
--   permission = 'viewer' → グループメンバーが解析成果を閲覧・引用できる
--   permission = 'editor' → グループメンバーが再解析・説明追加など編集できる
--
-- 実際の適用は backend/api/main.py の _run_migrations()（Migration 035）で行う。
-- このファイルは正本スキーマのリファレンス。
-- ============================================================

CREATE TABLE IF NOT EXISTS document_group_permissions (
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    group_id    UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    permission  TEXT NOT NULL DEFAULT 'viewer'
                    CHECK (permission IN ('viewer', 'editor')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, group_id)
);

CREATE INDEX IF NOT EXISTS idx_dgp_document ON document_group_permissions(document_id);
CREATE INDEX IF NOT EXISTS idx_dgp_group    ON document_group_permissions(group_id);
CREATE INDEX IF NOT EXISTS idx_dgp_group_permission
    ON document_group_permissions(group_id, permission);
