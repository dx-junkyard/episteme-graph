-- ============================================================
-- Migration 017: TeX references/citations as graph mentions
-- ============================================================

ALTER TABLE chunk_graph_mentions
    DROP CONSTRAINT IF EXISTS chunk_graph_mentions_element_type_check;

ALTER TABLE chunk_graph_mentions
    ADD CONSTRAINT chunk_graph_mentions_element_type_check
    CHECK (element_type IN (
        'concept',
        'relationship',
        'formula',
        'keyword',
        'reference',
        'citation'
    ));
