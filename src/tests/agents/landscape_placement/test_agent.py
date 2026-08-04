"""LandscapePlacementAgent の run() 経路（縮退・修復・上限・プロンプト内容）。

修復ループは ``core/llm_worker/repair.run_with_repair`` を実地で通す
（conftest.py が backend/ を sys.path に載せる）。LLM 呼び出しは client の
``complete_json`` を差し替えて遮断する。
"""
from __future__ import annotations

from episteme_graph.agents.landscape_placement.agent import LandscapePlacementAgent
from episteme_graph.agents.landscape_placement.schema import (
    LandscapePlacementResult,
    SKIPPED_REASON_LLM_CALL_FAILED,
    SKIPPED_REASON_NO_DOMAINS,
    SKIPPED_REASON_NO_MATERIAL,
    SKIPPED_REASON_REPAIR_FAILED,
)

_THESIS = "A single consistency relation links the temperature and polarization amplitudes."
_CLAIM = "A quadratic estimator is applied to the foreground-cleaned temperature maps."

_DOMAINS = [
    {
        "domain_key": "astrophysics",
        "domain_name": "宇宙物理",
        "nodes": [
            {"node_id": "cosmology", "label": "宇宙論・大規模構造", "kind": "region", "region_id": ""},
            {"node_id": "cmb", "label": "宇宙背景放射", "kind": "concept", "region_id": "cosmology"},
            {
                "node_id": "observation_methods",
                "label": "観測・装置・データ解析",
                "kind": "region",
                "region_id": "",
            },
        ],
    }
]


def _payload(**kwargs) -> dict:
    payload = {
        "document_id": "doc-1",
        "cartridge_id": "astrophysics",
        "paper_title": "A consistency relation for the lensing amplitude",
        "central_question": "Can the two measured amplitudes be described by a single relation?",
        "central_thesis": _THESIS,
        "paper_goal": "Establish a consistency relation between the two amplitudes.",
        "headline_claim": "The two amplitudes agree once the same beam model is used.",
        "claim_summaries": [{"claim_id": "c_1", "text": _CLAIM}],
        "domains": _DOMAINS,
        "max_placements": 8,
    }
    payload.update(kwargs)
    return payload


def _placement(**kwargs) -> dict:
    entry = {
        "domain_key": "astrophysics",
        "node_id": "cmb",
        "perspective": "subject",
        "weight": 0.8,
        "reason": "論文の対象は宇宙背景放射から再構成した振幅である。",
        "evidence_quote": _THESIS,
        "claim_id": None,
        "confidence": 0.7,
    }
    entry.update(kwargs)
    return entry


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


def _agent(responses) -> tuple[LandscapePlacementAgent, _FakeClient]:
    agent = LandscapePlacementAgent()
    client = _FakeClient(responses)
    agent._llm_client = client  # noqa: SLF001 — test seam, same as other agents
    return agent, client


