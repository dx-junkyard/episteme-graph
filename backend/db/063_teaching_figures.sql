-- Migration 063: 教材図スタジオ（Teaching Figure Studio）の生成図とギャップ候補
-- 正本: docs/features/teaching_figure_studio_design.md §3.1 / §3.2
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う
-- （冪等・毎起動・番号順に再実行）。すべて IF NOT EXISTS で冪等。
--
-- 設計上の要点:
--   1. FG1 A層非改変: `document_figures`（migration 041 系の解析パイプライン成果
--      レジストリ）には列を足さない。生成図はライフサイクルが違う（コース教材の
--      付属物）ので独立テーブルに積む。ID は両方 UUID なので、配信側の
--      figures_by_id マップ上で衝突しない。
--   2. FG7 情報を落とさない: 削除 API は作らない（`draft → adopted → retired` の
--      状態遷移のみ）。SVG の改訂は `revisions` JSONB へ append する。
--      提案の却下も `dismissed` 遷移で保持する。
--   3. course_id / topic_id は FK を張らない（既存慣例。course_id は
--      learning_courses.id の TEXT 正規化表現、topic_id は data JSONB 内のキー）。
--      孤児掃除はコース物理削除の**5経路すべて**が明示 DELETE で担う
--      （object_group_permissions と同じ orphan gap パターン）:
--        ① api/services.py::delete_course_data（learning 側の DELETE 経路）
--        ② core/versioning/deletion.py::_purge_course（V層スイーパ）
--        ③ 同 _purge_document 内のコース削除（document 巻き添え）
--        ④ api/routes/admin.py::delete_material（教材削除の巻き添えコース削除。
--           教員の主経路）
--        ⑤ api/routes/admin.py::delete_course（管理画面のコース削除ボタンの実体）
--      ※ 設計書 §3.1 は「3経路」と書いているが、実装には ④⑤ が別に存在する
--        （2026-07-31 の実装時に確認。④⑤ を抜くと教員が管理画面から消した分の
--        孤児が残る）。コード側は5経路すべて対応済みで、構造検査は
--        backend/tests/test_teaching_figures_api.py::TestOrphanCleanupPaths が担う。
--      MinIO オブジェクトの削除は StorageManager.remove_object に委ねる
--      （best-effort・commit 後に実行し、失敗しても DB 削除は止めない）。
--   4. FG8 数値を見せない: `teaching_figure_suggestions.confidence` は DB のみに
--      置き、API は段階ラベル（core/teaching_figures/schema.py::confidence_label）
--      へ変換して返す。

-- ----------------------------------------------------------------------------
-- 1. course_teaching_figures — 生成図の正本（§3.1）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS course_teaching_figures (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id       TEXT NOT NULL,                 -- learning_courses.id（TEXT 正規化、FK なし=既存慣例）
    topic_id        TEXT,                          -- 作成時の対象トピック（provenance。移動可のため制約にしない）
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    title           TEXT NOT NULL DEFAULT '',
    caption         TEXT NOT NULL DEFAULT '',      -- 学習者に見えるキャプション
    figure_kind     TEXT NOT NULL DEFAULT 'concept_map'
                    CHECK (figure_kind IN ('concept_map','process_flow','structure_diagram',
                                           'comparison','timeline','coordinate','data_plot_schematic',
                                           'other')),
    svg_source      TEXT NOT NULL,                 -- サニタイズ済み SVG（正本）
    minio_key       TEXT NOT NULL,                 -- figure-images バケット内キー（配信スナップショット）
    content_type    TEXT NOT NULL DEFAULT 'image/svg+xml',
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','adopted','retired')),
    source_suggestion_id UUID,                     -- 提案起点の場合の由来（FK なし・provenance のみ）
    revisions       JSONB NOT NULL DEFAULT '[]',   -- [{svg_source, caption, updated_at, updated_by}] 旧版
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 配信解決（status='adopted' のコース単位マージ）と挿入タブ一覧が引く経路。
CREATE INDEX IF NOT EXISTS idx_teaching_figures_course
    ON course_teaching_figures (course_id, status);

-- ----------------------------------------------------------------------------
-- 2. teaching_figure_suggestions — ギャップ候補（candidate 層、§3.2）
-- ----------------------------------------------------------------------------
-- 再生成時は既存 candidate を 'superseded' に遷移する（#496 / discuss_opening と
-- 同じ原則。accepted / dismissed は教員の判断済みなので不変）。
-- anchor_excerpt は本文の逐語部分文字列であることを validator で強制する
-- （discuss_opening の evidence_quote verbatim 検査と同型の捏造ガード）。
CREATE TABLE IF NOT EXISTS teaching_figure_suggestions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id       TEXT NOT NULL,
    topic_id        TEXT NOT NULL,
    anchor_excerpt  TEXT NOT NULL DEFAULT '',      -- 本文からの逐語引用（挿入位置の手がかり。捏造ガード）
    gap_reason      TEXT NOT NULL DEFAULT '',      -- 「なぜここが分かりづらいか」の事実文
    signal_basis    TEXT NOT NULL DEFAULT 'text_analysis'
                    CHECK (signal_basis IN ('text_analysis','learner_signals','both')),
    figure_kind     TEXT NOT NULL,                 -- 3.1 と同語彙
    figure_brief    TEXT NOT NULL DEFAULT '',      -- 「何をどう描くか」の一文
    confidence      REAL,                          -- DB のみ。API は段階ラベル（FG8）
    status          TEXT NOT NULL DEFAULT 'candidate'
                    CHECK (status IN ('candidate','accepted','dismissed','superseded')),
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_teaching_figure_suggestions_topic
    ON teaching_figure_suggestions (course_id, topic_id, status);
