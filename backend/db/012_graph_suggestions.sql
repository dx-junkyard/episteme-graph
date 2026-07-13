-- ============================================================
-- Migration 012: チャンク別グラフサジェストと教員向けつまづき記録
-- ============================================================
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。
-- element_type の CHECK は最初から6値（'reference' / 'citation' を含む）で作る。
-- 旧バージョンの本ファイルで4値のまま作られた既存 DB の治癒は
-- backend/db/017_tex_references_mentions.sql が担当する（本ファイルでは再定義しない）。

CREATE TABLE IF NOT EXISTS chunk_graph_mentions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id         UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    material_id      TEXT,
    element_id       TEXT NOT NULL,
    element_type     TEXT NOT NULL
                         CHECK (element_type IN ('concept', 'relationship', 'formula', 'keyword', 'reference', 'citation')),
    surface_text     TEXT NOT NULL DEFAULT '',
    importance_score REAL NOT NULL DEFAULT 0.5,
    offsets          JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chunk_id, element_id, element_type)
);

CREATE INDEX IF NOT EXISTS idx_chunk_graph_mentions_chunk
    ON chunk_graph_mentions(chunk_id);

CREATE INDEX IF NOT EXISTS idx_chunk_graph_mentions_material
    ON chunk_graph_mentions(material_id);

CREATE TABLE IF NOT EXISTS student_stumble_events (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instructor_id          UUID REFERENCES users(id) ON DELETE SET NULL,
    student_id             UUID REFERENCES users(id) ON DELETE CASCADE,
    course_id              TEXT REFERENCES learning_courses(id) ON DELETE CASCADE,
    material_id            TEXT,
    chunk_id               UUID REFERENCES chunks(id) ON DELETE SET NULL,
    element_id             TEXT,
    element_label          TEXT,
    event_type             TEXT NOT NULL
                               CHECK (event_type IN (
                                   'clicked_explain',
                                   'explanation_missing',
                                   'generated_for_student',
                                   'misconception'
                               )),
    user_message           TEXT,
    generated_explanation  TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_student_stumble_events_instructor
    ON student_stumble_events(instructor_id);

CREATE INDEX IF NOT EXISTS idx_student_stumble_events_course
    ON student_stumble_events(course_id);

CREATE INDEX IF NOT EXISTS idx_student_stumble_events_element
    ON student_stumble_events(element_id, event_type);
