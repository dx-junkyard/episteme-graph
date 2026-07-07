"""D1-4 — 検証スコープ候補（LLM 補助）の validator / repair テスト。"""

from __future__ import annotations

from core.doubt.scope_candidates.repair import run_with_repair
from core.doubt.scope_candidates.schema import (
    MAX_CANDIDATES_PER_TARGET,
    ScopeTargetContext,
    SourceBlock,
)
from core.doubt.scope_candidates.validator import validate_output

_SOURCE = "この収束は一次摂動の精度で、電磁相互作用の領域において確認されている。"


def _context() -> ScopeTargetContext:
    return ScopeTargetContext(
        target_id="c1",
        target_type="claim",
        source_blocks=[SourceBlock("S1", "evidence", _SOURCE)],
    )


def _valid_candidate() -> dict:
    return {
        "condition": "",
        "domain": "電磁相互作用の領域で",
        "precision": "一次摂動の精度で",
        "system": "",
        "evidence_quote": _SOURCE,
        "reason": "evidence に精度と領域の限定が明示されている",
        "confidence": 0.8,
    }


class TestValidator:
    def test_valid_output_passes(self):
        result, errors, _ = validate_output({"candidates": [_valid_candidate()]}, _context())
        assert errors == []
        assert result is not None
        assert len(result.candidates) == 1
        # AIに疑わせない: 候補は常に candidate
        assert result.candidates[0].status == "candidate"

    def test_missing_axis_fails(self):
        candidate = _valid_candidate()
        candidate.update({"condition": "", "domain": "", "precision": "", "system": ""})
        result, errors, _ = validate_output({"candidates": [candidate]}, _context())
        assert result is None
        assert any("condition/domain/precision/system" in e for e in errors)

    def test_non_verbatim_quote_fails(self):
        """evidence_quote は出典の逐語部分文字列でなければならない（P5）。"""
        candidate = _valid_candidate()
        candidate["evidence_quote"] = "この収束はすべての場合に成り立つ。"
        result, errors, _ = validate_output({"candidates": [candidate]}, _context())
        assert result is None
        assert any("verbatim" in e for e in errors)

    def test_missing_reason_fails(self):
        candidate = _valid_candidate()
        candidate["reason"] = ""
        result, errors, _ = validate_output({"candidates": [candidate]}, _context())
        assert result is None

    def test_too_many_candidates_fails(self):
        candidates = [_valid_candidate() for _ in range(MAX_CANDIDATES_PER_TARGET + 1)]
        result, errors, _ = validate_output({"candidates": candidates}, _context())
        assert result is None
        assert any("too many" in e for e in errors)

    def test_confidence_clamped_with_warning(self):
        candidate = _valid_candidate()
        candidate["confidence"] = 1.7
        result, errors, warnings = validate_output({"candidates": [candidate]}, _context())
        assert errors == []
        assert result.candidates[0].confidence == 1.0
        assert warnings

    def test_empty_candidates_is_valid(self):
        """出典から読み取れない場合の空配列は正常出力（無理に作らない）。"""
        result, errors, _ = validate_output({"candidates": []}, _context())
        assert errors == []
        assert result.candidates == []


class _FailingClient:
    def __init__(self):
        self.calls = 0

    def complete_json(self, content: str) -> dict:
        self.calls += 1
        return {"candidates": [{"evidence_quote": "出典に無い文", "reason": "", "confidence": 0.5}]}


class TestRepair:
    def test_two_failures_keep_row_with_repair_failed(self):
        """repair 2 回失敗でも結果は破棄されず repair_failed=True で保持（P4）。"""
        client = _FailingClient()
        result = run_with_repair(client, "base", _context())
        assert result.repair_failed is True
        assert result.candidates == []
        assert client.calls == 3  # 初回 + 修復2回

    def test_success_on_repair(self):
        class _RecoveringClient:
            def __init__(self):
                self.calls = 0

            def complete_json(self, content: str) -> dict:
                self.calls += 1
                if self.calls == 1:
                    return {"candidates": "not-a-list"}
                return {"candidates": [_valid_candidate()]}

        result = run_with_repair(_RecoveringClient(), "base", _context())
        assert result.repair_failed is False
        assert len(result.candidates) == 1
