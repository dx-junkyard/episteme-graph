"""上限と取りこぼし報告（P0-1 の claim_qualification 版）。

従来は ``_MAX_SPANS = 96`` で文書順の先頭 96 span に切り捨てていたため、結論・
限界の節が 1 件も claim 化されない論文があった。ここで固定するのは:
  (a) 既定では打ち切らない
  (b) 上限があるときは先頭切り捨てではなく節単位の層化サンプリング
  (c) 取りこぼしが共通形式の coverage で報告される
  (d) agent の summary_stats に coverage が載る
"""
from unittest.mock import patch

import pytest

from episteme_graph.agents.claim_qualification.agent import ClaimQualificationAgent
from episteme_graph.agents.claim_qualification.input_builder import (
    ClaimQualificationInputBuilder,
)
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
from episteme_graph.agents.rhetorical_role.schema import (
    BlockRoleAnnotation,
    RhetoricalRoleResult,
    SpanAnnotation,
)

BUILDER = ClaimQualificationInputBuilder()

_SECTIONS = [("sec_intro", "Introduction", 12), ("sec_mid", "Analysis", 12),
             ("sec_conc", "Conclusions", 4)]


@pytest.fixture(autouse=True)
def _no_env_limit(monkeypatch):
    monkeypatch.delenv("CLAIM_QUALIFICATION_MAX_SPANS", raising=False)


def _structure():
    blocks = []
    order = 0
    for section_id, _title, count in _SECTIONS:
        for i in range(count):
            b = TypedBlock(f"{section_id}_b{i}", 1, order, f"text {order}", "body_paragraph")
            b.section_id = section_id
            blocks.append(b)
            order += 1
    return DocumentStructureResult(
        document_id="doc",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section(sid, title, 1, 1, 1) for sid, title, _ in _SECTIONS],
        blocks=blocks,
    )


def _skeleton():
    return PaperSkeletonResult(
        document_id="doc",
        skeleton_version=SKELETON_VERSION,
        cartridge_id=None,
        paper_goal={"text": "goal", "evidence_block_ids": [], "reason": "", "confidence": 0.8},
        central_question={"text": "q", "evidence_block_ids": [], "reason": "", "confidence": 0.8},
        headline_claim={"text": "headline", "evidence_block_ids": [], "reason": "", "confidence": 0.8},
        supporting_subclaims=[],
        logical_blocks=[LogicalBlock("l1", "assumptions", "A", ["sec_intro"], [], "s", "r", 0.8)],
        excluded_regions=[],
        review_notes=[],
        confidence=0.8,
    )


def _roles():
    annotations = []
    for section_id, _title, count in _SECTIONS:
        for i in range(count):
            block_id = f"{section_id}_b{i}"
            annotations.append(BlockRoleAnnotation(
                block_id=block_id,
                section_id=section_id,
                backbone_block_type="derivation",
                span_annotations=[SpanAnnotation(
                    span_id=f"{block_id}_s0",
                    text=f"claim {block_id}",
                    char_start=0,
                    char_end=10,
                    role_labels=["relation"],
                    is_claim_candidate=True,
                    is_reject_candidate=False,
                    confidence=0.9,
                    reason="mock",
                )],
            ))
    return RhetoricalRoleResult(
        document_id="doc", cartridge_id=None,
        role_annotations=annotations, summary_stats={},
    )


def test_default_does_not_truncate():
    inputs, coverage = BUILDER.build_with_coverage(_structure(), _skeleton(), _roles())
    assert len(inputs) == 28
    assert is_coverage_report(coverage)
    assert coverage["population"] == 28
    assert coverage["truncated"] == 0
    assert coverage["reasons"] == []


def test_limit_is_spread_across_sections_not_head_truncated():
    inputs, coverage = BUILDER.build_with_coverage(
        _structure(), _skeleton(), _roles(), config={"max_spans": 6}
    )
    assert len(inputs) == 6
    sections = {i.section_id for i in inputs}
    # 先頭切り捨てなら sec_conc は 1 件も入らない。
    assert sections == {"sec_intro", "sec_mid", "sec_conc"}
    assert coverage["population"] == 28
    assert coverage["processed"] == 6
    assert coverage["truncated"] == 22
    assert coverage["reasons"] == ["max_spans"]
    assert coverage["unit"] == "spans"


def test_env_sets_the_default_limit(monkeypatch):
    monkeypatch.setenv("CLAIM_QUALIFICATION_MAX_SPANS", "5")
    inputs, coverage = BUILDER.build_with_coverage(_structure(), _skeleton(), _roles())
    assert len(inputs) == 5
    assert coverage["reasons"] == ["max_spans"]


def test_coverage_details_have_no_counts():
    _inputs, coverage = BUILDER.build_with_coverage(
        _structure(), _skeleton(), _roles(), config={"max_spans": 3}
    )
    details = coverage["details"]
    assert "unprocessed_sections" in details
    assert all(not isinstance(value, int) for value in details.values())


def test_build_keeps_the_legacy_signature():
    """orchestrator の `_agent_input_count` が呼ぶ build() の戻り値は list のまま。"""
    inputs = BUILDER.build(_structure(), _skeleton(), _roles())
    assert isinstance(inputs, list)
    assert len(inputs) == 28


def test_agent_puts_coverage_into_summary_stats():
    agent = ClaimQualificationAgent()
    response = {
        "qualification": {
            "status": "accepted", "claim_tier": "paper_supporting",
            "claim_type_candidate": "relation", "granularity": "good",
            "evidence_adequacy": "sufficient", "reviewability": "good",
        },
        "reason": "mock", "confidence": 0.9,
    }
    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(
            _structure(), _skeleton(), _roles(), config={"max_spans": 2}
        )
    coverage = result.summary_stats["coverage"]
    assert is_coverage_report(coverage)
    assert coverage["population"] == 28
    assert coverage["processed"] == 2


def test_fallback_result_still_reports_coverage():
    """対象 span ゼロでも「見た母集合は 0 だった」ことを残す。"""
    agent = ClaimQualificationAgent()
    empty_roles = RhetoricalRoleResult(
        document_id="doc", cartridge_id=None, role_annotations=[], summary_stats={},
    )
    result = agent.run(_structure(), _skeleton(), empty_roles)
    coverage = result.summary_stats["coverage"]
    assert is_coverage_report(coverage)
    assert coverage["population"] == 0


# ---------------------------------------------------------------------------
# R-4（2026-09-19 レビュー）: 表本体の span は主張採否の母集合に入れない
# ---------------------------------------------------------------------------


def test_spans_on_table_body_blocks_are_excluded_from_the_population():
    structure = _structure()
    # 先頭の block を表本体に見立てる（GROBID は表を body_paragraph + raw.in_table で残す）
    structure.blocks[0].raw = {"container_type": "table", "in_table": True}
    roles = _roles()
    table_block_id = structure.blocks[0].block_id

    inputs, coverage = BUILDER.build_with_coverage(structure, _skeleton(), roles)

    assert all(i.block_id != table_block_id for i in inputs)
    assert len(inputs) == 27
    assert coverage["population"] == 27
    assert coverage["details"]["excluded_table_block_ids"] == [table_block_id]
    assert is_coverage_report(coverage)
