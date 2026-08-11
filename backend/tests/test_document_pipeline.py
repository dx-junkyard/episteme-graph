"""Tests for the document-first analysis pipeline (issue #226).

Covers:
    - section-aware chunker (pure logic, no DB)
    - DSL graph → search text (pure logic, no DB)
    - orchestrator stage flow with all agents and persistence mocked
"""
from __future__ import annotations

import io
import sys
import tarfile
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


def test_chunker_orders_sections_by_first_source_block_not_section_metadata():
    from core.document_pipeline.chunker import build_source_chunks

    structure = _Structure(
        document_id="doc-order",
        sections=[
            _Section(section_id="late", title="Late", order=1, page_start=1),
            _Section(section_id="early", title="Early", order=2, page_start=1),
        ],
        blocks=[
            _Block("early-b", 1, 0, "Early paragraph." * 20, section_id="early"),
            _Block("late-b", 2, 0, "Late paragraph." * 20, section_id="late"),
        ],
    )

    chunks = build_source_chunks(structure, max_chars=1000)

    assert [c.section_id for c in chunks] == ["early", "late"]
    assert chunks[0].block_ids == ["early-b"]


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


# --- TeX archive input ----------------------------------------------------

def _make_tex_archive(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_tex_archive_builds_document_structure_with_sections_and_equations():
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive

    archive = _make_tex_archive({
        "main.tex": r"""
            \documentclass{article}
            \title{Consistency Relations}
            \author{A. Author and B. Writer}
            \begin{document}
            \maketitle
            \section{Introduction}
            We derive a consistency relation for scalar perturbations \cite{Maldacena:2002vr}.

            \begin{equation}
            R = H^2 / \dot{\phi}^2
            \label{eq:scalar_relation}
            \end{equation}

            \subsection{Result}
            The squeezed limit fixes the observable in Eq.~\eqref{eq:scalar_relation}.
            \end{document}
        """,
        "refs.bib": r"""
            @article{Maldacena:2002vr,
              author = {Maldacena, Juan Martin},
              title = {Non-Gaussian features of primordial fluctuations},
              journal = {JHEP},
              year = {2003},
              doi = {10.1088/1126-6708/2003/05/013}
            }
        """,
    })

    structure = build_structure_from_tex_archive(
        archive,
        document_id="doc-tex",
        source_file="paper.tar.gz",
        cartridge_id="particle_physics",
    )

    assert structure.document_id == "doc-tex"
    assert structure.metadata.title == "Consistency Relations"
    assert structure.metadata.authors == ["A. Author", "B. Writer"]
    assert structure.metadata.author_extraction["source"] == "tex_author"
    assert structure.metadata.author_extraction["needs_review"] is False
    assert [s.title for s in structure.sections] == ["Introduction", "Result"]
    equation_blocks = [b for b in structure.blocks if b.block_type == "equation_block"]
    assert any("R = H^2" in b.text for b in equation_blocks)
    assert equation_blocks[0].equation_label == "eq:scalar_relation"
    assert equation_blocks[0].raw["latex"] == r"R = H^2 / \dot{\phi}^2"
    assert equation_blocks[0].raw["extraction_source"] == "tex_source"
    assert any(
        b.raw.get("citations", [{}])[0].get("bib", {}).get("title") == "Non-Gaussian features of primordial fluctuations"
        for b in structure.blocks
        if b.raw.get("citations")
    )
    assert any(
        b.raw.get("refs", [{}])[0].get("key") == "eq:scalar_relation"
        for b in structure.blocks
        if b.raw.get("refs")
    )
    assert all(b.raw.get("parser_source") == "tex_archive" for b in structure.blocks)


def test_tex_archive_preserves_inverted_authors_and_provenance():
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive

    archive = _make_tex_archive({
        "main.tex": r"""
            \documentclass{article}
            \title{Inverted Authors}
            \author{Smith, John \and Doe, Jane}
            \begin{document}
            \maketitle
            \section{Introduction}
            Body.
            \end{document}
        """,
    })
    structure = build_structure_from_tex_archive(
        archive, document_id="doc-authors", source_file="paper.tar.gz"
    )

    assert structure.metadata.authors == ["Smith, John", "Doe, Jane"]
    assert structure.metadata.author_extraction["source"] == "tex_author"
    restored = type(structure).from_dict(structure.to_dict())
    assert restored.metadata.authors == ["Smith, John", "Doe, Jane"]
    assert restored.metadata.author_extraction["source"] == "tex_author"


def test_tex_archive_records_unclosed_math_environment_for_completeness():
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive
    from core.document_pipeline.completeness import analyze_document_completeness

    archive = _make_tex_archive({
        "main.tex": r"""
            \documentclass{article}
            \begin{document}
            \section{Result}
            \begin{align}
            a &= b
        """,
    })
    structure = build_structure_from_tex_archive(
        archive, document_id="doc-cut-align", source_file="paper.tar.gz"
    )

    assert structure.metadata.unclosed_math_environments == ["align"]
    report = analyze_document_completeness(
        structure.to_dict(), document_id=structure.document_id
    )
    assert report["tail_truncation"]["unclosed_math_environments"] == ["align"]
    assert "equation_environment_cut_at_boundary" in report["tail_truncation"]["signals"]


def _tex_archive_with_math() -> bytes:
    return _make_tex_archive({
        "main.tex": r"""
            \documentclass{article}
            \begin{document}
            \section{Results}
            \begin{equation}\label{eq:energy} E = mc^2 \end{equation}
            \begin{align}\label{eq:force} F = ma \end{align}
            \[ a^2 + b^2 = c^2 \]
            \end{document}
        """,
    })


def test_tex_archive_ingest_stores_equation_inventory():
    # AC #420(1,2): ingest persists display-math counts and symbolic labels.
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive

    structure = build_structure_from_tex_archive(
        _tex_archive_with_math(), document_id="doc-inv", source_file="paper.tar.gz"
    )
    inv = structure.metadata.tex_equation_inventory
    assert inv is not None
    assert inv["display_math_blocks"] == 3
    assert "eq:energy" in inv["labels"]
    assert "eq:force" in inv["labels"]
    assert inv["label_count"] == 2


def test_tex_equation_inventory_survives_serialization_roundtrip():
    # AC #420(3): inventory persists through to_dict / from_dict.
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive
    from episteme_graph.agents.document_structure.schema import DocumentStructureResult

    structure = build_structure_from_tex_archive(
        _tex_archive_with_math(), document_id="doc-rt", source_file="paper.tar.gz"
    )
    restored = DocumentStructureResult.from_dict(structure.to_dict())
    assert restored.metadata.tex_equation_inventory == structure.metadata.tex_equation_inventory
    assert restored.metadata.tex_equation_inventory["display_math_blocks"] == 3


def test_completeness_uses_precomputed_inventory_without_tex_source():
    # AC #420(4,6): a TeX doc (pages=0) with display math but no EquationRecords is
    # incomplete, detected from the persisted inventory alone (no tex_source).
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive
    from core.document_pipeline.completeness import analyze_document_completeness

    structure = build_structure_from_tex_archive(
        _tex_archive_with_math(), document_id="doc-cov", source_file="paper.tar.gz"
    )
    struct_dict = structure.to_dict()
    # The structure carries no raw tex_source — only the precomputed inventory.
    assert "tex_source" not in (struct_dict.get("metadata") or {})
    report = analyze_document_completeness(
        struct_dict,
        document_id=structure.document_id,
        equations={"equations": [], "equation_candidates": []},
    )
    cov = report["equation_artifact_coverage"]
    assert cov["tex_display_math_blocks"] == 3
    assert cov["complete"] is False
    assert "tex_display_math_without_equation_records" in cov["review_reasons"]
    assert report["complete"] is False


def test_completeness_passes_when_records_cover_tex_math():
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive
    from core.document_pipeline.completeness import analyze_document_completeness

    structure = build_structure_from_tex_archive(
        _tex_archive_with_math(), document_id="doc-cov2", source_file="paper.tar.gz"
    )
    report = analyze_document_completeness(
        structure.to_dict(),
        document_id=structure.document_id,
        equations={
            "equations": [{"equation_id": "eq_1"}],
            "equation_candidates": [
                {"candidate_id": "c1", "acceptance_status": "accepted",
                 "accepted_equation_id": "eq_1"},
            ],
        },
    )
    cov = report["equation_artifact_coverage"]
    assert cov["tex_display_math_blocks"] == 3
    assert cov["complete"] is True


class _FakeEquations:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return self._payload


def test_orchestrator_records_completeness_with_equation_records():
    # Regression for #420 P1: the orchestrator runs equation_semantics (stage 8)
    # before this completeness call, so it must pass the real EquationRecords —
    # the persisted document_completeness artifact must NOT be stale-incomplete
    # for a normal TeX document with math.
    from core.document_pipeline.orchestrator import _record_document_completeness
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive

    structure = build_structure_from_tex_archive(
        _tex_archive_with_math(), document_id="doc-orch", source_file="paper.tar.gz"
    )
    equations = _FakeEquations({
        "equations": [{"equation_id": "eq_1"}],
        "equation_candidates": [
            {"candidate_id": "c1", "acceptance_status": "accepted",
             "accepted_equation_id": "eq_1"},
        ],
    })
    saved: dict = {}
    report = _record_document_completeness(
        structure=structure,
        evidence=None,
        equations=equations,
        document_id="doc-orch",
        save_artifact=lambda name, value: saved.__setitem__(name, value),
    )
    cov = saved["document_completeness"]["equation_artifact_coverage"]
    assert cov["equation_record_count"] == 1
    assert cov["tex_display_math_blocks"] == 3
    assert cov["complete"] is True
    assert report["complete"] is True
    # A complete document leaves structure untouched (no stale propagation).
    assert not any(
        getattr(i, "rule_id", "").startswith("document_completeness_equation")
        for i in structure.validation_issues
    )


def test_orchestrator_completeness_incomplete_when_records_missing():
    # The genuine missing-records case still reports incomplete and propagates.
    from core.document_pipeline.orchestrator import _record_document_completeness
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive

    structure = build_structure_from_tex_archive(
        _tex_archive_with_math(), document_id="doc-orch2", source_file="paper.tar.gz"
    )
    saved: dict = {}
    report = _record_document_completeness(
        structure=structure,
        evidence=None,
        equations=_FakeEquations({"equations": [], "equation_candidates": []}),
        document_id="doc-orch2",
        save_artifact=lambda name, value: saved.__setitem__(name, value),
    )
    assert report["equation_artifact_coverage"]["complete"] is False
    assert any(
        getattr(i, "rule_id", "") == "document_completeness_equation_artifact_coverage_incomplete"
        for i in structure.validation_issues
    )


def test_build_document_completeness_passes_equations_to_coverage():
    # Regression for #420 P1: the export-route wrapper must forward the
    # equation_semantics artifact so a normal TeX document with records is not
    # reported as permanently incomplete.
    import sys as _sys
    from pathlib import Path as _Path
    _api = _Path(__file__).resolve().parents[1] / "api"
    if str(_api) not in _sys.path:
        _sys.path.insert(0, str(_api))
    from routes import export_artifacts as ea
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive

    structure = build_structure_from_tex_archive(
        _tex_archive_with_math(), document_id="doc-route", source_file="paper.tar.gz"
    )
    struct_dict = structure.to_dict()
    equations = {
        "equations": [{"equation_id": "eq_1"}],
        "equation_candidates": [
            {"candidate_id": "c1", "acceptance_status": "accepted",
             "accepted_equation_id": "eq_1"},
        ],
    }
    # Without equations → incomplete; with equations → complete.
    without = ea.build_document_completeness(struct_dict, document_id="doc-route")
    assert without["equation_artifact_coverage"]["complete"] is False
    with_eq = ea.build_document_completeness(
        struct_dict, document_id="doc-route", equations_artifact=equations
    )
    assert with_eq["equation_artifact_coverage"]["complete"] is True
    assert "equation_artifact_coverage_incomplete" not in with_eq["review_reasons"]


def test_tex_archive_wraps_alignment_equations_for_rendering():
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive

    archive = _make_tex_archive({
        "main.tex": r"""
            \documentclass{article}
            \begin{document}
            \section{Operators}
            \begin{align}
            &O_{V_L} = (\overline{c}\gamma^\mu P_L b)(\overline{\tau}\gamma_\mu P_L\nu_\tau) \nonumber \\
            &O_T = (\overline{c}\sigma^{\mu\nu}P_L b)(\overline{\tau}\sigma_{\mu\nu}P_L\nu_\tau)
            \label{eq:operator}
            \end{align}
            \end{document}
        """,
    })

    structure = build_structure_from_tex_archive(
        archive,
        document_id="doc-tex-align",
        source_file="paper.tar.gz",
    )

    equation = next(b for b in structure.blocks if b.block_type == "equation_block")
    assert equation.raw["latex"].startswith(r"\begin{aligned}")
    assert equation.raw["latex"].endswith(r"\end{aligned}")
    assert r"\nonumber" not in equation.raw["latex"]
    assert equation.equation_label == "eq:operator"


def test_tex_archive_wraps_eqnarray_with_inner_aligned_for_rendering():
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive

    archive = _make_tex_archive({
        "main.tex": r"""
            \documentclass{article}
            \begin{document}
            \section{Kernels}
            \begin{eqnarray}
            &\begin{aligned}
            &\alpha({\bm{k}}_1,{\bm{k}}_2) := 1+\frac{{\bm{k}}_1\cdot{\bm{k}}_2}{k_1^2},\\
            &\gamma({\bm{k}}_1,{\bm{k}}_2) := 1 - (\hat{\bm{k}}_1\cdot\hat{\bm{k}}_2)^2
            \end{aligned}&
            &\begin{aligned}
            &\xi({\bm{k}}_1,{\bm{k}}_2,{\bm{k}}_3) := 1-3(\hat{\bm{k}}_1\cdot\hat{\bm{k}}_2)^2
            \end{aligned}\hspace{-50em}
            \label{eq:functions}
            \end{eqnarray}
            \end{document}
        """,
    })

    structure = build_structure_from_tex_archive(
        archive,
        document_id="doc-tex-eqnarray",
        source_file="paper.tar.gz",
    )

    equation = next(b for b in structure.blocks if b.block_type == "equation_block")
    assert equation.raw["latex"].startswith(r"\begin{aligned} &\begin{aligned}")
    assert equation.raw["latex"].endswith(r"\end{aligned}")
    assert r"\hspace" not in equation.raw["latex"]
    assert equation.equation_label == "eq:functions"


def test_tex_archive_expands_zero_arg_equation_macros():
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive

    archive = _make_tex_archive({
        "main.tex": r"""
            \documentclass{article}
            \def\x{{\bf x}}
            \def\ldef{\equiv}
            \newcommand{\tdelta}{\tilde{\delta}}
            \begin{document}
            \section{Density}
            \begin{equation}
            \delta(t,\x)\ldef
            \frac{\rho(t,\x)-\bar{\rho}(t)}{\bar{\rho}(t)}.
            \label{eq:density}
            \end{equation}
            \[
            \tdelta(t) = \delta(t,\x)
            \]
            \end{document}
        """,
    })

    structure = build_structure_from_tex_archive(
        archive,
        document_id="doc-tex-macros",
        source_file="paper.tar.gz",
    )

    equations = [b.raw["latex"] for b in structure.blocks if b.block_type == "equation_block"]
    assert equations[0] == r"\delta(t,{\bf x})\equiv \frac{\rho(t,{\bf x})-\bar{\rho}(t)}{\bar{\rho}(t)}."
    assert equations[1] == r"\tilde{\delta}(t) = \delta(t,{\bf x})"
    assert all("\\x" not in latex for latex in equations)
    assert all(r"\ldef" not in latex for latex in equations)
    assert all(r"\tdelta" not in latex for latex in equations)


def test_tex_archive_expands_simple_one_arg_equation_macros():
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive

    archive = _make_tex_archive({
        "main.tex": r"""
            \documentclass{article}
            \newcommand{\ev}[1]{\left\langle #1 \right\rangle}
            \def\sq#1{[#1]^2}
            \begin{document}
            \begin{equation}
            \sigma_0^2(R) := \ev{\delta_{gs}^2({\bm{x}};R)}
            \,,\quad
            \sigma_1^2(R) := \ev{\sq{\nabla\delta_{gs}({\bm{x}};R)}}.
            \end{equation}
            \end{document}
        """,
    })

    structure = build_structure_from_tex_archive(
        archive,
        document_id="doc-tex-arg-macro",
        source_file="paper.tar.gz",
    )

    equation = next(b for b in structure.blocks if b.block_type == "equation_block")
    assert equation.raw["latex"] == (
        r"\sigma_0^2(R) := \left\langle \delta_{gs}^2({\bm{x}};R) \right\rangle"
        r" \,,\quad \sigma_1^2(R) := \left\langle [\nabla\delta_{gs}({\bm{x}};R)]^2 \right\rangle."
    )
    assert r"\ev" not in equation.raw["latex"]
    assert r"\sq" not in equation.raw["latex"]


def test_tex_archive_replaces_refs_inside_equations_for_rendering():
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive

    archive = _make_tex_archive({
        "main.tex": r"""
            \documentclass{article}
            \begin{document}
            \begin{equation}
            \begin{aligned}
            \mathcal S &= \textrm{RHS of \eqref{skewness-consistency}},\\
            \mathcal K_1 &= \textrm{RHS of \ref{kurtosis-consistency-1}},\\
            \mathcal K_2 &= \textrm{RHS of \Cref{kurtosis-consistency-2}}.
            \end{aligned}
            \end{equation}
            \end{document}
        """,
    })

    structure = build_structure_from_tex_archive(
        archive,
        document_id="doc-tex-equation-refs",
        source_file="paper.tar.gz",
    )

    equation = next(b for b in structure.blocks if b.block_type == "equation_block")
    assert equation.raw["latex"] == (
        r"\begin{aligned} \mathcal S &= \textrm{RHS of (skewness-consistency)},\\ "
        r"\mathcal K_1 &= \textrm{RHS of kurtosis-consistency-1},\\ "
        r"\mathcal K_2 &= \textrm{RHS of kurtosis-consistency-2}. \end{aligned}"
    )
    assert r"\eqref" not in equation.raw["latex"]
    assert r"\ref" not in equation.raw["latex"]
    assert r"\Cref" not in equation.raw["latex"]


def test_tex_archive_does_not_expand_multi_arg_macros():
    from core.document_pipeline.tex_archive import build_structure_from_tex_archive

    archive = _make_tex_archive({
        "main.tex": r"""
            \documentclass{article}
            \newcommand{\pair}[2]{\left(#1,#2\right)}
            \begin{document}
            \begin{equation}
            \pair{x}{y} = z
            \end{equation}
            \end{document}
        """,
    })

    structure = build_structure_from_tex_archive(
        archive,
        document_id="doc-tex-multi-arg-macro",
        source_file="paper.tar.gz",
    )

    equation = next(b for b in structure.blocks if b.block_type == "equation_block")
    assert equation.raw["latex"] == r"\pair{x}{y} = z"


def test_orchestrator_accepts_tex_archive_source_kind():
    from core.document_pipeline import orchestrator

    archive = _make_tex_archive({
        "main.tex": r"""
            \documentclass{article}
            \author{Smith, John \and Doe, Jane}
            \begin{document}
            \section{Intro}
            TeX source can enter the same agent pipeline.
            \begin{align}
            a &= b
        """,
    })

    @dataclass
    class _Result:
        document_id: str = "doc-tex"
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)
        components: list = field(default_factory=list)
        qualified_spans: list = field(default_factory=list)
        equations: list = field(default_factory=list)
        review_notes: list = field(default_factory=list)

    @dataclass
    class _CourseMappingResult:
        document_id: str = "doc-tex"
        cartridge_id: str | None = None
        topics: list = field(default_factory=list)
        validation_issues: list = field(default_factory=list)

    @dataclass
    class _ComponentGraphResult:
        document_id: str = "doc-tex"
        graph_schema_version: str = "0.1.0"
        cartridge_id: str | None = None
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)
        review_notes: list = field(default_factory=list)
        confidence: float = 0.9
        validation_issues: list = field(default_factory=list)

        def to_dict(self):
            return {"nodes": [], "edges": [], "document_id": self.document_id,
                    "graph_schema_version": self.graph_schema_version,
                    "cartridge_id": self.cartridge_id,
                    "review_notes": self.review_notes,
                    "confidence": self.confidence,
                    "validation_issues": []}

        def to_graph_payload(self):
            return {"graph_schema_version": "0.1.0", "nodes": [], "edges": []}

    agents = {
        "PaperSkeletonAgent": _MockAgent(_Result()),
        "RhetoricalRoleAgent": _MockAgent(_Result()),
        "ClaimQualificationAgent": _MockAgent(_Result()),
        "EquationSemanticsAgent": _MockAgent(_Result()),
        "ThesisReconstructionAgent": _MockAgent(_Result()),
        "DSLLinkingAgent": _MockAgent(_Result()),
        "ComponentAssemblyAgent": _MockAgent(_Result()),
        "ComponentGraphAgent": _MockAgent(_ComponentGraphResult()),
        "CourseMappingAgent": _MockAgent(_CourseMappingResult()),
    }

    visited: list[str] = []
    saved_artifacts: dict[str, dict] = {}

    def fake_upsert(*, run_id=None, document_id, material_id, cartridge_id=None,
                    status="running", current_stage="save_pdf", stage_outputs=None,
                    error_message=None, options=None):
        if stage_outputs and "_artifacts" in stage_outputs:
            saved_artifacts.update(stage_outputs["_artifacts"])
        return run_id or "run-tex"

    fake_persistence = {
        "persist_source_chunks": MagicMock(return_value=[
            {"chunk_id": "c1", "chunk_index": 0, "section_id": "sec_1",
             "block_ids": ["tex_b1"], "page_start": 1, "page_end": 1, "text": "Hello"}
        ]),
        "persist_qualified_claims": MagicMock(return_value=[]),
        "persist_components": MagicMock(return_value={}),
        "persist_component_graph": MagicMock(return_value="graph-tex"),
        "persist_document_embedding": MagicMock(return_value="emb-tex"),
        "upsert_analysis_run": fake_upsert,
    }

    with patch.multiple(orchestrator, **fake_persistence):
        result = orchestrator.run_document_pipeline(
            pdf_bytes=archive,
            document_id="doc-tex",
            material_id="mat-tex",
            filename="paper.tar.gz",
            source_kind="tex_archive",
            agents=agents,
            progress_callback=lambda stage, info: visited.append(stage),
        )

    assert result.final_stage == "completed"
    assert "grobid_parse" in visited
    assert "document_structure" in visited
    assert "source_chunking" in visited
    structure_artifact = saved_artifacts["document_structure"]
    assert structure_artifact["metadata"]["authors"] == ["Smith, John", "Doe, Jane"]
    assert structure_artifact["metadata"]["author_extraction"]["source"] == "tex_author"
    assert structure_artifact["metadata"]["unclosed_math_environments"] == ["align"]
    completeness = saved_artifacts["document_completeness"]
    assert completeness["tail_truncation"]["unclosed_math_environments"] == ["align"]
    assert "equation_environment_cut_at_boundary" in completeness["tail_truncation"]["signals"]


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
        equations: list = field(default_factory=list)
        sections: list = field(default_factory=lambda: [_Section("s1", "Intro", order=1, page_start=1)])
        blocks: list = field(default_factory=lambda: [_Block("b1", 1, 0, "Hello world body.", section_id="s1")])
        review_notes: list = field(default_factory=list)

    @dataclass
    class _CourseMappingResult:
        document_id: str = "doc"
        cartridge_id: str | None = None
        topics: list = field(default_factory=list)
        validation_issues: list = field(default_factory=list)

    structure_result = _Result()

    @dataclass
    class _ComponentGraphResult:
        document_id: str = "doc"
        graph_schema_version: str = "0.1.0"
        cartridge_id: str | None = None
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)
        review_notes: list = field(default_factory=list)
        confidence: float = 0.9
        validation_issues: list = field(default_factory=list)

        def to_dict(self):
            return {"nodes": [], "edges": [], "document_id": self.document_id,
                    "graph_schema_version": self.graph_schema_version,
                    "cartridge_id": self.cartridge_id,
                    "review_notes": self.review_notes,
                    "confidence": self.confidence,
                    "validation_issues": []}

        def to_graph_payload(self):
            return {"graph_schema_version": "0.1.0", "nodes": [], "edges": []}

    agents = {
        "DocumentStructureAgent": _MockAgent(structure_result),
        "PaperSkeletonAgent": _MockAgent(_Result()),
        "RhetoricalRoleAgent": _MockAgent(_Result()),
        "ClaimQualificationAgent": _MockAgent(_Result()),
        "EquationSemanticsAgent": _MockAgent(_Result()),
        "ThesisReconstructionAgent": _MockAgent(_Result()),
        "DSLLinkingAgent": _MockAgent(_Result()),
        "ComponentAssemblyAgent": _MockAgent(_Result()),
        "ComponentGraphAgent": _MockAgent(_ComponentGraphResult()),
        "CourseMappingAgent": _MockAgent(_CourseMappingResult()),
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
        "grobid_parse",
        "document_structure",
        "source_chunking",
        "source_embedding",
        "paper_skeleton",
        "rhetorical_role",
        "claim_qualification",
        "equation_semantics",
        "evidence_registry",
        "claim_object_builder",
        "derivation_chain",
        "figure_table_semantics",
        "thesis_reconstruction",
        "dsl_linking",
        "dsl_embedding",
        "component_assembly",
        "component_graph",
        "course_mapping",
        "blueprint",
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


# --- issue #266: ComponentGraphAgent pipeline integration ----------------


def test_issue_266_component_graph_stage_in_pipeline_stages():
    """PIPELINE_STAGES must include 'component_graph' between component_assembly and course_mapping."""
    from core.document_pipeline.orchestrator import PIPELINE_STAGES

    assert "component_graph" in PIPELINE_STAGES
    ca_idx = PIPELINE_STAGES.index("component_assembly")
    cg_idx = PIPELINE_STAGES.index("component_graph")
    cm_idx = PIPELINE_STAGES.index("course_mapping")
    assert ca_idx < cg_idx < cm_idx, (
        "component_graph must come after component_assembly and before course_mapping"
    )


def test_issue_266_orchestrator_passes_component_graph_result_to_persist():
    """persist_component_graph must be called with component_graph_result kwarg."""
    from core.document_pipeline import orchestrator

    @dataclass
    class _R:
        document_id: str = "doc"
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)
        components: list = field(default_factory=list)
        qualified_spans: list = field(default_factory=list)
        equations: list = field(default_factory=list)
        sections: list = field(default_factory=lambda: [_Section("s1", "Intro", order=1, page_start=1)])
        blocks: list = field(default_factory=lambda: [_Block("b1", 1, 0, "Hello.", section_id="s1")])
        review_notes: list = field(default_factory=list)

    @dataclass
    class _CGR:
        document_id: str = "doc"
        graph_schema_version: str = "0.1.0"
        cartridge_id: str | None = None
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)
        review_notes: list = field(default_factory=list)
        confidence: float = 0.9
        validation_issues: list = field(default_factory=list)

        def to_dict(self):
            return {"nodes": [], "edges": [], "document_id": self.document_id,
                    "graph_schema_version": self.graph_schema_version,
                    "cartridge_id": self.cartridge_id,
                    "review_notes": self.review_notes,
                    "confidence": self.confidence,
                    "validation_issues": []}

        def to_graph_payload(self):
            return {"graph_schema_version": "0.1.0", "nodes": [], "edges": []}

    @dataclass
    class _CMR:
        document_id: str = "doc"
        cartridge_id: str | None = None
        topics: list = field(default_factory=list)
        validation_issues: list = field(default_factory=list)

    component_graph_instance = _CGR()

    agents = {
        "DocumentStructureAgent": _MockAgent(_R()),
        "PaperSkeletonAgent": _MockAgent(_R()),
        "RhetoricalRoleAgent": _MockAgent(_R()),
        "ClaimQualificationAgent": _MockAgent(_R()),
        "EquationSemanticsAgent": _MockAgent(_R()),
        "ThesisReconstructionAgent": _MockAgent(_R()),
        "DSLLinkingAgent": _MockAgent(_R()),
        "ComponentAssemblyAgent": _MockAgent(_R()),
        "ComponentGraphAgent": _MockAgent(component_graph_instance),
        "CourseMappingAgent": _MockAgent(_CMR()),
    }

    persist_mock = MagicMock(return_value="graph-1")
    fake_persistence = {
        "persist_source_chunks": MagicMock(return_value=[
            {"chunk_id": "c1", "chunk_index": 0, "section_id": "s1",
             "block_ids": ["b1"], "page_start": 1, "page_end": 1, "text": "Hello"}
        ]),
        "persist_qualified_claims": MagicMock(return_value=[]),
        "persist_components": MagicMock(return_value={}),
        "persist_component_graph": persist_mock,
        "persist_document_embedding": MagicMock(return_value="emb-1"),
        "upsert_analysis_run": MagicMock(return_value="run-1"),
    }

    with patch.multiple(orchestrator, **fake_persistence):
        result = orchestrator.run_document_pipeline(
            pdf_bytes=b"%PDF-1.4 fake",
            document_id="doc-266",
            material_id="mat-1",
            agents=agents,
        )

    assert result.final_stage == "completed"
    # persist_component_graph must be called with component_graph_result
    call_kwargs = persist_mock.call_args.kwargs
    assert "component_graph_result" in call_kwargs, (
        "persist_component_graph must receive component_graph_result kwarg"
    )
    assert call_kwargs["component_graph_result"] is component_graph_instance


