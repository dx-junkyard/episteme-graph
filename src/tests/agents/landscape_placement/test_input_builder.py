"""入力の境界（プロンプト予算の防波堤）と引用ヘイスタックの規約（設計書 §7.3）。"""
from __future__ import annotations

import json
from pathlib import Path

from episteme_graph.agents.landscape_placement.input_builder import (
    LandscapePlacementInputBuilder,
    PREFILTER_NOTE,
    MAX_CLAIMS,
    MAX_CLAIM_CHARS,
    MAX_DOMAINS,
    MAX_NODES_PER_DOMAIN,
    MAX_THESIS_CHARS,
    normalize_for_quote_match,
    truncate,
)
from episteme_graph.agents.landscape_placement.schema import (
    ClaimSummary,
    DomainOption,
    LandscapePlacementInput,
    PERSPECTIVES,
    SkeletonNodeOption,
)
from episteme_graph.agents.landscape_placement.validator import (
    LandscapePlacementValidator,
)

BUILDER = LandscapePlacementInputBuilder()

_EXAMPLE_PATH = (
    Path(__file__).resolve().parents[3]
    / "episteme_graph"
    / "agents"
    / "landscape_placement"
    / "examples"
    / "landscape_placement_example.json"
)


def _input(**kwargs) -> LandscapePlacementInput:
    defaults = dict(
        document_id="doc-1",
        central_thesis="A single relation links the two amplitudes.",
        paper_goal="Establish a consistency relation.",
        claim_summaries=[ClaimSummary("c_1", "A quadratic estimator is applied.")],
        domains=[
            DomainOption(
                domain_key="astrophysics",
                nodes=[SkeletonNodeOption("cosmology", "宇宙論", "region", "")],
            )
        ],
    )
    defaults.update(kwargs)
    return LandscapePlacementInput(**defaults)


class TestNormalization:
    def test_only_whitespace_is_folded(self):
        assert normalize_for_quote_match("a\n  b\tc ") == "a b c"

    def test_case_and_punctuation_are_untouched(self):
        assert normalize_for_quote_match("A-B, C.") == "A-B, C."

    def test_truncate_marks_the_cut(self):
        assert truncate("abcdef", 3) == "abc…"
        assert truncate("abc", 3) == "abc"


class TestPromptBounds:
    def test_claims_are_capped(self):
        item = _input(
            claim_summaries=[
                ClaimSummary(f"c_{i}", f"Claim number {i} text.") for i in range(MAX_CLAIMS + 5)
            ]
        )
        prepared = BUILDER.prepare_for_prompt(item)
        assert len(prepared["claims"]) == MAX_CLAIMS

    def test_domains_and_nodes_are_capped(self):
        item = _input(
            domains=[
                DomainOption(
                    domain_key=f"d_{i}",
                    nodes=[
                        SkeletonNodeOption(f"n_{j}")
                        for j in range(MAX_NODES_PER_DOMAIN + 3)
                    ],
                )
                for i in range(MAX_DOMAINS + 2)
            ]
        )
        prepared = BUILDER.prepare_for_prompt(item)
        assert len(prepared["domains"]) == MAX_DOMAINS
        # 既定 kind は region なので、上限で切られたノードが regions に並ぶ
        assert len(prepared["domains"][0]["regions"]) == MAX_NODES_PER_DOMAIN

    def test_long_texts_are_truncated_for_the_prompt(self):
        item = _input(central_thesis="x" * (MAX_THESIS_CHARS + 100))
        prepared = BUILDER.prepare_for_prompt(item)
        assert len(prepared["paper"]["central_thesis"]) == MAX_THESIS_CHARS + 1  # + "…"

    def test_long_claims_are_truncated_for_the_prompt(self):
        item = _input(claim_summaries=[ClaimSummary("c_1", "y" * (MAX_CLAIM_CHARS + 50))])
        prepared = BUILDER.prepare_for_prompt(item)
        assert len(prepared["claims"][0]["text"]) == MAX_CLAIM_CHARS + 1

    def test_domains_without_nodes_are_dropped_from_the_prompt(self):
        item = _input(
            domains=[
                DomainOption(domain_key="empty", nodes=[]),
                DomainOption(
                    domain_key="astrophysics",
                    nodes=[SkeletonNodeOption("cosmology")],
                ),
            ]
        )
        prepared = BUILDER.prepare_for_prompt(item)
        assert [d["domain_key"] for d in prepared["domains"]] == ["astrophysics"]


