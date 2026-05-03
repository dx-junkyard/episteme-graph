"""Tests for the document-first analysis pipeline (issue #226).

Covers:
    - section-aware chunker (pure logic, no DB)
    - DSL graph → search text (pure logic, no DB)
    - orchestrator stage flow with all agents and persistence mocked
"""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# --- Helpers ---------------------------------------------------------------

@dataclass
class _Block:
    block_id: str
    page: int
    order: int
    text: str
    block_type: str = "body_paragraph"
    section_id: str | None = None


@dataclass
class _Section:
    section_id: str
    title: str
    level: int = 1
    order: int = 0
    page_start: int = 1
    page_end: int | None = None


@dataclass
class _Structure:
    document_id: str
    blocks: list
    sections: list


# --- chunker ---------------------------------------------------------------

def test_chunker_groups_blocks_by_section_and_respects_max_chars():
    from core.document_pipeline.chunker import build_source_chunks

    structure = _Structure(
        document_id="doc-1",
        sections=[
            _Section(section_id="s1", title="Intro", order=1, page_start=1),
            _Section(section_id="s2", title="Method", order=2, page_start=2),
        ],
        blocks=[
            _Block("b1", 1, 0, "A" * 100, section_id="s1"),
            _Block("b2", 1, 1, "B" * 100, section_id="s1"),
            _Block("b3", 2, 0, "C" * 100, section_id="s2"),
        ],
    )

    chunks = build_source_chunks(structure, max_chars=300)
    assert len(chunks) >= 1
    # 1個目の chunk が s1 section にあること
    assert chunks[0].section_id == "s1"
    # block_ids が記録されていること
    assert "b1" in chunks[0].block_ids
    # chunk_index が単調増加
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_chunker_splits_oversized_block():
    from core.document_pipeline.chunker import build_source_chunks

    long_text = "X" * 5000
    structure = _Structure(
        document_id="doc-2",
        sections=[_Section("s1", "Long", order=1, page_start=1)],
        blocks=[_Block("big", 1, 0, long_text, section_id="s1")],
    )
    chunks = build_source_chunks(structure, max_chars=1000)
    # 5000 / 1000 = 5 個に分割される（吸収後でも複数）
    assert len(chunks) >= 4
    assert all(c.section_id == "s1" for c in chunks)
    assert all(c.metadata.get("split_long_block") for c in chunks)


def test_chunker_handles_blocks_without_section():
    from core.document_pipeline.chunker import build_source_chunks

    structure = _Structure(
        document_id="doc-3",
        sections=[],
        blocks=[
            _Block("b1", 1, 0, "Lone paragraph one.", section_id=None),
            _Block("b2", 1, 1, "Lone paragraph two.", section_id=None),
        ],
    )
    chunks = build_source_chunks(structure, max_chars=1000)
    assert len(chunks) == 1
    assert chunks[0].section_id is None
    assert "Lone paragraph one." in chunks[0].text
    assert "Lone paragraph two." in chunks[0].text


# --- dsl_text -------------------------------------------------------------

def test_dsl_result_to_search_text_includes_nodes_and_edges():
    from core.document_pipeline.dsl_text import dsl_result_to_search_text

    @dataclass
    class N:
        node_id: str
        node_type: str
        node_value: str
        reason: str = ""
        source_kind: str = "claim"
        source_refs: dict = field(default_factory=dict)
        confidence: float = 0.9

    @dataclass
    class E:
        edge_id: str
        from_node_id: str
        to_node_id: str
        core_predicate: str
        domain_verb: str
        polarity: str
        evidence_refs: dict = field(default_factory=dict)
        reason: str = ""
        confidence: float = 0.9

    @dataclass
    class R:
        nodes: list
        edges: list
        review_notes: list = field(default_factory=list)

    result = R(
        nodes=[
            N("n1", "Relation", "total_rate_sum_rule", reason="from claim 12"),
            N("n2", "Approximation", "isospin_limit"),
        ],
        edges=[
            E("e1", "n1", "n2", "REQUIRES", "depends_on", "+", reason="motivated by"),
        ],
        review_notes=["uncertain edge n1→n2"],
    )

    text = dsl_result_to_search_text(result, document_id="doc-9")
    assert "DSL graph for document doc-9" in text
    assert "n1 Relation total_rate_sum_rule" in text
    assert "n1 REQUIRES(depends_on, +) n2" in text
    assert "uncertain edge" in text


# --- orchestrator stage flow ---------------------------------------------

class _MockAgent:
    def __init__(self, returns):
        self._returns = returns

    def run(self, **kwargs):
        return self._returns


