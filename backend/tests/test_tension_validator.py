"""TensionMiningAgent validator — §6 の全ルール（evidence_quote 逐語一致・断定形検出・id捏造など）。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from core.tension.schema import ConversationTurn, ConversationWindow
from core.tension.validator import validate_output

EXAMPLES = Path(__file__).resolve().parents[1] / "core" / "tension" / "examples"


def _load_example(name: str) -> tuple[ConversationWindow, dict]:
    data = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    w = data["window"]
    window = ConversationWindow(
        course_id=w["course_id"], topic_id=w["topic_id"], topic_title=w["topic_title"],
        turns=[ConversationTurn(**t) for t in w["turns"]],
        components=w.get("components", []), chunks=w.get("chunks", []),
    )
    return window, data["llm_output"]


def _golden():
    return _load_example("positive_definition_unease.json")


class TestGoldenPasses:
    def test_positive_example_passes_validation(self):
        window, output = _golden()
        result, errors, warnings = validate_output(output, window, max_candidates=3)
        assert errors == []
        assert result is not None
        assert len(result.candidates) == 1
        assert result.candidates[0].tension_type == "definition_unease"

    def test_negative_examples_pass_with_empty_candidates(self):
        for name in ("negative_gap.json", "negative_satisfied.json"):
            window, output = _load_example(name)
            result, errors, _ = validate_output(output, window, max_candidates=3)
            assert errors == [], name
            assert result.candidates == [], name


class TestHardRules:
    def _validate(self, mutate, max_candidates: int = 3):
        window, output = _golden()
        output = copy.deepcopy(output)
        mutate(output)
        return validate_output(output, window, max_candidates=max_candidates)

    def test_evidence_quote_must_be_verbatim_learner_substring(self):
        _, errors, _ = self._validate(
            lambda o: o["candidates"][0].__setitem__("evidence_quote", "この文は会話に存在しない"))
        assert any("verbatim" in e for e in errors)

    def test_evidence_quote_from_tutor_is_rejected(self):
        # tutor 発話の引用は learner 発話でないため逐語一致でも不可
        _, errors, _ = self._validate(
            lambda o: o["candidates"][0].__setitem__(
                "evidence_quote", "その通りです。カットオフスケールより上の自由度を経路積分で積分し"))
        assert any("verbatim" in e for e in errors)

    def test_unknown_turn_ids_rejected(self):
        _, errors, _ = self._validate(
            lambda o: o["candidates"][0].__setitem__("turn_ids", ["msg_9999"]))
        assert any("unknown ids" in e for e in errors)

    def test_invented_target_ref_ids_rejected(self):
        _, errors, _ = self._validate(
            lambda o: o["candidates"][0]["target_refs"].__setitem__("component_ids", ["tc_invented"]))
        assert any("invented ids" in e for e in errors)

    def test_tension_type_must_be_in_taxonomy(self):
        _, errors, _ = self._validate(
            lambda o: o["candidates"][0].__setitem__("tension_type", "made_up_type"))
        assert any("taxonomy" in e for e in errors)

    def test_assertive_paraphrase_rejected(self):
        _, errors, _ = self._validate(
            lambda o: o["candidates"][0].__setitem__("paraphrase", "あなたはカットオフに不満を感じています"))
        assert any("assertive" in e or "tentative" in e for e in errors)

    def test_paraphrase_requires_tentative_ending(self):
        _, errors, _ = self._validate(
            lambda o: o["candidates"][0].__setitem__("paraphrase", "カットオフの恣意性が問題です"))
        assert any("tentative" in e for e in errors)

    def test_paraphrase_tentative_endings_accepted(self):
        for ending in ("〜に引っかかりがあるのかもしれません", "〜が気になっているように見えます。"):
            result, errors, _ = self._validate(
                lambda o, e=ending: o["candidates"][0].__setitem__("paraphrase", e))
            assert errors == [], ending

    def test_paraphrase_max_120_chars(self):
        _, errors, _ = self._validate(
            lambda o: o["candidates"][0].__setitem__("paraphrase", "あ" * 121 + "かもしれません"))
        assert any("120" in e for e in errors)

    def test_gap_entry_must_not_be_in_candidates(self):
        _, errors, _ = self._validate(
            lambda o: o["candidates"][0].__setitem__("is_tension_not_gap", False))
        assert any("rejected_as_gap" in e for e in errors)

    def test_max_candidates_enforced(self):
        def dup(o):
            o["candidates"] = o["candidates"] * 4
        _, errors, _ = self._validate(dup, max_candidates=3)
        assert any("max_candidates" in e for e in errors)

    def test_empty_reason_rejected(self):
        _, errors, _ = self._validate(
            lambda o: o["candidates"][0].__setitem__("reason", ""))
        assert any("reason" in e for e in errors)

    def test_non_dict_output_rejected(self):
        window, _ = _golden()
        result, errors, _ = validate_output([], window, max_candidates=3)
        assert result is None
        assert errors


class TestConfidenceCalibration:
    def test_single_signal_reason_clamps_confidence_to_half(self):
        window, output = _golden()
        output = copy.deepcopy(output)
        output["candidates"][0]["reason"] = "Learner said なんとなく once."
        output["candidates"][0]["confidence"] = 0.9
        result, errors, warnings = validate_output(output, window, max_candidates=3)
        assert errors == []
        assert result.candidates[0].confidence == 0.5
        assert any("clamped" in w for w in warnings)

    def test_multi_signal_reason_keeps_confidence(self):
        window, output = _golden()
        result, errors, warnings = validate_output(output, window, max_candidates=3)
        assert errors == []
        assert result.candidates[0].confidence > 0.5
        assert warnings == []

    def test_out_of_range_confidence_is_hard_error(self):
        window, output = _golden()
        output = copy.deepcopy(output)
        output["candidates"][0]["confidence"] = 1.5
        _, errors, _ = validate_output(output, window, max_candidates=3)
        assert any("[0.0, 1.0]" in e for e in errors)
