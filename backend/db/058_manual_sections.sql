-- Migration 058: docs/manual ベクトル補助層（Phase 3 ①）
-- 正本: docs/features/manual_help_kb_design.md §5 Phase 3 ①
--
-- 変更概要:
--   非ベクトル索引（backend/core/help_kb/）の補助として、docs/manual の凍結節を
--   埋め込みベクトルで検索できる専用テーブルを新設する。chunks テーブルへの
--   相乗りは禁止（§1-1 — 学習質問の全域検索に無関係なマニュアル節が混入し
--   content_grounding 判定を誤らせるため）。
--
--   同期（core/help_kb/vector.py::sync_manual_vectors）は「全置換スナップショット」
--   方式: 現行の docs/manual 節集合に無い (audience, file, anchor) 行は同期処理内で
--   同一トランザクション DELETE する。孤児行（存在しない機能の古い説明）が
--   ドリフトとして残り続けることを構造的に防ぐ。
--
--   70節規模のためHNSW等のANNインデックスは作らない（IVFFlat/HNSWは行数が
--   増えたときの最適化であり、現行規模では逐次スキャンで十分高速）。
--
--   embedding は既存の pgvector 拡張・3072次元（migration 016 で導入済み）を
--   流用する。FK は張らない（他テーブルとの関係を持たない独立テーブル）。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。
-- すべて IF NOT EXISTS で冪等。

CREATE TABLE IF NOT EXISTS manual_sections (
    audience     TEXT NOT NULL CHECK (audience IN ('student', 'teacher', 'system_admin')),
    file         TEXT NOT NULL,
    anchor       TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    body         TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    embedding    vector(3072),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (audience, file, anchor)
);
