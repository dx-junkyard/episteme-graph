-- Migration 065: 知識ランドスケープの配置層 landscape_placements
-- 正本: docs/features/knowledge_landscape_design.md §5（不変条項 LS1〜LS10 は §0）
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う
-- （冪等・毎起動・番号順に再実行）。すべて IF NOT EXISTS で冪等。
--
-- 設計上の要点:
--   1. LS2 A層非改変: A層成果（theory_components / theory_claims / artifacts）には列を
--      足さず、document ⇄ 基準地図（atlas_skeletons の凍結版ノード）の配置だけを本表に積む。
--   2. FK は documents(id) に CASCADE。既存の document 削除経路
--      （core/versioning/deletion.py::_purge_document / api/routes/admin.py::delete_material）は
--      どちらも最終的に documents 行を削除するため、明示 DELETE の追加なしで孤児が出ない
--      （document_figures の orphan gap パターンを踏襲しない）。document_id は常に UUID
--      （パイプライン ctx.document_id）を格納し source_path は使わない。
--   3. LS5 数値を見せない: weight は DB のみに置き、API/UI は段階ラベル
--      （core/landscape/schema.py::weight_label）へ変換する。evidence 要素の形は
--      {"quote": str, "claim_id": str|null}（v1。Phase 2 で chunk_id 遡及を追加）。
--   4. LS3 AI配置は inferred 止まり・確定は教員: 再解析セマンティクスは
--      ① 当該 document の status='inferred' 行を全て 'superseded' に遷移
--      ② 新候補のうち、生きている行（confirmed/rejected/review_required）と同一キーの
--         ものは挿入をスキップ
--      ③ 残りを 'inferred' で挿入
--      （教員の判断を AI が上書きできない。行削除はしない = P4。正本の実装は
--        core/landscape/store.py::supersede_and_insert_candidates）。
--   5. node_id は骨格（凍結版）の region id / concept id。骨格側の座標は本表からは
--      変更しない（LS7 地図の安定性）。骨格に無い node_id 行は読み時に落ちる
--      （fail-closed。行自体は履歴として残す）。

CREATE TABLE IF NOT EXISTS landscape_placements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    domain_key TEXT NOT NULL,
    skeleton_version TEXT NOT NULL DEFAULT '',
    node_id TEXT NOT NULL,
    node_kind TEXT NOT NULL DEFAULT 'region'
        CONSTRAINT landscape_placements_node_kind_check CHECK (node_kind IN ('region','concept')),
    perspective TEXT NOT NULL
        CONSTRAINT landscape_placements_perspective_check CHECK (perspective IN
            ('subject','question','method','theory','observation','application')),
    weight REAL NOT NULL DEFAULT 0.5,
    reason TEXT NOT NULL DEFAULT '',
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'inferred'
        CONSTRAINT landscape_placements_status_check CHECK (status IN
            ('inferred','confirmed','rejected','review_required','superseded')),
    provenance TEXT NOT NULL DEFAULT 'llm'
        CONSTRAINT landscape_placements_provenance_check CHECK (provenance IN
            ('llm','deterministic','human')),
    run_id UUID,
    created_by TEXT NOT NULL DEFAULT 'pipeline',
    reviewed_by UUID,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 生きている行（superseded 以外）は (document, domain, node, perspective) で一意
CREATE UNIQUE INDEX IF NOT EXISTS uq_landscape_placements_live
    ON landscape_placements (document_id, domain_key, node_id, perspective)
    WHERE status <> 'superseded';
CREATE INDEX IF NOT EXISTS idx_landscape_placements_document
    ON landscape_placements (document_id, status);
CREATE INDEX IF NOT EXISTS idx_landscape_placements_domain
    ON landscape_placements (domain_key, status);