def test_issue_266_persist_component_graph_new_schema():
    """persist_component_graph with ComponentGraphResult produces source_component_id/target_component_id edges."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from episteme_graph.agents.component_graph.schema import (
        ComponentGraphEdge,
        ComponentGraphNode,
        ComponentGraphResult,
        GRAPH_SCHEMA_VERSION,
    )
    from core.document_pipeline.persistence import persist_component_graph

    cg_result = ComponentGraphResult(
        document_id="doc-266",
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=None,
        nodes=[
            ComponentGraphNode("comp_A", "Component A", "TheoryComponent"),
            ComponentGraphNode("comp_B", "Component B", "TheoryComponent"),
        ],
        edges=[
            ComponentGraphEdge(
                edge_id="e1",
                source="comp_A",
                target="comp_B",
                edge_type="REQUIRES",
                support_status="dependency_declared",
                evidence_claims=["claim_001"],
                reasoning="B requires A",
                confidence=1.0,
            )
        ],
        review_notes=[],
        confidence=0.9,
    )

    id_map = {"comp_A": "db-uuid-A", "comp_B": "db-uuid-B"}

    @dataclass
    class _FakeDSL:
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)

    saved_graph = {}

    def fake_session():
        class _Row:
            def __getitem__(self, idx):
                return "graph-id-001"

        class _Exec:
            def fetchone(self):
                return _Row()

        class _Session:
            def execute(self, *a, **kw):
                params = kw or {}
                # Capture graph_json from the last positional params dict
                if a and len(a) >= 2 and isinstance(a[1], dict):
                    import json
                    saved_graph.update(json.loads(a[1].get("graph_json", "{}")))
                return _Exec()

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        return _Session()

    with patch("core.document_pipeline.persistence._pg_session", fake_session):
        persist_component_graph(
            document_id="doc-266",
            component_id_map=id_map,
            component_result=None,
            dsl_result=_FakeDSL(),
            component_graph_result=cg_result,
        )

    # Verify that if the session captured graph_json, it has new schema edges
    if saved_graph.get("edges"):
        e = saved_graph["edges"][0]
        assert "source_component_id" in e, "Edge must use source_component_id, not 'from'"
        assert "target_component_id" in e, "Edge must use target_component_id, not 'to'"
        assert e.get("relation") == "REQUIRES"
        assert e.get("edge_type") == "REQUIRES"
        assert e["source_component_id"] == "db-uuid-A"
        assert e["target_component_id"] == "db-uuid-B"


def test_issue_304_persist_component_graph_keeps_edge_review_reasons():
    """persist_component_graph must keep edge review_reasons (issue #304)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from episteme_graph.agents.component_graph.schema import (
        ComponentGraphEdge,
        ComponentGraphNode,
        ComponentGraphResult,
        GRAPH_SCHEMA_VERSION,
    )
    from core.document_pipeline.persistence import persist_component_graph

    cg_result = ComponentGraphResult(
        document_id="doc-304",
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=None,
        nodes=[
            ComponentGraphNode("comp_A", "Component A", "TheoryComponent"),
            ComponentGraphNode("comp_B", "Component B", "TheoryComponent"),
        ],
        edges=[
            ComponentGraphEdge(
                edge_id="e1",
                source="comp_A",
                target="comp_B",
                edge_type="requires_review",
                support_status="llm_inferred",
                evidence_claims=[],
                reasoning="no backing",
                confidence=0.5,
                review_status="review_required",
                review_reasons=["edge_not_source_backed", "fallback_or_inferred_node"],
            )
        ],
        review_notes=[],
        confidence=0.9,
    )

    id_map = {"comp_A": "db-uuid-A", "comp_B": "db-uuid-B"}

    @dataclass
    class _FakeDSL:
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)

    saved_graph = {}

    def fake_session():
        class _Row:
            def __getitem__(self, idx):
                return "graph-id-304"

        class _Exec:
            def fetchone(self):
                return _Row()

        class _Session:
            def execute(self, *a, **kw):
                if a and len(a) >= 2 and isinstance(a[1], dict):
                    import json
                    saved_graph.update(json.loads(a[1].get("graph_json", "{}")))
                return _Exec()

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        return _Session()

    with patch("core.document_pipeline.persistence._pg_session", fake_session):
        persist_component_graph(
            document_id="doc-304",
            component_id_map=id_map,
            component_result=None,
            dsl_result=_FakeDSL(),
            component_graph_result=cg_result,
        )

    assert saved_graph.get("edges"), "graph_json must contain edges"
    e = saved_graph["edges"][0]
    assert e.get("review_status") == "review_required"
    assert e.get("review_reasons") == [
        "edge_not_source_backed",
        "fallback_or_inferred_node",
    ], "edge review_reasons must survive persistence (issue #304)"


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
    assert "DELETE FROM theory_component_graphs" in sql
    assert "PARTITION BY document_id" in sql
    assert "ADD CONSTRAINT theory_component_graphs_document_uq UNIQUE (document_id)" in sql
    assert "embedding_type" in sql


