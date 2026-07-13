-- ============================================================
-- Migration 004: DSLスキーマの自己進化メカニズム
-- Issue #36: 動的スキーマ管理 + メタ分析提案 + 再抽出ジョブ
-- ============================================================
--
-- このファイルが正本。適用は backend/core/migrations.py のランナーが
-- 起動時に行う（冪等・毎起動再実行）。

-- 動的オントロジータイプ（概念カテゴリ）
CREATE TABLE IF NOT EXISTS schema_ontology_types (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_builtin  BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 動的述語（関係性タイプ）
CREATE TABLE IF NOT EXISTS schema_predicates (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_builtin  BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- スキーマ拡張提案
CREATE TABLE IF NOT EXISTS schema_proposals (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected')),
    summary     TEXT NOT NULL DEFAULT '',
    reasoning   TEXT NOT NULL DEFAULT '',
    source_query_count INTEGER NOT NULL DEFAULT 0,
    created_by  UUID REFERENCES users(id),
    reviewed_by UUID REFERENCES users(id),
    reviewed_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_schema_proposals_status ON schema_proposals(status);

-- 提案内の個別アイテム（新しいType or Predicate）
CREATE TABLE IF NOT EXISTS schema_proposal_items (
    id          TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES schema_proposals(id) ON DELETE CASCADE,
    item_type   TEXT NOT NULL CHECK (item_type IN ('ontology_type', 'predicate')),
    key         TEXT NOT NULL,
    label       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_proposal_items_proposal ON schema_proposal_items(proposal_id);

-- 再抽出ジョブ
CREATE TABLE IF NOT EXISTS reextraction_jobs (
    id            TEXT PRIMARY KEY,
    proposal_id   TEXT REFERENCES schema_proposals(id),
    status        TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    total_docs    INTEGER NOT NULL DEFAULT 0,
    processed_docs INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reextraction_status ON reextraction_jobs(status);
