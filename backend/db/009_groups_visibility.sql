-- ============================================================
-- Migration 009: グループ機能 + 資料の開示範囲（Issue #121）
-- ============================================================

-- ============================================================
-- グループ本体
-- ============================================================

CREATE TABLE IF NOT EXISTS groups (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    invite_code  TEXT UNIQUE,
    created_by   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_groups_created_by ON groups(created_by);

-- ============================================================
-- グループ所属
-- ============================================================

CREATE TABLE IF NOT EXISTS group_members (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id   UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       TEXT NOT NULL DEFAULT 'member'
                  CHECK (role IN ('admin', 'member')),
    joined_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(group_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(user_id);
CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id);

-- ============================================================
-- グループ招待（特定ユーザーへの直接招待）
-- ============================================================

CREATE TABLE IF NOT EXISTS group_invitations (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id         UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    invitee_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    inviter_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status           TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'accepted', 'declined', 'revoked')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    responded_at     TIMESTAMPTZ,
    UNIQUE(group_id, invitee_user_id, status)
);

CREATE INDEX IF NOT EXISTS idx_invitations_invitee ON group_invitations(invitee_user_id, status);
CREATE INDEX IF NOT EXISTS idx_invitations_group ON group_invitations(group_id);

-- ============================================================
-- 資料の開示範囲（Visibility）カラム
-- ============================================================

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'private'
        CHECK (visibility IN ('public', 'group', 'private')),
    ADD COLUMN IF NOT EXISTS group_id UUID REFERENCES groups(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_documents_visibility ON documents(visibility);
CREATE INDEX IF NOT EXISTS idx_documents_group ON documents(group_id) WHERE group_id IS NOT NULL;

ALTER TABLE learning_courses
    ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'private'
        CHECK (visibility IN ('public', 'group', 'private')),
    ADD COLUMN IF NOT EXISTS group_id UUID REFERENCES groups(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_courses_visibility ON learning_courses(visibility);
CREATE INDEX IF NOT EXISTS idx_courses_group ON learning_courses(group_id) WHERE group_id IS NOT NULL;

-- 既存の公開テンプレートを 'public' 扱いに移行（後方互換）
UPDATE learning_courses
    SET visibility = 'public'
    WHERE is_published = true AND is_template = true AND visibility = 'private';