def test_orchestrator_runs_newly_integrated_agents_and_saves_artifacts():
    """Regression: evidence_registry / claim_object_builder / derivation_chain /
    figure_table_semantics / course_mapping / blueprint must each run and emit
    an artifact entry in `_artifacts`."""
    from core.document_pipeline import orchestrator

    @dataclass
    class _QualifiedSpan:
        span_id: str
        block_id: str
        section_id: str | None
        text: str
        role_labels: list = field(default_factory=list)
        qualification: dict = field(default_factory=lambda: {"status": "accepted", "claim_type_candidate": "result"})
        edit_suggestions: dict = field(default_factory=dict)
        reason: str = ""
        confidence: float = 0.8

    @dataclass
    class _DefinedSymbol:
        symbol: str = "E"
        definition_status: str = "defined"
        evidence_text: str = "energy"

    @dataclass
    class _EquationSemantics:
        equation_type: str = "definition"
        secondary_types: list = field(default_factory=list)
        semantic_status: str = "source_backed"
        confidence: float = 0.9
        reason: str = ""
        defined_symbols: list = field(default_factory=lambda: [_DefinedSymbol()])
        used_symbols: list = field(default_factory=list)
        assumptions: list = field(default_factory=list)
        input_equation_ids: list = field(default_factory=list)
        output_equation_ids: list = field(default_factory=list)
        linked_text_spans: list = field(default_factory=list)
        source_evidence_ids: list = field(default_factory=list)
        linked_claim_ids: list = field(default_factory=list)
        summary: str = ""
        review_flags: list = field(default_factory=list)

    @dataclass
    class _EquationSourceExtraction:
        raw_text: str = ""
        latex: str | None = None
        plain_text: str | None = None
        source_location: dict = field(default_factory=dict)
        extraction_source: str = "pdf_text_layer"
        extraction_status: str = "complete"
        needs_math_review: bool = False
        review_reason: list = field(default_factory=list)

    @dataclass
    class _ConfidencePolicy:
        can_be_used_in_derivation: bool = True
        can_support_claim: bool = True
        can_be_rendered_as_final_formula: bool = True
        allowed_downstream_use: str = "unrestricted"
        can_be_displayed_in_course: bool = True
        display_requires_note: bool = False
        must_not_treat_as_source_extracted: bool = False

    @dataclass
    class _EquationRecord:
        equation_id: str
        document_id: str = "doc-int"
        label: str | None = None
        candidate_trace_ids: list = field(default_factory=list)
        source_extraction: _EquationSourceExtraction = field(default_factory=_EquationSourceExtraction)
        reconstruction: object = None
        semantics: _EquationSemantics = field(default_factory=_EquationSemantics)
        confidence_policy: object = field(default_factory=_ConfidencePolicy)

    @dataclass
    class _Component:
        component_id: str
        component_type: str = "RelationComponent"
        label: str = "Energy Relation"
        summary: str = "Connects E to mc^2"
        inputs: list = field(default_factory=list)
        outputs: list = field(default_factory=list)
        preconditions: list = field(default_factory=list)
        cautions: list = field(default_factory=list)
        dependencies: list = field(default_factory=list)
        evidence_refs: dict = field(default_factory=dict)
        reason: str = ""
        confidence: float = 0.8
        review_notes: list = field(default_factory=list)
        internal_flow: list = field(default_factory=lambda: [{"from": "eq_1", "relation": "derive", "to": "eq_2"}])
        equation_ids: list = field(default_factory=list)

    structure_blocks = [
        _Block("blk_1", 1, 0, "Section content body.", section_id="s1"),
        _Block("blk_eq_1", 1, 1, "E = mc^2", block_type="equation_block", section_id="s1"),
        _Block("blk_fig_1", 1, 2, "Figure 1: schematic of the apparatus.", block_type="figure_caption", section_id="s1"),
    ]

    structure = _Structure(
        document_id="doc-int",
        sections=[_Section("s1", "Intro", order=1, page_start=1)],
        blocks=structure_blocks,
    )

    qualified = type("Q", (), {
        "qualified_spans": [_QualifiedSpan(
            span_id="span_1", block_id="blk_1", section_id="s1", text="A claim text.",
        )],
    })()

    eq_record_1 = _EquationRecord(
        equation_id="eq_1",
        label="1",
        source_extraction=_EquationSourceExtraction(
            source_location={"block_id": "blk_eq_1", "section_id": "s1"},
        ),
        semantics=_EquationSemantics(equation_type="definition"),
    )
    eq_record_2 = _EquationRecord(
        equation_id="eq_2",
        label="2",
        source_extraction=_EquationSourceExtraction(
            source_location={"block_id": "blk_eq_1", "section_id": "s1"},
        ),
        semantics=_EquationSemantics(
            equation_type="result",
            input_equation_ids=["eq_1"],
        ),
    )
    equations = type("E", (), {
        "document_id": "doc-int",
        "equations": [eq_record_1, eq_record_2],
    })()

    component_result = type("C", (), {
        "components": [_Component(component_id="comp_1", equation_ids=["eq_1", "eq_2"])],
    })()

    structure_agent = _MockAgent(structure)
    qualified_agent = _MockAgent(qualified)
    equations_agent = _MockAgent(equations)
    components_agent = _MockAgent(component_result)
    skeleton_agent = _MockAgent(type("S", (), {"document_id": "doc-int"})())
    roles_agent = _MockAgent(type("R", (), {"document_id": "doc-int", "summary_stats": {}})())

    @dataclass
    class _DSLResult:
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)
        review_notes: list = field(default_factory=list)
        document_id: str = "doc-int"

    agents = {
        "DocumentStructureAgent": structure_agent,
        "PaperSkeletonAgent": skeleton_agent,
        "RhetoricalRoleAgent": roles_agent,
        "ClaimQualificationAgent": qualified_agent,
        "EquationSemanticsAgent": equations_agent,
        "ThesisReconstructionAgent": _MockAgent(type("T", (), {"document_id": "doc-int"})()),
        "DSLLinkingAgent": _MockAgent(_DSLResult()),
        "ComponentAssemblyAgent": components_agent,
    }

    saved_artifacts: dict[str, dict] = {}

    def fake_upsert(*, run_id=None, document_id, material_id, cartridge_id=None,
                    status="running", current_stage="save_pdf", stage_outputs=None,
                    error_message=None, options=None):
        if stage_outputs and "_artifacts" in stage_outputs:
            saved_artifacts.update(stage_outputs["_artifacts"])
        return run_id or "run-int"

    fake_persistence = {
        "persist_source_chunks": MagicMock(return_value=[
            {"chunk_id": "c1", "chunk_index": 0, "section_id": "s1",
             "block_ids": ["blk_1"], "page_start": 1, "page_end": 1, "text": "Hello"}
        ]),
        "persist_qualified_claims": MagicMock(return_value=[]),
        "persist_components": MagicMock(return_value={}),
        "persist_component_graph": MagicMock(return_value="graph-1"),
        "persist_document_embedding": MagicMock(return_value="emb-1"),
        "upsert_analysis_run": MagicMock(side_effect=fake_upsert),
    }

    with patch.multiple(orchestrator, **fake_persistence):
        result = orchestrator.run_document_pipeline(
            pdf_bytes=b"%PDF-1.4 fake bytes",
            document_id="doc-int",
            material_id="mat-int",
            cartridge_id=None,
            agents=agents,
        )

    assert result.final_stage == "completed"

    # Each newly-integrated agent must have produced an artifact.
    for stage in ("evidence_registry", "claim_object_builder", "derivation_chain",
                  "figure_table_semantics", "course_mapping", "blueprint"):
        assert stage in saved_artifacts, (
            f"stage {stage} did not save an artifact; saved={sorted(saved_artifacts)}"
        )

    # evidence_registry must register the accepted claim block, equation block,
    # and figure caption block.
    ev_records = saved_artifacts["evidence_registry"]["records"]
    block_ids = {r["source"]["block_id"] for r in ev_records}
    assert {"blk_1", "blk_eq_1", "blk_fig_1"}.issubset(block_ids)

    # claim_object_builder must produce one prose claim per accepted span. Issue
    # #388 additionally synthesises equation/derivation-backed claims (id prefix
    # "synth_claim_"); those are appended after the prose claims.
    claims = saved_artifacts["claim_object_builder"]["claims"]
    prose_claims = [c for c in claims if not str(c["claim_id"]).startswith("synth_claim_")]
    assert len(prose_claims) == 1
    assert prose_claims[0]["source_evidence_ids"], "claim must be source-backed"
    # The synthesised definition claim for the source-backed equation is present.
    synth_claims = [c for c in claims if str(c["claim_id"]).startswith("synth_claim_")]
    assert all(c.get("synthesis_method") for c in synth_claims)
    orchestrator_source = Path(orchestrator.__file__).read_text(encoding="utf-8")
    assert "equation_semantics_result=equations" in orchestrator_source

    # derivation_chain must build at least one chain from eq_1 -> eq_2.
    chains = saved_artifacts["derivation_chain"]["chains"]
    assert chains, "derivation_chain produced no chains"
    assert any("eq_1" in step["input_equation_ids"]
               for chain in chains for step in chain["steps"])
    assert "claim_build_result=claim_objects" in orchestrator_source
    assert "evidence_registry=evidence" in orchestrator_source

    # figure_table_semantics should detect the figure caption.
    figures = saved_artifacts["figure_table_semantics"]["figures"]
    assert figures, "figure_table_semantics produced no figures"

    # course_mapping must produce one topic linked to the component.
    topics = saved_artifacts["course_mapping"]["topics"]
    assert topics
    assert "comp_1" in topics[0]["linked_component_ids"]

    # blueprint must produce a non-empty narrative arc.
    arc = saved_artifacts["blueprint"]["narrative_arc"]
    assert arc


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


