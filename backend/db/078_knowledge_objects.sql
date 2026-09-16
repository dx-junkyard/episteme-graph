-- Migration 078: 知識オブジェクト層 M1 — 既存2表の拡張・新7表・語彙表・live ビュー
--
-- 設計正本: docs/features/knowledge_objects_design.md §4.1（不変条項 KO1〜KO10 は §2）。
-- 親: docs/architecture/knowledge_structure_review_2026-09-12.md Phase 1（P1-1〜P1-9）。
-- このファイルが DDL の正本。適用は `backend/core/migrations.py` のランナーが起動時に
-- 行う（冪等・毎起動・番号順に再実行）。
--
-- 何をするか:
--   1. theory_claims / theory_components に「内容由来の版非依存キー（stable_key）」と
--      supersede 遷移のための nullable 列を足す（既存行は全列 NULL = 意味不変。O-2(a)）。
--   2. equation / evidence / derivation step / symbol を一級の行にする新4表を作る
--      （KO4。document_id は最初から UUID + FK CASCADE = KO9）。
--   3. 参照の再係留の記録簿 element_id_remap を作る（KO8）。
--   4. 型語彙を DB CHECK から**語彙表への FK** に置き換える（KO7）。語彙表の中身は
--      core/schema.py の CLAIM_TYPES / COMPONENT_TYPES と同じ列挙を ON CONFLICT DO NOTHING
--      でシードする（一致は backend/tests/test_knowledge_objects_vocab.py が固定）。
--   5. 読み手が読む live ビュー（theory_claims_live / theory_components_live）を作る（KO5）。
--
-- 設計上の要点:
--   - **行を消さない**。再解析は DELETE ではなく superseded_at の刻印で表現する（KO3）。
--     本ファイルにも DELETE 文は無い。
--   - **confidence を列に昇格させない**（原則4）。agent 側の残りのフィールドは
--     agent_payload JSONB にまとめて持つ。
--   - 013 / 041 の claim_type / component_type の CHECK を作り直す DO ブロックには、
--     「本 migration の FK が在るときは CHECK を作らない」条件を足してある（毎起動の
--     DROP ↔ ADD の往復を止めるため。過去に適用済みの意味は変えていない）。
--   - RAISE NOTICE の書式指定子は ``%%`` と書く（ランナーは exec_driver_sql に空の
--     パラメータを渡すため、psycopg2 が ``%%`` を補間対象とみなして落ちる。013 / 041 の
--     ``LIKE '%%...%%'`` と同じ理由）。

-- ============================================================================
-- 1. 型語彙表（KO7）— FK の参照先なので最初に作る
-- ============================================================================

