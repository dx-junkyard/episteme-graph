-- Migration 071: 論文ディスカバリー層（arXiv 分野購読とコーパス成長ループ）Phase 1
--
-- 目的: 分野（atlas ドメイン / cartridge_id 名前空間）を単位に arXiv の購読条件
-- （カテゴリ + キーフレーズ + 著者フォロー）を持ち、候補論文の一覧から教員が選んだ
-- ものだけを既存の URL 取得・解析パイプライン（migration 070 / UF1〜UF6）へ流す。
--
-- 設計正本: docs/features/paper_discovery_design.md（不変条項 PD1〜PD8、§4.1 DB）。
-- このファイルが DDL の正本。適用は `backend/core/migrations.py` のランナーが起動時に
-- 行う（冪等・毎起動・番号順に再実行）。
--
-- 設計上の要点:
--   1. **シード行を入れない**（UF2 継承）。毎起動で全ファイルが再実行されるため、
--      初期購読を INSERT すると「教員が消した条件が次の再起動で復活する」。購読は
--      教員の意思の正本であり、サーバが勝手に書かない（PD3）。
--   2. **候補一覧のテーブルを持たない**（PD5）。保存するのは購読条件・見送り記録・
--      取り込み出所（`documents.source_url`）だけで、候補は毎回 arXiv API から
--      読み時導出する。完了フラグ・候補スナップショットを作らない（G1 と同じ思想）。
--   3. 見送りは行削除せず `revoked` 遷移で保持し、復帰できる（P4 / PD5）。
--      そのため本層のコード（`backend/core/paper_discovery/store.py`）には
--      `DELETE FROM` が無く、ガードレールが構造的に固定する。
--   4. `updated_by` / `dismissed_by` に FK を張らない — 操作した教員が後に墓標化
--      されうるため（AL1 / migration 068 §3.1、070 の `added_by` と同じ理由）。
--      表示は LEFT JOIN users で解決する。
--   5. `arxiv_id` は **version サフィックスを除いた**正規化 ID（例: `2608.20293`）。
--      正規化の正本は `backend/core/paper_discovery/schema.py::normalize_arxiv_id`。
--      version 違い（v1/v2）は同一論文とみなす。

-- 分野購読（分野単位1行の教員共同財。L層ライブラリと同じ立場）
CREATE TABLE IF NOT EXISTS paper_discovery_subscriptions (
    domain_key        TEXT PRIMARY KEY,
    arxiv_categories  TEXT[] NOT NULL DEFAULT '{}',
    -- keyphrases 要素: {"text": "...", "source": "skeleton"|"cartridge"|"component"|"manual",
    --                   "enabled": true} — 供給元の明示（PD3）。外した状態も保持する（P4）
    keyphrases        JSONB  NOT NULL DEFAULT '[]',
    followed_authors  JSONB  NOT NULL DEFAULT '[]',
    updated_by        UUID,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_checked_at   TIMESTAMPTZ
);

-- 見送り記録（行削除せず revoked 遷移で復帰 — P4 / PD5）
CREATE TABLE IF NOT EXISTS paper_discovery_dismissals (
    domain_key    TEXT NOT NULL,
    arxiv_id      TEXT NOT NULL,
    dismissed_by  UUID,
    dismissed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked       BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (domain_key, arxiv_id)
);

-- 取り込み出所（「取り込み済み」判定の正本 — PD5 の読み時導出に使う）。
-- URL 経由の取り込み（手動の「URLから取得」とディスカバリー経由の両方）が保存する。
-- 既存行は NULL のままで良く、UI は「URL 経由で取り込まれた論文のみ判定できます」の
-- 事実を注記する（手動アップロードされた同一論文は判定できない — 偽装しない、PD6）。
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_url TEXT;
