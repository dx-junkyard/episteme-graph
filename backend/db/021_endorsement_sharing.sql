-- Migration 021: 承認・共有レイヤー(C層) — endorsement & sharing
--
-- 位置づけ: A層(src/episteme_graph/ の生成パイプライン、export_validation_gate 等)を
--   書き換えず、その上に「教員による査読承認」と「教員間の共有」を積む層。
--   C層は A層が出力した theory_components / theory_claims を「読む側」として実装し、
--   承認と共有の情報を新規テーブルに積む(B層=学習者体験レイヤーと同じ立場)。
--
-- 適用: このファイルが正本。backend/core/migrations.py のランナーが起動時に行う
--   （冪等・毎起動再実行）。
--
-- 設計上の確定: 承認(endorsement)は「説明バージョン(explanation)」単位で付ける。
--   「標準説明を承認したのか、A先生の説明を承認したのか」を区別できるようにするため。

-- component_explanations: 1コンポーネントに複数の説明バージョンを並存させる。
--   kind='standard' は A層/AI 由来の標準説明(遅延生成)、'personal' は教員の独自解釈。
CREATE TABLE IF NOT EXISTS component_explanations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component_id    UUID NOT NULL REFERENCES theory_components(id) ON DELETE CASCADE,
    course_id       TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL DEFAULT 'personal'
                        CHECK (kind IN ('standard', 'personal')),
    author_id       UUID REFERENCES users(id) ON DELETE SET NULL,
    title           TEXT NOT NULL DEFAULT '',
    body            TEXT NOT NULL DEFAULT '',
    -- この説明が依拠する claim(A層への紐づけ。AI候補→教員確定)
    backing_claims  JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- 元になった受講者質問(unanswered_query_logs.id。あれば)。循環の起点を追跡
    origin_query_id TEXT NOT NULL DEFAULT '',
    review_status   TEXT NOT NULL DEFAULT 'teacher_review_required'
                        CHECK (review_status IN ('teacher_review_required', 'teacher_approved', 'needs_revision', 'rejected')),
    -- 他教員が再利用・引用してよいか(共有の可否)
    shared          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_component_explanations_component ON component_explanations(component_id);
CREATE INDEX IF NOT EXISTS idx_component_explanations_author ON component_explanations(author_id);
CREATE INDEX IF NOT EXISTS idx_component_explanations_shared ON component_explanations(shared) WHERE shared = TRUE;
-- 1コンポーネントに標準説明は1つだけ(遅延生成の二重作成を防ぐ)
CREATE UNIQUE INDEX IF NOT EXISTS uq_component_explanations_standard ON component_explanations(component_id) WHERE kind = 'standard';

-- component_endorsements: 個々の教員の承認を1行ずつ記録。
--   theory_review_events(状態遷移の監査ログ)とは役割が異なり、
--   こちらは「現在有効な承認の集合」。承認は explanation 単位。
CREATE TABLE IF NOT EXISTS component_endorsements (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    explanation_id UUID NOT NULL REFERENCES component_explanations(id) ON DELETE CASCADE,
    endorser_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- 承認の種類。暫定承認と本承認を区別できるようにする
    level          TEXT NOT NULL DEFAULT 'endorsed'
                       CHECK (level IN ('provisional', 'endorsed', 'strong')),
    -- 承認者の専門性の自己申告/付与。重み合成に使う
    expertise_tag  TEXT NOT NULL DEFAULT '',
    -- 承認時点のコメント(なぜ承認したか。履歴として残す)
    note           TEXT NOT NULL DEFAULT '',
    revoked        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 1教員1説明につき1承認(同じ教員の二重カウントを防ぐ)
    UNIQUE(explanation_id, endorser_id)
);
CREATE INDEX IF NOT EXISTS idx_component_endorsements_explanation ON component_endorsements(explanation_id);
CREATE INDEX IF NOT EXISTS idx_component_endorsements_endorser ON component_endorsements(endorser_id);

-- component_citations: 他教員が承認済み説明を再利用・引用したことを帰属付きで記録。
CREATE TABLE IF NOT EXISTS component_citations (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    explanation_id    UUID NOT NULL REFERENCES component_explanations(id) ON DELETE CASCADE,
    citing_course_id  TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
    citing_user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_component_citations_explanation ON component_citations(explanation_id);

-- 承認の重みの集計ビュー(テーブルは持たず endorsements から都度算出=last-write 競合を避ける)。
CREATE OR REPLACE VIEW component_explanation_endorsement_summary AS
SELECT
    explanation_id,
    COUNT(*) FILTER (WHERE NOT revoked)                                              AS endorser_count,
    COUNT(*) FILTER (WHERE NOT revoked AND level = 'strong')                         AS strong_count,
    COUNT(*) FILTER (WHERE NOT revoked AND level = 'provisional')                    AS provisional_count,
    COUNT(DISTINCT expertise_tag) FILTER (WHERE NOT revoked AND expertise_tag <> '') AS expertise_breadth
FROM component_endorsements
GROUP BY explanation_id;
