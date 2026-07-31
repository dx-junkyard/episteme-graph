"""修復フック（``repair.py``）が validator の hard error と矛盾しないこと。

[D-7] 2026-08-01 のレビュー修正: 修復指示に「素材が足りなければ `seeds` を空にし
`skipped_reason` を入れる」と書かれていたが、素材（未検証前提 / 著者の選んだ手）が
渡されている状態で空 `seeds` は validator の hard error
（``empty_seeds_with_material``）になる。指示どおりに従った出力が必ず落ちるため、
修復の1周が無駄になっていた。指示は validator が受け取れる出力を求めるものにする。
"""
from __future__ import annotations

from episteme_graph.agents.discuss_opening.repair import (
    build_repair_failed_result,
    build_repair_prompt,
)
from episteme_graph.agents.discuss_opening.schema import (
    AssumptionFact,
    DiscussOpeningInput,
    SKIPPED_REASON_REPAIR_FAILED,
)
from episteme_graph.agents.discuss_opening.validator import DiscussOpeningValidator

_STATEMENT = "The detector response is linear over the full energy range."


def _item_with_material() -> DiscussOpeningInput:
    return DiscussOpeningInput(
        document_id="doc-1",
        untested_assumptions=[
            AssumptionFact(statement=_STATEMENT, target_type="assumption",
                           verification_status="untested"),
        ],
    )


class TestRepairPromptMatchesValidator:
    def test_prompt_does_not_instruct_an_empty_seeds_array(self):
        prompt = build_repair_prompt("{}", ["seed_not_a_question: ..."])

        assert "空にしない" in prompt
        assert "最低1件" in prompt
        # validator が hard error にする出力を要求しない。
        assert "`seeds` を空にし" not in prompt
        assert "skipped_reason" not in prompt

    def test_prompt_still_forbids_fabrication_and_keeps_the_verbatim_rule(self):
        prompt = build_repair_prompt("{}", ["seed_evidence_quote_not_verbatim: ..."])

        assert "素材に無いことは書かない" in prompt
        assert "そのままコピー" in prompt

    def test_prompt_lists_the_errors_to_fix(self):
        prompt = build_repair_prompt("{...}", ["rule_a: bad", "rule_b: worse"])

        assert "- rule_a: bad" in prompt
        assert "- rule_b: worse" in prompt

    def test_following_the_instruction_passes_validation(self):
        """指示どおり「素材から逐語引用した1件」を返せば検証を通る。"""
        item = _item_with_material()
        raw = {
            "seeds": [
                {
                    "body": "この前提が別の条件で成り立たないとしたら、どこが変わると考えますか？",
                    "evidence_quote": _STATEMENT,
                    "reason": "未検証の前提だから",
                    "confidence": 0.5,
                    "grounded_in": "untested_assumption",
                }
            ]
        }

        result, errors, _warnings = DiscussOpeningValidator().validate(raw, item)

        assert errors == []
        assert result is not None
        assert len(result.seeds) == 1

    def test_empty_seeds_with_material_would_fail(self):
        """旧指示に従った出力（空 seeds + skipped_reason）は hard error のままである
        ことを固定する（指示側を validator に合わせたのであって、検証を緩めていない）。"""
        result, errors, _warnings = DiscussOpeningValidator().validate(
            {"seeds": [], "skipped_reason": "no_source_material"}, _item_with_material()
        )

        assert result is None
        assert any("empty_seeds_with_material" in e for e in errors)


class TestRepairFailedResult:
    def test_repair_exhaustion_generates_nothing_but_records_why(self):
        result = build_repair_failed_result(
            _item_with_material(), ["rule_a: bad"], cartridge_id="particle_physics"
        )

        assert result.seeds == []
        assert result.skipped_reason == SKIPPED_REASON_REPAIR_FAILED
        assert result.review_notes
        assert [i.rule_id for i in result.validation_issues] == ["repair_failed"]