def test_export_validation_error_summary_includes_first_errors():
    from core.document_pipeline.orchestrator import _summarize_export_validation_errors

    summary = _summarize_export_validation_errors({
        "errors": [
            {"code": "COMPONENT_MISSING_INTERNAL_FLOW", "message": "component 'c1' has no internal_flow"},
            {"code": "UNRESOLVED_COMPONENT_ID", "message": "topic references missing component"},
            {"code": "SOURCE_BACKED_CLAIM_NO_EVIDENCE_IDS", "message": "claim has no evidence"},
            {"code": "EXTRA", "message": "extra"},
        ]
    })

    assert "COMPONENT_MISSING_INTERNAL_FLOW: component 'c1' has no internal_flow" in summary
    assert "UNRESOLVED_COMPONENT_ID: topic references missing component" in summary
    assert "(+1 more)" in summary


# --- Tier 3-19: PipelineStage 抽象化の構造不変条件 ---------------------------


def test_pipeline_steps_match_pipeline_stages():
    """_PIPELINE_STEPS の named ステージ列は PIPELINE_STAGES（completed を除く）と
    完全一致しなければならない（ステージの脱落・順序ズレ・二重登録の構造的検出）。
    name=None のフックステップ（resume に関係なく毎回実行される中間処理）は対象外。"""
    from core.document_pipeline.orchestrator import PIPELINE_STAGES, _PIPELINE_STEPS

    named = [step.name for step in _PIPELINE_STEPS if step.name is not None]
    expected = [stage for stage in PIPELINE_STAGES if stage != "completed"]
    assert named == expected

    # 各ステップは実行可能な execute を持つ
    assert all(callable(step.execute) for step in _PIPELINE_STEPS)


