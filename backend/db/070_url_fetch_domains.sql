-- Migration 070: URL指定による教材取得 — 取得先ドメインの許可リスト
--
-- 目的: 教員が論文の URL（例 https://arxiv.org/src/1711.03050 = TeX tar.gz /
-- https://arxiv.org/pdf/1711.03050 = PDF）を指定すると、サーバがダウンロードして
-- 既存のアップロードパイプラインへ流す。サーバ側から任意の URL を取得する経路は
-- SSRF の入口になるため、**取得先ドメインを SYSTEM_ADMIN が管理する許可リストで
-- 制限する**。本テーブルがその許可リストの正本。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う
-- （冪等・毎起動・番号順に再実行）。テーブルは IF NOT EXISTS で冪等。
--
-- 設計上の要点:
--   1. **シード行を入れない。** 毎起動で全ファイルが再実行されるため、初期ドメインを
--      INSERT すると「管理者が削除した行が次の再起動で復活する」。初期状態は空 =
--      URL 取得機能は無効（fail-closed）で、SYSTEM_ADMIN が明示登録して初めて使える。
--   2. `domain` は正規化済みホスト名（小文字・scheme/path/port なし）を主キーにする。
--      正規化の正本は `backend/core/url_fetch.py::normalize_domain`。
--   3. `added_by` に FK を張らない — 登録した管理者が後に墓標化されうるため
--      （AL1 / migration 068 §3.1 と同じ理由）。表示は LEFT JOIN users で解決する。

CREATE TABLE IF NOT EXISTS url_fetch_domains (
    domain TEXT PRIMARY KEY,
    added_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
