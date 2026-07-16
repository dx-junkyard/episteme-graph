-- Migration 051: 図中ラベル抽出（装置図理解機能の拡張, Phase 1 サブタスク A）
-- 正本: docs/features/image_pipeline_knowledge_library_design.md（装置図理解拡張, §4-1/§4-2
--       の設計意図。旧設計書 apparatus_diagram_understanding_design.md では番号 044 だったが、
--       これは旧スナップショット時点の番号であり、現行の連番では 051 が正しい）
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。
--
-- 背景:
--   PDF の図領域内に埋まっているテキストラベル（TikZ 系ベクター図の "ECDL" / "EOM" / "PBS" 等）を
--   決定論的・非LLM で抽出し、document_figures.inner_labels に保存する（G2 ギャップの解消）。
--   抽出は core/document_pipeline/figure_images.py の `_extract_inner_labels` が行い、
--   figure_image_extraction ステージ（非LLM・決定論的）に相乗りする。apparatus_semantics
--   （vision LLM）はこの列を読んで part の label_ref 突合に使う（本 migration の範囲外）。
--
--   単一の ALTER TABLE ... ADD COLUMN IF NOT EXISTS ... 文で追加する。列が既に存在する場合は
--   IF NOT EXISTS により文全体が no-op になるため、この1文だけで冪等性が完結する
--   （新規列のため、後方互換 DO $$ ガードは不要）。

ALTER TABLE document_figures ADD COLUMN IF NOT EXISTS inner_labels JSONB NOT NULL DEFAULT '[]'::jsonb;
