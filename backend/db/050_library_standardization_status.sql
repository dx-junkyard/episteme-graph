-- Migration 050: 標準化判定 worker の確定先（Phase S, 知識ネットワークビジョン §6/§7）
-- 正本:
--   docs/features/knowledge_network_vision.md（§3 修正③ 標準化判定は三角測量・§6 実装方針・§7 Phase S）
--   docs/features/element_deliberation_workspace_design.md（§5.5 標準化判定・§6 element_annotations.kind='standardization'）
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。
--
-- 背景:
--   標準化判定 worker（core/deliberation/standardization/）は三角測量（①LLM 事前知識・
--   ②L層凍結版類似・③コーパス内反復）の結果を、常に candidate の element_annotations
--   （kind='standardization'）へ書く（KN-3: LLM が直接確定させない）。教員が候補を確定
--   （commit）すると、その確定先としてこの列へ反映される
--   （core/deliberation/annotations.py の commit ルーティング）。
--
--   standardization_status は library_entries の「ガバナンス列」として扱う。draft 本文編集
--   （core/library/store.py の UPDATABLE_FIELDS・revision 楽観ロック）とは意味・更新経路が
--   異なるため、revision 列は変更しない・増やさない（本文編集の楽観ロックと、標準化判定の
--   確定は独立した関心事という判断。§5.5 決定）。
--
--   単一の ALTER TABLE ... ADD COLUMN IF NOT EXISTS ... CHECK(...) 文で追加する。列が既に
--   存在する場合は IF NOT EXISTS により文全体が no-op になるため（CHECK も含めて再適用
--   されない）、この1文だけで冪等性が完結する（新規列のため、後方互換 DO $$ ガードは不要）。

ALTER TABLE library_entries
    ADD COLUMN IF NOT EXISTS standardization_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (standardization_status IN ('standard', 'field_standard', 'emerging_common', 'novel', 'unknown'));
