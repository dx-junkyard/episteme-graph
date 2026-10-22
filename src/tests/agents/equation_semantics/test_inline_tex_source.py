"""inline 式候補の切り出し元と、値の指定の除外（2610.19001 の radar_loop 所見）。

事実: TeX 経路の教材でも inline 候補が ``inline_pdf_text_layer`` / partial 扱いになり再構成必須に
なっていた。PDF 向けの正規表現が ``$…$`` の原文を途中で切り（``\\mathrm{halo}} = K_{ij}`` /
``0.1`` → ``= 0`` / ``M_{\\rm min}=10`` で ``^{11}`` が落ちる）、値の言及（``z=0`` / ``a=5`` /
質量閾値）まで式候補として LLM に流れ、後段に偽の依存を作っていた。ここで固定するのは:
  (a) TeX 経路の inline は ``$…$`` / ``\\(…\\)`` の原文境界で切り出し、``tex_source`` として
      再構成しない（needs_reconstruction=False・プロンプトは Source-backed TeX policy）
  (b) 値の代入・パラメータ指定・閾値は式候補にせず coverage の details に理由付きで残す
  (c) PDF 経路の正規表現は小数点の途中で切らない
"""
import pytest

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.equation_semantics.acceptance_gate import EquationAcceptanceGate
from episteme_graph.agents.equation_semantics.input_builder import (
    EquationSemanticsInputBuilder,
    classify_inline_relation,
)
from episteme_graph.agents.equation_semantics.prompt import EquationSemanticsPromptFactory
from episteme_graph.agents.equation_semantics.reconstruction_layer import (
    EquationReconstructionLayer,
)

BUILDER = EquationSemanticsInputBuilder()


@pytest.fixture(autouse=True)
def _no_env_limit(monkeypatch):
    monkeypatch.delenv("EQUATION_SEMANTICS_MAX_EQUATIONS", raising=False)
    monkeypatch.delenv("EQUATION_SEMANTICS_MAX_INLINE_EQUATIONS", raising=False)


def _block(block_id, text, *, tex=True, order=0):
    raw = {"parser_source": "tex_archive"} if tex else {}
    b = TypedBlock(block_id, 1, order, text, "body_paragraph", raw=raw)
    b.section_id = "sec_1"
    return b


def _structure(blocks):
    return DocumentStructureResult(
        document_id="doc",
        source_file="/tmp/paper.tar.gz",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Halo model", 1, 1, 1)],
        blocks=blocks,
    )


TEX_PARAGRAPH = (
    r"The halo term is $P_{\mathrm{halo}} = K_{ij} x_i x_j$ and we fix $\epsilon = 0.1$ "
    r"at $z=0$. Extending the estimate down to $M_{\rm min}=10^{11}M_\odot/h$ uses "
    r"$R_h = 3R_{\rm vir}$ and a Plummer sphere with $a=5$; the radius $R_D$ is free."
)


def test_tex_inline_is_cut_at_the_dollar_boundaries():
    structure = _structure([_block("tex_b1", TEX_PARAGRAPH)])
    texts = [c.raw_text for c in BUILDER.build_candidates(structure)]
    # 原文境界で切る — 添字や小数点の途中で切らない。
    assert texts == [r"P_{\mathrm{halo}} = K_{ij} x_i x_j", r"R_h = 3R_{\rm vir}"]
    for c in BUILDER.build_candidates(structure):
        assert c.source_location["extraction_source"] == "tex_source"
        assert "tex_source" in c.detection_method
        assert c.review_reason == []


@pytest.mark.parametrize(
    "expr",
    [
        "z=0", "k=0", "a=5", r"\alpha= 3", r"\epsilon = 0.1", "z=-0.75", "0<z<1",
        r"M_{\rm min}=10^{11}M_\odot/h", r"M_{\rm vir}>10^{12}\,h^{-1}M_\odot",
        r"T = 10^4\,{\rm K}", r"\sim 10^{12}",
    ],
)
def test_value_specifications_are_not_equation_candidates(expr):
    assert classify_inline_relation(expr) == "value_specification"


@pytest.mark.parametrize(
    "expr",
    [r"R_h=3R_{\rm vir}", r"L>2R_\Lambda", r"c_s^2 = \partial P/\partial\rho",
     r"k \le k_{\rm max}"],
)
def test_symbol_relations_stay_candidates(expr):
    assert classify_inline_relation(expr) is None