# --- issue #282: GROBID integration ----------------------------------------


def test_grobid_parse_stage_in_pipeline_stages():
    """grobid_parse must be in PIPELINE_STAGES immediately before document_structure."""
    from core.document_pipeline.orchestrator import PIPELINE_STAGES

    assert "grobid_parse" in PIPELINE_STAGES
    grobid_idx = PIPELINE_STAGES.index("grobid_parse")
    ds_idx = PIPELINE_STAGES.index("document_structure")
    assert grobid_idx == ds_idx - 1, (
        "grobid_parse must be immediately before document_structure"
    )


def test_orchestrator_grobid_fallback_when_unavailable():
    """When GROBID is unavailable, pipeline continues with tei_xml=None (PyMuPDF fallback)."""
    from core.document_pipeline import orchestrator

    @dataclass
    class _Result:
        document_id: str = "doc"
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)
        components: list = field(default_factory=list)
        qualified_spans: list = field(default_factory=list)
        equations: list = field(default_factory=list)
        sections: list = field(default_factory=lambda: [_Section("s1", "Intro", order=1, page_start=1)])
        blocks: list = field(default_factory=lambda: [_Block("b1", 1, 0, "Hello.", section_id="s1")])
        review_notes: list = field(default_factory=list)

    @dataclass
    class _CGR:
        document_id: str = "doc"
        graph_schema_version: str = "0.1.0"
        cartridge_id: str | None = None
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)
        review_notes: list = field(default_factory=list)
        confidence: float = 0.9
        validation_issues: list = field(default_factory=list)

        def to_dict(self):
            return {"nodes": [], "edges": [], "document_id": self.document_id,
                    "graph_schema_version": self.graph_schema_version,
                    "cartridge_id": None, "review_notes": [], "confidence": 0.9,
                    "validation_issues": []}

        def to_graph_payload(self):
            return {"graph_schema_version": "0.1.0", "nodes": [], "edges": []}

    @dataclass
    class _CMR:
        document_id: str = "doc"
        cartridge_id: str | None = None
        topics: list = field(default_factory=list)
        validation_issues: list = field(default_factory=list)

    structure_result = _Result()
    received_tei_xml: list[str | None] = []

    class _CapturingDSAgent:
        def run(self, pdf_path, cartridge_id=None, config=None, tei_xml=None):
            received_tei_xml.append(tei_xml)
            return structure_result

    agents = {
        "DocumentStructureAgent": _CapturingDSAgent(),
        "PaperSkeletonAgent": _MockAgent(_Result()),
        "RhetoricalRoleAgent": _MockAgent(_Result()),
        "ClaimQualificationAgent": _MockAgent(_Result()),
        "EquationSemanticsAgent": _MockAgent(_Result()),
        "ThesisReconstructionAgent": _MockAgent(_Result()),
        "DSLLinkingAgent": _MockAgent(_Result()),
        "ComponentAssemblyAgent": _MockAgent(_Result()),
        "ComponentGraphAgent": _MockAgent(_CGR()),
        "CourseMappingAgent": _MockAgent(_CMR()),
    }

    fake_persistence = {
        "persist_source_chunks": MagicMock(return_value=[
            {"chunk_id": "c1", "chunk_index": 0, "section_id": "s1",
             "block_ids": ["b1"], "page_start": 1, "page_end": 1, "text": "Hello"}
        ]),
        "persist_qualified_claims": MagicMock(return_value=[]),
        "persist_components": MagicMock(return_value={}),
        "persist_component_graph": MagicMock(return_value="graph-1"),
        "persist_document_embedding": MagicMock(return_value="emb-1"),
        "upsert_analysis_run": MagicMock(return_value="run-fallback"),
    }

    # _run_grobid_parse を失敗させて PyMuPDF フォールバックを強制する
    def _grobid_raises(pdf_bytes):
        raise ConnectionError("GROBID not available")

    with patch.multiple(orchestrator, **fake_persistence):
        with patch.object(orchestrator, "_run_grobid_parse", side_effect=_grobid_raises):
            result = orchestrator.run_document_pipeline(
                pdf_bytes=b"%PDF-1.4 fake bytes",
                document_id="doc-grobid-fallback",
                material_id="mat-1",
                agents=agents,
            )

    assert result.final_stage == "completed"
    # DocumentStructureAgent は tei_xml=None で呼ばれるはず
    assert received_tei_xml, "DocumentStructureAgent was not called"
    assert received_tei_xml[0] is None, (
        f"Expected tei_xml=None on GROBID failure, got {received_tei_xml[0]!r}"
    )


