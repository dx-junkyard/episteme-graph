-- Migration 049: 対話的検討 + 候補注釈（W層 Phase 2, 要素検討ワークスペース）
-- 正本: docs/features/element_deliberation_workspace_design.md（§0 W2/W3/W4/W6/W7/W8/W9・§6・§13 Phase 2）
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。
-- すべて IF NOT EXISTS で冪等。
--
-- 背景:
--   設計書は当初「migration 046 に同乗」としていたが、046/047/048 は起草後に他機能へ
--   割り当て済み（atlas_report_incorporation / topic_lecture_audio / element_identity_links）
--   のため、Phase 2（deliberation_sessions / element_annotations）は次番号の 049 を使う
--   （§13「Phase 2 の deliberation_sessions / element_annotations は着手時点の次番号を
--   取ること」の指示どおり）。
--
--   両テーブルとも scope='document' の行はポリモーフィック（document_id への FK なし）。
--   孤児掃除は document 削除経路（core/versioning/deletion.py::_purge_document /
--   api/routes/admin.py::delete_material）が明示 DELETE で担う（element_identity_links と
--   同じ orphan gap パターン）。scope='domain' の行（document_id は NULL）は
--   L層 library_entry のライフサイクルに従い、document 削除では触らない（P4）。

CREATE TABLE IF NOT EXISTS deliberation_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    scope TEXT NOT NULL CHECK (scope IN ('document', 'domain')),
    element_type TEXT NOT NULL
        CHECK (element_type IN ('figure', 'theory_component', 'theory_claim', 'equation', 'shared_part')),
    -- ElementRef（equation=equations.json の equation_id / shared_part=library_entries.id）
    element_id TEXT NOT NULL,

    -- scope='document' のとき正規化済み document_id。scope='domain' は NULL。
    document_id TEXT,
    -- scope='domain' のとき分野キー（cartridge_id と同一名前空間）。scope='document' は NULL。
    domain_key TEXT,

    title TEXT NOT NULL DEFAULT '',
    -- [{role, content, created_at}]（P4: 追記のみ。既存要素を書き換えない）
    messages JSONB NOT NULL DEFAULT '[]',

    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_deliberation_sessions_document
    ON deliberation_sessions(document_id);
CREATE INDEX IF NOT EXISTS idx_deliberation_sessions_domain
    ON deliberation_sessions(domain_key);
CREATE INDEX IF NOT EXISTS idx_deliberation_sessions_element
    ON deliberation_sessions(element_type, element_id);
CREATE INDEX IF NOT EXISTS idx_deliberation_sessions_created_by
    ON deliberation_sessions(created_by);


CREATE TABLE IF NOT EXISTS element_annotations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    scope TEXT NOT NULL CHECK (scope IN ('document', 'domain')),
    element_type TEXT NOT NULL
        CHECK (element_type IN ('figure', 'theory_component', 'theory_claim', 'equation', 'shared_part')),
    element_id TEXT NOT NULL,

    document_id TEXT,
    domain_key TEXT,

    session_id UUID REFERENCES deliberation_sessions(id) ON DELETE SET NULL,

    -- meaning/decomposition = 意味づけ・内訳補正、positioning_note = D層起動、
    -- interpretation = C層解釈バージョン、identity/standardization = W-α で追加（§5.5）。
    kind TEXT NOT NULL
        CHECK (kind IN ('meaning', 'decomposition', 'positioning_note', 'interpretation', 'identity', 'standardization')),

    -- identity: {shared_part_id, local_expression:{label,notation,context}}
    -- standardization: {standardization_status, claimed_canonical_name?, evidence_kinds[]}
    -- その他の kind: {text: str}（自由文の注釈本文）
    body JSONB NOT NULL DEFAULT '{}',
    evidence JSONB NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL DEFAULT '',
    confidence REAL,                                -- 生値は DB のみ（API は段階ラベル・W8）

    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'committed', 'dismissed')),
    -- コミット先の既存構造の参照（component_explanation/theory_component/identity_link の id 等）。
    committed_target JSONB NOT NULL DEFAULT '{}',

    created_by UUID REFERENCES users(id),
    updated_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_element_annotations_document
    ON element_annotations(document_id);
CREATE INDEX IF NOT EXISTS idx_element_annotations_domain
    ON element_annotations(domain_key);
CREATE INDEX IF NOT EXISTS idx_element_annotations_element
    ON element_annotations(element_type, element_id, status);
CREATE INDEX IF NOT EXISTS idx_element_annotations_session
    ON element_annotations(session_id);
