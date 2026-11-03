"""プロンプトの Output Schema ⇄ validator / schema の契約。

support_structure の各要素の形がプロンプトに書かれていないと、LLM（頭脳）が
``direct_supports: []`` だけを見て項目の形を推測するしかなく、validator が要求する
support_type / claim_ids / equation_ids / confidence を欠いた応答が修復に回る。
"""
import dataclasses

from episteme_graph.agents.thesis_reconstruction.prompt import (
    _OUTPUT_SCHEMA,
    _SUPPORT_ENTRY_EXAMPLE,
    ThesisReconstructionPromptFactory,
)
from episteme_graph.agents.thesis_reconstruction.schema import (
    SUPPORT_SECTIONS,
    SUPPORT_TYPES,
    SupportEntry,
    ThesisLLMInput,
    ThesisReconstructionResult,
)
from episteme_graph.agents.thesis_reconstruction.validator import (
    ThesisReconstructionValidator,
)


def test_support_entry_example_matches_schema_dataclass():
    assert set(_SUPPORT_ENTRY_EXAMPLE) == {f.name for f in dataclasses.fields(SupportEntry)}


def test_every_support_section_shows_the_entry_shape():
    support = _OUTPUT_SCHEMA["support_structure"]
    assert set(support) == set(SUPPORT_SECTIONS)
    for section in SUPPORT_SECTIONS:
        assert support[section], section
        assert set(support[section][0]) == set(_SUPPORT_ENTRY_EXAMPLE)


def test_filled_schema_example_passes_the_validator_without_errors():
    example = dict(_OUTPUT_SCHEMA)
    example["central_thesis"] = {**example["central_thesis"], "text": "X implies Y", "claim_ids": ["c1"]}
    support = {}
    for section in SUPPORT_SECTIONS:
        entry = {**_SUPPORT_ENTRY_EXAMPLE, "support_type": SUPPORT_TYPES[0], "claim_ids": ["c1"]}
        support[section] = [entry]
    example["support_structure"] = support
    example["alternative_theses"] = []
    example["thesis_graph_hints"] = [{"from": "central_thesis", "to": "support:direct_supports:0", "relation": "supported_by"}]
    result = ThesisReconstructionResult.from_dict(example)
    errors = [i for i in ThesisReconstructionValidator().validate(result) if i.severity == "error"]
    assert errors == []


def test_rendered_prompt_contains_entry_fields():
    llm_input = ThesisLLMInput(
        document_id="doc", cartridge_id=None, paper_goal=None, central_question=None,
        headline_claim=None, logical_blocks=[], accepted_claims=[], major_equations=[],
        excluded_regions=[],
    )
    content = ThesisReconstructionPromptFactory().build_messages(llm_input)[-1]["content"]
    schema_part = content.split("## Output Schema", 1)[1]
    for key in ("support_type", "claim_ids", "equation_ids", "reason", "confidence", "text"):
        assert f'"{key}"' in schema_part
