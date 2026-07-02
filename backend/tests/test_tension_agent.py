"""TensionMiningAgent — LLMモックでのゴールデン3例と repair の挙動。

観点（設計書 §11-4/5）:
- repair: 1回で直るケース / 2回失敗 → repair_failed（worker が unclassified 保存）
- agent: examples/ のゴールデン3例（陽性・gap陰性・満足陰性）
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from core.tension.agent import TensionMiningAgent
from core.tension.repair import MAX_REPAIR_ATTEMPTS
from core.tension.schema import ConversationTurn, ConversationWindow

EXAMPLES = Path(__file__).resolve().parents[1] / "core" / "tension" / "examples"

GOLDEN_FILES = (
    "positive_definition_unease.json",
    "negative_gap.json",
    "negative_satisfied.json",
)


def _load_example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _window_of(data: dict) -> ConversationWindow:
    w = data["window"]
    return ConversationWindow(
        course_id=w["course_id"], topic_id=w["topic_id"], topic_title=w["topic_title"],
        turns=[ConversationTurn(**t) for t in w["turns"]],
        components=w.get("components", []), chunks=w.get("chunks", []),
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


class TestGoldenExamples:
    def test_positive_example(self):
        data = _load_example("positive_definition_unease.json")
        agent = TensionMiningAgent(llm_client=_MockLLM([data["llm_output"]]))
        result = agent.run(_window_of(data))
        exp = data["expected"]
        assert len(result.candidates) == exp["candidate_count"]
        assert result.candidates[0].tension_type == exp["tension_type"]
        assert result.candidates[0].confidence >= exp["min_confidence"]
        assert result.repair_failed is False

    def test_negative_examples_yield_empty_candidates(self):
        for name in ("negative_gap.json", "negative_satisfied.json"):
            data = _load_example(name)
            agent = TensionMiningAgent(llm_client=_MockLLM([data["llm_output"]]))
            result = agent.run(_window_of(data))
            assert result.candidates == [], name
            assert len(result.rejected_as_gap) == data["expected"]["rejected_count"], name
            assert result.repair_failed is False, name

    def test_prompt_contains_instruction_and_window(self):
        data = _load_example("positive_definition_unease.json")
        mock = _MockLLM([data["llm_output"]])
        TensionMiningAgent(llm_client=mock).run(_window_of(data))
        content = mock.contents[0]
        assert "Return ONLY valid JSON" in content
        assert "msg_0012" in content  # 会話窓が入力に含まれる

    def test_no_learner_turns_skips_llm(self):
        window = ConversationWindow(
            course_id="c", topic_id="t", topic_title="T",
            turns=[ConversationTurn("msg_0000", "tutor", "こんにちは")],
        )
        mock = _MockLLM([{}])
        result = TensionMiningAgent(llm_client=mock).run(window)
        assert result.candidates == []
        assert mock.calls == 0


class TestRepair:
    def test_one_shot_repair_succeeds(self):
        data = _load_example("positive_definition_unease.json")
        broken = copy.deepcopy(data["llm_output"])
        broken["candidates"][0]["paraphrase"] = "あなたはカットオフに不満を感じています"  # 断定形
        mock = _MockLLM([broken, data["llm_output"]])
        result = TensionMiningAgent(llm_client=mock).run(_window_of(data))
        assert mock.calls == 2
        assert result.repair_failed is False
        assert len(result.candidates) == 1
        # 2回目の呼び出しには違反リストと元出力が含まれる（修復プロンプト）
        assert "violated these rules" in mock.contents[1]
        assert "Fix ONLY the violations" in mock.contents[1]

    def test_two_failures_yield_repair_failed_not_crash(self):
        data = _load_example("positive_definition_unease.json")
        broken = copy.deepcopy(data["llm_output"])
        broken["candidates"][0]["evidence_quote"] = "存在しない引用文"
        mock = _MockLLM([broken])  # 何度呼んでも同じ違反を返す
        result = TensionMiningAgent(llm_client=mock).run(_window_of(data))
        assert mock.calls == 1 + MAX_REPAIR_ATTEMPTS
        assert result.repair_failed is True
        assert result.candidates == []
        assert result.warnings  # 失敗理由を保持（P4: 情報を落とさない）

    def test_unparseable_json_goes_through_repair(self):
        data = _load_example("positive_definition_unease.json")
        mock = _MockLLM([ValueError("not json"), data["llm_output"]])
        result = TensionMiningAgent(llm_client=mock).run(_window_of(data))
        assert result.repair_failed is False
        assert len(result.candidates) == 1
