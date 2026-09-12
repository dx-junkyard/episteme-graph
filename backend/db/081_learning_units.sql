-- Migration 081: 学ぶ単位の一級化 Phase 2 — learning_units（新表）+ 語彙表 +
--                theory_components の親参照列
--
-- 設計正本: docs/features/learning_units_design.md §4（不変条項 LU1〜LU9 は §2）。
-- 親: docs/architecture/knowledge_structure_review_2026-09-12.md Phase 2（P2-1 / P2-2）。
-- 前提: migration 078 / 079 / 080（知識オブジェクト層 Phase 1）。stable_key / supersede /
-- live ビュー / 語彙表 FK / document_id の UUID + FK CASCADE は Phase 1 の作法をそのまま継承する。
--
-- 何をするか:
--   1. 語彙表 knowledge_unit_kinds を作り、core/schema.py::LEARNING_UNIT_KINDS と
--      同じ列挙を ON CONFLICT DO NOTHING でシードする（KO7 と同じ作法。一致は
--      backend/tests/test_learning_units_guardrails.py が固定する）。
--   2. learning_units（論文の「教える単位」を一級の行にする）+ learning_units_live を作る。
--      document_id は最初から UUID + FK CASCADE（KO9）。
--   3. theory_components に親参照列 parent_component_id / parent_agent_component_id を足す
--      （P2-2。決定論 refinement が分割した子から LLM 原案の親をたどれるようにする）。
--
-- 設計上の要点:
--   - **行を消さない**。再解析は DELETE ではなく superseded_at の刻印で表現する（LU4 / KO3）。
--     本ファイルにも DELETE 文は無い。
--   - **confidence を列に昇格させない**（LU5 / 原則4）。agent 側の残りは agent_payload へ。
--   - review_status / teacher_notes は人間の確定列で、再解析の同期では触らない（LU2）。
--   - parent_component_id には FK を張らない。supersede 遷移で親行が superseded になっても
--      子の参照を壊さないため（v1 では NULL のままで、実質の親参照は
--      parent_agent_component_id。設計書 §5.1）。
--   - 列を足したので、末尾で theory_claims_live / theory_components_live の
--     CREATE OR REPLACE VIEW を再実行する（078 §8 の規律。
--     test_knowledge_objects_vocab.py::test_migrations_adding_columns_recreate_the_view）。
--   - 書式指定子（パーセント記号）は本ファイルでは一切使わない（ランナーは
--     exec_driver_sql に空のパラメータを渡すため psycopg2 が補間しようとする。
--     078 / 079 / 080 と同じ理由）。

-- ============================================================================
-- 1. 語彙表（LU1 / KO7）— FK の参照先なので最初に作る
-- ============================================================================

CREATE TABLE IF NOT EXISTS knowledge_unit_kinds (
    kind  TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT ''
);

-- 語彙のシード。core/schema.py::LEARNING_UNIT_KINDS と**完全に同じ列挙**、label は
-- core/label_vocab.py::LEARNING_UNIT_KIND_LABELS と**完全に同じ文字列**であること
-- （test_learning_units_guardrails.py が両方向の一致を固定する）。
-- ON CONFLICT DO NOTHING なので毎起動の再実行で何も起きない。行削除もしない。
INSERT INTO knowledge_unit_kinds (kind, label) VALUES
    ('section_block',    '章立ての論理ブロック'),
    ('thesis_support',   '中心命題と支持構造'),
    ('parent_component', '理論の部品（原案）'),
    ('dsl_node',         '概念ノード'),
    ('figure',           '図')
ON CONFLICT (kind) DO NOTHING;

-- ============================================================================
-- 2. learning_units（P2-1）
-- ============================================================================
-- 1 行 = 論文の「教える単位」1 つ。A層 artifact（skeleton / thesis /
-- component_assembly / dsl / figure_table）から決定論的に導出される（LU3）。
-- topic はこの行を stable_key で参照するので、再解析後も同じ単位を指す（LU4）。

