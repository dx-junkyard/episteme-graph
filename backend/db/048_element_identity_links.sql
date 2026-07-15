-- Migration 048: 同一性リンク（Phase W-β, 知識ネットワークビジョン §7 / W層設計 §5.5）
-- 正本:
--   docs/features/knowledge_network_vision.md（§3 修正② / §4 KN-2・KN-3 / §7 Phase W-β）
--   docs/features/element_deliberation_workspace_design.md（§5.5 / §6 / §13 Phase W-β）
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。
-- すべて IF NOT EXISTS で冪等。
--
-- 背景:
--   「表現を潰すと接続が悪くなる」（ビジョン §1-3）ため、同一性の整理は正規化（置換）ではなく
--   リンクの追加（非破壊, KN-2）で表現する。ある論文のインスタンス要素（figure /
--   theory_component / theory_claim / equation）が、分野横断の共通部品（L層
--   library_entries、Phase W-β の設計では shared_part）と「同じものである」という対応を
--   人間確定（KN-3）の状態遷移付きで記録する。
--
--   設計書は当初「migration 046 に同乗」としていたが、046（atlas_report_incorporation）/
--   047（topic_lecture_audio）は本テーブル起草後に別機能へ割り当て済みのため、本実装は
--   新規番号 048 を使う（設計書の番号記述は本ファイルでは追わない。内容は不変）。
--
--   インスタンス側は scope='document' のポリモーフィック行（FK なし）。孤児掃除は
--   document 削除経路（core/versioning/deletion.py::_purge_document /
--   api/routes/admin.py::delete_material）が明示 DELETE で担う（V層 orphan gap と同じ扱い）。
--   共通部品側（shared_part_id）は L層 library_entries への実 FK とする（ビジョン §8 未決2 の
--   推奨案＝ハブ経由限定を採用。instance ↔ instance の直接リンクは作らない）。

CREATE TABLE IF NOT EXISTS element_identity_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- インスタンス側（scope='document'。ポリモーフィックなので FK なし）
    instance_element_type TEXT NOT NULL
        CHECK (instance_element_type IN ('figure', 'theory_component', 'theory_claim', 'equation')),
    instance_element_id TEXT NOT NULL,
    instance_document_id TEXT NOT NULL,

    -- 共通部品側（ハブ経由限定。L層 library_entries への実 FK）
    shared_part_id UUID NOT NULL REFERENCES library_entries(id),

    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'confirmed', 'rejected')),

    -- この論文での表記（KN-2: インスタンス側の label/notation/文脈は書き換えない。
    -- ここに「この論文ではこう書く」を保持し、共通部品側 local_expressions への
    -- 反映は確定時に別途行う）。
    local_expression JSONB NOT NULL DEFAULT '{}',
    evidence JSONB NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL DEFAULT '',
    confidence REAL,                                -- 生値は DB のみ（API は段階ラベル・W8）

    created_by UUID REFERENCES users(id),
    decided_by UUID REFERENCES users(id),           -- 確定/却下した人間（KN-3: 同一視は人間が確定）
    decided_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- レビュー指摘（2026-07-15）: equation の element_id（例 eq_1）は論文間で衝突するため、
    -- instance_document_id を含めない3列制約では別論文の同一 equation_id が同じ行に
    -- 衝突してしまう（同一性リンクの作成・一覧の両方が別論文の情報を漏えいさせる）。
    -- instance_document_id を含めた4列で一意化する。制約名は下の DO $$ ブロックの
    -- 冪等チェックと突き合わせるため明示的に固定する。
    CONSTRAINT element_identity_links_instance_shared_uniq
        UNIQUE (instance_element_type, instance_element_id, instance_document_id, shared_part_id)
);

CREATE INDEX IF NOT EXISTS idx_element_identity_links_instance_document
    ON element_identity_links(instance_document_id);

CREATE INDEX IF NOT EXISTS idx_element_identity_links_shared_part_status
    ON element_identity_links(shared_part_id, status);

-- 冪等な後方互換ガード: 本ファイルの起草直後（2026-07-15 以前）にローカル dev DB へ
-- 適用済みの環境では、上の CREATE TABLE IF NOT EXISTS は素通りし、旧・
-- instance_document_id を含まない3列 UNIQUE 制約が残ったままになる。旧制約が
-- 存在すれば DROP し、新4列制約が無ければ追加する（毎起動再実行されても
-- 冪等に収束する）。テーブルがまだ存在しない場合は何もしない
-- （直前の CREATE TABLE が既に新制約付きで作成済みのため）。
DO $$
DECLARE
    old_conname text;
BEGIN
    IF to_regclass('public.element_identity_links') IS NULL THEN
        RETURN;
    END IF;

    SELECT c.conname INTO old_conname
    FROM pg_constraint c
    WHERE c.conrelid = 'element_identity_links'::regclass
      AND c.contype = 'u'
      AND (
          SELECT array_agg(a.attname ORDER BY a.attname)
          FROM unnest(c.conkey) AS k(attnum)
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
      ) = ARRAY['instance_element_id', 'instance_element_type', 'shared_part_id']::name[]
    LIMIT 1;

    IF old_conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE element_identity_links DROP CONSTRAINT %I', old_conname);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'element_identity_links'::regclass
          AND conname = 'element_identity_links_instance_shared_uniq'
    ) THEN
        ALTER TABLE element_identity_links
            ADD CONSTRAINT element_identity_links_instance_shared_uniq
            UNIQUE (instance_element_type, instance_element_id, instance_document_id, shared_part_id);
    END IF;
END $$;
