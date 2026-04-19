-- ============================================================
-- Migration 010: コース × グループ 権限マッピング（Issue #125）
-- ============================================================
--
-- 背景:
--   従来のコース共有は「公開（is_published=true）／グループ単指定（group_id）／private」の
--   3択だったが、実運用では「特定ゼミに閲覧権限、特定TAに編集権限」等、複数グループへの
--   細やかなアクセス制御が必要となった。
--
-- 設計:
--   learning_courses と groups の多対多マッピングを permission 付きで表現する。
--   permission = 'viewer' → グループメンバー（学生）がコースを受講できる
--   permission = 'editor' → グループメンバー（TA/教員）がコースを管理・編集できる
-- ============================================================

CREATE TABLE IF NOT EXISTS course_group_permissions (
    course_id   TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
    group_id    UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    permission  TEXT NOT NULL DEFAULT 'viewer'
                    CHECK (permission IN ('viewer', 'editor')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (course_id, group_id)
);

CREATE INDEX IF NOT EXISTS idx_cgp_course ON course_group_permissions(course_id);
CREATE INDEX IF NOT EXISTS idx_cgp_group  ON course_group_permissions(group_id);
CREATE INDEX IF NOT EXISTS idx_cgp_group_permission
    ON course_group_permissions(group_id, permission);
