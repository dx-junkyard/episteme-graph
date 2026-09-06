-- Migration 072: 論文ディスカバリー層 Phase 2 — バッチ取り込みキュー
--
-- 目的: 教員が選んだ候補を「まとめてキューへ積み、あとは非同期で 1 件ずつ取得・受理する」
-- ための取り込みキュー。Phase 1 の同期 `POST /ingest`（1リクエスト5件まで）を置き換えず、
-- まとまった件数を流す経路として並置する。
--
-- 設計正本: docs/features/paper_discovery_design.md §5（Phase 2）。不変条項 PD1〜PD8。
-- このファイルが DDL の正本。適用は `backend/core/migrations.py` のランナーが起動時に
-- 行う（冪等・毎起動・番号順に再実行）。
--
-- 設計上の要点:
--   1. **シード行を入れない**（UF2 / 071 継承）。毎起動で全ファイルが再実行されるため、
--      行を投入すると教員が処理し終えた項目が再起動で復活する。
--   2. **キューに積むのは教員の明示操作だけ**（PD1）。行を作る経路は
--      `POST /api/admin/discovery/ingest-batch`（教員のリクエスト）のみで、
--      検索・worker・スケジューラから行が生まれる経路は作らない。ここに積まれた行は
--      「教員が承認済みである」という事実そのものなので、worker は arXiv を検索しない。
--   3. **候補一覧のテーブルではない**（PD5）。保持するのは承認された取り込み指示だけで、
--      候補のスナップショットではない。取り込み済み判定の正本は引き続き
--      `documents.source_url`（071）。
--   4. 失敗行は削除せず `status='failed'` + `detail` で保持し、再試行は教員の明示操作
--      （`POST .../ingest-queue/{id}/retry`）のみ（P4 / PD1）。そのため
--      `backend/core/paper_discovery/ingest_queue.py` には `DELETE FROM` が無く、
--      ガードレールが構造的に固定する。
--   5. `requested_by` に FK を張らない — 操作した教員が後に墓標化されうるため
--      （AL1 / migration 068 §3.1、070 の `added_by`・071 の `updated_by` と同じ理由）。
--   6. `arxiv_id` は **version サフィックスを除いた**正規化 ID（071 と同じ）。正規化の
--      正本は `backend/core/paper_discovery/schema.py::normalize_arxiv_id`。
--   7. `detail` は日本語の事実文のみ（解決した IP・スタックトレースを入れない — UF6 継承）。

CREATE TABLE IF NOT EXISTS paper_discovery_ingest_items (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_key     TEXT NOT NULL DEFAULT 'arxiv',
    arxiv_id       TEXT NOT NULL,
    source_url     TEXT NOT NULL,
    title          TEXT,
    requested_by   UUID,
    analyze_images BOOLEAN NOT NULL DEFAULT FALSE,
    models         JSONB,
    -- queued   : 取得待ち（worker が claim する対象）
    -- fetching : worker が取得・受理を実行中
    -- accepted : 既存アップロードパイプラインへ受理済み（以後の進捗は教材一覧の status が正本）
    -- failed   : 失敗（行は消さず detail に事実文を残す。再試行は教員の明示操作のみ）
    status         TEXT NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued', 'fetching', 'accepted', 'failed')),
    detail         TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    material_id    TEXT,
    task_id        TEXT,
    requested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at     TIMESTAMPTZ,
    finished_at    TIMESTAMPTZ
);

-- worker の claim（最古の queued を1件）と、再起動で置き去りになった fetching の回収。
CREATE INDEX IF NOT EXISTS idx_pd_ingest_items_pending
    ON paper_discovery_ingest_items (status, requested_at)
    WHERE status IN ('queued', 'fetching');

-- 重複投入の判定（同一 arXiv ID の queued / fetching が既にあるか）と一覧の並び。
CREATE INDEX IF NOT EXISTS idx_pd_ingest_items_arxiv
    ON paper_discovery_ingest_items (arxiv_id);
