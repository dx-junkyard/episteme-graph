"""永続化への出所マージ（claim_concept_grounding_design.md §6）。

``_build_claim_items``（純関数）に接地 artifact を渡して、``theory_claims.concepts`` の
各要素に ``source`` / ``entry_id`` / ``mapping_justification`` / ``canonical`` が
**additive に**乗ること、既存キー・既存の値が変わらないこと、artifact が無ければ
従来どおりであることを固定する。DB には触らない。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.document_pipeline import persistence  # noqa: E402


@dataclass
class _Concept:
    name: str
    normalized: str
    concept_type: str = "unknown"
    role: str = "unknown"


@dataclass
class _Claim:
    claim_id: str
    text: str
    normalized_text: str = ""
    claim_type: str = "result"
    concepts: list = field(default_factory=list)
    support_status: str = "source_backed"
    source_evidence_ids: list = field(default_factory=list)
    equation_ids: list = field(default_factory=list)


@dataclass
class _ClaimObjects:
    claims: list = field(default_factory=list)


_GROUNDING = {
    "claims": {
        "c1": [
            {
                "normalized": "form factor",
                "name": "Form factor",
                "canonical": "Form factor",
                "source": "registry_label",
                "entry_id": "e1",
                "mapping_justification": "lexical_match",
            }
        ]
    },
    "population": 1,
    "processed": 1,
    "coverage": {"population": 1, "processed": 1, "truncated": 0, "reasons": []},
}


def _items(concept_grounding=None) -> list[dict]:
    claim = _Claim(
        "c1",
        "The form factor is normalised.",
        normalized_text="the form factor is normalised",
        concepts=[
            _Concept("Form factor", "form factor", "concept"),
            _Concept("R_D", "R_D", "symbol"),
        ],
    )
    return persistence._build_claim_items(
        document_id="11111111-1111-1111-1111-111111111111",
        spans=[],
        claim_objects=_ClaimObjects(claims=[claim]),
        claim_ids_by_span_key={},
        evidence_blocks={},
        block_to_chunk={},
        thesis_ref_index={},
        equation_keys={},
        concept_grounding=concept_grounding,
    )


class TestConceptProvenanceMerge:
    def test_provenance_is_added_to_the_matching_concept(self):
        concepts = _items(_GROUNDING)[0]["values"]["concepts"]
        matched = concepts[0]
        assert matched["entry_id"] == "e1"
        assert matched["source"] == "registry_label"
        assert matched["mapping_justification"] == "lexical_match"
        assert matched["canonical"] == "Form factor"
        # 既存キーは不変（名前を正規化で書き換えない = CG5）。
        assert matched["name"] == "Form factor"
        assert matched["normalized"] == "form factor"
        assert matched["concept_type"] == "concept"

    def test_unmatched_concepts_are_untouched(self):
        concepts = _items(_GROUNDING)[0]["values"]["concepts"]
        assert concepts[1]["name"] == "R_D"
        assert "entry_id" not in concepts[1]
        assert "source" not in concepts[1]

    def test_without_grounding_the_shape_is_unchanged(self):
        concepts = _items(None)[0]["values"]["concepts"]
        assert [set(c) for c in concepts] == [
            {"name", "normalized", "concept_type", "role"},
            {"name", "normalized", "concept_type", "role"},
        ]

    def test_other_claim_values_are_unchanged_by_the_merge(self):
        with_grounding = _items(_GROUNDING)[0]["values"]
        without = _items(None)[0]["values"]
        for key in without:
            if key == "concepts":
                continue
            assert with_grounding[key] == without[key], key

    def test_a_broken_artifact_is_ignored(self):
        for broken in ("nope", {"claims": "nope"}, {}, {"claims": {"c1": "x"}}):
            concepts = _items(broken)[0]["values"]["concepts"]
            assert "entry_id" not in concepts[0]

    def test_persist_accepts_the_keyword(self):
        import inspect

        signature = inspect.signature(persistence.persist_qualified_claims)
        parameter = signature.parameters["concept_grounding"]
        assert parameter.default is None
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
