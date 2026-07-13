-- Migration 028: 分野の地図 — カートリッジファイルの無い新分野のメタデータ永続化
--
-- 背景:
--   カートリッジファイルを持たない新分野 (例: modified_gravity) は
--   generate API 呼び出し時に body.domain (name/description 等) を都度渡す
--   設計だったが、このメタデータをどこにも永続化していなかった。フロント
--   (admin.js) は pendingDomainMeta という画面内メモリにしか保持しないため、
--   ページ再読込や別セッションから「draft を再生成」すると body.domain が
--   欠落し、generate_skeleton_draft() がカートリッジディレクトリを探しに行って
--   FileNotFoundError → 404 になる。
--
--   domain_meta を domain_key 単位で永続化し、body.domain が省略された場合の
--   フォールバックに使う。
--
-- 適用: このファイルが正本。backend/core/migrations.py のランナーが起動時に行う
--   （冪等・毎起動再実行）。

CREATE TABLE IF NOT EXISTS atlas_domain_meta (
    domain_key         TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    target_domain      JSONB NOT NULL DEFAULT '[]'::jsonb,
    concept_vocabulary TEXT NOT NULL DEFAULT '',
    created_by         UUID,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
