"""上限と取りこぼし報告（P0-1 の paper_skeleton 版）。

従来は ``_MAX_SECTIONS = 12`` で level-1 節の先頭 12 件しか骨格判断に見せていな
かった。ここで固定するのは:
  (a) 既定では節を打ち切らない
  (b) 上限があるときは先頭切り捨てにせず、結論節を必ず含める
  (c) 付録除外と上限が coverage の理由コードに出る
"""
import pytest

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
)
from episteme_graph.agents.paper_skeleton.input_builder import SkeletonInputBuilder

BUILDER = SkeletonInputBuilder()


@pytest.fixture(autouse=True)
def _no_env_limit(monkeypatch):
    monkeypatch.delenv("PAPER_SKELETON_MAX_SECTIONS", raising=False)


def _section(section_id, title, level=1):
    return Section(
        section_id=section_id, title=title, level=level,
        order=1, page_start=1, parent_section_id=None,
    )


def _structure(sections):
    return DocumentStructureResult(
        document_id="doc",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=20),
        sections=sections,
        blocks=[],
    )


def _twenty_sections():
    sections = [_section(f"sec_{i}", f"Section {i}") for i in range(19)]
    sections.append(_section("sec_conc", "Conclusions"))
    return sections


def test_default_shows_every_top_level_section():
    structure = _structure(_twenty_sections())
    llm_input = BUILDER.build(structure)
    assert len(llm_input.section_headers) == 20  # かつては 12 で止まっていた
    facts = BUILDER.compute_section_coverage(structure)
    assert facts["population"] == 20
    assert facts["processed"] == 20
    assert facts["reasons"] == []


def test_limit_always_keeps_the_conclusion_section():
    structure = _structure(_twenty_sections())
    llm_input = BUILDER.build(structure, config={"max_sections": 5})
    titles = [s["title"] for s in llm_input.section_headers]
    assert len(titles) == 5
    assert "Conclusions" in titles  # 先頭切り捨てなら落ちる
    assert titles[0] == "Section 0"  # 先頭節も残す


def test_limit_is_reported_as_a_reason():
    structure = _structure(_twenty_sections())
    facts = BUILDER.compute_section_coverage(structure, {"max_sections": 5})
    assert facts["population"] == 20
    assert facts["processed"] == 5
    assert facts["reasons"] == ["max_sections"]
    assert facts["unit"] == "sections"
    assert [e["title"] for e in facts["details"]["unprocessed_sections"]]


def test_appendix_exclusion_is_reported():
    structure = _structure([
        _section("sec_1", "Methods"),
        _section("sec_app", "Appendix A"),
    ])
    facts = BUILDER.compute_section_coverage(structure)
    assert facts["population"] == 2
    assert facts["processed"] == 1
    assert facts["reasons"] == ["appendix_excluded"]
    assert facts["details"]["unprocessed_sections"][0]["title"] == "Appendix A"


def test_env_sets_the_default_limit(monkeypatch):
    monkeypatch.setenv("PAPER_SKELETON_MAX_SECTIONS", "3")
    structure = _structure(_twenty_sections())
    assert len(BUILDER.build(structure).section_headers) == 3


def test_subsections_are_out_of_scope_and_said_so():
    structure = _structure([
        _section("sec_1", "Methods"),
        _section("sec_1_1", "Detail", level=2),
    ])
    facts = BUILDER.compute_section_coverage(structure)
    assert facts["population"] == 1
    assert facts["details"]["scope"] == "level_1_sections"


def test_representative_blocks_follow_the_selected_sections():
    structure = _structure(_twenty_sections())
    llm_input = BUILDER.build(structure, config={"max_sections": 3})
    selected_ids = {s["section_id"] for s in llm_input.section_headers}
    for block in llm_input.representative_blocks:
        assert block.get("section_title") is not None
    assert len(selected_ids) == 3
