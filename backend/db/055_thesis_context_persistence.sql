-- Migration 055: Phase 1 of the hierarchical context explanation design
-- (docs/features/hierarchical_context_explanation_design.md §4).
--
-- Persists structural metadata that already exists in pipeline artifacts but
-- was previously dropped at persistence time and only lived in the JSON
-- artifact (never queryable from the DB):
--
--   - theory_components.thesis_context: role_in_thesis /
--     supports_thesis_node_ids / support_role / support_distance_to_headline_claim
--     from ComponentRecord (component_assembly agent, issue #354/#440).
--     Gap (b) in the design doc's survey.
--   - theory_claims.thesis_refs: a deterministic, non-LLM reverse lookup of
--     which thesis nodes (central_thesis / support:<section>:<idx>) cite each
--     claim, derived at persist time from thesis_reconstruction's
--     central_thesis.claim_ids / support_structure[].claim_ids. Gap (d)
--     back-link (claim -> thesis) in the design doc's survey.
--
-- No LLM involved, no agent (src/episteme_graph/agents/) touched (E8). Both
-- columns are nullable with no default: existing rows stay NULL until the
-- next re-analysis populates them (idempotent — ADD COLUMN IF NOT EXISTS).

ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS thesis_context JSONB;
ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS thesis_refs JSONB;
