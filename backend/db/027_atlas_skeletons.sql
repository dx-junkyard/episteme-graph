-- Migration 027: 分野の地図 — 骨格 (S層) の DB 管理化
--
-- 背景 (docs/features/field_atlas_db_managed_skeleton.md):
--   骨格 draft / 凍結版はこれまでカートリッジ同梱ファイル
--   (atlas/skeleton.yaml / skeleton.draft.yaml) だった。
--   - 複数教員の編集がマージできない (後勝ち上書き)
--   - カートリッジはイメージ焼き込みのため、教員が API で凍結した骨格が
--     コンテナ再ビルドで消える
--   - 新分野の追加にファイルデプロイが必要
--   これらを解消するため骨格を DB で管理する。C層 (atlas_overlay_cache) /
--   P層 (interest_traces) / 修正報告 / 導線計測は既に DB。
--
-- 設計:
--   - domain_key は現行 cartridge_id と同一の名前空間。ただしカートリッジ
--     ファイルが無い domain でも骨格だけで成立する (地図はファイル非依存)
--   - draft は domain につき1行 (部分 UNIQUE)。楽観ロックは revision 照合
--   - 凍結版は (domain_key, version) で複数版を履歴として保持。
--     現行版は created_at 降順の先頭
--   - content は core/atlas.py の skeleton_to_dict() / parse_skeleton() と
--     同形の JSONB (YAML と共通スキーマ)
--   - 既存カートリッジ同梱の skeleton.yaml は起動時に一度だけ取り込む (冪等)。
--     以後 DB が正本、ファイルは新規環境のシードとして残置
--
-- 適用: 本 SQL ファイルが正本。backend/core/migrations.py のランナーが起動時に行う
--   （冪等・毎起動再実行）。

CREATE TABLE IF NOT EXISTS atlas_skeletons (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_key   TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('draft', 'frozen')),
    version      TEXT NOT NULL DEFAULT '',
    content      JSONB NOT NULL,
    revision     INTEGER NOT NULL DEFAULT 1,
    generated_by TEXT NOT NULL DEFAULT '',
    created_by   UUID,
    updated_by   UUID,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- draft は domain につき1行
CREATE UNIQUE INDEX IF NOT EXISTS uq_atlas_skeletons_draft
    ON atlas_skeletons(domain_key) WHERE status = 'draft';

-- 凍結版は版数で一意 (履歴保持)
CREATE UNIQUE INDEX IF NOT EXISTS uq_atlas_skeletons_frozen
    ON atlas_skeletons(domain_key, version) WHERE status = 'frozen';

CREATE INDEX IF NOT EXISTS idx_atlas_skeletons_domain
    ON atlas_skeletons(domain_key, status, created_at DESC);
