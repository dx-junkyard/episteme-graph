"""Regression checks for Claim / Component / Graph scope separation."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "backend" / "api" / "schemas.py"
ROUTES = ROOT / "backend" / "api" / "routes" / "theory_components.py"
MIGRATION = ROOT / "backend" / "db" / "013_theory_components.sql"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"


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
