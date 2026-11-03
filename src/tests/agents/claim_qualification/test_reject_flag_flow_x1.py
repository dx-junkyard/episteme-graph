"""2026-10-17 X1（題材 2610.08751・run 40ecf8df）seq 3316 → 3409 の reject 印の流れ（IK-0803）。

3316 で役割判定は tex_b25 の 3 span すべてに is_reject_candidate=true を返したが、3409 の
claim_qualification 入力では span_003 だけ reject=False で届いた。真因は経路の取り違えではなく
IK-0803 の規律（prior_work は reject 候補にしない）: span_003 は role_labels に prior_work を含む。
印は repair → artifact → input_builder を通って保存されること（span_001 / 002 は True のまま）と、
背景（background_general）を含む span は主張を含み得るので LLM に回ることをこの往復で固定する。
"""
from __future__ import annotations

from episteme_graph.agents.claim_qualification.input_builder import (
    ClaimQualificationInputBuilder,
)
from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.rhetorical_role.repair import _parse_block_annotation
from episteme_graph.agents.rhetorical_role.schema import (
    PRIOR_WORK_NOT_REJECT_NOTE,
    RhetoricalRoleResult,
    RoleLLMInput,
)
from tests.agents.claim_qualification.test_agent import _skeleton

S1 = (
    "The analysis of the large-scale distribution of galaxies from current and upcoming "
    "redshift surveys provides a powerful probe of the cosmological model."
)
S2 = (
    "Cosmological parameters can be inferred either from localised features such as the "
    "baryon acoustic oscillations (BAO), or by exploiting the full shape (FS) of correlators."
)
S3 = (
    "In this context, modern analyses rely on increasingly sophisticated theoretical "
    "frameworks, such as the effective field theory of large-scale structure (EFT) [ref]."
)
BLOCK = f"{S1} {S2} {S3}"


def _raw_3316() -> dict:
    """seq 3316 の応答（本文は短縮・役割と印は逐語）。"""
    def span(i, text, roles):
        start = BLOCK.index(text)
        return {
            "span_id": f"span_00{i}", "text": text, "char_start": start,
            "char_end": start + len(text), "role_labels": roles,
            "is_claim_candidate": False, "is_reject_candidate": True,
            "confidence": 0.7, "reason": "背景",
        }
    return {
        "block_id": "tex_b25", "section_id": "sec_1", "backbone_block_type": "problem_setting",
        "span_annotations": [
            span(1, S1, ["background_general", "citation_context"]),
            span(2, S2, ["background_general"]),
            span(3, S3, ["prior_work", "background_general"]),
        ],
    }


def _roles_from_artifact() -> RhetoricalRoleResult:
    llm_input = RoleLLMInput(
        document_id="doc_test", cartridge_id=None, block_id="tex_b25", section_id="sec_1",
        section_title="Introduction", backbone_block_type="problem_setting",
        headline_claim=None, block_text=BLOCK, prev_text=None, next_text=None,
    )
    annotation = _parse_block_annotation(_raw_3316(), llm_input)
    result = RhetoricalRoleResult(
        document_id="doc_test", cartridge_id=None,
        role_annotations=[annotation], summary_stats={},
    )
    # artifact 保存 → 読み戻し（orchestrator の resume と同じ往復）。
    return RhetoricalRoleResult.from_dict(result.to_dict())


def _structure() -> DocumentStructureResult:
    block = TypedBlock("tex_b25", 1, 0, BLOCK, "body_paragraph")
    block.section_id = "sec_1"
    return DocumentStructureResult(
        document_id="doc_test", source_file="paper.tar.gz", cartridge_id=None,
        metadata=DocumentMetadata(title="T", pages=1),
        sections=[Section("sec_1", "Introduction", 1, 1, 1)],
        blocks=[block],
    )


def test_reject_flag_survives_artifact_and_only_prior_work_is_unmarked():
    roles = _roles_from_artifact()
    spans = {s.span_id: s for s in roles.role_annotations[0].span_annotations}
    assert spans["span_001"].is_reject_candidate is True
    assert spans["span_002"].is_reject_candidate is True
    # prior_work は reject 候補にしない（IK-0803）— 外した事実は reason に残る。
    assert spans["span_003"].is_reject_candidate is False
    assert PRIOR_WORK_NOT_REJECT_NOTE in spans["span_003"].reason


def test_claim_qualification_input_carries_the_flags_seen_in_3409():
    inputs, skipped, _coverage = ClaimQualificationInputBuilder().build_partitioned(
        _structure(), _skeleton(), _roles_from_artifact()
    )
    by_id = {i.span_id: i for i in inputs}
    # 背景を含む span は主張を含み得るので決定論 reject にしない（LLM に回る）。
    assert skipped == []
    assert set(by_id) == {"span_001", "span_002", "span_003"}
    assert by_id["span_001"].is_reject_candidate is True
    assert by_id["span_002"].is_reject_candidate is True
    assert by_id["span_003"].is_reject_candidate is False
    assert by_id["span_003"].role_labels == ["prior_work", "background_general"]