CREATE TABLE IF NOT EXISTS learning_units (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id          UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    stable_key           TEXT,
    -- 出所 ID。section_block = skeleton の block_id / thesis_support =
    -- 'central_thesis' または 'support:{section}:{idx}' / parent_component =
    -- LLM 原案の agent component_id / dsl_node = node_id / figure = figure_id。
    agent_unit_id        TEXT,
    unit_kind            TEXT NOT NULL REFERENCES knowledge_unit_kinds(kind),
    label                TEXT NOT NULL DEFAULT '',
    summary              TEXT NOT NULL DEFAULT '',
    -- LRMI の teaches 相当: [{"kind": "concept"|"claim"|"equation", "ref", "label"}]
    teaches              JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- 論文順（種別内）。表示順の材料であって、学習者にも教員にも数値としては出さない（LU5）。
    order_index          INTEGER,
    section_ids          JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_block_ids     JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- claim / component は DB UUID、equation / figure は agent 側 ID（設計書 §4.1）。
    linked_claim_ids     JSONB NOT NULL DEFAULT '[]'::jsonb,
    linked_equation_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
    linked_component_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    linked_figure_ids    JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- 人間の確定列（LU2。candidate 始まり・行削除なし・同期では触らない）。
    review_status        TEXT NOT NULL DEFAULT 'candidate',
    teacher_notes        TEXT NOT NULL DEFAULT '',
    -- 素の record + linked_component_agent_ids（コース側が artifact と突合するために必須）。
    agent_payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
    produced_by_run_id   UUID,
    superseded_at        TIMESTAMPTZ,
    superseded_by_run_id UUID,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_learning_units_document ON learning_units(document_id);
CREATE INDEX IF NOT EXISTS idx_learning_units_agent_id
    ON learning_units(document_id, agent_unit_id);
CREATE INDEX IF NOT EXISTS idx_learning_units_kind
    ON learning_units(document_id, unit_kind, order_index);
CREATE INDEX IF NOT EXISTS idx_learning_units_run ON learning_units(produced_by_run_id);

-- live 行の同一性を DB が守る（LU4 / KO3）。superseded 行は同じキーで何本でも残ってよい。
CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_units_stable_key_live
    ON learning_units (document_id, stable_key)
    WHERE superseded_at IS NULL AND stable_key IS NOT NULL;

-- 読み手は live ビューを読む（KO5）。基表を SELECT してよいのは
-- core/document_pipeline/persistence.py と core/versioning/deletion.py だけ。
CREATE OR REPLACE VIEW learning_units_live AS
    SELECT * FROM learning_units WHERE superseded_at IS NULL;

-- ============================================================================
-- 3. theory_components の親参照（P2-2）
-- ============================================================================
-- 決定論 refinement（component_refiner）が LLM 原案 1 件を複数の理論操作へ分割する。
-- 子から原案をたどれるよう、子行に原案の agent component_id を刻む。原案そのものは
-- theory_components の行にはしない（学習者・グラフの主語は子 = 実際の理論操作で、
-- 原案は learning_units(unit_kind='parent_component') が一級に持つ。設計書 §5.1）。
--
-- parent_component_id は「原案を component 行として持つ将来」に備えた nullable 列で、
-- v1 では常に NULL のまま。FK を張らないのは supersede 遷移で親が superseded に
-- なっても子の参照を壊さないため。

ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS parent_component_id UUID;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS parent_agent_component_id TEXT;

CREATE INDEX IF NOT EXISTS idx_theory_components_parent_agent
    ON theory_components(document_id, parent_agent_component_id);

-- ============================================================================
-- 4. live ビューの再作成（078 §8 の規律）
-- ============================================================================
-- SELECT * のビューは作成時の列で固定されるため、theory_components に列を足した
-- 本ファイルの末尾で 078 と同じ定義を再実行する（そうしないと新しい列が live ビューに
-- 現れない）。theory_claims は本ファイルでは触っていないが、2 文セットで再実行する
-- 規律に揃えておく（CREATE OR REPLACE なので無害）。

CREATE OR REPLACE VIEW theory_claims_live AS
    SELECT * FROM theory_claims WHERE superseded_at IS NULL;

CREATE OR REPLACE VIEW theory_components_live AS
    SELECT * FROM theory_components WHERE superseded_at IS NULL;
