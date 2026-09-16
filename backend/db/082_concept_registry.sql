-- Migration 082: 概念レジストリ（Phase 3）— library_entries の概念レジストリ化 +
--                SKOS 相当のラベル / 関係 / 骨格リンク表 + mapping_justification の横断追加
--
-- 設計正本: docs/features/concept_registry_design.md §4（不変条項 KR1〜KR10 は §2）。
-- 親: docs/architecture/knowledge_structure_review_2026-09-12.md Phase 3（P3-1〜P3-7）。
-- 前提: migration 078 / 079 / 080（知識オブジェクト層 Phase 1）・081（学ぶ単位 Phase 2）。
-- 語彙表 FK・部分 UNIQUE・DELETE FROM なし・末尾の live ビュー再作成という Phase 1 の
-- 作法をそのまま継承する。
--
-- 何をするか:
--   1. 語彙表 4 つ（knowledge_entry_types / knowledge_label_kinds /
--      knowledge_relation_kinds / knowledge_mapping_justifications）を作り、
--      core/schema.py の列挙と**同じ値**を ON CONFLICT DO NOTHING でシードする（KO7 と同型。
--      一致は backend/tests/test_concept_registry_vocab.py が固定する）。
--   2. library_entries を概念レジストリへ拡張する（§4.2）。entry_type の CHECK を
--      語彙表 FK に置き換え、review_status / review_note / mapping_justification /
--      candidate_key / decided_by / decided_at を足す。既存行は DEFAULT 'confirmed' で
--      意味不変（既存行 = 人間が作った行）。
--   3. library_entry_labels / library_entry_relations / library_atlas_node_links を作る
--      （§4.3〜4.5）。SKOS の altLabel / hiddenLabel / broader / related / exactMatch /
--      closeMatch に一対一で対応する。
--   4. mapping_justification 列を既存 5 表へ additive に足し、**既存列から決定論的に
--      導ける行だけ**をバックフィルする（§4.6。導出できない表は NULL のまま = KR4
--      「推測で埋めない」）。
--   5. element_identity_links.instance_element_type の CHECK に 'symbol' を足す（§4.7）。
--   6. knowledge_symbols_live ビューを作る（§4.8）。
--
-- 設計上の要点:
--   - **行を消さない**（KR7）。本ファイルに DELETE 文は無い。却下は status 遷移で表す。
--   - **確定は人間**（KR2）。review_status の DEFAULT は 'confirmed' だが、AI が作る候補は
--     コード側（core/library/store.create_entry）が明示的に 'candidate' を渡す。
--   - **版非依存キー**（KR9）。library_atlas_node_links は skeleton_version を持たない。
--   - **confidence を表に出さない**（KR6）。列としては持つが API / UI へは出さない。
--   - 書式指定子（パーセント記号）は LIKE の中でだけ '%%' と二重に書く（ランナーは
--     exec_driver_sql に空のパラメータを渡すため psycopg2 が補間しようとする。064 / 078 と
--     同じ理由）。

-- ============================================================================
-- 1. 語彙表（§4.1 / KO7）— FK の参照先なので最初に作る
-- ============================================================================

