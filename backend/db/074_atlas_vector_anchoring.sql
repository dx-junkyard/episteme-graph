-- Migration 074: 分野マップのベクトル係留層（Atlas Vector Anchoring, VA層）
--
-- 設計正本: docs/features/atlas_vector_anchoring_design.md §3（不変条項 VA1〜VA9 は §2）。
-- このファイルが DDL の正本。適用は `backend/core/migrations.py` のランナーが起動時に
-- 行う（冪等・毎起動・番号順に再実行）。すべて IF NOT EXISTS で冪等。
--
-- 骨格（atlas 凍結版）の region / concept に**プロトタイプベクトル**を与え、
-- 論文（既に pgvector 空間に住んでいるチャンク埋め込み）と同じ空間で扱えるようにする。
-- プロトタイプ合成テキストの正本は core/atlas_vectors/schema.py::build_anchor_source_text。
--
-- 設計上の要点:
--   1. pgvector 拡張は init.sql で導入済み（3072次元 = text-embedding-3-large、
--      chunks と同一モデル。VA5 でモデル切替は非対応）。行数は高々
--      12 regions + 72 concepts / domain なので **index を作らない**
--      （058 manual_sections と同じ判断 — ANN は行数が増えたときの最適化）。
--   2. **FK なし**。骨格は atlas_skeletons の JSONB スナップショットで、node_id に
--      対応する行が存在しない。骨格に無い node_id の行は読み時に落ちる
--      （fail-closed。行自体は履歴として残す — 065 landscape_placements と同型）。
--   3. `skeleton_version` は骨格凍結版の刻印。読み取り側は常に**現行凍結版の行だけ**を
--      使うため、版が変わると自動的に stale = 不使用になり、freeze フックが新版を作る。
--   4. `source_hash` は合成テキストの sha256。refresh 時に不変なら再埋め込みを
--      スキップする（コスト節約・冪等）。アンカーベクトルは**導出データであり正本では
--      ない**ため、(domain_key, skeleton_version) 単位の全置換再構築だけが
--      VA6「情報を落とさない」の設計明示の例外（help_kb の vector.py と同じ扱い）。
--   5. **別名は版非依存**（gap decisions の cluster_key と同じ §4.2 裁定 — 却下・登録が
--      版更新で蒸発しない）。`normalized_alias` は core/atlas_gaps/schema.py::normalize_label
--      を正本として流用する。status の既定が 'confirmed' なのは、登録操作そのものが
--      教員の確定だから（candidate 状態は v1 に存在しない — 近傍注記は読み時導出で
--      保存しない）。削除は無く、見送り → 再登録は同一行の status 遷移（VA6）。
--   6. VA9 本層のコードから atlas_skeletons への INSERT/UPDATE は存在しない。別名は
--      骨格の**外**の独立テーブルであり、骨格 draft/freeze フローに触れない。

CREATE TABLE IF NOT EXISTS atlas_anchor_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_key       TEXT NOT NULL,
    skeleton_version TEXT NOT NULL,
    node_id          TEXT NOT NULL,
    node_kind        TEXT NOT NULL
        CONSTRAINT atlas_anchor_embeddings_node_kind_check
        CHECK (node_kind IN ('region', 'concept')),
    source_text      TEXT NOT NULL,
    source_hash      TEXT NOT NULL,
    embedding        vector(3072),
    built_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (domain_key, skeleton_version, node_id)
);

CREATE TABLE IF NOT EXISTS atlas_anchor_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_key       TEXT NOT NULL,
    node_id          TEXT NOT NULL,
    alias            TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'confirmed'
        CONSTRAINT atlas_anchor_aliases_status_check
        CHECK (status IN ('confirmed', 'dismissed')),
    source           TEXT NOT NULL DEFAULT 'manual'
        CONSTRAINT atlas_anchor_aliases_source_check
        CHECK (source IN ('gap_signal', 'manual')),
    evidence         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by       UUID,
    decided_by       UUID,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (domain_key, node_id, normalized_alias)
);
