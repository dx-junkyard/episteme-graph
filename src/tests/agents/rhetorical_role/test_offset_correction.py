"""サーバ側でのスパンオフセット訂正（2026-09-19）。

validator の「span.text は block の逐語スライスであること」という規則は不変で、
変えたのは**オフセットを誰が計算するか**だけ。LLM に 1,800 字ブロックの文字数を
数えさせると 65〜98 % の span が修復失敗に落ちていた（役割 unknown /
confidence 0.0）。ここでは訂正が効くこと・訂正が記録されること・見つからない
span は従来どおり error になることを固定する。
"""
from __future__ import annotations

from unittest.mock import patch

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
from episteme_graph.agents.rhetorical_role.agent import RhetoricalRoleAgent
from episteme_graph.agents.rhetorical_role.repair import (
    OFFSET_CORRECTION_ANCHOR,
    OFFSET_CORRECTION_EXACT,
    OFFSET_CORRECTION_WHITESPACE,
    REPAIR_FAILED_REASON,
    _parse_block_annotation,
    resolve_span_offsets,
)
from episteme_graph.agents.rhetorical_role.schema import RoleLLMInput
from episteme_graph.agents.rhetorical_role.validator import RhetoricalRoleValidator

BLOCK = (
    "We assume that the noise is stationary over the observing window. "
    "The resulting estimator is unbiased at first order, and the residual "
    "term is suppressed by the survey volume."
)


def _llm_input(block_text: str = BLOCK) -> RoleLLMInput:
    return RoleLLMInput(
        document_id="doc",
        cartridge_id=None,
        block_id="b1",
        section_id="sec_1",
        section_title="Method",
        backbone_block_type="assumptions",
        headline_claim=None,
        block_text=block_text,
        prev_text=None,
        next_text=None,
    )


def _raw(text: str, char_start: int, char_end: int) -> dict:
    return {
        "block_id": "b1",
        "section_id": "sec_1",
        "backbone_block_type": "assumptions",
        "span_annotations": [{
            "span_id": "span_001",
            "text": text,
            "char_start": char_start,
            "char_end": char_end,
            "role_labels": ["assumption"],
            "is_claim_candidate": True,
            "is_reject_candidate": False,
            "confidence": 0.9,
            "reason": "mocked",
        }],
    }


# ---------------------------------------------------------------------------
# resolve_span_offsets
# ---------------------------------------------------------------------------


def test_correct_offsets_are_left_untouched():
    text = BLOCK[3:20]
    assert resolve_span_offsets(BLOCK, text, 3, 20) == (3, 20, None)


def test_wrong_offsets_are_relocated_by_exact_match():
    text = "The resulting estimator is unbiased at first order"
    start, end, correction = resolve_span_offsets(BLOCK, text, 0, len(text))
    assert BLOCK[start:end] == text
    assert correction == OFFSET_CORRECTION_EXACT


def test_nearest_occurrence_to_the_hint_wins():
    block = "alpha beta. gamma. alpha beta."
    start, end, correction = resolve_span_offsets(block, "alpha beta", 17, 27)
    assert (start, end) == (19, 29)
    assert correction == OFFSET_CORRECTION_EXACT
    # ties resolve to the earlier occurrence (deterministic)
    assert resolve_span_offsets(block, "alpha beta", 0, 10)[0] == 0


def test_whitespace_normalized_match_maps_back_to_original_offsets():
    block = "The  resulting\nestimator is   unbiased at first order."
    text = "The resulting estimator is unbiased at first order"
    start, end, correction = resolve_span_offsets(block, text, 0, len(text))
    assert correction == OFFSET_CORRECTION_WHITESPACE
    assert " ".join(block[start:end].split()) == text


def test_head_tail_anchor_recovers_a_long_span_with_a_middle_typo():
    block = (
        "The estimator is unbiased at first order in the perturbative expansion, "
        "and the residual term is suppressed by the survey volume in every bin."
    )
    # The LLM dropped a word in the middle but copied both ends verbatim.
    text = block.replace("perturbative ", "")
    start, end, correction = resolve_span_offsets(block, text, 0, len(text))
    assert correction == OFFSET_CORRECTION_ANCHOR
    assert block[start:end] == block


def test_unlocatable_span_keeps_the_llm_offsets():
    start, end, correction = resolve_span_offsets(BLOCK, "text that is absent", 5, 24)
    assert (start, end, correction) == (5, 24, None)


def test_empty_inputs_are_safe():
    assert resolve_span_offsets("", "x", 0, 1) == (0, 1, None)
    assert resolve_span_offsets(BLOCK, "", 0, 0) == (0, 0, None)
    assert resolve_span_offsets(BLOCK, "   ", 0, 3) == (0, 3, None)


# ---------------------------------------------------------------------------
# _parse_block_annotation → validator
# ---------------------------------------------------------------------------


def test_parsed_span_passes_the_validator_after_correction():
    text = "the residual term is suppressed by the survey volume"
    annotation = _parse_block_annotation(_raw(text, 0, len(text)), _llm_input())
    span = annotation.span_annotations[0]

    assert BLOCK[span.char_start:span.char_end] == text
    assert span.offset_correction == OFFSET_CORRECTION_EXACT

    from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult

    result = RhetoricalRoleResult("doc", None, [annotation], {})
    issues = RhetoricalRoleValidator().validate(result, {"b1": BLOCK})
    assert [i for i in issues if i.severity == "error"] == []


