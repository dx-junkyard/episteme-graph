-- Migration 086: 理論モジュール層 Phase 1 — knowledge_theory_modules（新表）+ live ビュー +
--                同一性リンクの instance 型に 'theory_module' + mapping_justification 'structural_match'
--
-- 設計正本: docs/features/theory_module_layer_design.md §13（不変条項 TM1〜TM15・§9 O-1 (c)）。
-- 前提: migration 078 / 080（知識オブジェクト層 — stable_key / supersede / live ビュー /
-- document_id の UUID + FK CASCADE）と 082（概念レジストリ — 語彙表
-- knowledge_mapping_justifications と element_identity_links の instance 型 CHECK）。
-- このファイルが DDL の正本。適用は backend/core/migrations.py のランナーが起動時に行う
-- （冪等・毎起動・番号順に全ファイルを再実行）。
--
-- 何をするか:
--   1. 理論モジュール（外枠・内側）を一級の行にする knowledge_theory_modules と
--      knowledge_theory_modules_live を作る。document_id は最初から UUID + FK CASCADE（KO9）。
--   2. element_identity_links.instance_element_type の CHECK に 'theory_module' を足す
--      （構造の指紋による同一性候補の instance 側。§13.8）。
--   3. 語彙表 knowledge_mapping_justifications に 'structural_match'（構造の一致）を
--      ON CONFLICT DO NOTHING でシードする（core/schema.py::MAPPING_JUSTIFICATIONS と
--      core/library/schema.py::JUSTIFICATION_LABELS に同じ値・同じ文字列を足すこと）。
--
-- 設計上の要点:
--   - **保存行は表示の正本ではない**（TM11）。GET .../theory-modules は artifact と graph_json
--     からの読み時導出のままで、この表は同一性リンクの参照先と、論文をまたいだ構造の指紋の
--     照合の索引である。
--   - **行を消さない**。再解析は DELETE ではなく superseded_at の刻印で表現する（KO3）。
--     本ファイルにも DELETE 文は無い。教材の物理削除は documents の FK CASCADE で追随する。
--   - **数値を列にしない**（TM6 / 原則4）。成員数・接点の本数・工程の型の多重度は
--     structure_fingerprint（内部表現の文字列）と member_steps（JSONB）の中にだけあり、
--     identity_eligible はその判定結果のブールである。structure_fingerprint は API・DTO・
--     監査・候補の文面に出さない（TM12）。
--   - **人間の確定列を持たない**（承認オブジェクトを増やさない = PL5 の継承）。同一性の
--     判断は element_identity_links 側に残る。
--   - users(id) への参照を持たない（account_lifecycle の PURGE / RETAIN 宣言は不要）。
--   - level は構造の段（外枠 / 内側）であって型語彙ではないので、語彙表 FK ではなく
--     CREATE TABLE 内の CHECK で守る（core/theory_modules/schema.py の LEVEL_OUTER /
--     LEVEL_INNER と一致）。
--   - SQL 本文は psql で読める plain SQL で書く（LIKE のパーセント記号は 1 個）。psycopg2 向けの
--     二重化はランナー core/migrations.py::escape_percent_for_driver が 1 箇所で行う。

-- ============================================================================
-- 1. 語彙のシード（KR4 / KO7）— structural_match
-- ============================================================================
-- 082 と同じ表に 1 行足すだけ。082 のシード文は編集しない（適用済みファイルの意味を
-- 変えない）。ON CONFLICT DO NOTHING なので毎起動の再実行で何も起きない。

INSERT INTO knowledge_mapping_justifications (justification, label) VALUES
    ('structural_match', '構造の一致')
ON CONFLICT (justification) DO NOTHING;

-- ============================================================================
-- 2. knowledge_theory_modules（§13.2）
-- ============================================================================
-- 1 行 = 1 文書の理論モジュール 1 つ（外枠 = level 'outer' / 内側 = 'inner'）。
-- builder（core/theory_modules/builder.py::build_theory_module_records）の出力を、
-- パイプラインステージ theory_modules が core/knowledge_objects/sync.py::sync_live_rows で
-- 同期する（stable_key 一致 = 同 UUID で内容更新 / 不一致 = superseded 刻印 / DELETE なし）。
--
-- stable_key の材料（core/knowledge_objects/stable_key.py::theory_module_stable_key）:
--   document_id + 規則の版 + level + 成員 step が生む式の equation stable_key の集合。
-- agent_module_key は読み時 DTO の module_key（§5.7）と同じ値で、画面の読み時モジュールと
-- 保存行を突き合わせるためだけに持つ（sync_live_rows の agent_id 列）。