def test_orchestrator_runs_all_stages_in_order():
    from core.document_pipeline import orchestrator

    @dataclass
    class _Result:
        document_id: str = "doc"
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)
        components: list = field(default_factory=list)
        qualified_spans: list = field(default_factory=list)
        sections: list = field(default_factory=lambda: [_Section("s1", "Intro", order=1, page_start=1)])
        blocks: list = field(default_factory=lambda: [_Block("b1", 1, 0, "Hello world body.", section_id="s1")])
        review_notes: list = field(default_factory=list)

    structure_result = _Result()

    agents = {
        "DocumentStructureAgent": _MockAgent(structure_result),
        "PaperSkeletonAgent": _MockAgent(_Result()),
        "RhetoricalRoleAgent": _MockAgent(_Result()),
        "ClaimQualificationAgent": _MockAgent(_Result()),
        "EquationSemanticsAgent": _MockAgent(_Result()),
        "ThesisReconstructionAgent": _MockAgent(_Result()),
        "DSLLinkingAgent": _MockAgent(_Result()),
        "ComponentAssemblyAgent": _MockAgent(_Result()),
    }

    visited: list[str] = []

    def progress(stage, info):
        visited.append(stage)

    fake_persistence = {
        "persist_source_chunks": MagicMock(return_value=[
            {"chunk_id": "c1", "chunk_index": 0, "section_id": "s1",
             "block_ids": ["b1"], "page_start": 1, "page_end": 1, "text": "Hello"}
        ]),
        "persist_qualified_claims": MagicMock(return_value=[]),
        "persist_components": MagicMock(return_value={}),
        "persist_component_graph": MagicMock(return_value="graph-1"),
        "persist_document_embedding": MagicMock(return_value="emb-1"),
        "upsert_analysis_run": MagicMock(return_value="run-1"),
    }

    with patch.multiple(orchestrator, **fake_persistence):
        result = orchestrator.run_document_pipeline(
            pdf_bytes=b"%PDF-1.4 fake bytes",
            document_id="doc-42",
            material_id="mat-1",
            filename="paper.pdf",
            cartridge_id="particle_physics",
            progress_callback=progress,
            agents=agents,
        )

    assert result.final_stage == "completed"
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
        assert stage in visited, f"stage {stage} not invoked. got={visited}"
    # 各 persist 関数が一度ずつ呼ばれる
    fake_persistence["persist_source_chunks"].assert_called_once()
    fake_persistence["persist_qualified_claims"].assert_called_once()
    fake_persistence["persist_components"].assert_called_once()
    fake_persistence["persist_component_graph"].assert_called_once()
    fake_persistence["persist_document_embedding"].assert_called_once()


# --- regression-style structural assertions (issue #226) ----------------

def test_issue_226_pipeline_files_exist():
    repo = Path(__file__).resolve().parents[2]
    for rel in [
        "backend/core/document_pipeline/__init__.py",
        "backend/core/document_pipeline/orchestrator.py",
        "backend/core/document_pipeline/chunker.py",
        "backend/core/document_pipeline/persistence.py",
        "backend/core/document_pipeline/dsl_text.py",
        "backend/db/015_document_pipeline.sql",
    ]:
        assert (repo / rel).exists(), f"missing pipeline artifact {rel}"


def test_issue_226_old_pipeline_no_longer_called_from_upload():
    repo = Path(__file__).resolve().parents[2]
    services_src = (repo / "backend/api/services.py").read_text(encoding="utf-8")
    upload_section = services_src[
        services_src.index("def process_material_background"):
        services_src.index("def reanalyze_course_structure_background")
    ]
    # 旧 pipeline 関数は process_material_background から呼ばれてはいけない
    assert "build_knowledge_graph(" not in upload_section
    assert "chunk_pdf_pages(" not in upload_section
    # 新 pipeline へ委譲している
    assert "run_document_pipeline" in upload_section


def test_issue_226_admin_has_document_reanalyze_endpoint():
    repo = Path(__file__).resolve().parents[2]
    admin_src = (repo / "backend/api/routes/admin.py").read_text(encoding="utf-8")
    assert '"/documents/{document_id}/reanalyze"' in admin_src


def test_issue_226_migration_adds_document_pipeline_artifacts():
    repo = Path(__file__).resolve().parents[2]
    sql = (repo / "backend/db/015_document_pipeline.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS section_id" in sql
    assert "CREATE TABLE IF NOT EXISTS document_embeddings" in sql
    assert "CREATE TABLE IF NOT EXISTS document_analysis_runs" in sql
    assert "ALTER TABLE theory_components ALTER COLUMN course_id DROP NOT NULL" in sql
    assert "embedding_type" in sql


def test_orchestrator_records_failed_stage():
    from core.document_pipeline import orchestrator

    @dataclass
    class _Result:
        document_id: str = "doc"
        sections: list = field(default_factory=list)
        blocks: list = field(default_factory=list)

    class _Fail:
        def run(self, **kwargs):
            raise RuntimeError("boom")

    agents = {
        "DocumentStructureAgent": _Fail(),
        "PaperSkeletonAgent": _MockAgent(_Result()),
        "RhetoricalRoleAgent": _MockAgent(_Result()),
        "ClaimQualificationAgent": _MockAgent(_Result()),
        "EquationSemanticsAgent": _MockAgent(_Result()),
        "ThesisReconstructionAgent": _MockAgent(_Result()),
        "DSLLinkingAgent": _MockAgent(_Result()),
        "ComponentAssemblyAgent": _MockAgent(_Result()),
    }

    with patch.object(orchestrator, "upsert_analysis_run", return_value="run-x"):
        with pytest.raises(orchestrator.PipelineStageError) as exc_info:
            orchestrator.run_document_pipeline(
                pdf_bytes=b"x",
                document_id="doc-1",
                material_id="m-1",
                agents=agents,
            )
    assert exc_info.value.stage == "document_structure"
