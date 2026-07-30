"""DiscussOpeningValidator のハードルール（設計書 §4.1 / §8 Phase 1）。"""
from __future__ import annotations

import pytest

from episteme_graph.agents.discuss_opening.schema import (
    AssumptionFact,
    AuthorChoice,
    DiscussOpeningInput,
    FORBIDDEN_PHRASES,
    MAX_SEED_BODY_CHARS,
    ThesisFacts,
)
from episteme_graph.agents.discuss_opening.validator import DiscussOpeningValidator

VALIDATOR = DiscussOpeningValidator()

_ASSUMPTION_TEXT = "The detector response is linear over the full energy range considered."
_CHOICE_REASON = "The amplitude is small compared with the equilibrium separation."


def _input(**kwargs) -> DiscussOpeningInput:
    defaults = dict(
        document_id="doc-1",
        thesis=ThesisFacts(
            central_question="Can a single relation describe the three ratios?",
            paper_goal="Establish a consistency relation.",
            central_thesis_text="A single consistency relation links the three ratios.",
        ),
        untested_assumptions=[
            AssumptionFact(statement=_ASSUMPTION_TEXT, target_type="assumption",
                           verification_status="untested"),
        ],
        author_choices=[
            AuthorChoice(operation="linearize", operation_subtype="small-amplitude",
                         reason=_CHOICE_REASON, from_label="x'' + sin(x) = 0",
                         to_label="x'' + x = 0"),
        ],
        language="ja",
        max_seeds=4,
    )
    defaults.update(kwargs)
    return DiscussOpeningInput(**defaults)


def _seed(**kwargs) -> dict:
    defaults = dict(
        body="この前提が高エネルギー側で崩れるとしたら、結論のどこが変わると考えますか？",
        evidence_quote=_ASSUMPTION_TEXT,
        reason="未検証の前提が結論の精度に直結している",
        confidence=0.7,
        grounded_in="untested_assumption",
    )
    defaults.update(kwargs)
    return defaults


def _raw(*seeds, **extra) -> dict:
    payload = {"seeds": list(seeds) or [_seed()], "skipped_reason": None}
    payload.update(extra)
    return payload


def _errors(raw: dict, item: DiscussOpeningInput | None = None) -> list[str]:
    _result, errors, _warnings = VALIDATOR.validate(raw, item or _input())
    return errors


class TestHappyPath:
    def test_valid_output_produces_result(self):
        result, errors, warnings = VALIDATOR.validate(
            _raw(_seed(), _seed(body="著者は線形化を選んでいますが、別の扱いなら何が見えなくなると思いますか？",
                                evidence_quote=_CHOICE_REASON,
                                grounded_in="author_choice")),
            _input(),
        )
        assert errors == []
        assert result is not None
        assert len(result.seeds) == 2
        assert result.language == "ja"
        assert result.skipped_reason is None
        # 件数が目標レンジ内なので警告なし
        assert warnings == []

    def test_single_seed_is_kept_with_a_warning_not_an_error(self):
        result, errors, warnings = VALIDATOR.validate(_raw(_seed()), _input())
        assert errors == []
        assert result is not None and len(result.seeds) == 1
        assert any("fewer_seeds_than_target" in w for w in warnings)


class TestGroundingRules:
    def test_missing_evidence_quote_is_an_error(self):
        assert any(
            "seed_evidence_quote_missing" in e
            for e in _errors(_raw(_seed(evidence_quote="")))
        )

    def test_paraphrased_quote_is_rejected_as_not_verbatim(self):
        raw = _raw(_seed(evidence_quote="検出器の応答は線形であると仮定している"))
        assert any("seed_evidence_quote_not_verbatim" in e for e in _errors(raw))

    def test_verbatim_quote_survives_whitespace_differences(self):
        quote = "The detector response  is linear\nover the full energy range considered."
        assert _errors(_raw(_seed(evidence_quote=quote))) == []

    def test_quote_from_author_choice_reason_is_accepted(self):
        raw = _raw(_seed(evidence_quote=_CHOICE_REASON, grounded_in="author_choice"))
        assert _errors(raw) == []

    def test_too_short_quote_is_an_error(self):
        assert any(
            "seed_evidence_quote_too_short" in e for e in _errors(_raw(_seed(evidence_quote="The")))
        )


