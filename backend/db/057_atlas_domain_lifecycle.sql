-- Migration 057: 分野マップ（Field Atlas）ドメインのライフサイクル（retired 状態）
-- 正本: docs/features/atlas_binding_lifecycle_design.md（§3 ドメインライフサイクル・§6 DB）
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。
--
-- 背景:
--   `atlas_skeletons`（migration 027）の共有単位（domain_key）には終端（retire）が無く、
--   実験的に凍結した骨格も未来永劫、全教員の学習マップバインド候補に並び続けてしまう。
--   L層（`library_entries.status='retired'`）の先例に倣い、domain_key 単位のメタデータ
--   テーブル `atlas_domain_meta`（migration 028）へ lifecycle 列を追加し、明示操作による
--   退場を可能にする（AB3: 削除しない。retired 状態遷移のみ・履歴は保持）。
--
--   retired の効果自体は `backend/core/atlas_store.py` 側のロジックで実装する（本
--   migration は列追加のみ）: binding propose の照合対象から除外・binding 保存時の
--   422・generate / draft 保存 / freeze の 409（読み取り専用。draft 削除のみ許可）。
--   学習者向け骨格読み取り（`load_learner_skeleton`）は lifecycle を一切見ない
--   （AB3: バインド済みコースの地図・ミニマップ表示は retire 後も不変のまま）。
--
--   meta 行の無いドメイン（同梱カートリッジで一度も generate/freeze されていない等）は
--   常に active とみなし、retire 時に初めて upsert する（`atlas_store.retire_domain`）。
--
--   単一の ALTER TABLE ... ADD COLUMN IF NOT EXISTS ... 文（CHECK 付き含む）で追加する。
--   列が既に存在する場合は IF NOT EXISTS により文全体が no-op になるため（CHECK も
--   含めて再適用されない）、この数文だけで冪等性が完結する（新規列のため、後方互換
--   DO $$ ガードは不要 — migration 050/051 と同じパターン）。

ALTER TABLE atlas_domain_meta
    ADD COLUMN IF NOT EXISTS lifecycle TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle IN ('active', 'retired'));
ALTER TABLE atlas_domain_meta ADD COLUMN IF NOT EXISTS retired_at TIMESTAMPTZ;
ALTER TABLE atlas_domain_meta ADD COLUMN IF NOT EXISTS retired_by UUID;
ALTER TABLE atlas_domain_meta ADD COLUMN IF NOT EXISTS retire_note TEXT NOT NULL DEFAULT '';
