"""structure_anchor.validator の単体テスト。

帰属出力のハード検証（id 捏造・逐語引用・被覆・語彙）と warning クランプを検証する。
外部 API は一切呼び出さない。
"""

from __future__ import annotations

import copy

from core.structure_anchor.schema import AnchorContext, QuestionRecord
from core.structure_anchor.validator import (
    COARSE_ANCHOR_MAX_CONFIDENCE,
    UNCLASSIFIED_MAX_CONFIDENCE,
    validate_output,
)


def _context() -> AnchorContext:
    return AnchorContext(
        course_id="c1",
        topic_id="t1",
        topic_title="線形応答理論",
        questions=[
            QuestionRecord(trace_id="tr_001", text="式(12)の線形化で高次項を無視できるのはなぜですか。"),
            QuestionRecord(trace_id="tr_002", text="ここのエントロピーの意味がわかりません。", segment_id=3),
        ],
        derivation_steps=[{"id": "clm_042", "text": "線形化により高次項を無視"}],
        concepts=[{"id": "cmp_007", "label": "線形応答"}],
        segments=[{"id": "seg_3", "label": "第2章"}],
    )


def _valid_output() -> dict:
    return {
        "anchors": [
            {
                "trace_id": "tr_001",
                "anchor_type": "derivation_step",
                "anchor_id": "clm_042",
                "anchor_label": "線形化により高次項を無視",
                "doubt_type": "justification_gap",
                "evidence_quote": "高次項を無視できるのはなぜですか",
                "confidence": 0.86,
                "reason": "names the linearization step and asks why",
            },
            {
                "trace_id": "tr_002",
                "anchor_type": "segment",
                "anchor_id": "seg_3",
                "anchor_label": "第2章",
                "doubt_type": "definition",
                "evidence_quote": "エントロピーの意味がわかりません",
                "confidence": 0.6,
                "reason": "points at the current segment and asks the meaning of a term",
            },
        ],
        "unattributed": [],
    }


class TestHardRules:
    def test_valid_output_passes(self):
        result, errors, warnings = validate_output(_valid_output(), _context())
        assert errors == []
        assert result is not None
        assert len(result.candidates) == 2
        assert warnings == []

    def test_invented_anchor_id_is_hard_error(self):
        data = _valid_output()
        data["anchors"][0]["anchor_id"] = "clm_999"
        result, errors, _ = validate_output(data, _context())
        assert result is None
        assert any("invented id" in e for e in errors)

    def test_stage_vocabulary_is_the_allowed_set(self):
        data = _valid_output()
        data["anchors"][0].update({"anchor_type": "stage", "anchor_id": "elimination"})
        result, errors, _ = validate_output(data, _context())
        assert errors == []
        data["anchors"][0]["anchor_id"] = "made_up_stage"
        result, errors, _ = validate_output(data, _context())
        assert result is None

    def test_evidence_quote_must_be_verbatim(self):
        data = _valid_output()
        data["anchors"][0]["evidence_quote"] = "存在しない引用"
        result, errors, _ = validate_output(data, _context())
        assert result is None
        assert any("verbatim" in e for e in errors)

    def test_unknown_and_duplicate_trace_ids_fail(self):
        data = _valid_output()
        data["anchors"][0]["trace_id"] = "tr_999"
        result, errors, _ = validate_output(data, _context())
        assert result is None
        data = _valid_output()
        data["anchors"][1] = copy.deepcopy(data["anchors"][0])
        result, errors, _ = validate_output(data, _context())
        assert result is None
        assert any("more than once" in e or "missing trace_ids" in e for e in errors)

    def test_every_question_must_be_covered(self):
        data = _valid_output()
        data["anchors"].pop()  # tr_002 がどこにも現れない
        result, errors, _ = validate_output(data, _context())
        assert result is None
        assert any("missing trace_ids" in e for e in errors)

    def test_unattributed_covers_a_question(self):
        data = _valid_output()
        data["anchors"].pop()
        data["unattributed"] = [{"trace_id": "tr_002", "reason": "administrative question"}]
        result, errors, _ = validate_output(data, _context())
        assert errors == []
        assert len(result.unattributed) == 1

    def test_bad_doubt_type_fails(self):
        data = _valid_output()
        data["anchors"][0]["doubt_type"] = "confusion"
        result, errors, _ = validate_output(data, _context())
        assert result is None

    def test_empty_reason_fails(self):
        data = _valid_output()
        data["anchors"][0]["reason"] = ""
        result, errors, _ = validate_output(data, _context())
        assert result is None


class TestWarningClamps:
    def test_unclassified_confidence_clamped(self):
        data = _valid_output()
        data["anchors"][0]["doubt_type"] = "unclassified"
        data["anchors"][0]["confidence"] = 0.9
        result, errors, warnings = validate_output(data, _context())
        assert errors == []
        assert result.candidates[0].confidence == UNCLASSIFIED_MAX_CONFIDENCE
        assert warnings

    def test_coarse_anchor_confidence_clamped(self):
        data = _valid_output()
        data["anchors"][1]["confidence"] = 0.95
        result, errors, warnings = validate_output(data, _context())
        assert errors == []
        assert result.candidates[1].confidence == COARSE_ANCHOR_MAX_CONFIDENCE
        assert warnings

    def test_empty_label_filled_from_context(self):
        data = _valid_output()
        data["anchors"][0]["anchor_label"] = ""
        result, errors, warnings = validate_output(data, _context())
        assert errors == []
        assert result.candidates[0].anchor_label == "線形化により高次項を無視"
        assert any("filled from candidate block" in w for w in warnings)
