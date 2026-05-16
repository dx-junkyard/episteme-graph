"""Regression checks for issue #228.

旧 course/section/chunk 単位の LLM 解析 pipeline が完全に削除され、新 document-first
agent pipeline へ移行されていることを構造的に検証する。
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "backend" / "api" / "schemas.py"
ROUTES = ROOT / "backend" / "api" / "routes" / "theory_components.py"
API_MAIN = ROOT / "backend" / "api" / "main.py"
SERVICES = ROOT / "backend" / "api" / "services.py"
ADMIN_ROUTES = ROOT / "backend" / "api" / "routes" / "admin.py"
PIPELINE_INIT = ROOT / "backend" / "core" / "document_pipeline" / "__init__.py"
ORCHESTRATOR = ROOT / "backend" / "core" / "document_pipeline" / "orchestrator.py"
CHUNKER = ROOT / "backend" / "core" / "document_pipeline" / "chunker.py"
PERSISTENCE = ROOT / "backend" / "core" / "document_pipeline" / "persistence.py"
DSL_TEXT = ROOT / "backend" / "core" / "document_pipeline" / "dsl_text.py"
PIPELINE_MIGRATION = ROOT / "backend" / "db" / "015_document_pipeline.sql"
THEORY_MIGRATION = ROOT / "backend" / "db" / "013_theory_components.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- 削除されているべき旧 endpoint -------------------------------------------

def test_old_course_level_endpoints_are_removed():
    """旧 course 単位 LLM 解析 endpoint が router から消えていること."""
    source = _read(ROUTES)
    forbidden_endpoints = [
        '"/courses/{course_id}/claims/extract-all"',
        '"/courses/{course_id}/components/assemble-all"',
        '"/courses/{course_id}/component-graph/update"',
        '"/courses/{course_id}/analysis/run-all"',
        '"/courses/{course_id}/components/retry-failed"',
        '"/courses/{course_id}/components/assembly-status"',
        '"/courses/{course_id}/analysis-status"',
    ]
    for ep in forbidden_endpoints:
        assert ep not in source, f"旧 endpoint が残っている: {ep}"


def test_old_chunk_section_extract_endpoints_are_removed():
    """chunk / section 単位の旧 LLM 解析 endpoint が router から消えていること."""
    source = _read(ROUTES)
    assert '"/documents/{document_id}/chunks/{chunk_id}/claims/extract"' not in source
    assert '"/documents/{document_id}/sections/{section_id}/components/assemble"' not in source


# --- 削除されているべき旧 helper -------------------------------------------

def test_old_pipeline_helpers_are_removed():
    """issue #228 で削除指定された helper が source から消えていること."""
    source = _read(ROUTES)
    forbidden_helpers = [
        "def _run_claim_extraction",
        "def _semantic_claims_with_llm",
        "def _extract_claim_candidates",
        "def _fallback_semantic_claims",
        "def _run_component_assembly",
        "def _semantic_components_with_llm",
        "def _fallback_component_candidates",
        "def _run_graph_update",
        "def _analysis_pipeline_worker",
        "def _assemble_section",
        "def _component_assembly_worker",
        "def _retry_failed_sections_worker",
        "def _worker_wrapper",
        "def _upsert_section_assembly_status",
        "def _get_failed_section_statuses",
        "def _analysis_status",
    ]
    for helper in forbidden_helpers:
        assert helper not in source, f"旧 helper が残っている: {helper}"


# --- 新 document-first pipeline の存在確認 ---------------------------------

def test_new_document_pipeline_artifacts_exist():
    """新 pipeline モジュール群がリポジトリに存在すること."""
    for path in [PIPELINE_INIT, ORCHESTRATOR, CHUNKER, PERSISTENCE, DSL_TEXT, PIPELINE_MIGRATION]:
        assert path.exists(), f"新 pipeline artifact が無い: {path}"


def test_pdf_upload_background_calls_new_pipeline():
    """PDF アップロード background が新 agent pipeline を呼んでいること."""
    services_src = _read(SERVICES)
    upload_section = services_src[
        services_src.index("def process_material_background"):
        services_src.index("def reanalyze_course_structure_background")
    ]
    # 新 pipeline へ委譲
    assert "run_document_pipeline" in upload_section
    # 旧 pipeline 関数を呼んでいない
    assert "build_knowledge_graph(" not in upload_section
    assert "chunk_pdf_pages(" not in upload_section
    assert "_run_claim_extraction" not in upload_section
    assert "_run_component_assembly" not in upload_section
    assert "_run_graph_update" not in upload_section
    assert "_analysis_pipeline_worker" not in upload_section


