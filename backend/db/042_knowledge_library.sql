-- Migration 042: 分野別ナレッジライブラリ（L層）
-- 正本: docs/features/image_pipeline_knowledge_library_design.md (§6-2)
--
-- 変更概要:
--   library_entries / library_entry_versions を新設する。
--   atlas_skeletons（migration 027）と同じ「draft が正本・凍結版が履歴・
--   カートリッジ同梱シードを起動時に冪等取込」パターンを踏襲する。
--   embedding は既存の pgvector 拡張・3072次元（migration 016）を流用する。
--
-- すべて IF NOT EXISTS で冪等。

-- ----------------------------------------------------------------------------
-- library_entries: エントリ本体（draft が正本）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS library_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_key TEXT NOT NULL,            -- cartridge_id と同一名前空間（atlas と同じ）
    entry_type TEXT NOT NULL CHECK (entry_type IN ('apparatus','theory_component')),
    name TEXT NOT NULL,
    aliases JSONB NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    body JSONB NOT NULL DEFAULT '{}',    -- 型別ペイロード（§6-2）
    exemplar_images JSONB NOT NULL DEFAULT '[]',  -- §6-4 の含有承認済み参照のみ
    source_component_ids JSONB NOT NULL DEFAULT '[]',   -- provenance（複数可 = 統合）
    source_document_ids JSONB NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
    revision INT NOT NULL DEFAULT 1,     -- 楽観ロック
    latest_version_no INT NOT NULL DEFAULT 0,
    created_by TEXT, updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_library_entries_domain ON library_entries(domain_key, entry_type, status);

-- ----------------------------------------------------------------------------
-- library_entry_versions: 凍結版（不変・履歴保持。パイプラインはここだけを読む）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS library_entry_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_id UUID NOT NULL REFERENCES library_entries(id) ON DELETE CASCADE,
    version_no INT NOT NULL,
    content JSONB NOT NULL,              -- 凍結時点のエントリ全体スナップショット
    embedding vector(3072),              -- name+aliases+summary+visual_cues から凍結時に計算
    note TEXT,
    published_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(entry_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_library_versions_entry ON library_entry_versions(entry_id, version_no DESC);
