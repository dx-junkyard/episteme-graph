"""GROBID TEI author extraction limited to front matter (issue #372).

Document authors live in <teiHeader>; GROBID records citation authors under
<text><back><listBibl>. The parser must read only the header authors, so a
bibliography of ~100 people never contaminates the document author list, while
the genuine front-matter authors converge (e.g. #366: exactly 4).

bs4 が無い環境では skip。GROBID サーバ接続は行わない純粋なロジックテスト。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

pytest.importorskip("bs4", reason="beautifulsoup4 not installed")

from episteme_graph.agents.document_structure.grobid_parser import GROBIDTEIParser  # noqa: E402


def _ref_authors_xml(n: int) -> str:
    entries = []
    for i in range(n):
        entries.append(
            f'<biblStruct><analytic><author><persName>'
            f'<forename>Ref{i}</forename><surname>Citation{i}</surname>'
            f'</persName></author></analytic></biblStruct>'
        )
    return "".join(entries)


def _tei_with_header_and_reference_authors(n_refs: int) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title level="a" type="main">Kurtosis Consistency Relation</title></titleStmt>
      <sourceDesc>
        <biblStruct><analytic>
          <author><persName><forename>Taro</forename><surname>Yamada</surname></persName></author>
          <author><persName><forename>Hanako</forename><surname>Suzuki</surname></persName></author>
          <author><persName><forename>Jiro</forename><surname>Tanaka</surname></persName></author>
          <author><persName><forename>Saburo</forename><surname>Kato</surname></persName></author>
        </analytic></biblStruct>
      </sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <div n="1"><head>Introduction</head><p>We extend Horndeski gravity.</p></div>
    </body>
    <back>
      <div type="references"><listBibl>{_ref_authors_xml(n_refs)}</listBibl></div>
    </back>
  </text>
</TEI>"""


class TestGrobidFrontMatterAuthors:
    def test_only_header_authors_extracted(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_tei_with_header_and_reference_authors(3))
        assert result.metadata.authors == [
            "Taro Yamada", "Hanako Suzuki", "Jiro Tanaka", "Saburo Kato",
        ]

    def test_reference_authors_never_contaminate(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_tei_with_header_and_reference_authors(100))
        # #366: converges to exactly the 4 front-matter authors despite ~100
        # bibliography people.
        assert len(result.metadata.authors) == 4
        joined = " ".join(result.metadata.authors)
        assert "Citation" not in joined and "Ref" not in joined

    def test_author_extraction_provenance_recorded(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_tei_with_header_and_reference_authors(5))
        prov = result.metadata.author_extraction
        assert prov["source"] == "grobid_tei"
        assert prov["confidence"] == 0.95
        assert prov["needs_review"] is False
