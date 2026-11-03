"""2026-10-16 radar_loop（題材 2609.35757・TeX 経路）の所見の回帰。

* S2 seq 2750: span が数式の内側（``$\\hat{N}_{ijk.``）で途切れた → 数式は 1 トークン。
* S1 seq 2722 ほか: ブロック先頭の where / which / i.e. が指す式が材料に無かった
  → 直前 2 ブロック以内の式を ``preceding_equation`` として条件付きで渡す。
"""
from __future__ import annotations

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.paper_skeleton.schema import (
    LogicalBlock,
    PaperSkeletonResult,
    SKELETON_VERSION,
)
from episteme_graph.agents.rhetorical_role.input_builder import RhetoricalRoleInputBuilder
from episteme_graph.agents.rhetorical_role.prompt import RhetoricalRolePromptFactory
from episteme_graph.agents.rhetorical_role.repair import (
    OFFSET_CORRECTION_MATH_BOUNDARY,
    _parse_block_annotation,
)
from episteme_graph.agents.rhetorical_role.schema import RoleLLMInput
from episteme_graph.agents.sentence_split import math_regions, split_sentences

BLOCK = (
    r"where we have defined the tensors $\hat{N}_{ijk. l} = \hat{n}_i \hat{n}_j \hat{n}_k \cdots$. "
    "The products of velocities are tensor fields."
)


def _llm_input(text: str = BLOCK) -> RoleLLMInput:
    return RoleLLMInput(
        document_id="doc", cartridge_id=None, block_id="tex_b66", section_id="sec_3",
        section_title="Moments", backbone_block_type=None, headline_claim=None,
        block_text=text, prev_text=None, next_text=None,
    )


def _span(span_id, text, start, end):
    return {
        "span_id": span_id, "text": text, "char_start": start, "char_end": end,
        "role_labels": ["definition"], "is_claim_candidate": True,
        "is_reject_candidate": False, "confidence": 0.8, "reason": "r",
    }


# --- 文分割器（共有の正本） ----------------------------------------------------


def test_sentence_splitter_does_not_cut_inside_inline_math():
    sentences = split_sentences(BLOCK)
    assert sentences == [
        r"where we have defined the tensors $\hat{N}_{ijk. l} = \hat{n}_i \hat{n}_j \hat{n}_k \cdots$.",
        "The products of velocities are tensor fields.",
    ]
    assert split_sentences(r"See \(a. b\) and \[c. d\] here. Next.") == [
        r"See \(a. b\) and \[c. d\] here.", "Next."
    ]


def test_math_regions_ignore_escaped_and_unclosed_dollars():
    assert math_regions(r"costs \$5. Then $x$ and $y") == [(16, 19)]


# --- rhetorical_role の span 境界 ----------------------------------------------


def test_span_ending_inside_math_is_extended_to_the_math_end():
    cut = BLOCK.index("ijk.") + len("ijk.")
    first = BLOCK[:cut]
    second_start = BLOCK.index("The products")
    raw = {
        "block_id": "tex_b66",
        "span_annotations": [
            _span("span_001", first, 0, cut),
            _span("span_002", BLOCK[second_start:], second_start, len(BLOCK)),
        ],
    }
    annotation = _parse_block_annotation(raw, _llm_input())
    span1, span2 = annotation.span_annotations
    assert span1.text.endswith(r"\cdots$.")
    assert span1.offset_correction == OFFSET_CORRECTION_MATH_BOUNDARY
    assert BLOCK[span1.char_start:span1.char_end] == span1.text
    assert span2.text.startswith("The products")
    assert span2.offset_correction is None


def test_span_starting_inside_math_after_covering_span_moves_past_the_math():
    math_end = BLOCK.index("$. ") + 1
    inner = BLOCK.index(r"\hat{n}_i")
    raw = {
        "block_id": "tex_b66",
        "span_annotations": [
            _span("span_001", BLOCK[:math_end], 0, math_end),
            _span("span_002", BLOCK[inner:], inner, len(BLOCK)),
        ],
    }
    span2 = _parse_block_annotation(raw, _llm_input()).span_annotations[1]
    assert span2.text.startswith("The products")
    assert span2.offset_correction == OFFSET_CORRECTION_MATH_BOUNDARY


def test_prompt_states_math_is_one_token():
    messages = RhetoricalRolePromptFactory().build_messages(_llm_input(), None)
    assert "never start or end a span inside inline math" in messages[-1]["content"]


# --- preceding_equation -------------------------------------------------------


def _typed(block_id, text, block_type, order, *, tex=True, section_id="sec_1"):
    b = TypedBlock(block_id, 1, order, text, block_type,
                   raw={"parser_source": "tex_archive"} if tex else {})
    b.section_id = section_id
    return b


def _structure(blocks):
    return DocumentStructureResult(
        document_id="doc", source_file="paper.tar.gz", cartridge_id=None,
        metadata=DocumentMetadata(title="T", pages=1),
        sections=[Section("sec_1", "Model", 1, 1, 1), Section("sec_2", "Next", 1, 1, 1)],
        blocks=blocks,
    )


def _skeleton():
    return PaperSkeletonResult(
        document_id="doc", skeleton_version=SKELETON_VERSION, cartridge_id=None,
        paper_goal={}, central_question={}, headline_claim={"text": "h"},
        supporting_subclaims=[],
        logical_blocks=[LogicalBlock("l1", "derivation", "D", ["sec_1"], ["p1"], "s", "r", 0.8)],
        excluded_regions=[], review_notes=[], confidence=0.8,
    )


def _inputs(blocks):
    return {i.block_id: i for i in RhetoricalRoleInputBuilder().build(_structure(blocks), _skeleton())}


