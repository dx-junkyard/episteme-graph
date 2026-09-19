-- Migration 085: 論文ディスカバリー層 — arXiv メタデータ（外部事実）のキャッシュ
--
-- 設計正本: docs/features/paper_radar_design.md §14「arXiv 呼び出しの上限」。
-- 親: docs/features/paper_discovery_design.md（PD1〜PD8。特に PD5 候補を保存しない /
-- PD7 外部 API の行儀）。同型の先例: backend/db/077_paper_discovery_reference_cache.sql。
-- このファイルが DDL の正本。適用は `backend/core/migrations.py` のランナーが起動時に
-- 行う（冪等・毎起動・番号順に再実行）。すべて IF NOT EXISTS で冪等。
--
-- 何を保存するか:
--   arXiv API が公開している論文1本のメタデータ（タイトル・要旨・カテゴリ・著者・
--   日付・URL）の写し。1行 = 論文1本。キーは version 抜きの正規化 arXiv ID
--   （core/paper_discovery/schema.py::normalize_arxiv_id と同じ規則）。
--
-- 設計上の要点:
--   1. **CC3 と同型の設計明示例外**（077 の判断をそのまま引く）。発見層は候補・レンズ
--      判定を保存しない（PD5）が、**外部 API が公開している事実の写し**は保存する。
--      教員の判断でも候補一覧のスナップショットでもない（`documents.source_url` と
--      同じ「事実の記帳」側）。保存するのは arXiv が返した値だけで、教員が何を見たか・
--      何を選んだか・どの候補が新着だったかは1ビットも入らない。
--   2. **理由は外部 API の行儀（PD7）と教員の待ち時間**（2026-09-14 オーナー指示）。
--      教員の一連の操作（開く → 検索 → 比較）で arXiv API を叩くのは最大2回に抑える。
--      画面を開き直すたびに同じ論文のメタデータを引き直すのは、相手にも筋が悪い。
--      陳腐化は TTL（DISCOVERY_ARXIV_METADATA_TTL_DAYS、既定30日）で抑える。
--   3. **行削除をしない**。更新は upsert（ON CONFLICT DO UPDATE）で、core 側にも
--      DELETE 文を置かない（既存ガードレールが core ツリー全体を検査する）。
--   4. **シードしない**。毎起動・全再実行方式のため、初期行を INSERT すると
--      キャッシュの更新が再起動で巻き戻る（070 / 077 と同じ判断）。
--   5. **FK なし・users 参照なし**。メタデータは特定の document にも特定の教員にも
--      従属しない外部事実で、キーは正規化 arXiv ID である。users を参照しないので
--      account_lifecycle の PURGE/RETAIN 宣言も不要。
--   6. **取得の失敗は保存しない**。077 と違い fetch_status 列を持たない。失敗の抑制は
--      arxiv_client 側の 429 クールダウン（ARXIV_RATE_LIMIT_COOLDOWN_SECONDS）が
--      担当し、キャッシュには「読めた事実」だけが入る（読めなかった記録を
--      「読んだ結果」と取り違えない）。
--   7. **version を保存しない**。キーが version 抜きの正規化 ID なので、版番号を
--      持たせると「この版のメタデータ」と読めてしまう（v1/v2 は同一論文 — 設計書 §4.1）。

CREATE TABLE IF NOT EXISTS paper_discovery_arxiv_metadata_cache (
    -- 正規化済み（version 抜き）の arXiv ID。
    arxiv_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    -- ArxivEntry.categories（文字列の配列）。
    categories JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- ArxivEntry.primary_category（未指定は空文字。DTO 側の None と対応する）。
    primary_category TEXT NOT NULL DEFAULT '',
    -- ArxivEntry.authors（文字列の配列）。
    authors JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- ArxivEntry.published / updated（解釈できなければ NULL のまま保持する）。
    published_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NULL,
    abs_url TEXT NOT NULL DEFAULT '',
    pdf_url TEXT NOT NULL DEFAULT '',
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- TTL の絞り込み（新鮮な行だけを読む read-through）。
CREATE INDEX IF NOT EXISTS idx_paper_discovery_arxiv_metadata_cache_fetched_at
    ON paper_discovery_arxiv_metadata_cache (fetched_at);