CREATE TABLE IF NOT EXISTS knowledge_theory_modules (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id             UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    stable_key              TEXT,
    agent_module_key        TEXT,
    rule_version            TEXT NOT NULL DEFAULT '',
    level                   TEXT NOT NULL
        CONSTRAINT knowledge_theory_modules_level_check CHECK (level IN ('outer', 'inner')),
    -- 内側モジュールの親（外枠）の stable_key。外枠は NULL。UUID FK にしないのは、
    -- 親が superseded になっても子の参照を壊さないため（081 の parent_component_id と同じ判断）。
    parent_stable_key       TEXT,
    -- 表示用の組み立て結果（DTO の label / visual_label / theory_object と同じ値）。
    -- 論文由来の語を含むので、候補エントリの name / reason には使わない（TM15）。
    label                   TEXT NOT NULL DEFAULT '',
    visual_label            TEXT NOT NULL DEFAULT '',
    theory_object           TEXT NOT NULL DEFAULT '',
    process_verbs           JSONB NOT NULL DEFAULT '[]'::jsonb,
    dominant_stage          TEXT NOT NULL DEFAULT '',
    stage_keys              JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_backing_status   TEXT NOT NULL DEFAULT '',
    isolated_reason         TEXT,
    -- 構造の指紋（§13.4 の書式）。**内部表現**。API・DTO・監査・候補の文面に出さない（TM12）。
    structure_fingerprint   TEXT NOT NULL DEFAULT '',
    -- 同一性候補の対象か（外枠・成員 step の下限・汎用でない工程の型の種類の下限を満たす）。
    identity_eligible       BOOLEAN NOT NULL DEFAULT FALSE,
    -- 成員 step ごとの記録:
    --   [{"step_refs": ["{derivation_id}:{step_id}", ...], "node_ids": [...], "operation",
    --     "edge_type", "generic": bool, "stage_key",
    --     "input_equation_ids": [...], "output_equation_ids": [...]}]
    member_steps            JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- 式は agent 側 ID（equation_semantics の equation_id）。式の同一性で結ぶときは
    -- produced_equation_keys（equation stable_key）を使う。
    produced_equation_ids   JSONB NOT NULL DEFAULT '[]'::jsonb,
    produced_equation_keys  JSONB NOT NULL DEFAULT '[]'::jsonb,
    input_equation_ids      JSONB NOT NULL DEFAULT '[]'::jsonb,
    output_equation_ids     JSONB NOT NULL DEFAULT '[]'::jsonb,
    foundation_equation_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    sink_equation_ids       JSONB NOT NULL DEFAULT '[]'::jsonb,
    required_claim_ids      JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- 前提の本文（derivation_chain の assumption_ids の中身を 200 字で丸めたもの。ID ではない）。
    assumptions             JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- 列に昇格させない残り（部品との照合名など）。confidence は持たない（原則4）。
    agent_payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
    produced_by_run_id      UUID,
    superseded_at           TIMESTAMPTZ,
    superseded_by_run_id    UUID,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_theory_modules_document
    ON knowledge_theory_modules(document_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_theory_modules_agent_key
    ON knowledge_theory_modules(document_id, agent_module_key);
CREATE INDEX IF NOT EXISTS idx_knowledge_theory_modules_run
    ON knowledge_theory_modules(produced_by_run_id);

-- 規則 ⑤（§13.8）と related（§13.7）の照合用。live かつ候補の対象の行だけに張る。
CREATE INDEX IF NOT EXISTS idx_knowledge_theory_modules_fingerprint_live
    ON knowledge_theory_modules (rule_version, structure_fingerprint)
    WHERE superseded_at IS NULL AND identity_eligible;

-- live 行の同一性を DB が守る（KO3）。superseded 行は同じキーで何本でも残ってよい。
CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_theory_modules_stable_key_live
    ON knowledge_theory_modules (document_id, stable_key)
    WHERE superseded_at IS NULL AND stable_key IS NOT NULL;

-- 読み手は live ビューを読む（KO5）。基表を SQL で触ってよいのは
-- core/document_pipeline/persistence.py（書き手）だけ（test_theory_module_store.py が固定する）。
CREATE OR REPLACE VIEW knowledge_theory_modules_live AS
    SELECT * FROM knowledge_theory_modules WHERE superseded_at IS NULL;

-- ============================================================================
-- 3. element_identity_links の instance 型に 'theory_module'（§13.8）
-- ============================================================================
-- 旧定義は 048（無名 CHECK → 自動命名 element_identity_links_instance_element_type_check）
-- を 082 が 'symbol' 入りで張り直したもの。082 の DROP ガードは「定義に symbol が無いとき
-- だけ」なので、ここで張る 'symbol' 入りの定義を 082 が毎起動に壊すことはない（往復しない）。

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'element_identity_links_instance_element_type_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%theory_module%'
    ) THEN
        ALTER TABLE element_identity_links
            DROP CONSTRAINT element_identity_links_instance_element_type_check;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'element_identity_links_instance_element_type_check'
    ) THEN
        ALTER TABLE element_identity_links
            ADD CONSTRAINT element_identity_links_instance_element_type_check
            CHECK (instance_element_type IN (
                'figure', 'theory_component', 'theory_claim', 'equation', 'symbol',
                'theory_module'
            ));
    END IF;
END $$;
