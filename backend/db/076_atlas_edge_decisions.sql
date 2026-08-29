-- Migration 076: 分野マップの関係表示（辺候補レビュー, RE層追補）
--
-- 設計正本: docs/features/atlas_relation_edges_design.md §3（不変条項 RE1〜RE8 は §2）。
-- 親: docs/architecture/field_map_display_principles_2026-08-29.md（原則①′）。
-- このファイルが DDL の正本。適用は `backend/core/migrations.py` のランナーが起動時に
-- 行う（冪等・毎起動・番号順に再実行）。すべて IF NOT EXISTS で冪等。
--
-- 骨格の辺（SkeletonEdge: adjacent / depends / related）は現在、教員が draft を手書き
-- する経路でしか増えない。本層は VA層のアンカーベクトル（保存済み）と配置データから
-- 「この2概念は近い / 同じ論文群で共起する」という**関係の候補**を読み時に導出し、
-- 教員の判断だけを行として持つ。
--
-- 設計上の要点:
--   1. **候補スナップショットを持たない**（RE6）。候補は毎回導出（gap 候補・PD5 と
--      同じ）で、行として残すのは教員の判断だけ。したがって本 migration が作るのは
--      判断1表のみで、documents / atlas_skeletons への FK も持たない
--      （判断は分野の共同財であり、特定の論文・特定の版に従属しない）。
--   2. `edge_key` は **無向・版非依存**（`edge|{domain_key}|{min}|{max}`）。
--      無向にするのは「A—B」と「B—A」を別の判断にしないため。版非依存なのは
--      gap decisions の cluster_key と同じ §4.2 裁定（凍結のたびに却下済み候補が
--      蘇る「ゾンビ候補」を防ぐ）。語彙・導出の正本は
--      backend/core/atlas_edges/schema.py（CHECK 語彙と一対一で一致させること）。
--   3. status は candidate / accepted / dismissed の3語彙のみ（gap の merged は
--      辺には無い）。**行削除はしない**（RE5 / P4）— 見送りは 'dismissed' への遷移、
--      その取り消しは 'candidate' への遷移で表す。遷移は
--      core/candidate_flow.py の CandidateFlow を通す（本番初適用）。
--   4. `edge_kind` は採用時に**教員が選ぶ**辺種別（core/atlas.py::EDGE_KINDS）。
--      AI 候補は種別を主張しない（RE3: 恒久配線への経路は教員確定のみ）ため、
--      既定は空文字であり、accepted のときだけ非空になる。
--   5. `applied_version` は「採用」と「凍結で実際に反映された」の分離（gap 同型）。
--      「draft に入ったか」は draft の edges に無向ペアが実在するかで判定するので、
--      gap の draft_node_id にあたる列は持たない（二重管理しない）。
--   6. **シードしない**。毎起動・全再実行方式のため、初期行を INSERT すると
--      教員が判断を変えても再起動で復活する（070 url_fetch_domains と同じ判断）。
--   7. RE3 / AB4 / KN-3: 本層のコードから atlas_skeletons への INSERT / UPDATE は
--      存在しない。draft への反映は教員の既存 PUT（revision 楽観ロック）だけである。

CREATE TABLE IF NOT EXISTS atlas_edge_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- edge|{domain_key}|{min(node_a,node_b)}|{max(node_a,node_b)}（無向・版非依存）
    edge_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'candidate'
        CONSTRAINT atlas_edge_decisions_status_check CHECK (status IN
            ('candidate', 'accepted', 'dismissed')),
    -- 採用時に教員が選ぶ辺種別（adjacent/depends/related。accepted のとき必須）
    edge_kind TEXT NOT NULL DEFAULT '',
    review_note TEXT NOT NULL DEFAULT '',
    -- 凍結で実際に反映された骨格の版（'' = 未反映。採用と反映の分離 — gap と同型）
    applied_version TEXT NOT NULL DEFAULT '',
    decided_by UUID REFERENCES users(id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_atlas_edge_decisions_status
    ON atlas_edge_decisions (status);

-- 凍結前ゲート（採用済みで未反映の辺）の絞り込み用。
CREATE INDEX IF NOT EXISTS idx_atlas_edge_decisions_pending_apply
    ON atlas_edge_decisions (edge_key)
    WHERE status = 'accepted' AND applied_version = '';
