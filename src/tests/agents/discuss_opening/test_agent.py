"""DiscussOpeningAgent の run() 経路（縮退・修復・上限・言語設定）。

修復ループは ``core/llm_worker/repair.run_with_repair`` を実地で通す
（conftest.py が backend/ を sys.path に載せる）。LLM 呼び出しは client の
``complete_json`` を差し替えて遮断する。
"""
from __future__ import annotations

from episteme_graph.agents.discuss_opening.agent import DiscussOpeningAgent
from episteme_graph.agents.discuss_opening.schema import (
    SKIPPED_REASON_LLM_CALL_FAILED,
    SKIPPED_REASON_NO_MATERIAL,
    SKIPPED_REASON_REPAIR_FAILED,
)

_ASSUMPTION = "The detector response is linear over the full energy range considered."
_CHOICE_REASON = "The amplitude is small compared with the equilibrium separation."


def _payload(**kwargs) -> dict:
    payload = {
        "document_id": "doc-1",
        "language": "ja",
        "max_seeds": 4,
        "thesis": {
            "central_question": "Can a single relation describe the three ratios?",
            "paper_goal": "Establish a consistency relation.",
            "central_thesis_text": "A single consistency relation links the three ratios.",
        },
        "untested_assumptions": [
            {
                "statement": _ASSUMPTION,
                "target_type": "assumption",
                "verification_status": "untested",
            }
        ],
        "author_choices": [
            {
                "operation": "linearize",
                "operation_subtype": "small-amplitude",
                "reason": _CHOICE_REASON,
                "from_label": "x'' + sin(x) = 0",
                "to_label": "x'' + x = 0",
            }
        ],
    }
    payload.update(kwargs)
    return payload


def _seed(body: str = "この前提が崩れたら結論のどこが変わると考えますか？", **kwargs) -> dict:
    seed = {
        "body": body,
        "evidence_quote": _ASSUMPTION,
        "reason": "未検証の前提が結論に直結している",
        "confidence": 0.7,
        "grounded_in": "untested_assumption",
    }
    seed.update(kwargs)
    return seed


class _FakeClient:
    """complete_json をスクリプト化した client（BaseJSONLLMClient と同じ interface）。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.contents: list[str] = []

    def complete_json(self, content: str) -> dict:
        self.contents.append(content)
        self.calls += 1
        if not self._responses:
            return {}
        value = self._responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _agent(responses) -> tuple[DiscussOpeningAgent, _FakeClient]:
    agent = DiscussOpeningAgent()
    client = _FakeClient(responses)
    agent._llm_client = client  # noqa: SLF001 — test seam, same as other agents
    return agent, client


class TestDegradation:
    def test_no_material_skips_without_calling_the_llm(self):
        agent, client = _agent([{"seeds": [_seed()]}])
        result = agent.run(_payload(untested_assumptions=[], author_choices=[]))

        assert client.calls == 0
        assert result.seeds == []
        assert result.skipped_reason == SKIPPED_REASON_NO_MATERIAL
        assert result.llm_call_count == 0
        # P4: 理由が残る
        assert result.review_notes

    def test_thesis_only_document_does_not_generate(self):
        """thesis だけでは火種を作らない（根拠の無い創作を防ぐ・設計書 §4.1）。"""
        agent, client = _agent([{"seeds": [_seed()]}])
        result = agent.run(_payload(untested_assumptions=[], author_choices=[]))
        assert client.calls == 0
        assert result.skipped_reason == SKIPPED_REASON_NO_MATERIAL

    def test_llm_exception_is_reported_not_raised(self):
        agent, _client = _agent([RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")])
        result = agent.run(_payload())
        assert result.seeds == []
        # JSON パース不能扱い → 修復上限まで再試行 → repair_failed
        assert result.skipped_reason in (
            SKIPPED_REASON_REPAIR_FAILED,
            SKIPPED_REASON_LLM_CALL_FAILED,
        )


class TestGenerationAndRepair:
    def test_single_call_success(self):
        agent, client = _agent([{"seeds": [_seed(), _seed(body="別の扱いなら何が見えなくなると思いますか？")]}])
        result = agent.run(_payload())

        assert client.calls == 1
        assert result.llm_call_count == 1
        assert len(result.seeds) == 2
        assert result.skipped_reason is None

    def test_invalid_first_attempt_is_repaired(self):
        bad = {"seeds": [_seed(body="この前提はまだ確かめられていません。")]}  # 疑問文でない
        good = {"seeds": [_seed()]}
        agent, client = _agent([bad, good])

        result = agent.run(_payload())

        assert client.calls == 2
        assert result.llm_call_count == 2
        assert len(result.seeds) == 1
        assert result.skipped_reason is None
        # 修復プロンプトには検証エラーが載る
        assert "seed_not_a_question" in client.contents[1]

    def test_repair_exhaustion_generates_nothing(self):
        bad = {"seeds": [_seed(evidence_quote="この論文は間違っている")]}
        agent, client = _agent([bad, bad, bad, bad])

        result = agent.run(_payload())

        # 1 initial + 2 repair attempts（core/llm_worker の共通上限）
        assert client.calls == 3
        assert result.seeds == []
        assert result.skipped_reason == SKIPPED_REASON_REPAIR_FAILED
        assert result.validation_issues


class TestLimitsAndLanguage:
    def test_seed_cap_truncates_and_records_it_honestly(self):
        seeds = [_seed(body=f"問い{i}はどう考えますか？") for i in range(5)]
        agent, _client = _agent([{"seeds": seeds}])

        result = agent.run(_payload(max_seeds=2))

        assert len(result.seeds) == 2
        assert result.truncated is True
        assert result.truncated_count == 3
        assert result.review_notes

    def test_config_max_seeds_overrides_payload(self):
        seeds = [_seed(body=f"問い{i}はどう考えますか？") for i in range(3)]
        agent, _client = _agent([{"seeds": seeds}])

        result = agent.run(_payload(max_seeds=4), config={"max_seeds": 1})

        assert len(result.seeds) == 1
        assert result.truncated_count == 2

    def test_language_is_carried_into_prompt_and_result(self):
        agent, client = _agent([
            {"seeds": [_seed(body="What changes if the assumption fails?")]}
        ])

        result = agent.run(_payload(language="en"))

        assert result.language == "en"
        assert "English" in client.contents[0]
        assert len(result.seeds) == 1

    def test_prompt_contains_material_but_no_opaque_ids(self):
        agent, client = _agent([{"seeds": [_seed()]}])
        agent.run(_payload())

        content = client.contents[0]
        assert _ASSUMPTION in content
        assert "linearize" in content
        # 解決済みテキストのみ（equation id / claim id は入力に含めない）
        assert "eq_" not in content