CREATE TABLE IF NOT EXISTS knowledge_entry_types (
    entry_type TEXT PRIMARY KEY,
    label      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS knowledge_label_kinds (
    kind  TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS knowledge_relation_kinds (
    kind  TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS knowledge_mapping_justifications (
    justification TEXT PRIMARY KEY,
    label         TEXT NOT NULL DEFAULT ''
);

-- 語彙のシード。値は core/schema.py::LIBRARY_ENTRY_TYPES / CONCEPT_LABEL_KINDS /
-- CONCEPT_RELATION_KINDS / MAPPING_JUSTIFICATIONS と**完全に同じ列挙**、label は
-- core/library/schema.py の ENTRY_TYPE_LABELS / LABEL_KIND_LABELS /
-- RELATION_KIND_LABELS / JUSTIFICATION_LABELS と**完全に同じ文字列**であること
-- （test_concept_registry_vocab.py が両方向の一致を固定する）。
INSERT INTO knowledge_entry_types (entry_type, label) VALUES
    ('apparatus',        '装置'),
    ('theory_component', '理論コンポーネント'),
    ('concept',          '概念'),
    ('theory',           '理論'),
    ('method',           '方法'),
    ('observable',       '観測量'),
    ('assumption',       '前提'),
    ('quantity',         '量'),
    ('process',          '過程')
ON CONFLICT (entry_type) DO NOTHING;

INSERT INTO knowledge_label_kinds (kind, label) VALUES
    ('preferred', '主ラベル'),
    ('alternate', '別名'),
    ('hidden',    '隠しラベル')
ON CONFLICT (kind) DO NOTHING;

INSERT INTO knowledge_relation_kinds (kind, label) VALUES
    ('broader',     '上位'),
    ('related',     '関連'),
    ('exact_match', '同じ'),
    ('close_match', '近い')
ON CONFLICT (kind) DO NOTHING;

INSERT INTO knowledge_mapping_justifications (justification, label) VALUES
    ('manual_curation',     '教員の判断'),
    ('lexical_match',       '表記の一致'),
    ('vector_similarity',   '意味の近さ'),
    ('cartridge_declared',  '分野の宣言'),
    ('corpus_cooccurrence', 'コーパス内の共起'),
    ('llm_candidate',       'AI の候補')
ON CONFLICT (justification) DO NOTHING;

-- ============================================================================
-- 2. library_entries の拡張（§4.2。既存行は意味不変）
-- ============================================================================

-- review_status は「候補 → 教員の確定」の軸（KR2）。status='retired'（公開を止めた）とは
-- **別軸**である。既存行は人間が作った行なので DEFAULT 'confirmed'。
ALTER TABLE library_entries
    ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'confirmed'
    CHECK (review_status IN ('candidate', 'confirmed', 'dismissed'));

-- 見送り理由（必須であることは core/library/registry.py が強制する。KR7）。
ALTER TABLE library_entries ADD COLUMN IF NOT EXISTS review_note TEXT NOT NULL DEFAULT '';

-- 「なぜこの概念を立てられたか」（KR4）。手動作成は manual_curation、候補生成は導出の種類。
-- 既存行は NULL = 「記録なし」のまま（推測で埋めない）。
ALTER TABLE library_entries
    ADD COLUMN IF NOT EXISTS mapping_justification TEXT
    REFERENCES knowledge_mapping_justifications(justification);

-- 候補の再提案を同一行に畳むためのキー（cand|{domain_key}|{normalize_label(name)}）。
-- 手動作成の行は NULL（畳む対象ではない）。
ALTER TABLE library_entries ADD COLUMN IF NOT EXISTS candidate_key TEXT;

ALTER TABLE library_entries ADD COLUMN IF NOT EXISTS decided_by UUID;
ALTER TABLE library_entries ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS uq_library_entries_candidate_key
    ON library_entries (candidate_key)
    WHERE candidate_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_library_entries_review
    ON library_entries (domain_key, review_status, status);

-- entry_type の CHECK（migration 042 のカラム直付け無名 CHECK = 自動命名
-- library_entries_entry_type_check）を落とし、語彙表への FK に置き換える（KO7 と同じ思想）。
-- 無名 CHECK の名前が環境によって違う可能性に備え、conkey が entry_type 1 列だけの
-- CHECK 制約も総なめで落とす。
--
-- P3-R14: この「総なめ DROP」は **FK 未作成のとき（= まだ置き換えが済んでいないとき）
-- だけ**走らせる。毎起動・番号順に全ファイルを再実行する方式（CLAUDE.md §マイグレーションの
-- 正本一本化）なので、無条件に置くと後続のマイグレーションが entry_type に正当な CHECK を
-- 足しても毎起動で黙って落ちてしまう（冪等ではなく「毎回壊す」）。置き換え済みの環境では
-- この DO ブロックは丸ごと no-op になる。
DO $$
DECLARE
    stale_conname text;
    rounded INTEGER;
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'library_entries_entry_type_fk'
    ) THEN
        RETURN;  -- 置き換え済み。以降は何もしない（他所が張った CHECK を巻き込まない）。
    END IF;

    -- 042 の自動命名 CHECK と、conkey が entry_type 1 列だけの CHECK 制約を落とす
    -- （FK は contype='f' なので巻き込まない）。
    FOR stale_conname IN
        SELECT c.conname
        FROM pg_constraint c
        WHERE c.conrelid = 'library_entries'::regclass
          AND c.contype = 'c'
          AND (
              SELECT array_agg(a.attname ORDER BY a.attname)
              FROM unnest(c.conkey) AS k(attnum)
              JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
          ) = ARRAY['entry_type']::name[]
    LOOP
        EXECUTE 'ALTER TABLE library_entries DROP CONSTRAINT ' || quote_ident(stale_conname);
    END LOOP;

    -- FK を張る前に語彙表に無い値を concept へ丸める（042 の CHECK 語彙は新語彙の
    -- 部分集合なので通常は 0 行）。丸めた事実は NOTICE に出すだけで行は消さない。
    UPDATE library_entries
       SET entry_type = 'concept'
     WHERE entry_type NOT IN (SELECT entry_type FROM knowledge_entry_types);
    GET DIAGNOSTICS rounded = ROW_COUNT;
    IF rounded > 0 THEN
        RAISE NOTICE 'migration 082: rounded %% library_entries row(s) to entry_type=concept', rounded;
    END IF;

    ALTER TABLE library_entries
        ADD CONSTRAINT library_entries_entry_type_fk
        FOREIGN KEY (entry_type) REFERENCES knowledge_entry_types(entry_type);
END $$;

-- ============================================================================
-- 3. library_entry_labels（§4.3 / P3-2）
-- ============================================================================
-- SKOS の altLabel / hiddenLabel。preferred は library_entries.name が正本なので
-- 行にしない（二重管理を作らない）。aliases JSONB は編集面として残り、store が
-- 同一トランザクションで alternate 行へ**片方向ミラー**する。

CREATE TABLE IF NOT EXISTS library_entry_labels (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_id              UUID NOT NULL REFERENCES library_entries(id) ON DELETE CASCADE,
    kind                  TEXT NOT NULL REFERENCES knowledge_label_kinds(kind),
    label                 TEXT NOT NULL,
    -- 正規化の正本は core/atlas_gaps/schema.py::normalize_label（NFKC → casefold →
    -- 空白畳み）。候補導出・別名検索はこの列で突き合わせる。
    normalized_label      TEXT NOT NULL,
    language              TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('confirmed', 'dismissed')),
    mapping_justification TEXT REFERENCES knowledge_mapping_justifications(justification),
    evidence              JSONB NOT NULL DEFAULT '[]'::jsonb,
    review_note           TEXT NOT NULL DEFAULT '',
    created_by            UUID,
    decided_by            UUID,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT library_entry_labels_entry_kind_label_uniq
        UNIQUE (entry_id, kind, normalized_label)
);

CREATE INDEX IF NOT EXISTS idx_library_entry_labels_entry
    ON library_entry_labels (entry_id, kind, status);
CREATE INDEX IF NOT EXISTS idx_library_entry_labels_normalized
    ON library_entry_labels (normalized_label, status);

-- ============================================================================
-- 4. library_entry_relations（§4.4 / P3-2）
-- ============================================================================
-- SKOS の broader / related / exactMatch / closeMatch。**リンクであってマージではない**
-- （KR3）。2 行は並存したまま「同じと言える」だけを記録する。ドメイン跨ぎ可。

CREATE TABLE IF NOT EXISTS library_entry_relations (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- 対称 kind: rel|{kind}|{min(a,b)}|{max(a,b)} / broader: rel|broader|{narrower}|{broader}
    relation_key          TEXT NOT NULL UNIQUE,
    subject_entry_id      UUID NOT NULL REFERENCES library_entries(id) ON DELETE CASCADE,
    object_entry_id       UUID NOT NULL REFERENCES library_entries(id) ON DELETE CASCADE,
    kind                  TEXT NOT NULL REFERENCES knowledge_relation_kinds(kind),
    status                TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'confirmed', 'dismissed')),
    mapping_justification TEXT NOT NULL
        REFERENCES knowledge_mapping_justifications(justification),
    reason                TEXT NOT NULL DEFAULT '',
    evidence              JSONB NOT NULL DEFAULT '[]'::jsonb,
    review_note           TEXT NOT NULL DEFAULT '',
    -- 生値は DB のみ（KR6）。API / UI へは段階ラベルすら出さない。
    confidence            REAL,
    created_by            UUID,
    decided_by            UUID,
    decided_at            TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT library_entry_relations_distinct_endpoints
        CHECK (subject_entry_id <> object_entry_id)
);

