-- Migration 077: コーパスを補う論文 — レンズC（基盤論文）の参照リストキャッシュ
--
-- 設計正本: docs/features/corpus_complement_design.md §5.4（不変条項 CC1〜CC8 は §2）。
-- 親: docs/features/paper_discovery_design.md（PD1〜PD8。特に PD5 候補を保存しない /
-- PD7 外部 API の行儀）。
-- このファイルが DDL の正本。適用は `backend/core/migrations.py` のランナーが起動時に
-- 行う（冪等・毎起動・番号順に再実行）。すべて IF NOT EXISTS で冪等。
--
-- 何を保存するか:
--   取り込み済みの arXiv 論文1本が**引用している**論文の一覧（Semantic Scholar の
--   graph API が公開しているメタデータの写し）。1行 = 引用している側1本。
--
-- 設計上の要点:
--   1. **CC3 の設計明示例外**。本層は候補・レンズ判定を保存しない（PD5 継承）が、
--      参照リストだけは保存する。これは**外部 API が公開している事実の写し**であって、
--      教員の判断でも候補一覧のスナップショットでもない（`documents.source_url` と
--      同じ「事実の記帳」側）。理由は外部 API の行儀（PD7）— 1シードあたり数十〜百件の
--      参照を教員が押すたびに引き直すのは、相手にも待ち時間にも筋が悪い。
--      陳腐化は TTL（DISCOVERY_REFERENCE_CACHE_TTL_DAYS、既定30日）で抑える。
--   2. **行削除をしない**。更新は upsert（ON CONFLICT DO UPDATE）で、core 側にも
--      DELETE 文を置かない（既存ガードレールが core ツリー全体を検査する）。
--   3. **シードしない**。毎起動・全再実行方式のため、初期行を INSERT すると
--      キャッシュの更新が再起動で巻き戻る（070 url_fetch_domains と同じ判断）。
--   4. **FK なし・users 参照なし**。参照リストは特定の document にも特定の教員にも
--      従属しない外部事実で、キーは正規化 arXiv ID（version 抜き）である。
--      users を参照しないので account_lifecycle の PURGE/RETAIN 宣言も不要。
--   5. `fetch_status = 'failed'` の行は「TTL 内は再取得しない」ための記録
--      （外部 API を叩き続けない）。読み出し側は 'ok' の行だけを「読めた」と扱う。
--   6. 列名は `reference_entries`。`references` は PostgreSQL の予約語で、引用符なしに
--      列名として使えないため（設計書 §5.4 の SQL からの逸脱点はこの1点のみ）。

CREATE TABLE IF NOT EXISTS paper_discovery_reference_cache (
    -- 引用している側（取り込み済みシード）の正規化 arXiv ID（version 抜き）
    arxiv_id TEXT PRIMARY KEY,
    -- CitationEntry.to_dict() の列（arXiv ID を持つ参照のみ）。失敗時は空配列。
    reference_entries JSONB NOT NULL DEFAULT '[]'::jsonb,
    fetch_status TEXT NOT NULL DEFAULT 'ok'
        CONSTRAINT paper_discovery_reference_cache_status_check
            CHECK (fetch_status IN ('ok', 'failed')),
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- TTL の絞り込み（新鮮な 'ok' 行だけを読む read-through）。
CREATE INDEX IF NOT EXISTS idx_paper_discovery_reference_cache_fetched_at
    ON paper_discovery_reference_cache (fetched_at);
