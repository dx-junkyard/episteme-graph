"""Tests for Lecture Studio theory component MVP."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
SCHEMAS = ROOT / "backend" / "api" / "schemas.py"
ROUTES = ROOT / "backend" / "api" / "routes" / "theory_components.py"
CORE = ROOT / "backend" / "core" / "theory_components.py"
MIGRATION = ROOT / "backend" / "db" / "013_theory_components.sql"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
STYLES = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestTheoryComponentMigration:
    def test_migration_creates_component_tables(self):
        sql = _read(MIGRATION)
        assert "CREATE TABLE IF NOT EXISTS theory_components" in sql
        assert "CREATE TABLE IF NOT EXISTS theory_component_links" in sql
        assert "source_chunks       JSONB" in sql
        assert "blackbox_policy     JSONB" in sql

    def test_main_applies_migration_013(self):
        source = _read(ROOT / "backend" / "api" / "main.py")
        assert "Migration 013" in source
        assert "idx_theory_components_course" in source
        assert "Migrations (002-013)" in source


class TestTheoryComponentSchemas:
    def test_schema_models_exist_with_factory_defaults(self):
        source = _read(SCHEMAS)
        assert "class TheorySourceRef" in source
        assert "class TheoryComponentOut" in source
        assert "class TheoryComponentUpsertRequest" in source
        assert "class TheoryComponentExtractResponse" in source
        assert "Field(default_factory=list)" in source
        assert "Field(default_factory=TheoryBlackboxPolicy)" in source


class TestTheoryComponentRoutes:
    def test_required_api_routes_exist(self):
        source = _read(ROUTES)
        assert '"/courses/{course_id}/theory-components"' in source
        assert '"/chunks/{chunk_id}/theory-components/extract"' in source
        assert '"/theory-components/{component_id}"' in source
        assert '"/theory-components/{component_id}/reject"' in source
        assert '"/courses/{course_id}/theory-components/validate-connection"' in source

    def test_permissions_follow_course_visibility_and_editability(self):
        source = _read(ROUTES)
        assert "Depends(_require_teacher)" in source
        assert "get_viewable_course_data" in source
        assert "get_editable_course_data" in source
        assert "_ensure_viewable(course_id, current_user)" in source
        assert "_ensure_editable(existing.course_id, current_user)" in source

    def test_approval_requires_sources(self):
        source = _read(ROUTES)
        assert 'payload.get("status") == "teacher_reviewed"' in source
        assert "raise HTTPException(status_code=422" in source
        assert "needs_source" in source
        assert "source_chunks" in source
        assert "inputs" in source
        assert "outputs" in source

    def test_extract_saves_candidates_not_reviewed(self):
        source = _read(ROUTES)
        assert '"status": "candidate"' in source
        assert "body.force" in source
        assert "body.use_llm" in source
        assert "should_update_existing" in source
        assert "_preserve_structural_io" in source
        assert "extract_theory_components_from_dsl" in source
        assert "primary_chunk_id" in source
        assert "lower(name) = lower(:name)" in source

    def test_lecture_studio_router_registers_theory_components(self):
        source = _read(ROOT / "backend" / "api" / "routes" / "admin.py")
        assert "routes.theory_components" in source
        assert "router.include_router(_theory_components_router)" in source


class TestTheoryExtractionPrompt:
    def test_dsl_extraction_is_default_path(self):
        source = _read(CORE)
        assert "def extract_theory_components_from_dsl" in source
        assert "def enrich_theory_components_with_llm" in source
        assert "_dsl_edges" in source
        assert "inputs/outputs remain DSL-derived" in source

    def test_prompt_marks_general_knowledge_as_needing_source(self):
        source = _read(CORE)
        assert "一般的な素粒子物理学" in source
        assert "一般知識で補った項目は needs_source: true" in source
        assert "needs_source: true" in source
        assert "JSONのみを出力してください" in source
        assert "THEORY_EXTRACTION_TIMEOUT_SECONDS" in source
        assert "TimeoutError" in source
        assert "inputs と outputs は変更しないでください" in source
        assert "実装説明は禁止" in source
        assert "return []" in source

    def test_dsl_extractor_does_not_call_llm(self):
        fake_llm = types.ModuleType("core.llm")

        def _fail_llm(*_args, **_kwargs):
            raise AssertionError("DSL extraction must not call LLM")

        fake_llm.generate_text = _fail_llm
        fake_llm.get_llm_params = lambda _mode: {"model": "dummy", "reasoning_effort": None}
        old_llm = sys.modules.get("core.llm")
        sys.modules["core.llm"] = fake_llm
        try:
            module = importlib.import_module("core.theory_components")
            components = module.extract_theory_components_from_dsl({
                "id": "11111111-1111-1111-1111-111111111111",
                "raw_text": "A requires B.",
                "page_start": 12,
                "smiles_dsl": "(a:Concept:A) ==[REQUIRES:requires:+]=> (b:Concept:B)",
            })
        finally:
            if old_llm is None:
                sys.modules.pop("core.llm", None)
            else:
                sys.modules["core.llm"] = old_llm
        assert components
        assert components[0]["name"] == "A → B"
        assert components[0]["inputs"][0]["source_refs"][0]["chunk_id"] == "11111111-1111-1111-1111-111111111111"



class TestTheoryComponentFrontend:
    def test_theory_tab_and_panel_exist(self):
        html = _read(ADMIN_HTML)
        assert 'data-ls-view="theory"' in html
        assert 'id="ls-theory-panel"' in html
        assert 'id="ls-extract-theory-btn"' in html

    def test_frontend_load_extract_insert_and_preview_exist(self):
        js = _read(ADMIN_JS)
        assert "componentsByChunk" in js
        assert "lsLoadTheoryComponentsForChunk" in js
        assert "lsExtractTheoryComponents" in js
        assert "lsInsertTheoryChip" in js
        assert "[[THEORY:" in js
        assert "EG_PREVIEW_THEORY" in js

    def test_existing_formula_placeholder_path_is_preserved(self):
        js = _read(ADMIN_JS)
        assert "FORMULA_" in js
        assert "EG_PREVIEW_MATH" in js
        assert "formulaById" in js

    def test_theory_styles_exist(self):
        css = _read(STYLES)
        assert ".ls-theory-panel" in css
        assert ".ls-theory-card" in css
        assert ".ls-theory-chip" in css
        assert ".ls-chunk-theory-state" in css