def test_whitespace_corrected_span_passes_the_validator():
    """R-2: 空白正規化で位置を当てた span は本文のスライスを text にし、逐語検査を通る。

    訂正前は「LLM の写し（空白違い）」を text に残していたため、位置は正しいのに
    ``span_text_mismatch`` で修復失敗に数えられていた。検証基準は不変。
    """
    llm_copy = "The resulting  estimator is unbiased at first order"  # 二重空白
    raw = _raw(llm_copy, 0, len(llm_copy))
    annotation = _parse_block_annotation(raw, _llm_input())
    span = annotation.span_annotations[0]

    assert span.offset_correction == OFFSET_CORRECTION_WHITESPACE
    assert span.text == BLOCK[span.char_start:span.char_end]
    assert "  " not in span.text

    from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult

    result = RhetoricalRoleResult("doc", None, [annotation], {})
    issues = RhetoricalRoleValidator().validate(result, {"b1": BLOCK})
    assert [i for i in issues if i.severity == "error"] == []


def test_anchor_corrected_span_passes_the_validator():
    """R-2: 頭尾アンカーで当てた span も本文のスライスが text になる（中間の脱字は本文が正）。"""
    # 本文と読点 1 つ分ずれた写し（exact / 空白正規化では当たらずアンカー戦略の対象）
    text = (
        "The resulting estimator is unbiased at first order and the residual "
        "term is suppressed by the survey volume."
    )
    annotation = _parse_block_annotation(_raw(text, 0, len(text)), _llm_input())
    span = annotation.span_annotations[0]
    assert span.offset_correction == OFFSET_CORRECTION_ANCHOR
    assert span.text == BLOCK[span.char_start:span.char_end]

    from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult

    result = RhetoricalRoleResult("doc", None, [annotation], {})
    issues = RhetoricalRoleValidator().validate(result, {"b1": BLOCK})
    assert [i for i in issues if i.severity == "error"] == []


def test_unlocatable_span_still_fails_validation():
    """検証基準は緩めない — 見つからない span は従来どおり error。"""
    annotation = _parse_block_annotation(_raw("not in the block at all", 0, 23), _llm_input())
    from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult

    result = RhetoricalRoleResult("doc", None, [annotation], {})
    errors = [
        i for i in RhetoricalRoleValidator().validate(result, {"b1": BLOCK})
        if i.severity == "error"
    ]
    assert [i.rule_id for i in errors] == ["span_text_mismatch"]


def test_non_numeric_offsets_do_not_raise():
    raw = _raw("The resulting estimator is unbiased at first order", "x", None)
    annotation = _parse_block_annotation(raw, _llm_input())
    span = annotation.span_annotations[0]
    assert BLOCK[span.char_start:span.char_end] == span.text


# ---------------------------------------------------------------------------
# agent の集計
# ---------------------------------------------------------------------------


def _structure(text: str) -> DocumentStructureResult:
    block = TypedBlock("b1", 1, 0, text, "body_paragraph")
    block.section_id = "sec_1"
    return DocumentStructureResult(
        document_id="doc",
        source_file="/tmp/t.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="T", pages=1),
        sections=[Section("sec_1", "Method", 1, 1, 1)],
        blocks=[block],
    )


def _skeleton() -> PaperSkeletonResult:
    return PaperSkeletonResult(
        document_id="doc",
        skeleton_version=SKELETON_VERSION,
        cartridge_id=None,
        paper_goal={"text": "g", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        central_question={"text": "q", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        headline_claim={"text": "h", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        supporting_subclaims=[],
        logical_blocks=[
            LogicalBlock("logic_1", "assumptions", "Method", ["sec_1"], ["b1"], "s", "r", 0.8)
        ],
        excluded_regions=[],
        review_notes=[],
        confidence=0.8,
    )


def test_offset_only_error_no_longer_needs_a_repair_call():
    """オフセットだけが間違っている応答は、修復 LLM を呼ばずに通る。"""
    agent = RhetoricalRoleAgent()
    text = "The resulting estimator is unbiased at first order"
    with patch.object(agent._llm_client, "generate", side_effect=[_raw(text, 900, 999)]) as mocked:
        result = agent.run(_structure(BLOCK), _skeleton())

    assert mocked.call_count == 1
    span = result.role_annotations[0].span_annotations[0]
    assert span.role_labels == ["assumption"]
    assert span.offset_correction == OFFSET_CORRECTION_EXACT
    stats = result.summary_stats
    assert stats["repair_failed_blocks"] == 0
    assert stats["offset_corrected_spans"] == 1
    assert stats["unknown_role_spans"] == 0
    assert stats["total_spans"] == 1
    assert stats["blocks_processed"] == 1


def test_repair_failed_blocks_are_counted():
    agent = RhetoricalRoleAgent()
    bad = _raw("not in the block at all", 0, 23)
    with patch.object(agent._llm_client, "generate", return_value=bad):
        result = agent.run(_structure(BLOCK), _skeleton())

    span = result.role_annotations[0].span_annotations[0]
    assert span.reason == REPAIR_FAILED_REASON
    stats = result.summary_stats
    assert stats["repair_failed_blocks"] == 1
    assert stats["repaired_blocks"] == 0
    assert stats["unknown_role_spans"] == 1
    assert stats["llm_error_blocks"] == 0


def test_llm_error_blocks_are_counted_separately():
    agent = RhetoricalRoleAgent()
    with patch.object(agent._llm_client, "generate", side_effect=RuntimeError("boom")):
        result = agent.run(_structure(BLOCK), _skeleton())

    stats = result.summary_stats
    assert stats["llm_error_blocks"] == 1
    assert stats["repair_failed_blocks"] == 0
    assert stats["unknown_role_spans"] == 1