def test_orchestrator_runs_eight_agents_in_order():
    """orchestrator が DocumentStructureAgent から ComponentAssemblyAgent まで順に呼ぶこと."""
    src = _read(ORCHESTRATOR)
    expected_agents = [
        "DocumentStructureAgent",
        "PaperSkeletonAgent",
        "RhetoricalRoleAgent",
        "ClaimQualificationAgent",
        "EquationSemanticsAgent",
        "ThesisReconstructionAgent",
        "DSLLinkingAgent",
        "ComponentAssemblyAgent",
    ]
    for agent in expected_agents:
        assert agent in src, f"orchestrator から {agent} が呼ばれていない"


def test_orchestrator_emits_required_stages():
    """新 pipeline のステージが orchestrator に揃っていること."""
    src = _read(ORCHESTRATOR)
    expected_stages = [
        "save_pdf",
        "document_structure",
        "source_chunking",
        "source_embedding",
        "paper_skeleton",
        "rhetorical_role",
        "claim_qualification",
        "equation_semantics",
        "thesis_reconstruction",
        "dsl_linking",
        "dsl_embedding",
        "component_assembly",
        "persist_claims_components_graph",
        "completed",
    ]
    for stage in expected_stages:
        assert f'"{stage}"' in src, f"orchestrator から stage {stage!r} が消えている"


# --- chunk テーブル / source embedding / DSL embedding が維持されること --

def test_chunks_table_and_source_embedding_kept():
    """chunks テーブルと source chunk embedding が維持されていること."""
    sql = _read(PIPELINE_MIGRATION)
    # chunks テーブルそのものは残し、section_id 列を追加している
    assert "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS section_id" in sql
    # 新規 DROP TABLE chunks や chunks テーブル削除は禁止
    assert "DROP TABLE chunks" not in sql
    assert "DROP TABLE IF EXISTS chunks" not in sql


def test_dsl_and_document_embeddings_persisted():
    """DSL embedding を含む document_embeddings テーブルが新 migration で作られていること."""
    sql = _read(PIPELINE_MIGRATION)
    assert "CREATE TABLE IF NOT EXISTS document_embeddings" in sql
    assert "embedding_type" in sql
    # orchestrator の persist 呼び出し
    orch = _read(ORCHESTRATOR)
    assert "persist_source_chunks" in orch
    assert "persist_document_embedding" in orch


# --- agent outputs が既存DBへ永続化されること ---------------------------

def test_agent_outputs_persisted_to_existing_tables():
    """ClaimQualification / ComponentAssembly / DSLLinking 結果が
    theory_claims / theory_components / theory_component_graphs に保存されること."""
    persistence = _read(PERSISTENCE)
    # 既存テーブルへ INSERT/UPSERT すること
    assert "theory_claims" in persistence
    assert "theory_components" in persistence
    assert "theory_component_graphs" in persistence
    orch = _read(ORCHESTRATOR)
    assert "persist_qualified_claims" in orch
    assert "persist_components" in orch
    assert "persist_component_graph" in orch


def test_theory_components_table_kept_for_new_pipeline():
    """theory_components テーブル定義は新 pipeline でも保存先として保持されること."""
    sql = _read(THEORY_MIGRATION)
    assert "CREATE TABLE IF NOT EXISTS theory_components" in sql
    assert "CREATE TABLE IF NOT EXISTS theory_claims" in sql


# --- 既存 read 系が動作することを構造的に確認 -----------------------------

def test_remaining_read_endpoints_kept():
    """教材一覧・PDF表示・RAG用 chunks 等で使う read 系 endpoint が残っていること."""
    source = _read(ROUTES)
    # GET 系は削除しない
    assert '"/documents/{document_id}/chunks/{chunk_id}/claims"' in source
    assert '"/documents/{document_id}/structure"' in source
    assert '"/documents/{document_id}/sections/{section_id}/components"' in source
    assert '"/documents/{document_id}/component-graph"' in source
    assert '"/courses/{course_id}/theory-components"' in source
    # claim PATCH と component CRUD は教師レビュー用に残す
    assert '"/claims/{claim_id}"' in source
    assert '"/courses/{course_id}/theory-components"' in source
    assert '"/theory-components/{component_id}"' in source


def test_admin_has_document_reanalyze_endpoint():
    """document を再解析するための新 endpoint が admin に存在すること."""
    admin = _read(ADMIN_ROUTES)
    assert '"/documents/{document_id}/reanalyze"' in admin
