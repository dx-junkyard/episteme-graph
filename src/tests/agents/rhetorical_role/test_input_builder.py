"""Tests for RhetoricalRoleInputBuilder."""
from episteme_graph.agents.coverage_report import is_coverage_report
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
from episteme_graph.agents.rhetorical_role.schema import CartridgeContext

BUILDER = RhetoricalRoleInputBuilder()


def _typed(block_id, text, block_type, order=0, section_id="sec_1"):
    b = TypedBlock(block_id, 1, order, text, block_type)
    b.section_id = section_id
    return b


def _structure(blocks):
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Derivation", 1, 1, 1)],
        blocks=blocks,
    )


def _skeleton():
    return PaperSkeletonResult(
        document_id="doc_test",
        skeleton_version=SKELETON_VERSION,
        cartridge_id=None,
        paper_goal={"text": "goal", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        central_question={"text": "question", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        headline_claim={"text": "headline", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        supporting_subclaims=[],
        logical_blocks=[
            LogicalBlock("logic_1", "derivation", "Derivation", ["sec_1"], ["b1"], "summary", "reason", 0.8)
        ],
        excluded_regions=[],
        review_notes=[],
        confidence=0.8,
    )


def test_build_targets_body_paragraphs_only_by_default():
    structure = _structure([
        _typed("h1", "1 Introduction", "section_heading", order=0),
        _typed("b1", "We assume a narrow-width limit.", "body_paragraph", order=1),
        _typed("e1", "E = mc^2", "equation_block", order=2),
        _typed("f1", "Figure 1 shows...", "figure_caption", order=3),
    ])
    inputs = BUILDER.build(structure, _skeleton())
    assert [i.block_id for i in inputs] == ["b1"]


def test_build_can_include_equation_blocks():
    structure = _structure([
        _typed("b1", "Body.", "body_paragraph", order=1),
        _typed("e1", "E = mc^2", "equation_block", order=2),
    ])
    inputs = BUILDER.build(structure, _skeleton(), config={"include_equation_blocks": True})
    assert [i.block_id for i in inputs] == ["b1", "e1"]


def test_backbone_and_headline_context_propagated():
    structure = _structure([_typed("b1", "By integrating both sides, ...", "body_paragraph")])
    role_input = BUILDER.build(structure, _skeleton())[0]
    assert role_input.backbone_block_type == "derivation"
    assert role_input.headline_claim == "headline"
    assert role_input.section_title == "Derivation"


def test_neighbor_context_uses_adjacent_body_or_equation():
    structure = _structure([
        _typed("b0", "Previous paragraph.", "body_paragraph", order=0),
        _typed("b1", "Current paragraph.", "body_paragraph", order=1),
        _typed("e1", "Equation context.", "equation_block", order=2),
    ])
    role_input = [i for i in BUILDER.build(structure, _skeleton()) if i.block_id == "b1"][0]
    assert role_input.prev_text == "Previous paragraph."
    assert role_input.next_text == "Equation context."


def test_normalized_terms_from_cartridge_aliases():
    cartridge = CartridgeContext(
        cartridge_id="test",
        ontology={},
        validation_rules={},
        aliases={"R Lambda c": ["RΛc", "R_Lambdac"]},
    )
    structure = _structure([_typed("b1", "RΛc is constrained.", "body_paragraph")])
    role_input = BUILDER.build(structure, _skeleton(), cartridge=cartridge)[0]
    assert role_input.cartridge_id == "test"
    assert role_input.normalized_terms is not None
    assert role_input.normalized_terms[0]["canonical"] == "R Lambda c"


# ---------------------------------------------------------------------------
# P0-1: 64 打ち切りの廃止 / ソートキー / 層化サンプリング / coverage 報告
# ---------------------------------------------------------------------------

def _multi_section_structure(sections_spec, page_of=None):
    """``[(section_id, 件数), ...]`` から複数節のブロックを組む。

    ``page_of(order)`` を渡すと page を差し替えられる（GROBID の page=1 誤付与の再現）。
    order は文書全体で単調増加する通し番号（実パーサと同じ意味）。
    """
    blocks = []
    sections = []
    order = 0
    for section_id, count in sections_spec:
        sections.append(Section(section_id, f"Title {section_id}", 1, len(sections), 1))
        for _ in range(count):
            block = TypedBlock(
                f"blk_{order}",
                page_of(order) if page_of else 1,
                order,
                f"Paragraph {order}.",
                "body_paragraph",
            )
            block.section_id = section_id
            blocks.append(block)
            order += 1
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=sections,
        blocks=blocks,
    )


def test_default_has_no_block_limit(monkeypatch):
    """既定は上限なし。かつての 64 打ち切りで落ちていた 65 件目以降も入力になる。"""
    monkeypatch.delenv("RHETORICAL_ROLE_MAX_BLOCKS", raising=False)
    structure = _multi_section_structure([("sec_1", 200)])
    inputs = BUILDER.build(structure, _skeleton())
    assert len(inputs) == 200
    assert inputs[64].block_id == "blk_64"


def test_env_max_blocks_is_used_as_default(monkeypatch):
    monkeypatch.setenv("RHETORICAL_ROLE_MAX_BLOCKS", "10")
    structure = _multi_section_structure([("sec_1", 40)])
    assert len(BUILDER.build(structure, _skeleton())) == 10
    # config の明示指定は env より優先する
    assert len(BUILDER.build(structure, _skeleton(), config={"max_blocks": 4})) == 4


def test_invalid_env_max_blocks_means_no_limit(monkeypatch):
    monkeypatch.setenv("RHETORICAL_ROLE_MAX_BLOCKS", "not-a-number")
    structure = _multi_section_structure([("sec_1", 70)])
    assert len(BUILDER.build(structure, _skeleton())) == 70


def test_max_blocks_stratifies_across_sections(monkeypatch):
    """先頭切り捨てではなく節単位の層化サンプリング（3節×10件 / 上限6 → 各2件）。"""
    monkeypatch.delenv("RHETORICAL_ROLE_MAX_BLOCKS", raising=False)
    structure = _multi_section_structure([("sec_1", 10), ("sec_2", 10), ("sec_3", 10)])
    inputs = BUILDER.build(structure, _skeleton(), config={"max_blocks": 6})

    by_section: dict[str, list[str]] = {}
    for role_input in inputs:
        by_section.setdefault(role_input.section_id, []).append(role_input.block_id)
    assert {k: len(v) for k, v in by_section.items()} == {"sec_1": 2, "sec_2": 2, "sec_3": 2}
    # 節内は順序どおり先頭から
    assert by_section["sec_2"] == ["blk_10", "blk_11"]
    # 先頭切り捨てなら sec_3 は 1件も入らない
    assert by_section["sec_3"] == ["blk_20", "blk_21"]


def test_max_blocks_smaller_than_section_count_keeps_one_each(monkeypatch):
    monkeypatch.delenv("RHETORICAL_ROLE_MAX_BLOCKS", raising=False)
    structure = _multi_section_structure([("sec_1", 3), ("sec_2", 5), ("sec_3", 4)])
    inputs = BUILDER.build(structure, _skeleton(), config={"max_blocks": 2})
    # 対象数の多い節（sec_2 -> sec_3）から1件ずつ
    assert [i.section_id for i in inputs] == ["sec_2", "sec_3"]


def test_sort_uses_order_only_when_page_is_unreliable(monkeypatch):
    """GROBID が多数のブロックに page=1 を誤付与しても order 順で並ぶ。"""
    monkeypatch.delenv("RHETORICAL_ROLE_MAX_BLOCKS", raising=False)
    # order 0,2,4,... は page=1 のまま（突合失敗）、奇数だけ実ページに書き換わった状況
    structure = _multi_section_structure(
        [("sec_1", 8)], page_of=lambda order: 1 if order % 2 == 0 else order + 1
    )
    inputs, coverage = BUILDER.build_with_coverage(structure, _skeleton())
    assert [i.block_id for i in inputs] == [f"blk_{n}" for n in range(8)]
    assert coverage["details"]["sort_key"] == "order"


def test_sort_falls_back_to_page_order_when_order_is_not_unique(monkeypatch):
    monkeypatch.delenv("RHETORICAL_ROLE_MAX_BLOCKS", raising=False)
    structure = _multi_section_structure([("sec_1", 3)])
    # order を重複させる（通し番号として信用できない状態）
    structure.blocks[2].order = 0
    structure.blocks[2].page = 5
    _, coverage = BUILDER.build_with_coverage(structure, _skeleton())
    assert coverage["details"]["sort_key"] == "page_order"


def test_coverage_report_shape_when_truncated(monkeypatch):
    monkeypatch.delenv("RHETORICAL_ROLE_MAX_BLOCKS", raising=False)
    structure = _multi_section_structure([("sec_1", 4), ("sec_2", 4), ("sec_3", 4)])
    inputs, coverage = BUILDER.build_with_coverage(
        structure, _skeleton(), config={"max_blocks": 3}
    )
    assert is_coverage_report(coverage)
    assert coverage["population"] == 12
    assert coverage["processed"] == len(inputs) == 3
    assert coverage["truncated"] == coverage["population"] - coverage["processed"]
    assert coverage["reasons"] == ["max_blocks"]
    assert coverage["unit"] == "blocks"
    unprocessed = coverage["details"]["unprocessed_sections"]
    assert [s["section_id"] for s in unprocessed] == ["sec_1", "sec_2", "sec_3"]
    assert unprocessed[0]["title"] == "Title sec_1"
    # 件数は population / processed / truncated で尽くす（details に載せない）
    assert all(
        not isinstance(value, int) for value in coverage["details"].values()
    )


def test_coverage_report_has_no_reasons_when_not_truncated(monkeypatch):
    monkeypatch.delenv("RHETORICAL_ROLE_MAX_BLOCKS", raising=False)
    structure = _multi_section_structure([("sec_1", 5)])
    inputs, coverage = BUILDER.build_with_coverage(structure, _skeleton())
    assert is_coverage_report(coverage)
    assert coverage["reasons"] == []
    assert coverage["truncated"] == 0
    assert coverage["processed"] == len(inputs) == 5
    assert "unprocessed_sections" not in coverage["details"]


def test_coverage_report_for_empty_population(monkeypatch):
    monkeypatch.delenv("RHETORICAL_ROLE_MAX_BLOCKS", raising=False)
    structure = _structure([_typed("h1", "1 Introduction", "section_heading", order=0)])
    inputs, coverage = BUILDER.build_with_coverage(structure, _skeleton())
    assert inputs == []
    assert is_coverage_report(coverage)
    assert coverage["population"] == 0
    assert coverage["truncated"] == 0
