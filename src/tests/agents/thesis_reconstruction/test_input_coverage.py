"""thesis_reconstruction の入力上限を「文脈サンプリング」として報告する（P0-10）。

上限（claim 32 / equation 16 / logical_block 16）は中心命題の再構成に必要な代表を
渡すためのもので、そのまま残す。ここで固定するのは**どれだけ渡さなかったかが
報告される**こと。
"""
from episteme_graph.agents.claim_qualification.schema import (
    ClaimQualificationResult,
    QualifiedSpanRecord,
)
from episteme_graph.agents.paper_skeleton.schema import (
    LogicalBlock,
    PaperSkeletonResult,
    SKELETON_VERSION,
)
from episteme_graph.agents.thesis_reconstruction.input_builder import (
    ThesisReconstructionInputBuilder,
)

BUILDER = ThesisReconstructionInputBuilder()


def _span(idx: int) -> QualifiedSpanRecord:
    return QualifiedSpanRecord(
        span_id=f"s{idx}",
        block_id=f"b{idx}",
        section_id="sec_1",
        text=f"claim text {idx}",
        role_labels=["relation"],
        qualification={
            "status": "accepted",
            "claim_tier": "paper_supporting",
            "claim_type_candidate": "relation",
            "granularity": "good",
            "evidence_adequacy": "sufficient",
            "reviewability": "good",
        },
        edit_suggestions={},
        reason="mock",
        confidence=0.8,
    )


def _claims(n: int) -> ClaimQualificationResult:
    return ClaimQualificationResult(
        document_id="doc",
        cartridge_id=None,
        qualified_spans=[_span(i) for i in range(n)],
        rejected_spans=[],
        deferred_spans=[],
        summary_stats={},
    )


def _skeleton(block_count: int = 0) -> PaperSkeletonResult:
    return PaperSkeletonResult(
        document_id="doc",
        skeleton_version=SKELETON_VERSION,
        cartridge_id=None,
        paper_goal={"text": "goal", "evidence_block_ids": [], "reason": "", "confidence": 0.8},
        central_question={"text": "q", "evidence_block_ids": [], "reason": "", "confidence": 0.8},
        headline_claim={"text": "h", "evidence_block_ids": [], "reason": "", "confidence": 0.8},
        supporting_subclaims=[],
        logical_blocks=[
            LogicalBlock(f"l{i}", "derivation", f"L{i}", ["sec_1"], [], "s", "r", 0.8)
            for i in range(block_count)
        ],
        excluded_regions=[],
        review_notes=[],
        confidence=0.8,
    )


def test_no_truncation_is_reported_when_everything_fits():
    facts = BUILDER.compute_input_coverage(_skeleton(3), _claims(5))
    assert facts["population"] == 8
    assert facts["processed"] == 8
    assert facts["reasons"] == []
    assert facts["unit"] == "context_items"


def test_claim_limit_is_named_in_the_reasons():
    facts = BUILDER.compute_input_coverage(_skeleton(2), _claims(50))
    assert facts["population"] == 52
    assert facts["processed"] == 34  # claim 32 + logical_block 2
    assert facts["reasons"] == ["max_claims"]


def test_logical_block_limit_is_named_in_the_reasons():
    facts = BUILDER.compute_input_coverage(_skeleton(40), _claims(1))
    assert "max_logical_blocks" in facts["reasons"]
    assert facts["population"] == 41
    assert facts["processed"] == 17  # claim 1 + logical_block 16


def test_config_overrides_the_limits():
    facts = BUILDER.compute_input_coverage(
        _skeleton(0), _claims(10), config={"max_claims": 4}
    )
    assert facts["processed"] == 4
    assert facts["reasons"] == ["max_claims"]


def test_details_have_no_counts():
    facts = BUILDER.compute_input_coverage(_skeleton(40), _claims(50))
    assert all(
        not isinstance(value, int) for value in facts["details"].values()
    )
