-- Migration 069: LLM 利用実績のユーザー別照会（アカウントライフサイクル管理 Phase 2）
-- 正本: docs/features/account_lifecycle_management_design.md §3.3 / §7-2
--
-- 目的: llm_usage_events.user_id によるユーザー別集計軸を core/llm_usage/metrics.py の
-- group_by に追加する（§7-2）。本インデックスはその集計クエリ（user_id 一致 WHERE +
-- occurred_at 範囲）を下支えする。user_id IS NULL（バッチ/worker 系の「未帰属」行）は
-- ユーザー別集計の対象外のため部分インデックスにして肥大化を避ける。
--
-- 043_llm_usage_events.sql は既存正本のため編集しない（新番号ファイルで追加する。
-- U6 は行削除 API の禁止であり DDL 追加は制約しない）。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う
-- （冪等・毎起動再実行）。

CREATE INDEX IF NOT EXISTS idx_llm_usage_events_user
    ON llm_usage_events(user_id, occurred_at) WHERE user_id IS NOT NULL;
