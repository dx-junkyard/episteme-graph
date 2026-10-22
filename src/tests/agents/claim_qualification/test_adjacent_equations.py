"""IK-0714 claim_qualification 側: 式の導入文・続き文に隣接式（前 1・後 1・同じ小節）を添える。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md seq 3425「is the Hessian …」/ 3428「The last factor …」/
3431「… can be schematically written as [ref]」— 指す式が材料に無く deferred になっていた。
IK-0808: 長いブロックの末尾側の span も原文ブロックの窓に残る。
"""
from episteme_graph.agents.claim_qualification.input_builder import ClaimQualificationInputBuilder
from episteme_graph.agents.claim_qualification.prompt import ClaimQualificationPromptFactory
from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult, SKELETON_VERSION
from episteme_graph.agents.rhetorical_role.schema import (
    BlockRoleAnnotation,
    RhetoricalRoleResult,
    SpanAnnotation,
)

BUILDER = ClaimQualificationInputBuilder()


def _block(block_id, text, block_type, order, section_id="sec_1", raw=None):
    b = TypedBlock(block_id, 1, order, text, block_type)
    b.section_id = section_id
    if raw is not None:
        b.raw = raw
    return b


def _tex_eq(block_id, latex, order, section_id="sec_1", printed="6"):
    return _block(
        block_id, latex, "equation_block", order, section_id,
        raw={"latex": latex, "extraction_source": "tex_source", "printed_numbers": [printed]},
    )


def _structure(blocks):
    return DocumentStructureResult(
        document_id="doc", source_file="/tmp/t.tex", cartridge_id=None,
        metadata=DocumentMetadata(title="T", pages=1),
        sections=[Section("sec_1", "Formalism", 1, 1, 1), Section("sec_2", "Next", 1, 2, 1)],
        blocks=blocks,
    )


def _skeleton():
    return PaperSkeletonResult(
        document_id="doc", skeleton_version=SKELETON_VERSION, cartridge_id=None,
        paper_goal={}, central_question={}, headline_claim={"text": "h"},
        supporting_subclaims=[], logical_blocks=[], excluded_regions=[],
        review_notes=[], confidence=0.8,
    )


def _roles(block_spans):
    annotations = []
    for block_id, texts in block_spans:
        spans = [
            SpanAnnotation(f"span_{i+1:03d}", t, 0, len(t), ["relation"], True, False, 0.8, "r")
            for i, t in enumerate(texts)
        ]
        annotations.append(BlockRoleAnnotation(block_id, "sec_1", "derivation", spans))
    return RhetoricalRoleResult(document_id="doc", cartridge_id=None,
                                role_annotations=annotations, summary_stats={})


def _by_span(inputs, block_id, span_id):
    return [i for i in inputs if i.block_id == block_id and i.span_id == span_id][0]


def test_continuation_block_gets_preceding_tex_equation():
    blocks = [
        _tex_eq("e1", r"F_{ij} = \partial_i \partial_j (-\log L)", 1),
        _block("b2", "is the Hessian of the negative log-likelihood with respect to n.", "body_paragraph", 2),
    ]
    inputs = BUILDER.build(_structure(blocks), _skeleton(), _roles([("b2", [blocks[1].text])]))
    eqs = inputs[0].adjacent_equations
    assert eqs == [{
        "position": "before", "block_id": "e1", "label": None,
        "printed_number": "6", "latex": r"F_{ij} = \partial_i \partial_j (-\log L)",
    }]


def test_named_reference_to_the_preceding_equation_counts():
    blocks = [
        _tex_eq("e1", r"P = \int dn\, L \, \Pi", 1),
        _block("b2", "The last factor encodes the effective volume.", "body_paragraph", 2),
    ]
    inputs = BUILDER.build(_structure(blocks), _skeleton(), _roles([("b2", [blocks[1].text])]))
    assert [e["position"] for e in inputs[0].adjacent_equations] == ["before"]


def test_introduction_gets_following_equation_only_for_last_span():
    text = "We consider PVE. The power spectrum can be schematically written as [ref]"
    blocks = [
        _block("b1", text, "body_paragraph", 1),
        _tex_eq("e2", r"P_{gg} = P_{tree} + P_{1loop} + P_{ctr}", 2, printed="7"),
    ]
    roles = _roles([("b1", ["We consider PVE.", "The power spectrum can be schematically written as [ref]"])])
    inputs = BUILDER.build(_structure(blocks), _skeleton(), roles)
    assert _by_span(inputs, "b1", "span_001").adjacent_equations is None
    eqs = _by_span(inputs, "b1", "span_002").adjacent_equations
    assert [(e["position"], e["printed_number"]) for e in eqs] == [("after", "7")]
    prompt = ClaimQualificationPromptFactory().build_messages(_by_span(inputs, "b1", "span_002"), None)
    user = prompt[-1]["content"]
    assert "## Adjacent Equations" in user and "P_{gg}" in user and "printed_number=(7)" in user
    assert "never turn the equation itself into a claim" in prompt[0]["content"]


def test_no_adjacent_equation_for_topic_change_closed_sentence_or_other_section():
    blocks = [
        _tex_eq("e1", r"a = b", 1),
        _block("b2", "The problem of PVE is well known.", "body_paragraph", 2),
        _tex_eq("e3", r"c = d", 3, section_id="sec_2"),
    ]
    inputs = BUILDER.build(_structure(blocks), _skeleton(), _roles([("b2", [blocks[1].text])]))
    assert inputs[0].adjacent_equations is None


def test_pdf_equation_body_is_not_passed():
    blocks = [
        _block("e1", "F = H (6)", "equation_block", 1),
        _block("b2", "where F is the Fisher matrix.", "body_paragraph", 2),
    ]
    inputs = BUILDER.build(_structure(blocks), _skeleton(), _roles([("b2", [blocks[1].text])]))
    eq = inputs[0].adjacent_equations[0]
    assert eq["latex"] is None
    user = ClaimQualificationPromptFactory().build_messages(inputs[0], None)[-1]["content"]
    assert "[EQUATION_TEXT_FROM_PDF_OMITTED_AS_UNTRUSTED]" in user


def test_tail_span_of_long_block_stays_in_source_block_window():
    """IK-0808 / X2 seq 3374・3389: 上限より後ろの span も Source Block の窓に入る。"""
    head = "These differences remain minimal and we take the agreement as evidence. " * 40
    tail = "This can be understood by noting that R depends on h."
    text = head + tail
    blocks = [_block("b1", text, "body_paragraph", 1)]
    span = SpanAnnotation("span_001", tail, text.index(tail), len(text), ["relation"], True, False, 0.8, "r")
    roles = RhetoricalRoleResult(
        document_id="doc", cartridge_id=None,
        role_annotations=[BlockRoleAnnotation("b1", "sec_1", "derivation", [span])], summary_stats={},
    )
    inputs, coverage = BUILDER.build_with_coverage(_structure(blocks), _skeleton(), roles)
    source = inputs[0].source_block_text
    assert source.endswith(tail) and source.startswith("…")
    assert len(source) <= 1800 + 1
    assert coverage["details"]["truncated_blocks"][0]["block_id"] == "b1"
