"""Tests for Lecture Studio theory component MVP."""

from __future__ import annotations

import importlib
import json
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
LECTURE_STUDIO = ROOT / "backend" / "api" / "routes" / "lecture_studio.py"


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

    def test_lecture_studio_components_reads_document_scoped_pipeline_results(self):
        source = _read(LECTURE_STUDIO)
        assert "source_document_ids" in source
        assert "OR document_id IN" in source
        assert "FROM theory_components" in source
        assert "FROM theory_component_graphs" in source


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


class TestNulCharacterSanitization:
    """Tests for NUL character removal utilities in theory_components route."""

    def _load_helpers(self):
        source = (ROOT / "backend" / "api" / "routes" / "theory_components.py").read_text(encoding="utf-8")
        # exec only the utility functions, not the full module (avoids import of FastAPI deps)
        ns: dict = {"__builtins__": __builtins__, "json": json}
        # Extract and exec the four helpers via exec of isolated blocks
        import re as _re
        for name in ("_strip_nul", "_clean_pg_text", "_json_dumps_pg_safe", "_contains_nul", "_nul_fields"):
            pattern = rf"(^def {name}\(.*?)(?=^def |\Z)"
            match = _re.search(pattern, source, _re.MULTILINE | _re.DOTALL)
            assert match, f"{name} not found in source"
            exec(match.group(1), ns)  # noqa: S102
        return ns

    def test_strip_nul_removes_from_string(self):
        ns = self._load_helpers()
        assert ns["_strip_nul"]("hello\x00world") == "helloworld"

    def test_strip_nul_removes_from_nested_list(self):
        ns = self._load_helpers()
        result = ns["_strip_nul"]([{"name": "mass\x00"}, {"name": "energy"}])
        assert result == [{"name": "mass"}, {"name": "energy"}]

    def test_strip_nul_removes_from_nested_dict(self):
        ns = self._load_helpers()
        result = ns["_strip_nul"]({"section": "Intro\x00", "page": 1})
        assert result == {"section": "Intro", "page": 1}

    def test_strip_nul_passthrough_non_string(self):
        ns = self._load_helpers()
        assert ns["_strip_nul"](42) == 42
        assert ns["_strip_nul"](None) is None

    def test_clean_pg_text_removes_nul(self):
        ns = self._load_helpers()
        assert ns["_clean_pg_text"]("This is a claim\x00 with nul") == "This is a claim with nul"

    def test_clean_pg_text_handles_none(self):
        ns = self._load_helpers()
        assert ns["_clean_pg_text"](None) == ""

    def test_json_dumps_pg_safe_removes_nul_in_string(self):
        ns = self._load_helpers()
        result = ns["_json_dumps_pg_safe"]({"latex": "E=mc^2\x00"}, {})
        parsed = json.loads(result)
        assert parsed["latex"] == "E=mc^2"

    def test_json_dumps_pg_safe_removes_nul_in_list(self):
        ns = self._load_helpers()
        result = ns["_json_dumps_pg_safe"]([{"name": "mass\x00", "role": "variable"}], [])
        parsed = json.loads(result)
        assert parsed[0]["name"] == "mass"

    def test_json_dumps_pg_safe_uses_default_on_none(self):
        ns = self._load_helpers()
        result = ns["_json_dumps_pg_safe"](None, {})
        assert json.loads(result) == {}

    def test_contains_nul_detects_in_string(self):
        ns = self._load_helpers()
        assert ns["_contains_nul"]("hello\x00") is True
        assert ns["_contains_nul"]("hello") is False

    def test_contains_nul_detects_in_nested_structures(self):
        ns = self._load_helpers()
        assert ns["_contains_nul"]([{"name": "mass\x00"}]) is True
        assert ns["_contains_nul"]({"section": "clean"}) is False

    def test_nul_fields_returns_affected_field_names(self):
        ns = self._load_helpers()
        payload = {
            "text": "This is a claim\x00 with nul",
            "normalized_text": "clean",
            "evidence_text": "Evidence\x00 text",
            "concepts": [],
        }
        fields = ns["_nul_fields"](payload)
        assert set(fields) == {"text", "evidence_text"}

    def test_nul_fields_empty_when_no_nul(self):
        ns = self._load_helpers()
        payload = {"text": "clean", "evidence_text": "also clean", "concepts": []}
        assert ns["_nul_fields"](payload) == []

    def test_route_source_uses_nul_helpers_in_insert_claim(self):
        source = _read(ROUTES)
        assert "_nul_fields(payload)" in source
        assert "_clean_pg_text(document_id)" in source
        assert "_json_dumps_pg_safe(payload.get" in source

    def test_route_source_uses_nul_helpers_in_normalize_claim(self):
        source = _read(ROUTES)
        assert "_clean_pg_text(raw.get(\"text\")" in source or "_clean_pg_text(raw.get('text')" in source
        assert "_clean_pg_text(raw.get(\"evidence_text\")" in source or "_clean_pg_text(raw.get('evidence_text')" in source
        assert "return _strip_nul({" in source
