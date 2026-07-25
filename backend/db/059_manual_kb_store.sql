--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。
--
-- 背景:
--   利用者マニュアル KB（help_kb, migration 0本・2026-07-25）は Phase 1/2 まで
--   「デプロイ = 凍結版の切替」（docs/manual を PR → イメージ再ビルド → 再起動）を
--   正本運用としてきた（設計は docs/features/manual_help_kb_design.md）。
--   Phase 3 では atlas_skeletons（027）/ library_entries（042）の draft/freeze
--   パターンを踏襲した DB ストアを追加するが、**着手条件は「事務職員等 git 非利用の
--   編集者の運用開始」等の将来条件**（同設計書 §7-2）であり、本 migration の適用
--   自体が配信ソースを DB に切り替えるわけではない。
--
--   `manual_kb_state.serving_source` は既定 'files' のまま据え置く。DB 配信へ切り替わる
--   のは `POST /api/admin/help-kb/freeze` を実行した後のみ（atlas_store.py が起動時
--   `import_bundled_skeletons()` で同梱データを自動的に正本化するのとは異なる設計 —
--   help_kb は Phase 1 運用（イメージ再ビルド = ファイル更新）を DB 導入後も既定として
--   維持し、DB 配信は運用者の明示操作でのみ有効化する）。
--
--   manual_kb_drafts: audience 別・ファイル単位の編集中コンテンツ（revision 楽観ロック、
--   `core/revision_store.py::update_with_revision_lock` 経由でのみ更新する）。
--   manual_kb_versions: freeze で発行される不変スナップショット（append-only。削除 API
--   なし。`validation` 列は凍結検証ゲート通過時の検証結果を保持する）。
--   manual_kb_state: 単一行（id=1固定）。配信ソースと現行 active version を持つ。
--

CREATE TABLE IF NOT EXISTS manual_kb_drafts (
    audience TEXT NOT NULL,
    file TEXT NOT NULL,
    content TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT,
    PRIMARY KEY (audience, file)
);

CREATE TABLE IF NOT EXISTS manual_kb_versions (
    version_no INTEGER PRIMARY KEY,
    files JSONB NOT NULL,
    validation JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT
);

CREATE TABLE IF NOT EXISTS manual_kb_state (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    serving_source TEXT NOT NULL DEFAULT 'files' CHECK (serving_source IN ('files', 'db')),
    active_version_no INTEGER,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 単一行の冪等シード（既に存在すれば何もしない = serving_source を巻き戻さない）。
INSERT INTO manual_kb_state (id, serving_source, active_version_no)
VALUES (1, 'files', NULL)
ON CONFLICT (id) DO NOTHING;
