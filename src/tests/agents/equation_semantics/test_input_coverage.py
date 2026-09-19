"""上限と取りこぼし報告（P0-1 の equation_semantics 版）。

従来は ``_MAX_EQUATIONS = 64`` で ``(page, order)`` 順の先頭 64 候補に切り捨てて
いた（実測 10 本中 8 本がちょうど 64）。ここで固定するのは:
  (a) 既定では打ち切らない
  (b) 並び順は order が一意なら order のみ（GROBID の page=1 で先頭に寄せない）
  (c) 上限があるときは display 式ブロックを節単位で層化サンプリングし inline は残枠
  (d) 母集合は display + inline で、inline の切り捨ても truncated に出る
"""
import pytest

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.equation_semantics.input_builder import (
    EquationSemanticsInputBuilder,
)

BUILDER = EquationSemanticsInputBuilder()


@pytest.fixture(autouse=True)
def _no_env_limit(monkeypatch):
    monkeypatch.delenv("EQUATION_SEMANTICS_MAX_EQUATIONS", raising=False)
    monkeypatch.delenv("EQUATION_SEMANTICS_MAX_INLINE_EQUATIONS", raising=False)


def _typed(block_id, text, block_type, order, section_id="sec_1", page=1):
    b = TypedBlock(block_id, page, order, text, block_type)
    b.section_id = section_id
    return b


def _structure(blocks, sections=None):
    return DocumentStructureResult(
        document_id="doc",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=sections or [Section("sec_1", "Formulation", 1, 1, 1)],
        blocks=blocks,
    )


def _many_equations(counts: dict[str, int]):
    blocks = []
    order = 0
    for section_id, n in counts.items():
        for i in range(n):
            blocks.append(
                _typed(f"{section_id}_e{i}", f"x_{order} = {order} ({order})",
                       "equation_block", order, section_id=section_id)
            )
            order += 1
    sections = [Section(sid, f"Section {sid}", 1, 1, 1) for sid in counts]
    return _structure(blocks, sections)


def test_default_does_not_truncate():
    structure = _many_equations({"sec_1": 40, "sec_2": 40})
    candidates = BUILDER.build_candidates(structure)
    assert len(candidates) == 80  # かつては 64 で止まっていた
    facts = BUILDER.compute_candidate_coverage(structure)
    assert facts["population"] == 80
    assert facts["processed"] == 80


def test_limit_spreads_display_blocks_across_sections():
    structure = _many_equations({"sec_1": 40, "sec_2": 40, "sec_conc": 4})
    candidates = BUILDER.build_candidates(structure, config={"max_equations": 6})
    assert len(candidates) == 6
    sections = {c.source_location["section_id"] for c in candidates}
    # 先頭切り捨てなら sec_conc の式は 1 本も入らない。
    assert sections == {"sec_1", "sec_2", "sec_conc"}


def test_env_sets_the_default_limit(monkeypatch):
    monkeypatch.setenv("EQUATION_SEMANTICS_MAX_EQUATIONS", "3")
    structure = _many_equations({"sec_1": 10})
    assert len(BUILDER.build_candidates(structure)) == 3


def test_order_is_used_when_unique_even_if_pages_are_broken():
    """GROBID で page=1 のまま残ったブロックを先頭に寄せない。"""
    blocks = [
        _typed("late", "z = 9 (9)", "equation_block", 90, page=1),
        _typed("early", "a = 1 (1)", "equation_block", 1, page=7),
    ]
    candidates = BUILDER.build_candidates(_structure(blocks), config={"max_equations": 1})
    # order が一意なので order 順 → 上限 1 でも「early」が先。
    assert candidates[0].source_location["block_id"] == "early"
    facts = BUILDER.compute_candidate_coverage(_structure(blocks))
    assert facts["details"]["sort_key"] == "order"


def test_inline_candidates_are_counted_in_the_population():
    """display と inline が枠を分け合ったとき、inline の切り捨ても truncated に出る。

    かつて orchestrator は式ブロックだけを母集合に数えていたため、
    「式ブロック 37 + inline 27 = 上限 64」の論文で truncated が 0 に見えた。
    """
    blocks = [
        _typed("e1", "a = 1 (1)", "equation_block", 0),
        _typed("b1", "Here N = 5 holds for the sample.", "body_paragraph", 1),
        _typed("b2", "We also take M = 7 in this case.", "body_paragraph", 2),
    ]
    structure = _structure(blocks)
    full = BUILDER.compute_candidate_coverage(structure)
    assert full["population"] == 3  # 式ブロック 1 + inline 2
    assert full["processed"] == 3

    limited = BUILDER.compute_candidate_coverage(structure, {"max_equations": 2})
    assert limited["population"] == 3
    assert limited["processed"] == 2
    assert limited["reasons"] == ["max_equations"]
    assert limited["unit"] == "equation_candidates"