class TestPrefilterNote:
    """前段絞り込み（atlas_vector_anchoring_design.md §6）の正直な提示。"""

    def _prepared(self, *, prefiltered):
        item = _input(
            domains=[
                DomainOption(
                    domain_key="astrophysics",
                    nodes=[SkeletonNodeOption("cosmology", "宇宙論", "region", "")],
                    prefiltered=prefiltered,
                )
            ]
        )
        return BUILDER.prepare_for_prompt(item)["domains"][0]

    def test_no_note_when_the_skeleton_was_presented_in_full(self):
        assert "note" not in self._prepared(prefiltered=False)

    def test_note_is_added_when_nodes_were_trimmed(self):
        assert self._prepared(prefiltered=True)["note"] == PREFILTER_NOTE

    def test_note_says_the_list_is_not_the_whole_skeleton(self):
        assert "全ノードではありません" in PREFILTER_NOTE

    def test_flag_survives_the_dict_round_trip(self):
        payload = DomainOption(
            domain_key="astrophysics",
            nodes=[SkeletonNodeOption("cosmology")],
            prefiltered=True,
        ).to_dict()
        assert payload["prefiltered"] is True
        assert DomainOption.from_dict(payload).prefiltered is True
        # 呼び出し側が付けなければ従来どおり False（既定は全提示）。
        assert DomainOption.from_dict({"domain_key": "d"}).prefiltered is False


class TestHaystack:
    def test_haystack_uses_untruncated_text(self):
        tail = "The tail sentence matters."
        item = _input(central_thesis=("filler " * 300) + tail)
        haystack = BUILDER.quote_haystack(item)
        assert tail in haystack
        # プロンプト側は切り詰められている（照合対象と別物であること）
        prepared = BUILDER.prepare_for_prompt(item)
        assert tail not in prepared["paper"]["central_thesis"]

    def test_haystack_excludes_the_title(self):
        item = _input(paper_title="A very distinctive paper title")
        assert "distinctive paper title" not in BUILDER.quote_haystack(item)

    def test_haystack_separator_prevents_cross_text_matches(self):
        item = _input(central_thesis="alpha", paper_goal="beta", claim_summaries=[])
        assert "alpha beta" not in BUILDER.quote_haystack(item)


class TestIndexHelpers:
    def test_node_index_is_keyed_by_domain_and_node(self):
        item = _input(
            domains=[
                DomainOption(
                    domain_key="a",
                    nodes=[SkeletonNodeOption("shared", "A側", "region", "")],
                ),
                DomainOption(
                    domain_key="b",
                    nodes=[SkeletonNodeOption("shared", "B側", "concept", "r")],
                ),
            ]
        )
        index = BUILDER.node_index(item)
        assert index[("a", "shared")].label == "A側"
        assert index[("b", "shared")].kind == "concept"

    def test_claim_ids_helper(self):
        item = _input(
            claim_summaries=[ClaimSummary("c_1", "one"), ClaimSummary("c_2", "two")]
        )
        assert BUILDER.claim_ids(item) == {"c_1", "c_2"}


class TestExampleFile:
    """examples/*.json が現行スキーマと整合していること（人間向け参照の陳腐化防止）。"""

    def _example(self) -> dict:
        return json.loads(_EXAMPLE_PATH.read_text(encoding="utf-8"))

    def test_example_input_round_trips(self):
        example = self._example()
        item = LandscapePlacementInput.from_dict(example["input"])

        assert item.document_id
        assert item.has_material()
        assert item.has_domains()
        # from_dict → to_dict → from_dict が安定
        again = LandscapePlacementInput.from_dict(item.to_dict())
        assert again.to_dict() == item.to_dict()

    def test_example_output_passes_validation(self):
        example = self._example()
        item = LandscapePlacementInput.from_dict(example["input"])

        result, errors, _warnings = LandscapePlacementValidator().validate(
            example["output"], item, cartridge_id=item.cartridge_id
        )

        assert errors == []
        assert result is not None
        assert len(result.placements) == len(example["output"]["placements"])
        assert [u.domain_key for u in result.unplaced_domains] == ["particle_physics"]
        assert all(p.perspective in PERSPECTIVES for p in result.placements)

    def test_example_output_round_trips_through_the_result_dataclass(self):
        from episteme_graph.agents.landscape_placement.schema import (
            LandscapePlacementResult,
        )

        example = self._example()
        item = LandscapePlacementInput.from_dict(example["input"])
        result, _errors, _warnings = LandscapePlacementValidator().validate(
            example["output"], item
        )

        payload = result.to_dict()
        again = LandscapePlacementResult.from_dict(payload)
        assert again.to_dict() == payload
        assert json.loads(result.to_json())["placements"]