class TestQuestionAndLanguageRules:
    def test_statement_without_question_mark_is_an_error(self):
        raw = _raw(_seed(body="この前提はまだ確かめられていません。"))
        assert any("seed_not_a_question" in e for e in _errors(raw))

    def test_ascii_question_mark_is_accepted(self):
        raw = _raw(_seed(body="この前提が崩れたら結論はどう変わりますか?"))
        assert _errors(raw) == []

    def test_english_body_is_rejected_when_language_is_ja(self):
        raw = _raw(_seed(body="What would change if the assumption failed?"))
        assert any("seed_language_mismatch" in e for e in _errors(raw))

    def test_english_body_is_accepted_when_language_is_en(self):
        item = _input(language="en")
        raw = _raw(_seed(body="What would change if the assumption failed?"))
        assert _errors(raw, item) == []

    def test_over_long_body_is_an_error(self):
        raw = _raw(_seed(body="あ" * (MAX_SEED_BODY_CHARS + 1) + "？"))
        assert any("seed_body_too_long" in e for e in _errors(raw))

    def test_empty_body_is_an_error(self):
        assert any("seed_body_empty" in e for e in _errors(_raw(_seed(body=""))))

    def test_missing_reason_is_an_error(self):
        assert any("seed_reason_missing" in e for e in _errors(_raw(_seed(reason=""))))


class TestDenylist:
    @pytest.mark.parametrize("phrase", list(FORBIDDEN_PHRASES))
    def test_forbidden_phrase_in_body_is_an_error(self, phrase):
        raw = _raw(_seed(body=f"{phrase}という視点で、どこが変わると考えますか？"))
        assert any("forbidden_phrase" in e for e in _errors(raw))

    @pytest.mark.parametrize("phrase", list(FORBIDDEN_PHRASES))
    def test_forbidden_phrase_in_reason_is_an_error(self, phrase):
        raw = _raw(_seed(reason=f"{phrase}"))
        assert any("forbidden_phrase" in e for e in _errors(raw))


class TestStructuralRules:
    def test_non_object_output_is_an_error(self):
        result, errors, _warnings = VALIDATOR.validate([], _input())  # type: ignore[arg-type]
        assert result is None
        assert any("output_not_object" in e for e in errors)

    def test_missing_seeds_key_is_an_error(self):
        result, errors, _warnings = VALIDATOR.validate({"skipped_reason": None}, _input())
        assert result is None
        assert any("seeds_missing" in e for e in errors)

    def test_confidence_out_of_range_is_an_error(self):
        # from_dict clamps, so an out-of-range value cannot silently pass through
        # as a raw number — the clamp itself is the guard we assert here.
        result, errors, _warnings = VALIDATOR.validate(_raw(_seed(confidence=7.5)), _input())
        assert errors == []
        assert result is not None and result.seeds[0].confidence == 1.0

    def test_unknown_grounding_is_a_warning_not_an_error(self):
        result, errors, warnings = VALIDATOR.validate(
            _raw(_seed(grounded_in="vibes")), _input()
        )
        assert errors == []
        assert result is not None
        assert any("seed_unknown_grounding" in w for w in warnings)


class TestSkipSemantics:
    def test_empty_seeds_with_material_is_an_error(self):
        result, errors, _warnings = VALIDATOR.validate({"seeds": []}, _input())
        assert result is None
        assert any("empty_seeds_with_material" in e for e in errors)

    def test_empty_seeds_without_material_is_accepted_as_skipped(self):
        item = _input(untested_assumptions=[], author_choices=[])
        result, errors, _warnings = VALIDATOR.validate(
            {"seeds": [], "skipped_reason": "no_source_material"}, item
        )
        assert errors == []
        assert result is not None
        assert result.seeds == []
        assert result.skipped_reason == "no_source_material"