def test_preceding_equation_is_attached_only_when_an_equation_is_close():
    inputs = _inputs([
        _typed("p0", "We start from the moment expansion.", "body_paragraph", 0),
        _typed("e1", r"\hat{N}_{ijk} = \hat{n}_i \hat{n}_j \hat{n}_k \label{eq:n}", "equation_block", 1),
        _typed("p1", "where the hats denote unit vectors.", "body_paragraph", 2),
        _typed("p2", "i.e. the tensors are symmetric.", "body_paragraph", 3),
        _typed("p3", "A third paragraph.", "body_paragraph", 4),
        _typed("p4", "Far away from any equation.", "body_paragraph", 5),
    ])
    assert inputs["p0"].preceding_equation is None
    eq = inputs["p1"].preceding_equation
    assert eq["block_id"] == "e1" and r"\hat{N}_{ijk}" in eq["latex"]
    # IK-0714 精密化（2026-10-17）: 直前の文脈ブロックが式のときだけ（間に段落があれば渡さない）。
    assert inputs["p2"].preceding_equation is None
    assert inputs["p3"].preceding_equation is None
    assert inputs["p4"].preceding_equation is None
    content = RhetoricalRolePromptFactory().build_messages(inputs["p1"], None)[-1]["content"]
    assert "## Preceding Equation" in content and r"\hat{N}_{ijk}" in content
    content0 = RhetoricalRolePromptFactory().build_messages(inputs["p0"], None)[-1]["content"]
    assert "## Preceding Equation" not in content0


def test_preceding_equation_does_not_cross_sections_or_trust_pdf_math():
    inputs = _inputs([
        _typed("e1", "x = y", "equation_block", 0),
        _typed("p1", "where y is fixed.", "body_paragraph", 1, section_id="sec_2"),
        _typed("e2", "E = mc 2 (3)", "equation_block", 2, tex=False, section_id="sec_2"),
        _typed("p2", "where m is the mass.", "body_paragraph", 3, tex=False, section_id="sec_2"),
    ])
    assert inputs["p1"].preceding_equation is None
    pdf_eq = inputs["p2"].preceding_equation
    assert pdf_eq["block_id"] == "e2" and pdf_eq["latex"] is None


# --- 2026-10-17 X1（2610.08751）: 話題の変わった段落に前の式を渡さない ------------


def test_preceding_equation_not_given_to_a_new_topic_paragraph_3334_3361():
    inputs = _inputs([
        _typed("b51", "This effect can be mitigated by a non-flat prior.", "body_paragraph", 0),
        _typed("b52", r"\Pi_{\rm Jeffreys} \propto \sqrt{\det F}\label{eq:jeffreys_prior}",
               "equation_block", 1),
        _typed("b53", "which, when substituted in Eq. (6), cancels the factor.", "body_paragraph", 2),
        _typed("b54", "The problem of PVE can nonetheless be visualised through a toy model.",
               "body_paragraph", 3),
        _typed("b96", r"\log L = -\frac{1}{2} r^T C^{-1} r", "equation_block", 4),
        _typed("b97", "up to an additive constant. Here d and t denote data and theory.",
               "body_paragraph", 5),
        _typed("b98", "The prior ranges adopted for the baseline configurations are listed.",
               "body_paragraph", 6),
        _typed("b99", r"x = y", "equation_block", 7),
        _typed("b100", "In this work we adopt flat priors.", "body_paragraph", 8),
    ])
    assert inputs["b53"].preceding_equation["block_id"] == "b52"  # which … 続き
    assert inputs["b54"].preceding_equation is None  # 3334: 新しい話題
    assert inputs["b97"].preceding_equation["block_id"] == "b96"  # 小文字始まりの続き
    assert inputs["b98"].preceding_equation is None  # 3361: 間に段落・新しい話題
    assert inputs["b100"].preceding_equation is None  # 直前が式でも続き語でない


def test_continuation_words():
    from episteme_graph.agents.rhetorical_role.input_builder import starts_as_continuation

    for text in ("where a is", "Where a is", "i.e. the", "This is because x", "In the second line, we",
                 "Here, d is", "which gives", "that is, x"):
        assert starts_as_continuation(text), text
    for text in ("The problem of PVE", "In this work we", "We now turn", "Figure 2 shows", ""):
        assert not starts_as_continuation(text), text


def test_previous_context_from_another_section_is_labelled_3376():
    inputs = _inputs([
        _typed("b122", "As a further check, in Appendix D we repeat the tests.", "body_paragraph", 0,
               section_id="sec_1"),
        _typed("b123", "Robustness", "subsection_heading", 1, section_id="sec_2"),
        _typed("b124", "To assess the robustness of our conclusions, we repeat the analysis.",
               "body_paragraph", 2, section_id="sec_2"),
        _typed("b125", "Figure 7 compares the posteriors.", "body_paragraph", 3, section_id="sec_2"),
    ])
    assert inputs["b124"].prev_text_other_section is True
    assert inputs["b125"].prev_text_other_section is False
    factory = RhetoricalRolePromptFactory()
    content = factory.build_messages(inputs["b124"], None)[-1]["content"]
    assert "## Previous Context (from the previous section, not part of this section)" in content
    content2 = factory.build_messages(inputs["b125"], None)[-1]["content"]
    assert "## Previous Context\n" in content2


def test_previous_context_not_duplicated_when_equation_is_shown():
    inputs = _inputs([
        _typed("e1", r"\hat{N}_{ijk} = \hat{n}_i", "equation_block", 0),
        _typed("p1", "where the hats denote unit vectors.", "body_paragraph", 1),
    ])
    content = RhetoricalRolePromptFactory().build_messages(inputs["p1"], None)[-1]["content"]
    assert "## Preceding Equation" in content
    assert "## Previous Context" not in content
    assert content.count(r"\hat{N}_{ijk}") == 1