def test_orchestrator_passes_tei_xml_to_document_structure_agent():
    """When GROBID succeeds, DocumentStructureAgent receives tei_xml."""
    from core.document_pipeline import orchestrator

    @dataclass
    class _Result:
        document_id: str = "doc"
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)
        components: list = field(default_factory=list)
        qualified_spans: list = field(default_factory=list)
        equations: list = field(default_factory=list)
        sections: list = field(default_factory=lambda: [_Section("s1", "Intro", order=1, page_start=1)])
        blocks: list = field(default_factory=lambda: [_Block("b1", 1, 0, "Hello.", section_id="s1")])
        review_notes: list = field(default_factory=list)

    @dataclass
    class _CGR:
        document_id: str = "doc"
        graph_schema_version: str = "0.1.0"
        cartridge_id: str | None = None
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)
        review_notes: list = field(default_factory=list)
        confidence: float = 0.9
        validation_issues: list = field(default_factory=list)

        def to_dict(self):
            return {"nodes": [], "edges": [], "document_id": self.document_id,
                    "graph_schema_version": self.graph_schema_version,
                    "cartridge_id": None, "review_notes": [], "confidence": 0.9,
                    "validation_issues": []}

        def to_graph_payload(self):
            return {"graph_schema_version": "0.1.0", "nodes": [], "edges": []}

    @dataclass
    class _CMR:
        document_id: str = "doc"
        cartridge_id: str | None = None
        topics: list = field(default_factory=list)
        validation_issues: list = field(default_factory=list)

    FAKE_TEI = "<TEI>fake tei xml</TEI>"
    structure_result = _Result()
    received_tei_xml: list[str | None] = []

    class _CapturingDSAgent:
        def run(self, pdf_path, cartridge_id=None, config=None, tei_xml=None):
            received_tei_xml.append(tei_xml)
            return structure_result

    agents = {
        "DocumentStructureAgent": _CapturingDSAgent(),
        "PaperSkeletonAgent": _MockAgent(_Result()),
        "RhetoricalRoleAgent": _MockAgent(_Result()),
        "ClaimQualificationAgent": _MockAgent(_Result()),
        "EquationSemanticsAgent": _MockAgent(_Result()),
        "ThesisReconstructionAgent": _MockAgent(_Result()),
        "DSLLinkingAgent": _MockAgent(_Result()),
        "ComponentAssemblyAgent": _MockAgent(_Result()),
        "ComponentGraphAgent": _MockAgent(_CGR()),
        "CourseMappingAgent": _MockAgent(_CMR()),
    }

    fake_persistence = {
        "persist_source_chunks": MagicMock(return_value=[
            {"chunk_id": "c1", "chunk_index": 0, "section_id": "s1",
             "block_ids": ["b1"], "page_start": 1, "page_end": 1, "text": "Hello"}
        ]),
        "persist_qualified_claims": MagicMock(return_value=[]),
        "persist_components": MagicMock(return_value={}),
        "persist_component_graph": MagicMock(return_value="graph-1"),
        "persist_document_embedding": MagicMock(return_value="emb-1"),
        "upsert_analysis_run": MagicMock(return_value="run-tei"),
    }

    with patch.multiple(orchestrator, **fake_persistence):
        with patch.object(orchestrator, "_run_grobid_parse", return_value=FAKE_TEI):
            result = orchestrator.run_document_pipeline(
                pdf_bytes=b"%PDF-1.4 fake bytes",
                document_id="doc-tei-pass",
                material_id="mat-2",
                agents=agents,
            )

    assert result.final_stage == "completed"
    assert received_tei_xml, "DocumentStructureAgent was not called"
    assert received_tei_xml[0] == FAKE_TEI, (
        f"Expected tei_xml=FAKE_TEI, got {received_tei_xml[0]!r}"
    )


def test_chunker_metadata_includes_extraction_source_from_grobid_blocks():
    """SourceChunk.metadata includes extraction_source and tei_section_id for GROBID blocks."""
    from core.document_pipeline.chunker import build_source_chunks

    @dataclass
    class _GROBIDBlock:
        block_id: str
        page: int
        order: int
        text: str
        block_type: str = "body_paragraph"
        section_id: str | None = "s1"
        raw: dict = field(default_factory=lambda: {
            "parser_source": "grobid_hybrid",
            "tei_section_id": "div_1",
        })

    @dataclass
    class _GSection:
        section_id: str = "s1"
        title: str = "Introduction"
        level: int = 1
        order: int = 0
        page_start: int = 1
        page_end: int | None = None

    @dataclass
    class _GStructure:
        document_id: str = "doc"
        blocks: list = field(default_factory=list)
        sections: list = field(default_factory=list)

    structure = _GStructure(
        blocks=[
            _GROBIDBlock("g1", 1, 0, "GROBID paragraph one."),
            _GROBIDBlock("g2", 1, 1, "GROBID paragraph two."),
        ],
        sections=[_GSection()],
    )

    chunks = build_source_chunks(structure)
    assert chunks, "No chunks produced"
    for chunk in chunks:
        assert chunk.metadata.get("extraction_source") == "grobid_hybrid", (
            f"Expected extraction_source='grobid_hybrid', got {chunk.metadata}"
        )
        assert chunk.metadata.get("tei_section_id") == "div_1"


# --- component_assembly deterministic fallback handling (#347) --------------

def _fallback_component(component_id="comp_fallback_001"):
    return types.SimpleNamespace(
        component_id=component_id,
        component_type="ClaimBundleComponent",
        label="fallback",
        summary="fallback",
        evidence_refs={"claim_ids": []},
        inputs=[], outputs=[], preconditions=[], cautions=[],
        dependencies=[], internal_flow=[],
        maturity_source="deterministic_fallback",
        fallback_reason="LLM component assembly returned no components",
        review_status="teacher_review_required",
    )


def _normal_component(component_id="comp_001"):
    return types.SimpleNamespace(
        component_id=component_id,
        component_type="RelationComponent",
        label="relation",
        summary="relation",
        evidence_refs={"claim_ids": ["claim_1"]},
        inputs=[], outputs=[], preconditions=[], cautions=[],
        dependencies=[], internal_flow=[],
        maturity_source="llm_proposed",
        fallback_reason="",
        review_status="teacher_review_required",
    )


def test_component_assembly_fallback_info_detects_fallback():
    from core.document_pipeline.orchestrator import _component_assembly_fallback_info

    result = types.SimpleNamespace(
        components=[_fallback_component()],
        diagnostics={
            "fallback_reason": "LLM component assembly returned no components",
            "original_failure_codes": ["no_components"],
        },
    )
    info = _component_assembly_fallback_info(result)
    assert info is not None
    assert info["fallback"] is True
    assert info["fallback_reason"] == "LLM component assembly returned no components"
    assert info["original_failure_codes"] == ["no_components"]
    assert info["fallback_component_count"] == 1


def test_component_assembly_fallback_info_detects_resumed_artifact_without_diagnostics():
    from core.document_pipeline.orchestrator import _component_assembly_fallback_info

    result = types.SimpleNamespace(components=[_fallback_component()], diagnostics={})
    info = _component_assembly_fallback_info(result)
    assert info is not None
    assert info["fallback_reason"] == "LLM component assembly returned no components"
    assert info["fallback_component_count"] == 1


def test_component_assembly_fallback_info_none_for_normal_result():
    from core.document_pipeline.orchestrator import _component_assembly_fallback_info

    result = types.SimpleNamespace(components=[_normal_component()], diagnostics={})
    assert _component_assembly_fallback_info(result) is None


def test_resumed_fallback_component_assembly_artifact_is_not_reusable():
    """fallback artifact は resume で再利用せず stage を再実行する (#347).

    deterministic fallback の artifact は export gate を絶対に通過できないため、
    再利用すると resume のたびに export_validation で同じ hard error になる。
    """
    from core.document_pipeline.orchestrator import _component_assembly_artifact_reusable

    fallback_result = types.SimpleNamespace(
        components=[_fallback_component()],
        diagnostics={
            "fallback_reason": "Repair failed after max attempts",
            "original_failure_codes": ["unresolved_claim_id"],
        },
    )
    assert _component_assembly_artifact_reusable(
        fallback_result, document_id="doc-1", material_id="mat-1"
    ) is False

    normal_result = types.SimpleNamespace(components=[_normal_component()], diagnostics={})
    assert _component_assembly_artifact_reusable(
        normal_result, document_id="doc-1", material_id="mat-1"
    ) is True


def test_persist_components_hard_fails_when_all_components_are_fallback():
    """全件 fallback は silent skip せず hard fail する (#347 review).

    silent に return {} すると同 document の既存 theory_components が
    削除も置換もされないまま run が成功扱いになり、古い通常成果物が
    downstream に見え続けるため。既存 rows は破壊しない（DB 未接続のまま）。
    """
    from core.document_pipeline import persistence

    component_result = types.SimpleNamespace(
        components=[_fallback_component("comp_fallback_001"), _fallback_component("comp_fallback_002")]
    )
    with patch.object(persistence, "_pg_session") as session_factory:
        with pytest.raises(persistence.DeterministicFallbackPersistError) as exc_info:
            persistence.persist_components(
                document_id="doc_1", component_result=component_result
            )
    assert "doc_1" in str(exc_info.value)
    assert "deterministic-fallback" in str(exc_info.value)
    session_factory.assert_not_called()