CREATE INDEX IF NOT EXISTS idx_library_entry_relations_subject
    ON library_entry_relations (subject_entry_id, status);
CREATE INDEX IF NOT EXISTS idx_library_entry_relations_object
    ON library_entry_relations (object_entry_id, status);

-- ============================================================================
-- 5. library_atlas_node_links（§4.5 / P3-4。**版非依存** = KR9）
-- ============================================================================
-- レジストリ ↔ 分野の地図（atlas 骨格）の対応。atlas_skeletons への FK も書き込み経路も
-- 作らない（KR2 / LS7 / AB4）。凍結で node_id が消えても行は残し、読み時に
-- node_in_current_version=false を付けるだけ（KR9）。

CREATE TABLE IF NOT EXISTS library_atlas_node_links (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- anode|{entry_id}|{domain_key}|{node_id}
    link_key              TEXT NOT NULL UNIQUE,
    entry_id              UUID NOT NULL REFERENCES library_entries(id) ON DELETE CASCADE,
    domain_key            TEXT NOT NULL,
    node_id               TEXT NOT NULL,
    node_kind             TEXT NOT NULL DEFAULT 'concept'
        CHECK (node_kind IN ('region', 'concept')),
    -- exact_match / close_match のみ（コード側 core/library/registry.py が強制する）。
    kind                  TEXT NOT NULL REFERENCES knowledge_relation_kinds(kind),
    status                TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'confirmed', 'dismissed')),
    mapping_justification TEXT NOT NULL
        REFERENCES knowledge_mapping_justifications(justification),
    reason                TEXT NOT NULL DEFAULT '',
    evidence              JSONB NOT NULL DEFAULT '[]'::jsonb,
    review_note           TEXT NOT NULL DEFAULT '',
    confidence            REAL,
    created_by            UUID,
    decided_by            UUID,
    decided_at            TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_library_atlas_node_links_entry
    ON library_atlas_node_links (entry_id, status);
