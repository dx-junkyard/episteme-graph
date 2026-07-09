"""StructureAnchorAgent — LLMモックでのゴールデン例と repair の挙動。

観点（tension の test_tension_agent.py と同方式）:
- agent: examples/ のゴールデン例（細粒度帰属 + 縮退帰属）
- repair: 1回で直るケース / 2回失敗 → repair_failed（worker が印だけ残す）
- 問いゼロの入力は LLM を呼ばない
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from core.structure_anchor.agent import StructureAnchorAgent
from core.structure_anchor.repair import MAX_REPAIR_ATTEMPTS
from core.structure_anchor.schema import AnchorContext, QuestionRecord

EXAMPLES = Path(__file__).resolve().parents[1] / "core" / "structure_anchor" / "examples"


def _load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _context_of(data: dict) -> AnchorContext:
    return AnchorContext(
        course_id=data["course_id"], topic_id=data["topic_id"], topic_title=data["topic_title"],
        questions=[QuestionRecord(**q) for q in data["questions"]],
        claims=data.get("claims", []),
        equations=data.get("equations", []),
        derivation_steps=data.get("derivation_steps", []),
        concepts=data.get("concepts", []),
        chunks=data.get("chunks", []),
        segments=data.get("segments", []),
    )


class _MockLLM:
    """complete_json の呼び出しごとに outputs を順に返すモック。"""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0
        self.contents: list[str] = []

    def complete_json(self, content: str) -> dict:
        self.contents.append(content)
        out = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        if isinstance(out, Exception):
            raise out
        return copy.deepcopy(out)


class TestGoldenExample:
    def test_sample_input_output_pair_validates(self):
        ctx = _context_of(_load("sample_input.json"))
        agent = StructureAnchorAgent(llm_client=_MockLLM([_load("sample_output.json")]))
        result = agent.run(ctx)
        assert result.repair_failed is False
        assert len(result.candidates) == 2
        fine = {c.trace_id: c for c in result.candidates}
        assert fine["tr_001"].anchor_type == "derivation_step"
        assert fine["tr_001"].doubt_type == "justification_gap"
        # 構造を持たない問いは segment へ縮退している
        assert fine["tr_002"].anchor_type == "segment"
        assert fine["tr_002"].doubt_type == "definition"

    def test_prompt_contains_instruction_and_questions(self):
        ctx = _context_of(_load("sample_input.json"))
        mock = _MockLLM([_load("sample_output.json")])
        StructureAnchorAgent(llm_client=mock).run(ctx)
        content = mock.contents[0]
        assert "Return ONLY valid JSON" in content
        assert "trace_id=tr_001" in content
        assert "clm_042" in content

    def test_no_questions_skips_llm(self):
        ctx = AnchorContext(course_id="c", topic_id="t", topic_title="T")
        mock = _MockLLM([{}])
        result = StructureAnchorAgent(llm_client=mock).run(ctx)
        assert result.candidates == []
        assert mock.calls == 0


class TestRepair:
    def test_one_shot_repair_succeeds(self):
        ctx = _context_of(_load("sample_input.json"))
        good = _load("sample_output.json")
        broken = copy.deepcopy(good)
        broken["anchors"][0]["anchor_id"] = "clm_999"  # 捏造 id
        mock = _MockLLM([broken, good])
        result = StructureAnchorAgent(llm_client=mock).run(ctx)
        assert mock.calls == 2
        assert result.repair_failed is False
        assert len(result.candidates) == 2
        assert "violated these rules" in mock.contents[1]
        assert "Fix ONLY the violations" in mock.contents[1]

    def test_two_failures_yield_repair_failed_not_crash(self):
        ctx = _context_of(_load("sample_input.json"))
        broken = copy.deepcopy(_load("sample_output.json"))
        broken["anchors"][0]["evidence_quote"] = "存在しない引用文"
        mock = _MockLLM([broken])  # 何度呼んでも同じ違反を返す
        result = StructureAnchorAgent(llm_client=mock).run(ctx)
        assert mock.calls == 1 + MAX_REPAIR_ATTEMPTS
        assert result.repair_failed is True
        assert result.candidates == []
        assert result.warnings  # 失敗理由を保持（P4: 情報を落とさない）

    def test_unparseable_json_goes_through_repair(self):
        ctx = _context_of(_load("sample_input.json"))
        mock = _MockLLM([ValueError("not json"), _load("sample_output.json")])
        result = StructureAnchorAgent(llm_client=mock).run(ctx)
        assert result.repair_failed is False
        assert len(result.candidates) == 2
