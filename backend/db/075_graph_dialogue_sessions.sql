-- Migration 075: グラフ対話レビュー — deliberation_sessions に 'document_graph' を追加
-- 正本: docs/features/graph_dialogue_review_design.md §5
--
-- 変更概要:
--   deliberation_sessions.element_type の CHECK に 'document_graph' を追加する。
--   'document_graph' はグラフ対話レビュー画面の「グラフ全体対話」用の疑似要素型で、
--   element_id / document_id はどちらも documents.id の正規化済みテキスト表現。
--
--   ※ element_annotations の CHECK は **変更しない** — グラフ全体対話は候補注釈を
--     生成しない（設計書 GR: 注釈=要素単位の commit ルーティング前提。全体対話は
--     見取り図の検討に限定し、確定操作は要素単位に降りてから行う）。
--   ※ ElementRef（core/deliberation/schema.py の ELEMENT_TYPES）にも加えない —
--     overview / context / annotations / identity の解決対象にしない（既存の未知
--     element_type 拒否がそのまま効く）。セッションの作成・参照は
--     core/deliberation/graph_dialogue.py 専用のプリミティブが行う。
--   孤児掃除は既存の document 削除経路が element_type を問わず document_id で消すため
--   追加不要（migration 064 と同じ理由）。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う
-- （冪等・毎起動再実行）。DO ブロックの存在チェックで冪等。

-- ----------------------------------------------------------------------------
-- deliberation_sessions.element_type の CHECK 差し替え（migration 064 と同型の手順）
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'deliberation_sessions_element_type_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%%document_graph%%'
    ) THEN
        ALTER TABLE deliberation_sessions
            DROP CONSTRAINT deliberation_sessions_element_type_check;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'deliberation_sessions_element_type_check'
    ) THEN
        ALTER TABLE deliberation_sessions
            ADD CONSTRAINT deliberation_sessions_element_type_check
            CHECK (element_type IN (
                'figure', 'theory_component', 'theory_claim', 'equation',
                'evidence', 'derivation', 'shared_part', 'document_graph'
            ));
    END IF;
END $$;
