"""Tests for parser-driven, front-matter-limited author extraction (issue #372).

All extraction here is non-LLM / parser-driven, so no external API is invoked
(there is no LLM client to mock on this path). The tests cover:

- TeX ``\\author{...}`` extraction (only front-matter macro, strips affiliations),
- PDF front-matter byline extraction (stops at Abstract/References, ignores
  reference entries and body terms like "Horndeski gravity"),
- self-citing authors preserved, reference people never added,
- Japanese / accented / hyphenated / compound names preserved,
- provenance resolution priority + source-mismatch flagging.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from episteme_graph.agents.document_structure.author_extraction import (  # noqa: E402
    AuthorExtractionResult,
    extract_authors_from_front_matter,
    extract_authors_from_tex,
    looks_like_person_name,
    resolve_author_extraction,
    split_author_list,
)


def _block(block_id, page, text, block_type="body_paragraph", order=0):
    return {"block_id": block_id, "page": page, "text": text,
            "block_type": block_type, "order": order}


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------

class TestNameValidation:
    def test_accepts_plain_and_accented_and_hyphenated_and_cjk(self):
        for name in [
            "Alice Smith", "José Hernández", "María García-Pérez",
            "Jean-Pierre Dubois", "山田太郎", "鈴木 花子", "O'Brien",
        ]:
            assert looks_like_person_name(name), name

    def test_rejects_sentences_numbers_and_citations(self):
        for bad in [
            "We derive the relation below.",
            "Horndeski gravity is a scalar-tensor theory",
            "Smith et al. 2019",
            "Phys. Rev. D 12, 3045 (2019)",
            "see https://example.com",
        ]:
            assert not looks_like_person_name(bad), bad


# ---------------------------------------------------------------------------
# TeX \author{...}
# ---------------------------------------------------------------------------

class TestTexAuthors:
    def test_extracts_authors_split_on_and(self):
        tex = r"\author{Alice Smith \and Bob Jones}"
        res = extract_authors_from_tex(tex)
        assert res.authors == ["Alice Smith", "Bob Jones"]
        assert res.source == "tex_author"
        assert res.needs_review is False

    def test_strips_thanks_and_affiliation_noise(self):
        tex = (
            r"\author{Alice Smith\thanks{MIT, alice@mit.edu}$^{1}$ \and "
            r"Bob Jones\inst{2}\footnote{Funding note}}"
        )
        res = extract_authors_from_tex(tex)
        assert res.authors == ["Alice Smith", "Bob Jones"]

    def test_preserves_accented_and_hyphenated_names(self):
        tex = r"\author{José Hernández \and María García-Pérez}"
        res = extract_authors_from_tex(tex)
        assert res.authors == ["José Hernández", "María García-Pérez"]

    def test_no_author_macro_returns_empty(self):
        res = extract_authors_from_tex(r"\title{Some Title}\section{Intro}")
        assert res.authors == []
        assert res.source == "none"


# ---------------------------------------------------------------------------
# PDF front-matter byline
# ---------------------------------------------------------------------------

class TestFrontMatterByline:
    def test_extracts_byline_before_abstract(self):
        blocks = [
            _block("t", 1, "A Study of Kurtosis Consistency Relations", order=0),
            _block("by", 1, "Taro Yamada, Hanako Suzuki and Jiro Tanaka", order=1),
            _block("h_abs", 1, "Abstract", block_type="section_heading", order=2),
            _block("abs", 1, "We study the consistency relation in detail.", order=3),
        ]
        res = extract_authors_from_front_matter(blocks, title="A Study of Kurtosis Consistency Relations")
        assert res.authors == ["Taro Yamada", "Hanako Suzuki", "Jiro Tanaka"]
        assert res.source == "pdf_front_matter"

    def test_ignores_reference_entries_and_body(self):
        # Byline on page 1; body mentions "Horndeski gravity"; references list
        # many citation authors. Only the byline must be returned.
        blocks = [
            _block("t", 1, "On Modified Gravity", order=0),
            _block("by", 1, "Alice Smith and Bob Jones", order=1),
            _block("h_intro", 1, "Introduction", block_type="section_heading", order=2),
            _block("body", 1, "We extend Horndeski gravity to new regimes.", order=3),
            _block("h_ref", 9, "References", block_type="section_heading", order=4),
            _block("r1", 9, "Horndeski, G. W. Second-order field equations. 1974.",
                   block_type="reference_entry", order=5),
        ]
        res = extract_authors_from_front_matter(blocks)
        assert res.authors == ["Alice Smith", "Bob Jones"]
        assert "Horndeski" not in res.authors

    def test_self_citing_author_extracted_from_byline(self):
        blocks = [
            _block("t", 1, "A Prior Result Revisited", order=0),
            _block("by", 1, "Alice Smith", order=1),
            _block("h_intro", 1, "Introduction", block_type="section_heading", order=2),
            _block("r1", 9, "Alice Smith. A prior result. 2019.",
                   block_type="reference_entry", order=3),
        ]
        res = extract_authors_from_front_matter(blocks)
        assert res.authors == ["Alice Smith"]

    def test_title_is_not_mistaken_for_byline(self):
        blocks = [
            _block("t", 1, "Consistency Relations In Cosmology", order=0),
            _block("h_abs", 1, "Abstract", block_type="section_heading", order=1),
        ]
        res = extract_authors_from_front_matter(
            blocks, title="Consistency Relations In Cosmology"
        )
        assert res.authors == []

    def test_no_blocks_returns_empty(self):
        assert extract_authors_from_front_matter([]).authors == []


# ---------------------------------------------------------------------------
# split_author_list
# ---------------------------------------------------------------------------

class TestSplit:
    def test_splits_on_commas_and_and(self):
        names, parts = split_author_list("Alice Smith, Bob Jones and Carol Lee")
        assert names == ["Alice Smith", "Bob Jones", "Carol Lee"]
        assert parts == 3


# ---------------------------------------------------------------------------
# Provenance resolution
# ---------------------------------------------------------------------------

class TestResolve:
    def test_prefers_first_non_empty_priority_source(self):
        tex = AuthorExtractionResult(authors=["Alice Smith"], source="tex_author")
        front = AuthorExtractionResult(authors=["Bob Jones"], source="pdf_front_matter")
        res = resolve_author_extraction([tex, front])
        assert res.authors == ["Alice Smith"]
        assert res.source == "tex_author"
        assert set(res.candidate_sources) == {"tex_author", "pdf_front_matter"}

    def test_falls_through_to_next_source_when_empty(self):
        empty = AuthorExtractionResult(authors=[], source="none")
        front = AuthorExtractionResult(authors=["Bob Jones"], source="pdf_front_matter")
        res = resolve_author_extraction([empty, front])
        assert res.authors == ["Bob Jones"]
        assert res.source == "pdf_front_matter"

    def test_disagreeing_sources_flag_review_without_deleting(self):
        tex = AuthorExtractionResult(authors=["Alice Smith"], source="tex_author")
        tei = AuthorExtractionResult(authors=["Zoe Park", "Yan Li"], source="grobid_tei")
        res = resolve_author_extraction([tex, tei])
        assert res.authors == ["Alice Smith"]  # chosen source preserved
        assert res.needs_review is True
        assert "author_source_mismatch" in res.review_reasons

    def test_all_empty_returns_none_source(self):
        res = resolve_author_extraction([AuthorExtractionResult()])
        assert res.authors == []
        assert res.source == "none"
        assert res.needs_review is False


# ---------------------------------------------------------------------------
# Pipeline-style integration (parser-driven, no LLM / network)
# ---------------------------------------------------------------------------

class TestFrontMatterIntegration:
    def test_converges_to_four_authors_with_huge_bibliography(self):
        # Issue #366/#372: a document with ~100 bibliography people but a clean
        # 4-author byline must converge to exactly the 4 front-matter authors.
        blocks = [
            _block("t", 1, "Kurtosis Consistency Relations", order=0),
            _block("by", 1,
                   "Taro Yamada, Hanako Suzuki, Jiro Tanaka and Saburo Kato",
                   order=1),
            _block("h_abs", 1, "Abstract", block_type="section_heading", order=2),
            _block("abs", 1, "We study Horndeski gravity consistency relations.", order=3),
        ]
        # 100 reference entries on a later page, each a citation author.
        blocks += [
            _block(f"r{i}", 9, f"Citation Person{i}. A cited work. 20{i % 100:02d}.",
                   block_type="reference_entry", order=100 + i)
            for i in range(100)
        ]
        res = resolve_author_extraction([extract_authors_from_front_matter(blocks)])
        assert res.authors == [
            "Taro Yamada", "Hanako Suzuki", "Jiro Tanaka", "Saburo Kato",
        ]
        assert res.source == "pdf_front_matter"
        # No citation person leaked in.
        assert not any("Person" in a for a in res.authors)

    def test_tex_source_takes_priority_over_front_matter(self):
        front = extract_authors_from_front_matter([
            _block("t", 1, "Title Of Paper", order=0),
            _block("by", 1, "Wrong Byline Parse", order=1),
            _block("h_abs", 1, "Abstract", block_type="section_heading", order=2),
        ])
        tex = extract_authors_from_tex(r"\author{Alice Smith \and Bob Jones}")
        res = resolve_author_extraction([tex, front])
        assert res.authors == ["Alice Smith", "Bob Jones"]
        assert res.source == "tex_author"