CREATE INDEX IF NOT EXISTS idx_library_atlas_node_links_domain
    ON library_atlas_node_links (domain_key, node_id, status);

-- ============================================================================
-- 6. mapping_justification の横断追加（§4.6 / P3-3）
-- ============================================================================
-- 「なぜ同じと言えたか」を記録する列がどこにも無かった（D4 診断）。5 表に additive に
-- 足す。NULL 可 = 「記録なし」を正直に残すため。

ALTER TABLE element_identity_links
    ADD COLUMN IF NOT EXISTS mapping_justification TEXT
    REFERENCES knowledge_mapping_justifications(justification);

ALTER TABLE atlas_anchor_aliases
    ADD COLUMN IF NOT EXISTS mapping_justification TEXT
    REFERENCES knowledge_mapping_justifications(justification);

ALTER TABLE atlas_gap_decisions
    ADD COLUMN IF NOT EXISTS mapping_justification TEXT
    REFERENCES knowledge_mapping_justifications(justification);

ALTER TABLE atlas_edge_decisions
    ADD COLUMN IF NOT EXISTS mapping_justification TEXT
    REFERENCES knowledge_mapping_justifications(justification);

ALTER TABLE landscape_placements
    ADD COLUMN IF NOT EXISTS mapping_justification TEXT
    REFERENCES knowledge_mapping_justifications(justification);

-- バックフィルは**既存列から決定論的に導ける行だけ**（KR4: 推測で埋めない）。
-- WHERE mapping_justification IS NULL で自己収束するので毎起動の再実行は無害。
--
-- landscape_placements: provenance 列が生成手段そのもの（llm / human / deterministic）。
UPDATE landscape_placements
   SET mapping_justification = 'llm_candidate'
 WHERE mapping_justification IS NULL AND provenance = 'llm';

UPDATE landscape_placements
   SET mapping_justification = 'manual_curation'
 WHERE mapping_justification IS NULL AND provenance = 'human';

UPDATE landscape_placements
   SET mapping_justification = 'lexical_match'
 WHERE mapping_justification IS NULL AND provenance = 'deterministic';

-- atlas_anchor_aliases: 別名は教員が確定したものしか行にならない（VA6）。source 列は
-- 登録導線（どの画面から入れたか）であって根拠ではないので、全行 manual_curation。
UPDATE atlas_anchor_aliases
   SET mapping_justification = 'manual_curation'
 WHERE mapping_justification IS NULL;

-- element_identity_links / atlas_gap_decisions / atlas_edge_decisions は既存列から
-- 導出できない（候補の出所が行に残っていない）。NULL のまま = 「記録なし」（KR4）。

-- ============================================================================
-- 7. element_identity_links の instance 型に 'symbol'（§4.7 / P3-5）
-- ============================================================================
-- 旧定義（048）はカラム直付けの無名 CHECK なので自動命名は
-- element_identity_links_instance_element_type_check（064 と同型の手順）。

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'element_identity_links_instance_element_type_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%%symbol%%'
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
                'figure', 'theory_component', 'theory_claim', 'equation', 'symbol'
            ));
    END IF;
END $$;

-- ============================================================================
-- 8. knowledge_symbols_live（§4.8 / KO5）
-- ============================================================================
-- 記号の読み手（学習者向けの「直前の定義」・同一性候補の導出）はこのビューを読む。
-- 基表 knowledge_symbols を SELECT してよいのは書き手（persistence / 削除経路）だけ。

CREATE OR REPLACE VIEW knowledge_symbols_live AS
    SELECT * FROM knowledge_symbols WHERE superseded_at IS NULL;
