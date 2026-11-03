-- Migration 088: 検証シミュレーション層（Simulation Bridge, SB層）— 理論⇔シミュレーション⇔実験の翻訳層
--
-- 設計正本: docs/features/simulation_bridge_design.md（不変条項 SB1〜SB10・§4 データモデル）。
-- 語彙の正本: backend/core/simulation_bridge/schema.py（PROGRAM_STATUSES / WIRING_ORIGINS /
-- WIRING_STATUSES / RUN_STATUSES / PART_TYPE_LABELS）。CHECK の値とシードのラベルは
-- そこと完全に一致させる（test_simulation_bridge_migration.py が固定する）。
-- このファイルが DDL の正本。適用は backend/core/migrations.py のランナーが起動時に行う
-- （冪等・毎起動・番号順に全ファイルを再実行）。
--
-- 何をするか:
--   1. L層の語彙表 knowledge_entry_types に実験機の部品 3 種（instrument_model /
--      noise_source / signal_processing）を ON CONFLICT DO NOTHING でシードする
--      （core/schema.py::LIBRARY_ENTRY_TYPES と core/library/schema.py::ENTRY_TYPE_LABELS に
--      同じ値・同じ文字列を足す。082 のシード文は編集しない）。
--   2. 理論シミュレータ simulation_programs（LLM 生成コードの候補 → 教員確定。§4.2）。
--   3. 実験機シミュレータ instrument_wirings（L層部品の配線。§4.3）。
--   4. 実行記録 simulation_runs（append-only。§4.4）。
--   5. 台帳 epistemic_ledger に simulation_candidates JSONB（反証条件候補と同型の
--      「配列に追記・配列内で status 遷移」。§4.5）。
--
-- 設計上の要点:
--   - **シミュレーションの一致は検証ではない**（SB1）。台帳への反映は simulation_candidates
--     の候補として入り、確定しても verification_status は indirectly_supported 止まり
--     （コード側 core/simulation_bridge/store.py が強制する）。
--   - **3 部品の独立**（SB2）。理論プログラム・配線・run を別の表に置き、1 行に混ぜない。
--   - **行を消さない**（SB7）。programs / wirings は status 遷移（再生成は superseded）、
--     runs は append-only。本ファイルにも DELETE 文は無い。教材の物理削除は documents の
--     FK CASCADE で追随する。
--   - created_by / decided_by は TEXT で users(id) を参照しない（library_entries.created_by と
--     同じ判断。account_lifecycle の PURGE / RETAIN 宣言の対象にしない）。
--   - confidence を列にしない（SB6）。比較の数値は simulation_runs.result の中だけにある。
--   - SQL 本文は psql で読める plain SQL で書く（パーセント記号は 1 個）。psycopg2 向けの
--     二重化はランナー core/migrations.py::escape_percent_for_driver が 1 箇所で行う。

-- ============================================================================
-- 1. L層 部品の entry_type（§4.1）
-- ============================================================================

INSERT INTO knowledge_entry_types (entry_type, label) VALUES
    ('instrument_model',  '装置モデル'),
    ('noise_source',      'ノイズ源'),
    ('signal_processing', '信号処理の工夫')
ON CONFLICT (entry_type) DO NOTHING;

-- ============================================================================
-- 2. simulation_programs（理論シミュレータ・§4.2）
-- ============================================================================
-- 1 行 = 理論モジュール 1 つに対する生成コード 1 版。関数 simulate(inputs, params) -> dict を
-- 1 つ定義する Python ソースを持つ。再生成は旧行（candidate / confirmed）を superseded にして
-- 新行を挿入する（superseded_by_id が新行を指す = prov:wasRevisionOf）。

