-- Migration 087: 図画像レジストリ — 最新の抽出で作られなかった行を「superseded」にする
--
-- 設計正本: docs/features/image_pipeline_knowledge_library_design.md §17-7 / §18。
-- このファイルが DDL の正本。適用は `backend/core/migrations.py` のランナーが起動時に
-- 行う（冪等・毎起動・番号順に再実行）。
--
-- 何のためか:
--   図の抽出（figure_image_extraction）は document ごとに全図を upsert するが、前回の
--   抽出で作られ今回は作られなかった行（領域推定の改訂で key が変わった図・部品画像の
--   断片・位置の取れない caption から作った画像なしの行）が、そのまま「図・画像」一覧に
--   残っていた（2026-09-24 実測: 学位論文 1 本で 35 行）。
--   行は消さない（教員のレビュー列・参照を保つ, P4）。抽出が最後まで通ったときに、
--   その回に作られなかった行を status='superseded' にし、一覧・検出要素・G層の
--   To-Do は status='extracted' の行だけを数える。同じ key が次の抽出で再び作られれば
--   upsert が status を 'extracted' に戻す。
--
-- 冪等性: 現在の CHECK 定義に 'superseded' が無いときだけ張り直す。

DO $$
DECLARE
    current_def TEXT;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO current_def
    FROM pg_constraint
    WHERE conrelid = 'document_figures'::regclass
      AND conname = 'document_figures_status_check';

    IF current_def IS NULL OR position('superseded' IN current_def) = 0 THEN
        IF current_def IS NOT NULL THEN
            ALTER TABLE document_figures DROP CONSTRAINT document_figures_status_check;
        END IF;
        ALTER TABLE document_figures ADD CONSTRAINT document_figures_status_check
            CHECK (status IN ('extracted', 'failed', 'superseded'));
    END IF;
END $$;