class TestDegradation:
    def test_no_domain_skips_without_calling_the_llm(self):
        agent, client = _agent([{"placements": [_placement()]}])
        result = agent.run(_payload(domains=[]))

        assert client.calls == 0
        assert result.placements == []
        assert result.skipped_reason == SKIPPED_REASON_NO_DOMAINS
        assert result.llm_call_count == 0
        assert result.review_notes  # P4: 理由が残る

    def test_domain_without_nodes_counts_as_no_domain(self):
        agent, client = _agent([{"placements": [_placement()]}])
        result = agent.run(
            _payload(domains=[{"domain_key": "astrophysics", "nodes": []}])
        )
        assert client.calls == 0
        assert result.skipped_reason == SKIPPED_REASON_NO_DOMAINS

    def test_no_material_skips_without_calling_the_llm(self):
        agent, client = _agent([{"placements": [_placement()]}])
        result = agent.run(
            _payload(
                central_question="",
                central_thesis="",
                paper_goal="",
                headline_claim="",
                claim_summaries=[],
            )
        )

        assert client.calls == 0
        assert result.skipped_reason == SKIPPED_REASON_NO_MATERIAL
        assert result.review_notes

    def test_title_only_document_does_not_generate(self):
        """タイトルだけでは配置しない（根拠の無い配置を防ぐ・設計書 §7.3）。"""
        agent, client = _agent([{"placements": [_placement()]}])
        result = agent.run(
            _payload(
                central_question="",
                central_thesis="",
                paper_goal="",
                headline_claim="",
                claim_summaries=[],
                paper_title="Some paper about galaxies",
            )
        )
        assert client.calls == 0
        assert result.skipped_reason == SKIPPED_REASON_NO_MATERIAL

    def test_llm_exception_is_reported_not_raised(self):
        agent, _client = _agent(
            [RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")]
        )
        result = agent.run(_payload())

        assert result.placements == []
        assert result.skipped_reason in (
            SKIPPED_REASON_REPAIR_FAILED,
            SKIPPED_REASON_LLM_CALL_FAILED,
        )

    def test_unusable_input_never_raises(self):
        agent, client = _agent([{"placements": [_placement()]}])
        result = agent.run("not a payload")  # type: ignore[arg-type]
        assert isinstance(result, LandscapePlacementResult)
        assert result.placements == []
        assert client.calls == 0


class TestGenerationAndRepair:
    def test_single_call_success(self):
        agent, client = _agent(
            [
                {
                    "placements": [
                        _placement(),
                        _placement(node_id="cosmology", perspective="question", weight=0.6),
                    ]
                }
            ]
        )
        result = agent.run(_payload())

        assert client.calls == 1
        assert result.llm_call_count == 1
        assert len(result.placements) == 2
        assert result.skipped_reason is None
        assert result.cartridge_id == "astrophysics"

    def test_invalid_first_attempt_is_repaired(self):
        bad = {"placements": [_placement(node_id="black_holes")]}  # 実在しないノード
        good = {"placements": [_placement()]}
        agent, client = _agent([bad, good])

        result = agent.run(_payload())

        assert client.calls == 2
        assert result.llm_call_count == 2
        assert len(result.placements) == 1
        assert result.skipped_reason is None
        assert "placement_unknown_node" in client.contents[1]

    def test_repair_exhaustion_places_nothing(self):
        bad = {"placements": [_placement(evidence_quote="この論文は宇宙論の論文である")]}
        agent, client = _agent([bad, bad, bad, bad])

        result = agent.run(_payload())

        # 1 initial + 2 repair attempts（core/llm_worker の共通上限）
        assert client.calls == 3
        assert result.placements == []
        assert result.skipped_reason == SKIPPED_REASON_REPAIR_FAILED
        assert result.validation_issues

    def test_declared_unplaced_domains_survive_without_placements(self):
        agent, client = _agent(
            [
                {
                    "placements": [],
                    "unplaced_domains": [
                        {"domain_key": "astrophysics", "reason": "対象が天体現象ではない。"}
                    ],
                }
            ]
        )
        result = agent.run(_payload())

        assert client.calls == 1
        assert result.placements == []
        assert result.skipped_reason is None
        assert [u.domain_key for u in result.unplaced_domains] == ["astrophysics"]


class TestLimits:
    def test_placement_cap_truncates_and_records_it_honestly(self):
        placements = [
            _placement(
                node_id=["cmb", "cosmology", "observation_methods"][index % 3],
                perspective=["subject", "question", "method", "theory", "observation"][index],
                weight=round(0.1 * (index + 1), 2),
            )
            for index in range(5)
        ]
        agent, _client = _agent([{"placements": placements}])

        result = agent.run(_payload(max_placements=2))

        assert len(result.placements) == 2
        assert result.truncated is True
        assert result.truncated_count == 3
        assert result.review_notes

    def test_config_max_placements_overrides_payload(self):
        placements = [
            _placement(perspective=p, weight=w)
            for p, w in (("subject", 0.9), ("question", 0.5), ("method", 0.2))
        ]
        agent, _client = _agent([{"placements": placements}])

        result = agent.run(_payload(max_placements=8), config={"max_placements": 1})

        assert len(result.placements) == 1
        assert result.placements[0].perspective == "subject"  # 最大 weight が残る
        assert result.truncated is True
        assert result.truncated_count == 2

    def test_cap_is_idempotent_when_validator_already_applied_it(self):
        placements = [
            _placement(perspective=p, weight=w)
            for p, w in (("subject", 0.9), ("question", 0.5), ("method", 0.2))
        ]
        agent, _client = _agent([{"placements": placements}])

        result = agent.run(_payload(max_placements=2), config={"max_placements": 2})

        assert len(result.placements) == 2
        assert result.truncated_count == 1


class TestPrompt:
    def test_prompt_contains_material_and_node_inventory(self):
        agent, client = _agent([{"placements": [_placement()]}])
        agent.run(_payload())

        content = client.contents[0]
        assert _THESIS in content
        assert _CLAIM in content
        assert "cmb" in content
        assert "observation_methods" in content
        # 観点語彙が明示されている
        for perspective in ("subject", "question", "method", "theory", "observation", "application"):
            assert perspective in content

    def test_prompt_asks_for_multi_domain_and_forbids_forcing(self):
        agent, client = _agent([{"placements": [_placement()]}])
        agent.run(_payload())

        content = client.contents[0]
        assert "複数の領域" in content
        assert "unplaced_domains" in content
        assert "逐語引用" in content

    def test_prompt_is_one_user_message_string(self):
        """開発ルール4: user ロール1本（client の interface は content 文字列1本のみ）。"""
        agent, client = _agent([{"placements": [_placement()]}])
        agent.run(_payload())
        assert len(client.contents) == 1
        assert isinstance(client.contents[0], str)

    def test_missing_cartridge_does_not_break_the_run(self):
        agent, client = _agent([{"placements": [_placement()]}])
        result = agent.run(_payload(), cartridge_id="no_such_cartridge")
        assert client.calls == 1
        assert len(result.placements) == 1
