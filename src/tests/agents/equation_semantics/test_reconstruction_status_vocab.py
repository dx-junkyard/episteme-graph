"""reconstruction.status の語彙はプロンプト・validator・schema で 1 つ（IK 起票: 頭脳 I1 1711）。

以前のプロンプトは経路を問わず許容値を「(exclude 'none')」と書き、出力スキーマの例も
'none' を含まなかった。一方で同じプロンプトの TeX 方針は reconstruction.status='none' を
使えと言っていた（自己矛盾）。語彙の正本は ``schema.RECONSTRUCTION_STATUSES`` で 'none' を
含み、validator も受ける。経路で変わるのは「どの値を選ぶか」だけ。
"""

from __future__ import annotations

from episteme_graph.agents.equation_semantics import prompt as prompt_mod
from episteme_graph.agents.equation_semantics.prompt import EquationSemanticsPromptFactory
from episteme_graph.agents.equation_semantics.repair import _parse_record
from episteme_graph.agents.equation_semantics.schema import (
    RECONSTRUCTION_STATUSES,
    EquationLLMInput,
)
from episteme_graph.agents.equation_semantics.validator import EquationSemanticsValidator


def _llm_input(*, trusted: bool) -> EquationLLMInput:
    return EquationLLMInput(
        document_id="doc_test",
        cartridge_id=None,
        equation_id="eq_2",
        block_id="blk_e2",
        section_id="sec_1",
        section_title="Model",
        backbone_block_type=None,
        label="(2)",
        equation_text="\\delta_g = b \\delta_m" if trusted else "δ g = b δ m (2)",
        latex="\\delta_g = b \\delta_m" if trusted else None,
        plain_text=None,
        prev_texts=[],
        next_texts=[],
        nearby_span_annotations=[],
        candidate_id="cand_1",
        extraction_status="complete" if trusted else "partial",
        acceptance_status="accepted",
        needs_reconstruction=not trusted,
        extraction_source="tex_source" if trusted else "pdf_text_layer",
        source_is_trusted=trusted,
    )


def _content(trusted: bool) -> str:
    return EquationSemanticsPromptFactory()._build_user_content(_llm_input(trusted=trusted), None)


def test_none_is_in_the_canonical_vocabulary():
    assert "none" in RECONSTRUCTION_STATUSES


def test_output_schema_example_lists_every_canonical_status():
    example = prompt_mod._OUTPUT_SCHEMA["reconstruction"]["status"]
    for status in RECONSTRUCTION_STATUSES:
        assert status in example


def test_prompt_never_excludes_none_unconditionally():
    for trusted in (True, False):
        assert "(exclude 'none')" not in _content(trusted)


def test_tex_prompt_allows_none_consistently_with_the_tex_policy():
    content = _content(True)
    assert "reconstruction.status='none'" in content  # TeX 方針
    assert "trusted TeX source: use 'none'" in content  # 許容値の行も同じことを言う


def test_pdf_prompt_still_forbids_none():
    content = _content(False)
    assert "do not use 'none'; a reconstruction is mandatory" in content
    assert "MUST include a 'reconstruction' block" in content


def test_tex_record_with_none_passes_validation():
    raw = {
        "equation_id": "eq_2",
        "equation_type": "relation",
        "semantic_status": "source_backed",
        "confidence": 0.8,
        "reason": "linear bias",
        "summary": "Galaxy overdensity is linearly biased.",
        "reconstruction": {"status": "none"},
    }
    record = _parse_record(raw, _llm_input(trusted=True), None)
    assert record.reconstruction.status == "none"
    issues = EquationSemanticsValidator()._check_reconstruction(record)
    assert not [i for i in issues if i.rule_id == "invalid_reconstruction_status"]
    assert not [i for i in issues if i.severity == "error"]