CREATE TABLE IF NOT EXISTS simulation_programs (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    -- 理論モジュール（保存行の stable_key と読み時の module_key）。保存行が無いときは module_key だけ。
    module_stable_key   TEXT NOT NULL DEFAULT '',
    module_key          TEXT NOT NULL DEFAULT '',
    language            TEXT NOT NULL DEFAULT 'python'
        CONSTRAINT simulation_programs_language_check CHECK (language IN ('python')),
    source_code         TEXT NOT NULL DEFAULT '',
    io_contract         JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- {model, equation_ids, symbol_ids, reconstructed_inputs, facts}（confidence は持たない）
    provenance          JSONB NOT NULL DEFAULT '{}'::jsonb,
    review_status       TEXT NOT NULL DEFAULT 'candidate'
        CONSTRAINT simulation_programs_review_status_check
        CHECK (review_status IN ('candidate', 'confirmed', 'dismissed', 'superseded')),
    review_note         TEXT NOT NULL DEFAULT '',
    decided_by          TEXT,
    decided_at          TIMESTAMPTZ,
    created_by          TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_at       TIMESTAMPTZ,
    superseded_by_id    UUID REFERENCES simulation_programs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_simulation_programs_document
    ON simulation_programs(document_id);
CREATE INDEX IF NOT EXISTS idx_simulation_programs_live_module
    ON simulation_programs(document_id, module_key)
    WHERE review_status IN ('candidate', 'confirmed');

-- ============================================================================
-- 3. instrument_wirings（実験機シミュレータ・§4.3）
-- ============================================================================
-- parts = [{slot, entry_id, version_no, params}]（entry_id は L層の行・version_no は凍結版）、
-- edges = [{from_slot, to_slot}]（信号の流れ）。document_id NULL は論文に紐づかない汎用配線。
-- 教員が組んだ配線は confirmed で始まり、LLM の改訂提案（origin='llm_candidate'）は
-- parent_wiring_id で親を指す candidate 行になる。

CREATE TABLE IF NOT EXISTS instrument_wirings (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id         UUID REFERENCES documents(id) ON DELETE CASCADE,
    name                TEXT NOT NULL DEFAULT '',
    parts               JSONB NOT NULL DEFAULT '[]'::jsonb,
    edges               JSONB NOT NULL DEFAULT '[]'::jsonb,
    origin              TEXT NOT NULL DEFAULT 'teacher'
        CONSTRAINT instrument_wirings_origin_check CHECK (origin IN ('teacher', 'llm_candidate')),
    parent_wiring_id    UUID REFERENCES instrument_wirings(id) ON DELETE SET NULL,
    revision_note       TEXT NOT NULL DEFAULT '',
    review_status       TEXT NOT NULL DEFAULT 'candidate'
        CONSTRAINT instrument_wirings_review_status_check
        CHECK (review_status IN ('candidate', 'confirmed', 'dismissed')),
    review_note         TEXT NOT NULL DEFAULT '',
    decided_by          TEXT,
    decided_at          TIMESTAMPTZ,
    created_by          TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_instrument_wirings_document
    ON instrument_wirings(document_id);
CREATE INDEX IF NOT EXISTS idx_instrument_wirings_parent
    ON instrument_wirings(parent_wiring_id);

-- ============================================================================
-- 4. simulation_runs（append-only・§4.4）
-- ============================================================================
-- 失敗した run・AST 検査で止めた run（rejected）も行として残す（SB7）。

CREATE TABLE IF NOT EXISTS simulation_runs (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id             UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    program_id              UUID NOT NULL REFERENCES simulation_programs(id) ON DELETE CASCADE,
    wiring_id               UUID REFERENCES instrument_wirings(id) ON DELETE SET NULL,
    observation_claim_ids   JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- {values, params, reference: {value, unit, source_note}?, seed}
    inputs                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- {theory_outputs, observed, comparison: {level, facts, metrics}}
    result                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- {status, error, ast_check, exit_code}（内部）
    runner                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    status                  TEXT NOT NULL
        CONSTRAINT simulation_runs_status_check
        CHECK (status IN ('succeeded', 'failed', 'rejected')),
    ledger_candidate_id     TEXT NOT NULL DEFAULT '',
    created_by              TEXT NOT NULL DEFAULT '',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_simulation_runs_document
    ON simulation_runs(document_id);
CREATE INDEX IF NOT EXISTS idx_simulation_runs_program
    ON simulation_runs(program_id);

-- ============================================================================
-- 5. epistemic_ledger.simulation_candidates（§4.5）
-- ============================================================================
-- 反証条件候補（067 falsification_candidates）と同型。確定は配列内の status 遷移で、
-- 教員が確定したときだけ verification_scopes にスコープが 1 件足される。

ALTER TABLE epistemic_ledger
    ADD COLUMN IF NOT EXISTS simulation_candidates JSONB NOT NULL DEFAULT '[]'::jsonb;
