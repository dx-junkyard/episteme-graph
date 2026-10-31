-- Migration 096: 分野マップの関係（RE層）— 採用の判断に「記載の向き」を持つ（IK-1149・第 25 周 第 11 波）
--
-- 有向の関係「依存」の向きは、教員が選ぶのではなく edge_key（無向・版非依存:
-- edge|{domain}|{min}|{max}）の正規化順で決まり、そのまま下書き・公開版の from → to になっていた。
-- 一意キーは無向のまま（「A—B」と「B—A」を別の判断にしない・見送りのゾンビ候補を防ぐ — 076 の裁定）、
-- 向きは判断の列として持つ:
--
--   from_node_id  記載の始点（依存では「依存する側」）。教員が採用のときに選ぶ
--   to_node_id    記載の終点（依存では「依存される側」）
--
-- 既存行は NULL のまま = 「向きの記録なし」（採用のときに向きを選ぶ手が無かった）。推測で埋めない —
-- 表示は従来の並び（node_id の辞書順）を使い、選ばれていないことを事実文で言う。
-- 行は削除しない（RE5 / P4）。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。

ALTER TABLE atlas_edge_decisions
    ADD COLUMN IF NOT EXISTS from_node_id TEXT;
ALTER TABLE atlas_edge_decisions
    ADD COLUMN IF NOT EXISTS to_node_id TEXT;
