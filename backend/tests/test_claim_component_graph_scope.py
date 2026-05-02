"""Regression checks for Claim / Component / Graph scope separation."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "backend" / "api" / "schemas.py"
ROUTES = ROOT / "backend" / "api" / "routes" / "theory_components.py"
API_MAIN = ROOT / "backend" / "api" / "main.py"
MIGRATION = ROOT / "backend" / "db" / "013_theory_components.sql"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
ADMIN_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
DOCUMENT_SECTIONS = ROOT / "backend" / "core" / "document_sections.py"
CONCEPT_NORMALIZER = ROOT / "backend" / "core" / "concept_normalizer.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_claim_component_graph_models_exist():
    source = _read(SCHEMAS)
    assert "class ClaimOut" in source
    assert "class TheorySourceScope" in source
    assert "source_scope: TheorySourceScope" in source
    assert "evidence_claims: list[str]" in source
    assert "maturity_level: str" in source
    assert "maturity_source: str" in source
    assert "review_status: str" in source
    assert "class ComponentGraphResponse" in source


def test_claim_component_graph_tables_and_columns_exist():
    sql = _read(MIGRATION)
    assert "CREATE TABLE IF NOT EXISTS theory_claims" in sql
    assert "source_scope       JSONB" in sql
    assert "support_status     TEXT" in sql
    assert "evidence_text      TEXT" in sql
    assert "review_status      TEXT" in sql
    assert "ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS evidence_claims" in sql
    assert "ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS maturity_level" in sql


def test_scope_api_routes_exist():
    source = _read(ROUTES)
    assert '"/documents/{document_id}/chunks/{chunk_id}/claims"' in source
    assert '"/documents/{document_id}/chunks/{chunk_id}/claims/extract"' in source
    assert '"/claims/{claim_id}"' in source
    assert '"/documents/{document_id}/sections/{section_id}/components"' in source
    assert '"/documents/{document_id}/sections/{section_id}/components/assemble"' in source
    assert '"/documents/{document_id}/component-graph"' in source
    assert '"/courses/{course_id}/claims/extract-all"' in source
    assert '"/courses/{course_id}/components/assemble-all"' in source
    assert '"/courses/{course_id}/component-graph/update"' in source
    assert '"/courses/{course_id}/analysis/run-all"' in source
    assert "_extract_claim_candidates" in source
    assert "_source_scope_for_chunk" in source


def test_lecture_studio_scope_ui_exists():
    html = _read(ADMIN_HTML)
    js = _read(ADMIN_JS)
    css = _read(ADMIN_CSS)
    assert 'data-ls-view="claims"' in html
    assert 'data-ls-view="graph"' in html
    assert 'id="ls-claims-panel"' in html
    assert 'id="ls-graph-panel"' in html
    assert "lsDocumentStructure" in js
    assert "lsSelectSection" in js
    assert "lsRenderClaimsPanel" in js
    assert "lsRenderGraphPanel" in js
    assert "ls-claims-all-btn" in html
    assert "ls-components-all-btn" in html
    assert "ls-graph-all-btn" in html
    assert "ls-menu-item-done" in css
    assert "ls-menu-item-running" in css
    assert "ls-menu-item-error" in css
    assert "lsHasAudio" in js
    assert "pipelineTask" in js
    assert ".ls-chunk-status.audio_ready::before" in css
    assert "/analysis/run-all" in js
    assert "ssAnalysisCell" in js


def test_lecture_studio_chunk_list_declares_analysis_buttons():
    js = _read(ADMIN_JS)
    start = js.index("function lsRenderChunkList()")
    end = js.index("function lsSelectChunk", start)
    chunk_list_source = js[start:end]
    assert 'var claimsAllBtn = document.getElementById("ls-claims-all-btn")' in chunk_list_source
    assert 'var componentsAllBtn = document.getElementById("ls-components-all-btn")' in chunk_list_source
    assert 'var graphAllBtn = document.getElementById("ls-graph-all-btn")' in chunk_list_source


def test_all_analysis_buttons_confirm_before_full_retry():
    routes = _read(ROUTES)
    main_source = _read(API_MAIN)
    js = _read(ADMIN_JS)
    assert '"/courses/{course_id}/analysis-status"' in routes
    assert "def _analysis_status" in routes
    assert "force = bool((body or {}).get(\"force\"))" in routes
    assert "_delete_claims_for_chunks" in routes
    assert "_delete_components_for_sections" in routes
    analysis_status_source = routes[routes.index("def _analysis_status"):routes.index("def _run_claim_extraction")]
    assert "CREATE TABLE IF NOT EXISTS section_assembly_status" in main_source
    assert '_table_exists(session, "section_assembly_status")' in analysis_status_source
    assert "section_assembly_status" in analysis_status_source
    assert "component_assembly_status IN ('success', 'skipped')" in analysis_status_source
    assert "task_type = 'component_assembly'" in analysis_status_source
    assert "lsRunCourseStepWithRetryConfirm" in js
    assert "window.confirm" in js
    assert "は解析済です。解析済のデータも含めてすべて再度実行しますか？" in js
    assert 'JSON.stringify({ force: force })' in js


def test_issue_181_semantic_claim_and_component_guards_exist():
    source = _read(ROUTES)
    assert "def _classify_chunk_role" in source
    assert "_CLAIM_SKIP_ROLES" in source
    assert "front_matter" in source
    assert "references" in source
    assert "def _semantic_claims_with_llm" in source
    assert "出版情報、著者情報、所属、ジャーナル情報、受理日" in source
    assert "def _semantic_components_with_llm" in source
    assert "Component名は内容に基づいて付ける" in source
    assert "Component for page N" in source
    assert "component_type_text" in source
    assert "internal_flow" in source


def test_issue_181_no_page_component_name_fallback():
    source = _read(ROUTES)
    assert 'f"Component for {section_id' not in source
    candidate_source = source[source.index("def _normalize_component_candidate"):source.index("def _semantic_components_with_llm")]
    assert "_PROHIBITED_COMPONENT_NAME_RE" in source
    assert "component for (page|section|chunk_group)" in source
    assert "raise ValueError" in candidate_source


def test_issue_184_llm_retry_and_no_low_quality_fallback_in_normal_path():
    source = _read(ROUTES)
    config = _read(ROOT / "backend" / "core" / "config.py")
    assert "llm_max_retries" in config
    assert "llm_retry_backoff_seconds" in config
    assert "LLMStructuredOutputError" in source
    assert "claim_extraction_failed" in source
    assert "component_assembly_failed" in source
    claim_path = source[source.index("def _extract_claim_candidates"):source.index("def _insert_claim")]
    assert "_fallback_semantic_claims" not in claim_path
    assemble_path = source[source.index("def _assemble_section"):source.index("def _run_component_assembly")]
    assert "_fallback_component_candidates" not in assemble_path


def test_claim_llm_metadata_is_server_enriched_not_retry_blocking():
    source = _read(ROUTES)
    normalize_source = source[source.index("def _normalize_claim_payload"):source.index("def _fallback_concepts")]
    assert 'required = ("claim_type", "text", "support_status", "evidence_text")' in normalize_source
    assert 'review_status = str(raw.get("review_status") or "teacher_review_required").strip()' in normalize_source
    assert "scope = _source_scope_for_chunk(chunk, \"chunk\")" in normalize_source
    assert "claim missing required fields: source_scope" not in normalize_source


def test_claim_all_extraction_skips_failed_chunks_and_keeps_progress():
    source = _read(ROUTES)
    run_source = source[source.index("def _run_claim_extraction"):source.index("def _claim_item")]
    assert "existing += 1" in run_source
    assert "except LLMStructuredOutputError as exc" in run_source
    assert "Skipping claim extraction for chunk" in run_source
    assert '"failed_chunks": failed_chunks[-50:]' in run_source
    assert '"failed": failed' in run_source
    js = _read(ADMIN_JS)
    assert "件エラー（該当箇所はスキップ）" in js


def test_llm_response_shape_debug_logging_exists():
    source = _read(ROUTES)
    assert "def _llm_response_debug" in source
    assert "raw_preview" in source
    assert "parsed_keys" in source
    assert "claims_type" in source
    assert "Claim LLM response did not contain claims array" in source
    assert "doc=%s, section=%s, page=%s, chars=%s" in source


def test_claim_extraction_uses_structured_json_output():
    source = _read(ROUTES)
    claim_source = source[source.index("def _semantic_claims_with_llm"):source.index("def _fallback_semantic_claims")]
    assert "generate_text_with_structured_output" in claim_source
    assert "_LLMClaimExtractionResult" in claim_source
    assert "generate_text(" not in claim_source


def test_issue_184_meaningful_graph_edges_replace_sequential_supports():
    source = _read(ROUTES)
    schemas = _read(SCHEMAS)
    js = _read(ADMIN_JS)
    graph_source = source[source.index("def _build_component_graph_payload"):source.index("def _save_component_graph")]
    assert '"relation": "SUPPORTS"' not in graph_source
    assert "input_output_match" in graph_source
    assert "dependency_match" in graph_source
    assert "uncertainty_affects_prediction" in graph_source
    assert "diagnostic_uses_output" in graph_source
    assert "isolated_component" in graph_source
    assert "edge_type: str" in schemas
    assert "confidence: float" in schemas
    assert "evidence: dict" in schemas
    assert "ls-graph-edge" in js
    assert "support_status:" in js


def test_issue_184_ui_displays_llm_failure_messages():
    js = _read(ADMIN_JS)
    assert "lsApiErrorMessage" in js
    assert "LLM応答が有効な主張JSONとして解釈できませんでした" in js
    assert "意味のある論理要素候補を生成できなかったため、保存しませんでした" in js


def test_issue_185_section_reconstruction_and_ui_section_metadata():
    routes = _read(ROUTES)
    lecture = _read(ROOT / "backend" / "api" / "routes" / "lecture_studio.py")
    schemas = _read(SCHEMAS)
    js = _read(ADMIN_JS)
    section_source = _read(DOCUMENT_SECTIONS)
    assert "def enrich_chunks_with_sections" in section_source
    assert "Appendix" in section_source
    assert '"/documents/{document_id}/structure"' in routes
    assert "build_document_structure" in routes
    assert "enrich_chunks_with_sections(chunks)" in lecture
    assert "section_id: str" in schemas
    assert "section_level: int" in schemas
    assert "chunk.section_title" in js
    assert "chunk.section_id" in js


def test_issue_185_equation_claim_and_concept_normalizer():
    routes = _read(ROUTES)
    schemas = _read(SCHEMAS)
    migration = _read(MIGRATION)
    normalizer = _read(CONCEPT_NORMALIZER)
    assert "equation_relation" in routes
    assert "equation_transformation" in routes
    assert "def _equation_payload_from_claim" in routes
    assert "equation: dict" in schemas
    assert "ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS equation" in migration
    assert "def normalize_concept" in normalizer
    assert "ontology_alias" in normalizer
    assert "R_{\\\\Lambda_c}" in normalizer or "lambda" in normalizer


def test_issue_185_domain_edges_duplicates_and_review_propagation():
    routes = _read(ROUTES)
    schemas = _read(SCHEMAS)
    migration = _read(MIGRATION)
    js = _read(ADMIN_JS)
    assert "def _domain_components_from_cartridge" in routes
    assert "domain_prerequisite_match" in routes
    assert "support_status\": \"domain_inferred\"" in routes
    assert "def _duplicate_candidates_for_component" in routes
    assert "duplicate_candidates" in schemas
    assert "duplicate candidates" in js
    assert "theory_review_events" in migration
    assert "def _propagate_rejected_claim" in routes
    assert "def _propagate_rejected_component" in routes
    assert "needs_revision" in routes
