"""修復フック（``repair.py``）が validator の hard error と矛盾しないこと。

``discuss_opening`` の [D-7] と同じ落とし穴を最初から避ける: 修復指示が
「置けなければ ``placements`` を空にせよ」だけを言うと、``unplaced_domains`` の
申告が無い空出力は validator の hard error
（``no_placement_and_no_unplaced_declaration``）になり、指示どおりに従った出力が
必ず落ちて修復の1周が無駄になる。指示は validator が受け取れる出力
（= 空にするなら理由を domain ごとに書く）を求めるものにする。
"""
from __future__ import annotations

from episteme_graph.agents.landscape_placement.repair import (
    build_repair_failed_result,
    build_repair_prompt,
)
from episteme_graph.agents.landscape_placement.schema import (
    ClaimSummary,
    DomainOption,
    LandscapePlacementInput,
    SKIPPED_REASON_REPAIR_FAILED,
    SkeletonNodeOption,
)
from episteme_graph.agents.landscape_placement.validator import (
    LandscapePlacementValidator,
)

_THESIS = "A single relation links the temperature and polarization amplitudes."


def _item_with_material() -> LandscapePlacementInput:
    return LandscapePlacementInput(
        document_id="doc-1",
        central_thesis=_THESIS,
        claim_summaries=[ClaimSummary("c_1", "A quadratic estimator is applied.")],
        domains=[
            DomainOption(
                domain_key="astrophysics",
                nodes=[SkeletonNodeOption("cosmology", "宇宙論", "region", "")],
            )
        ],
    )


class TestRepairPromptMatchesValidator:
    def test_prompt_pairs_the_empty_case_with_a_reason_requirement(self):
        prompt = build_repair_prompt("{}", ["placement_unknown_node: ..."])

        assert "unplaced_domains" in prompt
        assert "理由なしで空にはしない" in prompt

    def test_prompt_keeps_the_verbatim_and_no_invention_rules(self):
        prompt = build_repair_prompt("{}", ["placement_evidence_quote_not_verbatim: ..."])

        assert "そのままコピー" in prompt
        assert "創作しない" in prompt

    def test_prompt_lists_the_errors_to_fix(self):
        prompt = build_repair_prompt("{...}", ["rule_a: bad", "rule_b: worse"])

        assert "- rule_a: bad" in prompt
        assert "- rule_b: worse" in prompt

    def test_prompt_includes_the_previous_raw_output(self):
        prompt = build_repair_prompt('{"placements": []}', ["rule_a: bad"])
        assert '{"placements": []}' in prompt

        blind = build_repair_prompt("", ["rule_a: bad"])
        assert "出力なし" in blind

    def test_following_the_instruction_passes_validation(self):
        """指示どおり「実在ノード + 素材からの逐語引用」を1件返せば検証を通る。"""
        item = _item_with_material()
        raw = {
            "placements": [
                {
                    "domain_key": "astrophysics",
                    "node_id": "cosmology",
                    "perspective": "subject",
                    "weight": 0.7,
                    "reason": "論文の対象は宇宙論的な振幅の測定である。",
                    "evidence_quote": _THESIS,
                    "claim_id": None,
                    "confidence": 0.6,
                }
            ]
        }

        result, errors, _warnings = LandscapePlacementValidator().validate(raw, item)

        assert errors == []
        assert result is not None
        assert len(result.placements) == 1

    def test_empty_placements_with_declared_reasons_also_passes(self):
        result, errors, _warnings = LandscapePlacementValidator().validate(
            {
                "placements": [],
                "unplaced_domains": [
                    {"domain_key": "astrophysics", "reason": "天体を扱っていない。"}
                ],
            },
            _item_with_material(),
        )

        assert errors == []
        assert result.placements == []
        assert result.unplaced_domains

    def test_silently_empty_output_would_still_fail(self):
        """検証を緩めていないことを固定する（指示側を validator に合わせただけ）。"""
        result, errors, _warnings = LandscapePlacementValidator().validate(
            {"placements": []}, _item_with_material()
        )

        assert result is None
        assert any("no_placement_and_no_unplaced_declaration" in e for e in errors)


class TestRepairFailedResult:
    def test_repair_exhaustion_places_nothing_but_records_why(self):
        result = build_repair_failed_result(
            _item_with_material(), ["rule_a: bad"], cartridge_id="astrophysics"
        )

        assert result.placements == []
        assert result.skipped_reason == SKIPPED_REASON_REPAIR_FAILED
        assert result.review_notes
        assert [i.rule_id for i in result.validation_issues] == ["repair_failed"]
        assert result.cartridge_id == "astrophysics"