def test_mentions_without_relation_are_not_candidates():
    assert classify_inline_relation("R_D") == "no_relation"


def test_excluded_value_specifications_are_listed_with_reason_in_coverage():
    facts = BUILDER.compute_candidate_coverage(_structure([_block("tex_b1", TEX_PARAGRAPH)]))
    # 値の指定は式ではないので母集合に入れない（取りこぼしではない）。
    assert facts["population"] == 2
    excluded = facts["details"]["excluded_inline"]
    assert {e["reason"] for e in excluded} == {"value_specification"}
    assert [e["text"] for e in excluded] == [
        r"\epsilon = 0.1", "z=0", r"M_{\rm min}=10^{11}M_\odot/h", "a=5",
    ]
    assert facts["details"]["excluded_inline_listing_truncated"] is False


def test_tex_inline_needs_no_reconstruction_and_is_shown_as_trusted_source():
    structure = _structure([_block("tex_b1", TEX_PARAGRAPH)])
    candidates = EquationAcceptanceGate().process(BUILDER.build_candidates(structure))
    candidates = EquationReconstructionLayer().prepare_candidates(candidates)
    inputs = BUILDER.build_llm_inputs(
        structure, candidates, accepted_statuses={"accepted", "provisional"},
        force_reconstruction=True,
    )
    assert len(inputs) == 2
    first = inputs[0]
    assert first.extraction_source == "tex_source"
    assert first.source_is_trusted is True
    assert first.needs_reconstruction is False
    assert first.latex == r"P_{\mathrm{halo}} = K_{ij} x_i x_j"
    # 原文は伏せず、対象の位置を印で示す。
    context = first.prev_texts[-1]
    assert "[INLINE_MATH_TARGET]$P_{\\mathrm{halo}} = K_{ij} x_i x_j$[/INLINE_MATH_TARGET]" in context
    assert "OMITTED_AS_UNTRUSTED" not in context
    content = EquationSemanticsPromptFactory().build_messages(first, None)[-1]["content"]
    assert "Source-backed TeX policy" in content
    assert "Mandatory reconstruction policy" not in content


def test_pdf_inline_keeps_the_untrusted_route_and_does_not_cut_decimals():
    text = "Here the coupling eps = 0.1 is fixed and the scale obeys L = k R for the sample."
    structure = _structure([_block("b1", text, tex=False)])
    candidates = BUILDER.build_candidates(structure)
    assert [c.raw_text for c in candidates] == ["L = k"]  # 値の指定（eps = 0.1）は除外
    assert candidates[0].review_reason == ["inline_pdf_math_untrusted"]
    excluded = BUILDER.compute_candidate_coverage(structure)["details"]["excluded_inline"]
    assert excluded == [{"block_id": "b1", "text": "eps = 0.1", "reason": "value_specification"}]


def test_agent_records_tex_inline_as_source_backed_without_reconstruction():
    """エージェントを通しても TeX inline は再構成されず、source_backed の式として残る。"""
    from unittest.mock import patch

    from episteme_graph.agents.equation_semantics.agent import EquationSemanticsAgent

    structure = _structure([_block("tex_b1", r"We define $R_h = 3R_{\rm vir}$ at $z=0$.")])
    response = {
        "equation_id": "eq_x", "label": None, "equation_type": "definition",
        "secondary_types": [], "semantic_status": "context_inferred", "confidence": 0.9,
        "reason": "mocked", "defined_symbols": [], "used_symbols": [], "assumptions": [],
        "input_equation_ids": [], "output_equation_ids": [], "linked_text_spans": [],
        "summary": "Halo radius definition.", "review_flags": [],
    }
    agent = EquationSemanticsAgent()
    with patch.object(agent, "_llm_client") as mock_llm:
        mock_llm.generate.return_value = response
        result = agent.run(structure)
    assert mock_llm.generate.call_count == 1  # z=0 は LLM に流れない
    record = result.equations[0]
    assert record.source_extraction.extraction_source == "tex_source"
    assert record.source_extraction.latex == r"R_h = 3R_{\rm vir}"
    assert record.reconstruction.status == "none"
    assert record.semantics.semantic_status == "source_backed"
    assert record.confidence_policy.must_not_treat_as_source_extracted is False