def test_persist_components_empty_result_returns_empty_without_db_access():
    from core.document_pipeline import persistence

    component_result = types.SimpleNamespace(components=[])
    with patch.object(persistence, "_pg_session") as session_factory:
        id_map = persistence.persist_components(
            document_id="doc_1", component_result=component_result
        )
    assert id_map == {}
    session_factory.assert_not_called()


def test_persist_components_filters_fallback_but_persists_normal_components():
    from core.document_pipeline import persistence

    component_result = types.SimpleNamespace(
        components=[_fallback_component(), _normal_component()]
    )
    session = MagicMock()
    session.execute.return_value.fetchone.return_value = ("db-uuid-1",)
    with patch.object(persistence, "_pg_session", return_value=session):
        id_map = persistence.persist_components(
            document_id="doc_1", component_result=component_result
        )
    assert id_map == {"comp_001": "db-uuid-1"}
    inserted_names = [
        call.args[1].get("name")
        for call in session.execute.call_args_list
        if len(call.args) > 1 and isinstance(call.args[1], dict) and "maturity_source" in call.args[1]
    ]
    assert inserted_names == ["relation"]


# --- F2: persist_components が agent の source_scope を保持する ------------
# (docs/features/figure_concept_linking_design.md F2)
#
# 従来は theory_components.source_scope を {"document_id", "legacy_ids"} で
# 全上書きしていたため、apparatus_components.py の ComponentRecord.source_scope
# に載る figure_id / figure_key / match_status 等が DB 行から失われていた。
# document_id / legacy_ids の上書きセマンティクスは不変（context_lens.py の
# _component_id_lookup_from_rows が legacy_ids=[component_id] に依存する）。


def _apparatus_component(component_id="comp_apparatus_001", label="spectrometer"):
    return types.SimpleNamespace(
        component_id=component_id,
        component_type="apparatus",
        label=label,
        summary="apparatus candidate",
        evidence_refs={"claim_ids": []},
        inputs=[], outputs=[], preconditions=[], cautions=[],
        dependencies=[], internal_flow=[],
        maturity_source="llm_proposed",
        fallback_reason="",
        review_status="teacher_review_required",
        source_scope={
            "source": "apparatus_semantics",
            # agent 側が書く document_id は persist 側で上書きされる想定の値
            # （わざと本来の document_id と異なる値にして上書きを確認する）。
            "document_id": "stale-document-id-from-agent",
            "cartridge_id": "particle_physics",
            "figure_id": "fig-uuid-1",
            "figure_key": "fig_5.2",
            "match_status": "matched",
            "matched_library_entry_id": "lib-entry-1",
            "matched_library_version_no": 3,
            "evidence_quote": "The spectrometer (Fig. 5.2) ...",
            "source_backing_status": "source_backed",
            "repair_failed": False,
        },
    )


def _inserted_params_for(session, name):
    for call in session.execute.call_args_list:
        if len(call.args) > 1 and isinstance(call.args[1], dict) and call.args[1].get("name") == name:
            return call.args[1]
    raise AssertionError(f"no INSERT params captured for name={name!r}")


def test_persist_components_preserves_agent_source_scope_for_apparatus_components():
    """apparatus 候補の figure_id 等が persist 後も DB 行の source_scope に残る。"""
    import json

    from core.document_pipeline import persistence

    component_result = types.SimpleNamespace(components=[_apparatus_component()])
    session = MagicMock()
    session.execute.return_value.fetchone.return_value = ("db-uuid-apparatus",)
    with patch.object(persistence, "_pg_session", return_value=session):
        persistence.persist_components(document_id="doc_1", component_result=component_result)

    params = _inserted_params_for(session, "spectrometer")
    scope = json.loads(params["source_scope"])
    assert scope["source"] == "apparatus_semantics"
    assert scope["cartridge_id"] == "particle_physics"
    assert scope["figure_id"] == "fig-uuid-1"
    assert scope["figure_key"] == "fig_5.2"
    assert scope["match_status"] == "matched"
    assert scope["matched_library_entry_id"] == "lib-entry-1"
    assert scope["matched_library_version_no"] == 3
    assert scope["source_backing_status"] == "source_backed"
    # document_id / legacy_ids は persist 側の値で上書きされる（agent 側の値は捨てる）。
    assert scope["document_id"] == "doc_1"
    assert scope["legacy_ids"] == ["comp_apparatus_001"]


def test_persist_components_claim_derived_source_scope_stays_document_and_legacy_ids_only():
    """source_scope を持たない claim 由来コンポーネントは従来どおりの形のまま（後方互換）。"""
    import json

    from core.document_pipeline import persistence

    component_result = types.SimpleNamespace(components=[_normal_component()])
    session = MagicMock()
    session.execute.return_value.fetchone.return_value = ("db-uuid-1",)
    with patch.object(persistence, "_pg_session", return_value=session):
        persistence.persist_components(document_id="doc_1", component_result=component_result)

    params = _inserted_params_for(session, "relation")
    scope = json.loads(params["source_scope"])
    assert scope == {"document_id": "doc_1", "legacy_ids": ["comp_001"]}


def test_persist_components_legacy_ids_always_component_id():
    """legacy_ids は常に [component_id]（_component_id_lookup_from_rows の依存が不変）。"""
    import json

    from core.document_pipeline import persistence

    component_result = types.SimpleNamespace(
        components=[
            _apparatus_component("comp_apparatus_XYZ", label="apparatus_xyz"),
            _normal_component("comp_XYZ"),
        ]
    )
    session = MagicMock()
    session.execute.return_value.fetchone.return_value = ("db-uuid-x",)
    with patch.object(persistence, "_pg_session", return_value=session):
        persistence.persist_components(document_id="doc_1", component_result=component_result)

    for name, expected_component_id in (
        ("apparatus_xyz", "comp_apparatus_XYZ"),
        ("relation", "comp_XYZ"),
    ):
        params = _inserted_params_for(session, name)
        scope = json.loads(params["source_scope"])
        assert scope["legacy_ids"] == [expected_component_id]


# --- options 継承（画像パイプライン §3-2 / 解析再開の analyze_images 引き継ぎ） ------
#
# バグ修正: reanalyze_document が analyze_images 未指定でも常に明示 False の options を
# 渡していたため、orchestrator の「options is None なら前回 run の options を引き継ぐ」
# 分岐が到達不能だった。ここでは orchestrator 側の継承分岐（全体再解析 = resume=True /
# resume=False の全体再実行の両方）と、route 側が None のとき options=None を渡すことを
# 固定する。パイプライン本体は _PIPELINE_STEPS を1ステップの即時停止に差し替えて
# 走らせない（options の確定はステップループより前に行われる）。


def _run_pipeline_capturing_options(*, options, resume, latest_run):
    from core.document_pipeline import orchestrator

    captured: dict = {}

    def fake_upsert(*, run_id=None, document_id, material_id, cartridge_id=None,
                    status="running", current_stage=None, stage_outputs=None,
                    error_message="", options=None):
        if options is not None and "options" not in captured:
            captured["options"] = options
        return run_id or "run-new"

    fake_get_latest = MagicMock(return_value=latest_run)
    noop_steps = [orchestrator.PipelineStageDef("save_pdf", lambda ctx: True)]
    with patch.object(orchestrator, "get_latest_analysis_run", fake_get_latest), \
         patch.object(orchestrator, "upsert_analysis_run", side_effect=fake_upsert), \
         patch.object(orchestrator, "_PIPELINE_STEPS", noop_steps):
        orchestrator.run_document_pipeline(
            pdf_bytes=b"%PDF-1.4",
            document_id="doc-1",
            material_id="mat-1",
            resume=resume,
            options=options,
        )
    return captured, fake_get_latest


_PREVIOUS_RUN_WITH_IMAGES = {
    "id": "run-prev",
    "status": "completed",
    "current_stage": "completed",
    "stage_outputs": {},
    "options": {"analyze_images": True},
}


def test_orchestrator_inherits_previous_options_when_options_none_full_reanalysis():
    """全体再解析（resume=True・options=None）: 前回 run の analyze_images=True を引き継ぐ。"""
    captured, _ = _run_pipeline_capturing_options(
        options=None, resume=True, latest_run=dict(_PREVIOUS_RUN_WITH_IMAGES),
    )
    assert captured["options"] == {"analyze_images": True}


def test_orchestrator_inherits_previous_options_even_when_resume_false():
    """resume=False の全体再実行（lecture studio の document-pipeline/run 等）でも
    options だけは最新 run から引き継ぐ（artifact 再利用とは独立の関心事）。"""
    captured, fake_get_latest = _run_pipeline_capturing_options(
        options=None, resume=False, latest_run=dict(_PREVIOUS_RUN_WITH_IMAGES),
    )
    assert captured["options"] == {"analyze_images": True}
    assert fake_get_latest.called

def test_orchestrator_explicit_options_override_previous_run():
    """明示 options は前回 run より優先される（明示 False で OFF にできる）。"""
    captured, _ = _run_pipeline_capturing_options(
        options={"analyze_images": False}, resume=True,
        latest_run=dict(_PREVIOUS_RUN_WITH_IMAGES),
    )
    assert captured["options"] == {"analyze_images": False}


def test_orchestrator_no_previous_run_defaults_to_empty_options():
    """前回 run が無ければ空 options（apparatus_semantics は既定 OFF・原則6）。"""
    captured, _ = _run_pipeline_capturing_options(
        options=None, resume=True, latest_run=None,
    )
    assert captured["options"] == {}


# --- reanalyze_document ルート: analyze_images 未指定は options=None を渡す -----------