CREATE TABLE IF NOT EXISTS knowledge_claim_types (
    value TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS knowledge_component_types (
    value TEXT PRIMARY KEY
);

-- 語彙のシード。core/schema.py::CLAIM_TYPES と**完全に同じ列挙**であること
-- （test_knowledge_objects_vocab.py が両者の集合一致を固定する）。
-- ON CONFLICT DO NOTHING なので毎起動の再実行で何も起きない。行削除もしない
-- （語彙を減らすときは新しい migration で明示的に扱う）。
INSERT INTO knowledge_claim_types (value) VALUES
    ('definition'), ('assumption'), ('approximation'), ('equation'), ('relation'),
    ('derivation_step'), ('observable_definition'), ('correction'), ('uncertainty'),
    ('limitation'), ('result'), ('diagnostic_claim'), ('equation_definition'),
    ('equation_relation'), ('equation_transformation'), ('equation_approximation'),
    ('equation_constraint'), ('criterion'), ('setup'), ('operator_relation'),
    ('measurement_or_update'), ('causal_or_dependency_claim'),
    ('incompatibility_or_constraint'), ('comparison'), ('conclusion'), ('method_choice'),
    ('background'), ('prior_work'), ('meta'), ('problem_statement'), ('method_motivation'),
    ('theory_encoding'), ('method'), ('structural_property'), ('derivation_result'),
    ('main_result'), ('interpretation'),
    -- 式由来の合成 claim（equation_claim_synthesis の 4 型。2026-09-13 追加）。
    ('definition_claim'), ('dependency_claim'), ('equation_system_claim'), ('result_claim'),
    ('unknown')
ON CONFLICT (value) DO NOTHING;

-- core/schema.py::COMPONENT_TYPES と同じ列挙。
INSERT INTO knowledge_component_types (value) VALUES
    ('theory'), ('concept'), ('law'), ('mechanism'), ('operator'), ('observation'),
    ('apparatus'), ('instrument'), ('part'),
    ('DomainConceptComponent'), ('DomainTheoryComponent'), ('DomainMethodComponent'),
    ('DomainAssumptionComponent'), ('DomainObservableComponent'), ('PaperClaimComponent'),
    ('PaperHypothesisComponent'), ('PaperRelationComponent'), ('PaperCorrectionComponent'),
    ('PaperUncertaintyComponent'), ('PaperEvidenceComponent'), ('unknown')
ON CONFLICT (value) DO NOTHING;

-- ============================================================================
-- 2. theory_claims の拡張（nullable 追加 — 既存行の意味は変えない）
-- ============================================================================

ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS stable_key TEXT;
ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS agent_claim_id TEXT;
ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS parent_claim_id UUID;
ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS origin TEXT;
ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS claim_tier TEXT;
ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS claim_type_text TEXT NOT NULL DEFAULT '';
ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS produced_by_run_id UUID;
ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;
ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS superseded_by_run_id UUID;

-- atomic 子 → 親 span claim の自己参照。親行が消えても子を消さない（SET NULL）。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'theory_claims_parent_claim_fk'
    ) THEN
        ALTER TABLE theory_claims
            ADD CONSTRAINT theory_claims_parent_claim_fk
            FOREIGN KEY (parent_claim_id) REFERENCES theory_claims(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_theory_claims_parent ON theory_claims(parent_claim_id);
CREATE INDEX IF NOT EXISTS idx_theory_claims_agent_id ON theory_claims(document_id, agent_claim_id);
CREATE INDEX IF NOT EXISTS idx_theory_claims_run ON theory_claims(produced_by_run_id);

-- live 行の同一性を DB が守る（KO3）。superseded 行は同じキーで何本でも残ってよい。
CREATE UNIQUE INDEX IF NOT EXISTS uq_theory_claims_stable_key_live
    ON theory_claims (document_id, stable_key)
    WHERE superseded_at IS NULL AND stable_key IS NOT NULL;

-- ============================================================================
-- 3. theory_components の拡張（P1-9 — agent 側フィールドの落とし穴を塞ぐ）
-- ============================================================================

ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS stable_key TEXT;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS agent_component_id TEXT;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS operation TEXT NOT NULL DEFAULT '';
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS teaching_takeaway TEXT NOT NULL DEFAULT '';
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS teaching_granularity JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS prerequisite_concepts JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS assumptions JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS approximations JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS linked_claim_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS linked_equation_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS linked_evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS linked_derivation_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
-- 上記以外の agent フィールドの置き場（列に昇格させない値はここに全部残す。KO4 / 原則4）。
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS agent_payload JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS produced_by_run_id UUID;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS superseded_by_run_id UUID;

CREATE INDEX IF NOT EXISTS idx_theory_components_document ON theory_components(document_id);
CREATE INDEX IF NOT EXISTS idx_theory_components_agent_id ON theory_components(document_id, agent_component_id);
CREATE INDEX IF NOT EXISTS idx_theory_components_run ON theory_components(produced_by_run_id);
CREATE INDEX IF NOT EXISTS idx_theory_components_agent_payload
    ON theory_components USING gin (agent_payload);

CREATE UNIQUE INDEX IF NOT EXISTS uq_theory_components_stable_key_live
    ON theory_components (document_id, stable_key)
    WHERE superseded_at IS NULL AND stable_key IS NOT NULL;

-- ============================================================================
-- 4. theory_component_links / theory_component_graphs — 出所 run の記録
-- ============================================================================
-- links は派生構造で人間の書き込み経路が無いため、document 単位の DELETE → 再作成を
-- 本 Phase の明示例外として維持する（設計書 §4.1）。produced_by_run_id は「この辺は
-- どの run が出したか」に答えるためだけに持つ。

ALTER TABLE theory_component_links ADD COLUMN IF NOT EXISTS produced_by_run_id UUID;
ALTER TABLE theory_component_graphs ADD COLUMN IF NOT EXISTS produced_by_run_id UUID;

-- ============================================================================
-- 5. 新4表（知識オブジェクトを一級の行にする。KO4）
-- ============================================================================
-- document_id は最初から UUID + FK CASCADE（KO9）。既存2表の TEXT → UUID 移行は
-- 別 migration（M3）で行う。

CREATE TABLE IF NOT EXISTS knowledge_equations (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id          UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    stable_key           TEXT,
    agent_equation_id    TEXT,
    label                TEXT NOT NULL DEFAULT '',
    latex                TEXT NOT NULL DEFAULT '',
    plain_text           TEXT NOT NULL DEFAULT '',
    raw_text             TEXT NOT NULL DEFAULT '',
    block_id             TEXT NOT NULL DEFAULT '',
    section_id           TEXT NOT NULL DEFAULT '',
    page                 INTEGER,
    equation_type        TEXT NOT NULL DEFAULT '',
    semantic_status      TEXT NOT NULL DEFAULT '',
    content_hash         TEXT,
    defined_symbols      JSONB NOT NULL DEFAULT '[]'::jsonb,
    used_symbols         JSONB NOT NULL DEFAULT '[]'::jsonb,
    input_equation_ids   JSONB NOT NULL DEFAULT '[]'::jsonb,
    output_equation_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
    linked_claim_ids     JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_evidence_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
    review_status        TEXT NOT NULL DEFAULT 'teacher_review_required',
    needs_math_review    BOOLEAN NOT NULL DEFAULT FALSE,
    agent_payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
    produced_by_run_id   UUID,
    superseded_at        TIMESTAMPTZ,
    superseded_by_run_id UUID,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_equations_document ON knowledge_equations(document_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_equations_agent_id
    ON knowledge_equations(document_id, agent_equation_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_equations_stable_key_live
    ON knowledge_equations (document_id, stable_key)
    WHERE superseded_at IS NULL AND stable_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS knowledge_evidence (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id          UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    stable_key           TEXT,
    agent_evidence_id    TEXT,
    block_id             TEXT NOT NULL DEFAULT '',
    section_id           TEXT NOT NULL DEFAULT '',
    page                 INTEGER,
    span_start           INTEGER,
    span_end             INTEGER,
    evidence_text        TEXT NOT NULL DEFAULT '',
    evidence_role        TEXT NOT NULL DEFAULT '',
    -- 親 evidence は agent 側 ID（同一 run 内の入れ子）。UUID FK にはしない。
    parent_evidence_id   TEXT,
    public_export_policy TEXT NOT NULL DEFAULT '',
    agent_payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
    produced_by_run_id   UUID,
    superseded_at        TIMESTAMPTZ,
    superseded_by_run_id UUID,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_evidence_document ON knowledge_evidence(document_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_evidence_agent_id
    ON knowledge_evidence(document_id, agent_evidence_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_evidence_stable_key_live
    ON knowledge_evidence (document_id, stable_key)
    WHERE superseded_at IS NULL AND stable_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS knowledge_derivation_steps (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id          UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    stable_key           TEXT,
    agent_derivation_id  TEXT,
    agent_step_id        TEXT,
    step_index           INTEGER,
    operation            TEXT NOT NULL DEFAULT '',
    operation_subtype    TEXT NOT NULL DEFAULT '',
    chain_type           TEXT NOT NULL DEFAULT '',
    input_equation_ids   JSONB NOT NULL DEFAULT '[]'::jsonb,
    output_equation_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
    input_claim_ids      JSONB NOT NULL DEFAULT '[]'::jsonb,
    output_claim_ids     JSONB NOT NULL DEFAULT '[]'::jsonb,
    required_claim_ids   JSONB NOT NULL DEFAULT '[]'::jsonb,
    assumption_ids       JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_evidence_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
    review_status        TEXT NOT NULL DEFAULT 'teacher_review_required',
    teaching_takeaway    TEXT NOT NULL DEFAULT '',
    agent_payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
    produced_by_run_id   UUID,
    superseded_at        TIMESTAMPTZ,
    superseded_by_run_id UUID,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_derivation_steps_document
    ON knowledge_derivation_steps(document_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_derivation_steps_agent_id
    ON knowledge_derivation_steps(document_id, agent_step_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_derivation_steps_stable_key_live
    ON knowledge_derivation_steps (document_id, stable_key)
    WHERE superseded_at IS NULL AND stable_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS knowledge_symbols (
    id                        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id               UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    stable_key                TEXT,
    agent_symbol_id           TEXT,
    canonical_symbol          TEXT NOT NULL DEFAULT '',
    notation_variants         JSONB NOT NULL DEFAULT '[]'::jsonb,
    kind                      TEXT NOT NULL DEFAULT '',
    unit                      TEXT NOT NULL DEFAULT '',
    scope                     TEXT NOT NULL DEFAULT '',
    definition_status         TEXT NOT NULL DEFAULT '',
    defining_equation_ids     JSONB NOT NULL DEFAULT '[]'::jsonb,
    used_in_equation_ids      JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_evidence_ids       JSONB NOT NULL DEFAULT '[]'::jsonb,
    definition_evidence_texts JSONB NOT NULL DEFAULT '[]'::jsonb,
    agent_payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
    produced_by_run_id        UUID,
    superseded_at             TIMESTAMPTZ,
    superseded_by_run_id      UUID,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_symbols_document ON knowledge_symbols(document_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_symbols_agent_id
    ON knowledge_symbols(document_id, agent_symbol_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_symbols_stable_key_live
    ON knowledge_symbols (document_id, stable_key)
    WHERE superseded_at IS NULL AND stable_key IS NOT NULL;

-- ============================================================================
-- 6. element_id_remap — 参照の再係留の記録簿（KO8）
-- ============================================================================
-- 「この run で old_id が new_id に変わった」という事実だけを残す。stable_key が
-- 一致した組にだけ行を作り、存在しない対応は捏造しない（設計書 §5.5）。

CREATE TABLE IF NOT EXISTS element_id_remap (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    run_id      UUID,
    object_kind TEXT NOT NULL
        CONSTRAINT element_id_remap_object_kind_check
            CHECK (object_kind IN (
                'claim', 'component', 'equation', 'evidence', 'derivation_step', 'symbol'
            )),
    old_id      TEXT NOT NULL,
    new_id      TEXT NOT NULL,
    stable_key  TEXT NOT NULL DEFAULT '',
    -- 表ごとの書換件数とスキップ理由（一意制約に当たった参照は書き換えず記録だけ残す）。
    reanchored  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_element_id_remap_lookup
    ON element_id_remap (document_id, object_kind, old_id);
CREATE INDEX IF NOT EXISTS idx_element_id_remap_run ON element_id_remap (run_id);

-- ============================================================================
-- 7. 型 CHECK → 語彙表 FK（KO7 / P1-4）
-- ============================================================================
-- 旧 CHECK は語彙を17 / 9 に潰しており、agent が出した型（例: PaperClaimComponent /
-- main_result）は全部 diagnostic_claim / theory に丸められていた（F-3 / K-1）。
-- 語彙表への FK に置き換えることで、語彙の正本が core/schema.py に一本化される。

ALTER TABLE theory_claims DROP CONSTRAINT IF EXISTS theory_claims_claim_type_check;
ALTER TABLE theory_components DROP CONSTRAINT IF EXISTS theory_components_component_type_check;

DO $$
DECLARE
    rounded INTEGER;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'theory_claims_claim_type_fk'
    ) THEN
        -- FK を張る前に、語彙表に無い値を持つ行を unknown へ丸める（自称は
        -- claim_type_text に残す = KO7「情報を落とさない」）。旧 CHECK 語彙は
        -- 新語彙の部分集合なので通常は 0 行。
        UPDATE theory_claims
           SET claim_type_text = CASE WHEN claim_type_text = '' THEN claim_type ELSE claim_type_text END,
               claim_type = 'unknown'
         WHERE claim_type NOT IN (SELECT value FROM knowledge_claim_types);
        GET DIAGNOSTICS rounded = ROW_COUNT;
        IF rounded > 0 THEN
            RAISE NOTICE 'migration 078: rounded %% theory_claims row(s) to claim_type=unknown', rounded;
        END IF;

        ALTER TABLE theory_claims
            ADD CONSTRAINT theory_claims_claim_type_fk
            FOREIGN KEY (claim_type) REFERENCES knowledge_claim_types(value);
    END IF;
END $$;

DO $$
DECLARE
    rounded INTEGER;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'theory_components_component_type_fk'
    ) THEN
        UPDATE theory_components
           SET component_type_text = CASE WHEN component_type_text = '' THEN component_type ELSE component_type_text END,
               component_type = 'theory'
         WHERE component_type NOT IN (SELECT value FROM knowledge_component_types);
        GET DIAGNOSTICS rounded = ROW_COUNT;
        IF rounded > 0 THEN
            RAISE NOTICE 'migration 078: rounded %% theory_components row(s) to component_type=theory', rounded;
        END IF;

        ALTER TABLE theory_components
            ADD CONSTRAINT theory_components_component_type_fk
            FOREIGN KEY (component_type) REFERENCES knowledge_component_types(value);
    END IF;
END $$;

-- ============================================================================
-- 8. live ビュー（KO5）
-- ============================================================================
-- 読み手はこの2つを読む。基表を直接読んでよいのは書き手（persistence / 削除経路 /
-- knowledge_objects/*）と、superseded を明示的に見せる監査・履歴目的の読み手だけ。
--
-- ★★ 規律 ★★
-- `SELECT *` はビュー作成時の列で固定される。以後 theory_claims / theory_components に
-- 列を足す migration は、**そのファイルの末尾で下の CREATE OR REPLACE VIEW 2 文を
-- そのまま再実行すること**（そうしないと新しい列が live ビューに現れない）。
-- backend/tests/test_knowledge_objects_vocab.py がこの規律を機械検査する。

CREATE OR REPLACE VIEW theory_claims_live AS
    SELECT * FROM theory_claims WHERE superseded_at IS NULL;

CREATE OR REPLACE VIEW theory_components_live AS
    SELECT * FROM theory_components WHERE superseded_at IS NULL;
