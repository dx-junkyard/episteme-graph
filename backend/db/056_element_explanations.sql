-- Migration 056: 要素説明の二層台帳 element_explanations（Phase 2, Track A 本丸）
-- 正本: docs/features/hierarchical_context_explanation_design.md §2（E1〜E8）・§5.2
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う
-- （冪等・毎起動再実行）。すべて IF NOT EXISTS で冪等。
--
-- 全要素型（figure / theory_component / theory_claim / equation）を受けるポリモーフィック
-- 台帳（W層 element_annotations と同型の設計）。C層 component_explanations は component 専用
-- かつ course 文脈前提のため流用しない（§5.2）。
--
-- - generic（分野一般の説明。L層凍結版への confirmed identity link がある場合のみ生成・E3）と
--   contextual（この論文での位置づけ）を kind で分離するが、テーブル自体は混在させず
--   1行=1説明バージョンとして持つ（E1）。
-- - status は candidate → approved / dismissed の一方向遷移、または candidate → superseded
--   （再解析・編集時）。行削除 API は作らない（P4。document 削除経路の明示 DELETE にのみ同乗する
--   ことを想定 — element_annotations / element_identity_links と同じ orphan gap パターン）。
-- - 再解析時: 既存 candidate は superseded に遷移して保持する。approved は消さない
--   （AI 再解析が教員確定を消さない — migration 053 と同じ原則）。
-- - 編集（PATCH）時: 旧行を superseded にし、旧 evidence を引き継いだ新行を
--   created_by=user_id で INSERT する（履歴保持）。
-- - document_id は権限継承の単位（実体は documents(id) に準拠する UUID）。ポリモーフィックな
--   element_annotations / document_figures 等と異なり素朴な TEXT にしていないのは、figure 以外の
--   要素型（theory_component/theory_claim/equation）が既に document スコープであることが
--   自明であり、FK 無しの UUID 列で十分なため（figure_id 自体は element_id 側に入る）。

CREATE TABLE IF NOT EXISTS element_explanations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 権限継承の単位（実体 FK は documents に準拠。ポリモーフィック格納庫のため FK 制約は張らない）。
    document_id UUID NOT NULL,

    element_type TEXT NOT NULL
        CHECK (element_type IN ('figure', 'theory_component', 'theory_claim', 'equation')),
    -- ポリモーフィック（FK なし）。figure=document_figures.id / theory_component・theory_claim=
    -- DB UUID / equation=equations.json の equation_id（独立テーブルを持たないため element_id は
    -- document_id と組み合わせて一意）。
    element_id TEXT NOT NULL,

    kind TEXT NOT NULL CHECK (kind IN ('generic', 'contextual')),

    body TEXT NOT NULL,
    -- evidence_quote/reason/confidence（生値。API 層で段階ラベルへ変換・E6）/
    -- generic の場合は引用元（L層 entry_id + version_no）も含む。
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,

    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'approved', 'dismissed', 'superseded')),

    created_by TEXT NOT NULL DEFAULT 'pipeline',   -- 'pipeline' | user_id（教員手書きも可）
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_element_explanations_document
    ON element_explanations(document_id);
CREATE INDEX IF NOT EXISTS idx_element_explanations_element
    ON element_explanations(element_type, element_id);