def test_include_inline_false_is_recorded_in_details():
    blocks = [
        _typed("e1", "a = 1 (1)", "equation_block", 0),
        _typed("b1", "Here N = 5 holds.", "body_paragraph", 1),
    ]
    facts = BUILDER.compute_candidate_coverage(_structure(blocks), {"include_inline": False})
    assert facts["details"]["includes_inline_candidates"] is False
    assert facts["population"] == 1


# ── inline 専用の上限（既定 32） ──────────────────────────────────────────────

def _inline_heavy(n_inline: int, n_display: int = 3):
    """inline 数式を n_inline 件含む本文 + display 式ブロック n_display 件。"""
    blocks = []
    order = 0
    for i in range(n_display):
        blocks.append(
            _typed(f"e{i}", f"a_{i} = {i} ({i})", "equation_block", order)
        )
        order += 1
    for i in range(n_inline):
        blocks.append(
            _typed(f"p{i}", f"Here N_{i} = {i} holds for the sample.",
                   "body_paragraph", order)
        )
        order += 1
    return _structure(blocks)


def test_inline_candidates_are_capped_at_32_by_default():
    structure = _inline_heavy(50)
    candidates = BUILDER.build_candidates(structure)
    inline = [c for c in candidates
              if "inline_equation_heuristic" in c.detection_method]
    display = [c for c in candidates
               if "document_structure_equation_block" in c.detection_method]
    assert len(inline) == 32
    assert len(display) == 3  # display は inline 上限では減らない


def test_inline_cap_is_reported_in_the_coverage_reasons():
    facts = BUILDER.compute_candidate_coverage(_inline_heavy(50))
    assert facts["population"] == 53   # display 3 + inline 50
    assert facts["processed"] == 35    # display 3 + inline 32
    # 効いた弁だけを書く: 全体上限（max_equations）は既定 0 = 無しなので並べない。
    assert facts["reasons"] == ["max_inline_equations"]


def test_inline_cap_zero_means_no_limit():
    structure = _inline_heavy(50)
    facts = BUILDER.compute_candidate_coverage(
        structure, {"max_inline_equations": 0}
    )
    assert facts["processed"] == facts["population"] == 53
    candidates = BUILDER.build_candidates(structure, config={"max_inline_equations": 0})
    assert len(candidates) == 53


def test_inline_cap_is_not_reported_when_it_does_not_bite():
    facts = BUILDER.compute_candidate_coverage(_inline_heavy(5))
    # どの弁も効いていないので理由は空（「max_equations で切った」と言わない）。
    assert facts["reasons"] == []
    assert facts["processed"] == facts["population"] == 8


def test_total_limit_reason_appears_only_when_the_total_limit_bites():
    """``max_equations`` は全体上限が実際に効いたときだけ理由に書く。"""
    facts = BUILDER.compute_candidate_coverage(_inline_heavy(50), {"max_equations": 5})
    assert facts["reasons"][0] == "max_equations"
    facts = BUILDER.compute_candidate_coverage(_inline_heavy(50), {"max_equations": 1000})
    assert facts["reasons"] == ["max_inline_equations"]


def test_inline_cap_does_not_shrink_display_blocks():
    """display の式ブロックが 32 を超えていても inline 上限では切れない。"""
    structure = _many_equations({"sec_1": 40})
    candidates = BUILDER.build_candidates(structure)
    assert len(candidates) == 40


def test_inline_cap_env_overrides_the_default(monkeypatch):
    monkeypatch.setenv("EQUATION_SEMANTICS_MAX_INLINE_EQUATIONS", "4")
    facts = BUILDER.compute_candidate_coverage(_inline_heavy(50))
    assert facts["processed"] == 7  # display 3 + inline 4
    assert "max_inline_equations" in facts["reasons"]


def test_config_wins_over_the_inline_env(monkeypatch):
    monkeypatch.setenv("EQUATION_SEMANTICS_MAX_INLINE_EQUATIONS", "4")
    facts = BUILDER.compute_candidate_coverage(
        _inline_heavy(50), {"max_inline_equations": 10}
    )
    assert facts["processed"] == 13


def test_overall_limit_still_wins_over_the_inline_allowance():
    """候補全体の上限がある場合、inline は残枠までしか入らない。"""
    structure = _inline_heavy(50)
    candidates = BUILDER.build_candidates(structure, config={"max_equations": 5})
    assert len(candidates) == 5


def test_coverage_details_have_no_counts():
    structure = _many_equations({"sec_1": 10, "sec_2": 10})
    facts = BUILDER.compute_candidate_coverage(structure, {"max_equations": 3})
    assert all(
        isinstance(value, bool) or not isinstance(value, int)
        for value in facts["details"].values()
    )