def _import_admin_routes():
    """routes/admin.py を import する（本ファイルは既定で src しか sys.path に足さない
    ため、API 系テストファイルと同じ backend / backend/api のパス設定をここで行う）。"""
    pytest.importorskip("fastapi")
    backend_dir = Path(__file__).resolve().parents[1]
    for _p in (str(backend_dir), str(backend_dir / "api")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    import routes.admin as admin_mod
    return admin_mod


def _reanalyze_with_mocks(monkeypatch, body):
    admin_mod = _import_admin_routes()

    captured: dict = {}

    class _FakeThread:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def start(self):
            pass

    # P0（オブジェクトスコープ権限）: reanalyze は document owner/editor ゲートを
    # 通ってから canonical document_id / source_path を再利用する。実 DB を引かせない。
    from services import DocumentAccess

    monkeypatch.setattr(
        admin_mod, "resolve_document_access",
        lambda uid, ref: DocumentAccess(
            document_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
            source_path="mat-1", uploaded_by=uid, is_owner=True,
            can_view=True, can_edit=True,
        ),
    )

    # ゲート通過後の最小 SELECT（title, filename）。
    select_session = MagicMock()
    select_session.execute.return_value.fetchone.return_value = ("Title", "file.pdf")
    update_session = MagicMock()
    fake_pg = MagicMock(side_effect=[select_session, update_session])

    storage = MagicMock()
    storage.get_object.return_value = b"%PDF-1.4"

    monkeypatch.setattr(admin_mod, "_pg_session", fake_pg)
    monkeypatch.setattr(admin_mod, "get_storage_client", lambda: storage)
    monkeypatch.setattr(admin_mod, "create_background_task", lambda *a, **k: None)
    monkeypatch.setattr(admin_mod.threading, "Thread", _FakeThread)

    response = admin_mod.reanalyze_document(
        "dddddddd-dddd-dddd-dddd-dddddddddddd",
        body=body,
        current_user={"id": "22222222-2222-2222-2222-222222222222"},
    )
    return captured, response


def test_reanalyze_request_analyze_images_defaults_to_none():
    admin_mod = _import_admin_routes()

    assert admin_mod.ReanalyzeRequest().analyze_images is None


def test_reanalyze_without_body_passes_options_none_for_inheritance(monkeypatch):
    """analyze_images 未指定（body なし）→ options=None を渡し、orchestrator の
    前回 run 継承分岐を生かす（従来は常に {"analyze_images": False} で上書きされ、
    未レビューの AI 図分類が無警告で消えていた）。"""
    captured, response = _reanalyze_with_mocks(monkeypatch, body=None)
    assert captured["kwargs"] == {"options": None, "user_id": "22222222-2222-2222-2222-222222222222"}
    assert response["status"] == "pending"


def test_reanalyze_with_unset_field_passes_options_none(monkeypatch):
    admin_mod = _import_admin_routes()

    captured, _ = _reanalyze_with_mocks(monkeypatch, body=admin_mod.ReanalyzeRequest())
    assert captured["kwargs"] == {"options": None, "user_id": "22222222-2222-2222-2222-222222222222"}


def test_reanalyze_with_explicit_true_passes_true(monkeypatch):
    admin_mod = _import_admin_routes()

    captured, _ = _reanalyze_with_mocks(
        monkeypatch, body=admin_mod.ReanalyzeRequest(analyze_images=True)
    )
    assert captured["kwargs"] == {"options": {"analyze_images": True}, "user_id": "22222222-2222-2222-2222-222222222222"}


def test_reanalyze_with_explicit_false_passes_false(monkeypatch):
    """明示 false は従来どおり上書き（オプトアウトの意思を尊重する）。"""
    admin_mod = _import_admin_routes()

    captured, _ = _reanalyze_with_mocks(
        monkeypatch, body=admin_mod.ReanalyzeRequest(analyze_images=False)
    )
    assert captured["kwargs"] == {"options": {"analyze_images": False}, "user_id": "22222222-2222-2222-2222-222222222222"}


# --- _build_figure_table_semantics claim_link_index (F1 cross-link) ----------
#
# docs/features/figure_concept_linking_design.md F1: the agent's mention-based
# cross-link pass looks the index up with block ids, but claims only carry
# rhetorical-role span ids ("span_001"-style, generated per block). The
# orchestrator must resolve span_id -> block_id via claim_qualification's
# qualified_spans (and via evidence ids, which are block-scoped) so the index
# is keyed by block_id.


class _FigTblSpyAgent:
    """Records the kwargs the orchestrator passes to agent.run()."""

    def __init__(self):
        self.captured = None

    def run(self, structure, *, cartridge_id=None, evidence_index=None, claim_link_index=None):
        self.captured = {
            "structure": structure,
            "cartridge_id": cartridge_id,
            "evidence_index": evidence_index,
            "claim_link_index": claim_link_index,
        }
        return types.SimpleNamespace(figures=[], tables=[], validation_issues=[])


def _fig_tbl_structure():
    return _Structure(
        document_id="doc-figtbl",
        blocks=[
            _Block(block_id="block_A", page=1, order=1, text="Fig. 1 shows the result."),
            _Block(block_id="block_B", page=1, order=2, text="Another paragraph."),
        ],
        sections=[_Section(section_id="sec_a", title="A")],
    )


def _qualified_with(spans):
    return types.SimpleNamespace(
        qualified_spans=[
            types.SimpleNamespace(span_id=s, block_id=b) for (s, b) in spans
        ]
    )


def _claims_with(claims):
    return types.SimpleNamespace(
        claims=[
            types.SimpleNamespace(
                claim_id=cid,
                source_span_ids=list(span_ids),
                source_evidence_ids=list(ev_ids),
            )
            for (cid, span_ids, ev_ids) in claims
        ]
    )


def _run_build_figure_table_semantics(*, claim_objects, qualified, evidence=None):
    from core.document_pipeline.orchestrator import _build_figure_table_semantics

    spy = _FigTblSpyAgent()
    _build_figure_table_semantics(
        agent_classes={"FigureTableSemanticsAgent": spy},
        cartridge_id=None,
        structure=_fig_tbl_structure(),
        evidence=evidence or types.SimpleNamespace(records=[]),
        claim_objects=claim_objects,
        qualified=qualified,
    )
    assert spy.captured is not None, "spy agent.run was never called"
    return spy.captured


def test_fig_tbl_claim_link_index_keyed_by_block_id_via_qualified_spans():
    """span_001 -> block_A の対応（qualified_spans）から block_id キーで索引が作られる。"""
    captured = _run_build_figure_table_semantics(
        claim_objects=_claims_with([("claim_x", ["span_001"], [])]),
        qualified=_qualified_with([("span_001", "block_A")]),
    )
    assert captured["claim_link_index"] == {"block_A": ["claim_x"]}


def test_fig_tbl_claim_link_index_qualified_none_keeps_span_id_keys():
    """qualified=None でも落ちず、従来どおり span_id キーで保持される（防御的縮退）。"""
    captured = _run_build_figure_table_semantics(
        claim_objects=_claims_with([("claim_x", ["span_001"], [])]),
        qualified=None,
    )
    assert captured["claim_link_index"] == {"span_001": ["claim_x"]}


def test_fig_tbl_claim_link_index_unknown_span_id_keeps_span_id_key():
    """マップに無い span_id はそのまま span_id をキーに保持する（情報を落とさない, P4）。"""
    captured = _run_build_figure_table_semantics(
        claim_objects=_claims_with([
            ("claim_x", ["span_001"], []),
            ("claim_y", ["span_zzz"], []),
        ]),
        qualified=_qualified_with([("span_001", "block_A")]),
    )
    assert captured["claim_link_index"] == {
        "block_A": ["claim_x"],
        "span_zzz": ["claim_y"],
    }


def test_fig_tbl_claim_link_index_ambiguous_span_id_not_guessed():
    """span_001 が複数 block に存在する（rhetorical_role の span id は block ごとに
    振り直されるため実運用で頻出）場合、first-wins で誤った block に紐づけず
    span_id キーへ縮退する。"""
    captured = _run_build_figure_table_semantics(
        claim_objects=_claims_with([("claim_x", ["span_001"], [])]),
        qualified=_qualified_with([("span_001", "block_A"), ("span_001", "block_B")]),
    )
    assert captured["claim_link_index"] == {"span_001": ["claim_x"]}


def test_fig_tbl_claim_link_index_evidence_join_resolves_block():
    """claim.source_evidence_ids は block 単位で解決されているため、evidence 経由で
    曖昧な span_id でも正しい block キーに解決できる。"""
    evidence = types.SimpleNamespace(records=[
        types.SimpleNamespace(
            evidence_id="ev_A1",
            source=types.SimpleNamespace(block_id="block_A"),
        ),
    ])
    captured = _run_build_figure_table_semantics(
        claim_objects=_claims_with([("claim_x", ["span_001"], ["ev_A1"])]),
        qualified=_qualified_with([("span_001", "block_A"), ("span_001", "block_B")]),
        evidence=evidence,
    )
    index = captured["claim_link_index"]
    assert index["block_A"] == ["claim_x"]
    # 曖昧 span_id 分は span_id キーで保持される（P4）が、block_B には付かない。
    assert index.get("span_001") == ["claim_x"]
    assert "block_B" not in index


def test_fig_tbl_claim_link_index_dedups_claim_ids_per_block():
    """同一 block に evidence 経由 + span 経由で同じ claim が来ても重複しない。
    複数 claim が同一 block に来る場合は順序を保って並ぶ。"""
    evidence = types.SimpleNamespace(records=[
        types.SimpleNamespace(
            evidence_id="ev_A1",
            source=types.SimpleNamespace(block_id="block_A"),
        ),
    ])
    captured = _run_build_figure_table_semantics(
        claim_objects=_claims_with([
            ("claim_x", ["span_001"], ["ev_A1"]),
            ("claim_y", ["span_002"], ["ev_A1"]),
        ]),
        qualified=_qualified_with([
            ("span_001", "block_A"),
            ("span_002", "block_A"),
        ]),
        evidence=evidence,
    )
    assert captured["claim_link_index"] == {"block_A": ["claim_x", "claim_y"]}


def test_fig_tbl_stage_passes_ctx_qualified():
    """_stage_figure_table_semantics が ctx.qualified を _build_figure_table_semantics
    に渡している（実配線の確認。resume 経路でも ctx.qualified は
    _stage_claim_qualification が artifact から復元する）。"""
    import inspect

    from core.document_pipeline import orchestrator as orch

    src_stage = inspect.getsource(orch._stage_figure_table_semantics)
    assert "qualified=ctx.qualified" in src_stage
    sig = inspect.signature(orch._build_figure_table_semantics)
    assert "qualified" in sig.parameters
    assert sig.parameters["qualified"].default is None
