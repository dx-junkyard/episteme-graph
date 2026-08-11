"""カテゴリギャップ候補（``docs/features/category_gap_candidates_design.md`` §5.1）。

最重要の契約は **soft collector**: 候補の不備は ``warnings`` にしか出さず、
``errors`` には一度も積まない。積むと validator の ``if errors: return None`` →
repair 2回失敗 → **placements が全滅**する（設計書 §1-6）。ここではそれを
「1件だけ壊れた候補を混ぜても placements が無傷で残る」形で固定し、加えて
``_collect_category_gaps`` のソースに ``errors.append`` が無いことを構造検査する。
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

from episteme_graph.agents.landscape_placement.agent import LandscapePlacementAgent
from episteme_graph.agents.landscape_placement.input_builder import (
    LandscapePlacementInputBuilder,
)
from episteme_graph.agents.landscape_placement.prompt import (
    LandscapePlacementPromptFactory,
)
from episteme_graph.agents.landscape_placement.schema import (
    CategoryGapRecord,
    ClaimSummary,
    DEFAULT_MAX_GAPS_PER_DOCUMENT,
    DEFAULT_MAX_PLACEMENTS,
    DomainOption,
    GAP_LAYERS,
    LandscapePlacementInput,
    LandscapePlacementResult,
    MAX_CONCEPTS_PER_REGION,
    MAX_EVIDENCE_QUOTE_CHARS,
    MAX_PROPOSED_LABEL_CHARS,
    SkeletonNodeOption,
)
from episteme_graph.agents.landscape_placement.validator import (
    LandscapePlacementValidator,
)

VALIDATOR = LandscapePlacementValidator()
BUILDER = LandscapePlacementInputBuilder()
PROMPT = LandscapePlacementPromptFactory()

_THESIS = "A single consistency relation links the temperature and polarization amplitudes."
_QUESTION = "Can the two measured amplitudes be described by a single relation?"
_CLAIM_1 = "A quadratic estimator is applied to the foreground-cleaned temperature maps."
_CLAIM_2 = "The inferred amplitude constrains the sum of the neutrino masses."

_EXAMPLE_PATH = (
    Path(__file__).resolve().parents[3]
    / "episteme_graph"
    / "agents"
    / "landscape_placement"
    / "examples"
    / "landscape_placement_example.json"
)

# 必須リテラル（backend 側のガードレールが grep する契約文言。設計書 §5.1 / §5.7⑨）。
REQUIRED_PROMPT_LITERALS = (
    "既存の概念の言い換えを新しい概念として申告しないでください",
    "配置（placements）の判定を最優先",
)


def _domains() -> list[DomainOption]:
    return [
        DomainOption(
            domain_key="astrophysics",
            domain_name="宇宙物理",
            nodes=[
                SkeletonNodeOption("cosmology", "宇宙論・大規模構造", "region", ""),
                SkeletonNodeOption("cmb", "宇宙背景放射", "concept", "cosmology"),
                SkeletonNodeOption(
                    "observation_methods", "観測・装置・データ解析", "region", ""
                ),
                SkeletonNodeOption(
                    "statistical_inference", "統計推論", "concept", "observation_methods"
                ),
            ],
        ),
        DomainOption(
            domain_key="particle_physics",
            domain_name="素粒子物理",
            nodes=[SkeletonNodeOption("detector", "検出器", "region", "")],
        ),
    ]


def _input(**kwargs) -> LandscapePlacementInput:
    defaults = dict(
        document_id="doc-1",
        cartridge_id="astrophysics",
        paper_title="A consistency relation for the lensing amplitude",
        central_question=_QUESTION,
        central_thesis=_THESIS,
        claim_summaries=[ClaimSummary("c_1", _CLAIM_1), ClaimSummary("c_2", _CLAIM_2)],
        domains=_domains(),
        max_placements=DEFAULT_MAX_PLACEMENTS,
    )
    defaults.update(kwargs)
    return LandscapePlacementInput(**defaults)


def _placement(**kwargs) -> dict:
    entry = dict(
        domain_key="astrophysics",
        node_id="cmb",
        perspective="subject",
        weight=0.8,
        reason="論文の対象は宇宙背景放射から再構成した振幅である。",
        evidence_quote=_THESIS,
        claim_id=None,
        confidence=0.7,
    )
    entry.update(kwargs)
    return entry


def _gap(**kwargs) -> dict:
    entry = dict(
        layer="concept",
        domain_key="astrophysics",
        parent_region_id="cosmology",
        proposed_label="重力レンズ再構成",
        reason="論文が扱うレンズ効果の再構成に対応する概念が、この領域には並んでいない。",
        evidence_quote=_CLAIM_1,
        confidence=0.5,
    )
    entry.update(kwargs)
    return entry


def _validate(raw: dict, item: LandscapePlacementInput | None = None):
    return VALIDATOR.validate(raw, item or _input(), cartridge_id="astrophysics")


def _raw(*gaps, placements=None, **extra) -> dict:
    payload: dict = {"placements": list(placements or [_placement()])}
    if gaps:
        payload["category_gaps"] = list(gaps)
    payload.update(extra)
    return payload


def _warn_ids(warnings: list[str]) -> set[str]:
    return {message.split(":", 1)[0] for message in warnings}


# ---------------------------------------------------------------------------
# (a) プロンプト: 閉世界の提示 + 必須リテラル
# ---------------------------------------------------------------------------


class TestPromptClosedWorld:
    def test_skeleton_is_presented_as_regions_with_nested_concepts(self):
        prepared = BUILDER.prepare_for_prompt(_input())
        astro = prepared["domains"][0]

        assert astro["domain_key"] == "astrophysics"
        assert "nodes" not in astro  # フラット列挙は廃止
        assert [r["node_id"] for r in astro["regions"]] == [
            "cosmology",
            "observation_methods",
        ]
        assert [c["node_id"] for c in astro["regions"][0]["concepts"]] == ["cmb"]
        assert [c["node_id"] for c in astro["regions"][1]["concepts"]] == [
            "statistical_inference"
        ]

    def test_concept_slots_remaining_is_deterministic(self):
        prepared = BUILDER.prepare_for_prompt(_input())
        regions = prepared["domains"][0]["regions"]
        assert regions[0]["concept_slots_remaining"] == MAX_CONCEPTS_PER_REGION - 1
        assert regions[1]["concept_slots_remaining"] == MAX_CONCEPTS_PER_REGION - 1

    def test_full_region_reports_zero_slots(self):
        item = _input(
            domains=[
                DomainOption(
                    domain_key="astrophysics",
                    nodes=[SkeletonNodeOption("cosmology", "宇宙論", "region", "")]
                    + [
                        SkeletonNodeOption(f"c_{i}", f"概念{i}", "concept", "cosmology")
                        for i in range(MAX_CONCEPTS_PER_REGION + 2)
                    ],
                )
            ]
        )
        region = BUILDER.prepare_for_prompt(item)["domains"][0]["regions"][0]
        assert region["concept_slots_remaining"] == 0
        assert len(region["concepts"]) == MAX_CONCEPTS_PER_REGION + 2  # 情報は落とさない

    def test_concepts_without_a_presented_parent_are_kept(self):
        """親領域が提示されていない概念も落とさない（P4）。"""
        item = _input(
            domains=[
                DomainOption(
                    domain_key="astrophysics",
                    nodes=[
                        SkeletonNodeOption("cmb", "宇宙背景放射", "concept", "missing"),
                    ],
                )
            ]
        )
        prepared = BUILDER.prepare_for_prompt(item)["domains"][0]
        assert prepared["regions"] == []
        assert [c["node_id"] for c in prepared["other_concepts"]] == ["cmb"]

    def test_prompt_states_the_closed_world_and_the_slot_field(self):
        content = PROMPT.build_content(_input())
        assert "concept_slots_remaining" in content
        assert "そこに並んでいるものだけです" in content
        assert '"regions"' in content  # ネストした骨格が実際に載っている
        assert "category_gaps" in content

    def test_prompt_contains_the_required_literals(self):
        content = PROMPT.build_content(_input())
        for literal in REQUIRED_PROMPT_LITERALS:
            assert literal in content, literal

    def test_required_literals_survive_a_disabled_gap_budget(self):
        """上限0でも「配置最優先」「言い換え禁止」の文言は落とさない。"""
        content = PROMPT.build_content(_input(max_gaps_per_document=0))
        for literal in REQUIRED_PROMPT_LITERALS:
            assert literal in content, literal
        assert "空配列" in content

    def test_prompt_announces_the_candidate_cap(self):
        content = PROMPT.build_content(_input(max_gaps_per_document=3))
        assert "最大 3 件" in content
        for layer in GAP_LAYERS:
            assert layer in content


# ---------------------------------------------------------------------------
# (b) soft collector: errors を積まない
# ---------------------------------------------------------------------------


class TestSoftCollector:
    def test_valid_gap_is_collected_without_errors(self):
        result, errors, _warnings = _validate(_raw(_gap()))

        assert errors == []
        assert [g.proposed_label for g in result.category_gaps] == ["重力レンズ再構成"]
        assert result.category_gaps[0].layer == "concept"
        assert result.category_gaps[0].parent_region_id == "cosmology"

    def test_every_malformed_gap_only_warns(self):
        broken = [
            "not an object",
            _gap(layer="galaxy"),
            _gap(domain_key="chemistry"),
            _gap(proposed_label=""),
            _gap(proposed_label="あ" * (MAX_PROPOSED_LABEL_CHARS + 1)),
            _gap(parent_region_id=""),
            _gap(reason=""),
            _gap(evidence_quote=""),
            _gap(evidence_quote="A"),
            _gap(evidence_quote="この論文は宇宙論の論文である"),
        ]
        result, errors, warnings = _validate(_raw(*broken))

        assert errors == []  # ← 最重要（placements を巻き込まない）
        assert result is not None
        assert result.category_gaps == []
        assert len(result.placements) == 1
        assert warnings

    def test_non_list_category_gaps_is_ignored_with_a_warning(self):
        result, errors, warnings = _validate(_raw(**{"category_gaps": "none"}))
        assert errors == []
        assert result.category_gaps == []
        assert "category_gaps_not_list" in _warn_ids(warnings)

    def test_absent_section_is_not_warned(self):
        result, errors, warnings = _validate(_raw())
        assert errors == []
        assert result.category_gaps == []
        assert not any(w.startswith("category_gap") for w in warnings)

    def test_gap_warnings_are_recorded_as_warning_issues_only(self):
        result, _errors, _warnings = _validate(_raw(_gap(layer="galaxy")))
        gap_issues = [
            issue
            for issue in result.validation_issues
            if issue.rule_id.startswith("category_gap")
        ]
        assert gap_issues
        assert {issue.severity for issue in gap_issues} == {"warning"}

    def test_collector_source_never_appends_to_errors(self):
        """構造検査: soft collector に hard error 経路が生えないよう固定する。"""
        source = inspect.getsource(
            LandscapePlacementValidator._collect_category_gaps  # noqa: SLF001
        )
        assert "errors.append" not in source
        assert "error(" not in source.replace("hard error", "")

    def test_collector_signature_has_no_error_hook(self):
        params = list(
            inspect.signature(
                LandscapePlacementValidator._collect_category_gaps  # noqa: SLF001
            ).parameters
        )
        assert "warn" in params
        assert "error" not in params


# ---------------------------------------------------------------------------
# (c) verbatim 不一致は個別 drop（placements は無傷）
# ---------------------------------------------------------------------------


class TestVerbatimGuard:
    def test_non_verbatim_gap_is_dropped_alone(self):
        result, errors, warnings = _validate(
            _raw(
                _gap(evidence_quote="この地図には重力レンズの概念がない"),
                _gap(proposed_label="ニュートリノ質量", evidence_quote=_CLAIM_2),
                placements=[
                    _placement(),
                    _placement(node_id="cosmology", perspective="question", weight=0.6),
                ],
            )
        )

        assert errors == []
        assert len(result.placements) == 2  # 配置は無傷
        assert [g.proposed_label for g in result.category_gaps] == ["ニュートリノ質量"]
        assert "category_gap_evidence_quote_not_verbatim" in _warn_ids(warnings)

    def test_whitespace_only_differences_are_tolerated(self):
        noisy = _CLAIM_1.replace(" ", "\n   ", 1)
        result, errors, _warnings = _validate(_raw(_gap(evidence_quote=noisy)))
        assert errors == []
        assert len(result.category_gaps) == 1

    def test_over_long_quote_is_dropped(self):
        quote = _THESIS + " " + ("x" * MAX_EVIDENCE_QUOTE_CHARS)
        item = _input(central_thesis=quote)
        result, errors, warnings = _validate(_raw(_gap(evidence_quote=quote)), item)
        assert errors == []
        assert result.category_gaps == []
        assert "category_gap_evidence_quote_too_long" in _warn_ids(warnings)

    def test_paper_title_is_not_a_valid_gap_quote_source(self):
        result, _errors, warnings = _validate(
            _raw(
                _gap(
                    evidence_quote="A consistency relation for the lensing amplitude"
                )
            )
        )
        assert result.category_gaps == []
        assert "category_gap_evidence_quote_not_verbatim" in _warn_ids(warnings)


# ---------------------------------------------------------------------------
# (d) 上限
# ---------------------------------------------------------------------------


class TestCap:
    def _many(self, count: int) -> list[dict]:
        return [
            _gap(proposed_label=f"候補{index}", confidence=0.4)
            for index in range(count)
        ]

    def test_default_cap_is_three(self):
        assert DEFAULT_MAX_GAPS_PER_DOCUMENT == 3
        assert _input().max_gaps_per_document == 3

    def test_over_cap_candidates_are_dropped_in_input_order(self):
        result, errors, warnings = _validate(_raw(*self._many(5)))

        assert errors == []
        assert [g.proposed_label for g in result.category_gaps] == [
            "候補0",
            "候補1",
            "候補2",
        ]
        assert "category_gaps_over_cap" in _warn_ids(warnings)

    def test_cap_zero_keeps_no_candidate(self):
        item = _input(max_gaps_per_document=0)
        result, errors, warnings = _validate(_raw(_gap()), item)
        assert errors == []
        assert result.category_gaps == []
        assert "category_gaps_over_cap" in _warn_ids(warnings)

    def test_duplicate_candidates_are_collapsed(self):
        result, errors, warnings = _validate(
            _raw(_gap(reason="一つ目の理由。"), _gap(reason="二つ目の理由。"))
        )
        assert errors == []
        assert len(result.category_gaps) == 1
        assert result.category_gaps[0].reason == "一つ目の理由。"
        assert "category_gap_duplicate" in _warn_ids(warnings)

    def test_agent_config_can_tighten_the_cap(self):
        agent = LandscapePlacementAgent()
        result = LandscapePlacementResult(
            document_id="doc-1",
            category_gaps=[
                CategoryGapRecord(
                    layer="concept",
                    domain_key="astrophysics",
                    parent_region_id="cosmology",
                    proposed_label=f"候補{index}",
                    reason="理由。",
                    evidence_quote=_CLAIM_1,
                )
                for index in range(3)
            ],
        )
        tightened = agent._apply_gap_cap(result, 1)  # noqa: SLF001 — test seam

        assert [g.proposed_label for g in tightened.category_gaps] == ["候補0"]
        assert tightened.review_notes  # 切ったことは正直に記録する
        assert tightened.truncated is False  # 配置の切り詰めとは混ぜない

    def test_agent_resolves_the_cap_from_input_and_config(self):
        item = _input(max_gaps_per_document=2)
        assert LandscapePlacementAgent._resolve_max_gaps(item, None) == 2  # noqa: SLF001
        assert (
            LandscapePlacementAgent._resolve_max_gaps(  # noqa: SLF001
                item, {"max_gaps_per_document": 0}
            )
            == 0
        )
        assert (
            LandscapePlacementAgent._resolve_max_gaps(  # noqa: SLF001
                item, {"max_gaps_per_document": "nope"}
            )
            == 2
        )


# ---------------------------------------------------------------------------
# (e) 層と親領域
# ---------------------------------------------------------------------------


class TestLayerAndParent:
    def test_concept_without_parent_region_is_dropped(self):
        result, errors, warnings = _validate(_raw(_gap(parent_region_id="")))
        assert errors == []
        assert result.category_gaps == []
        assert "category_gap_parent_missing" in _warn_ids(warnings)

    def test_unknown_parent_region_is_only_warned(self):
        """実在検査は warning（設計書 §5.1）— 教員が親を選び直せる。"""
        result, errors, warnings = _validate(_raw(_gap(parent_region_id="black_holes")))
        assert errors == []
        assert len(result.category_gaps) == 1
        assert result.category_gaps[0].parent_region_id == "black_holes"
        assert "category_gap_unknown_parent" in _warn_ids(warnings)

    def test_concept_parent_must_be_a_region_not_a_concept(self):
        result, _errors, warnings = _validate(_raw(_gap(parent_region_id="cmb")))
        assert len(result.category_gaps) == 1
        assert "category_gap_unknown_parent" in _warn_ids(warnings)

    def test_region_candidate_keeps_an_empty_parent(self):
        result, errors, _warnings = _validate(
            _raw(_gap(layer="region", parent_region_id="", proposed_label="重力波天文学"))
        )
        assert errors == []
        assert result.category_gaps[0].layer == "region"
        assert result.category_gaps[0].parent_region_id == ""

    def test_region_candidate_with_a_parent_is_normalized(self):
        result, errors, warnings = _validate(
            _raw(
                _gap(
                    layer="region",
                    parent_region_id="cosmology",
                    proposed_label="重力波天文学",
                )
            )
        )
        assert errors == []
        assert result.category_gaps[0].parent_region_id == ""
        assert "category_gap_parent_ignored" in _warn_ids(warnings)

    def test_unknown_layer_is_dropped(self):
        result, _errors, warnings = _validate(_raw(_gap(layer="galaxy")))
        assert result.category_gaps == []
        assert "category_gap_unknown_layer" in _warn_ids(warnings)

    def test_domain_must_have_been_offered(self):
        result, _errors, warnings = _validate(_raw(_gap(domain_key="chemistry")))
        assert result.category_gaps == []
        assert "category_gap_unknown_domain" in _warn_ids(warnings)


# ---------------------------------------------------------------------------
# (f) 言い換え申告の捏造ガード
# ---------------------------------------------------------------------------


class TestRewordingGuard:
    def test_existing_concept_label_is_dropped(self):
        result, errors, warnings = _validate(_raw(_gap(proposed_label="宇宙背景放射")))
        assert errors == []
        assert result.category_gaps == []
        assert "category_gap_duplicates_existing_label" in _warn_ids(warnings)

    def test_existing_region_label_is_dropped(self):
        result, _errors, warnings = _validate(
            _raw(_gap(layer="region", parent_region_id="", proposed_label="宇宙論・大規模構造"))
        )
        assert result.category_gaps == []
        assert "category_gap_duplicates_existing_label" in _warn_ids(warnings)

    def test_normalization_ignores_spacing_and_middle_dots(self):
        result, _errors, warnings = _validate(
            _raw(_gap(proposed_label="宇宙論 大規模 構造"))
        )
        assert result.category_gaps == []
        assert "category_gap_duplicates_existing_label" in _warn_ids(warnings)

    def test_existing_node_id_is_also_matched(self):
        result, _errors, warnings = _validate(_raw(_gap(proposed_label="CMB")))
        assert result.category_gaps == []
        assert "category_gap_duplicates_existing_label" in _warn_ids(warnings)

    def test_a_label_from_another_domain_is_not_a_duplicate(self):
        """他ドメインの語彙は当該ドメインの重複ではない。"""
        result, errors, _warnings = _validate(_raw(_gap(proposed_label="検出器")))
        assert errors == []
        assert len(result.category_gaps) == 1


# ---------------------------------------------------------------------------
# run() の一周（配置と候補が同じ1コールから出る）
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.contents: list[str] = []

    def complete_json(self, content: str) -> dict:
        self.contents.append(content)
        self.calls += 1
        return self._responses.pop(0) if self._responses else {}


def _run(payload_extra: dict, responses, config=None):
    agent = LandscapePlacementAgent()
    client = _FakeClient(responses)
    agent._llm_client = client  # noqa: SLF001 — test seam, same as test_agent.py
    payload = _input().to_dict()
    payload.update(payload_extra)
    return agent.run(payload, config=config), client


class TestAgentRun:
    def test_candidates_ride_on_the_same_single_call(self):
        result, client = _run({}, [_raw(_gap())])

        assert client.calls == 1  # 追加 LLM コールなし（設計書 §3-2）
        assert len(result.placements) == 1
        assert [g.proposed_label for g in result.category_gaps] == ["重力レンズ再構成"]

    def test_a_broken_candidate_never_costs_a_placement(self):
        result, client = _run(
            {}, [_raw(_gap(evidence_quote="素材に無い文字列"), _gap(layer="galaxy"))]
        )

        assert client.calls == 1  # repair に入っていない
        assert len(result.placements) == 1
        assert result.category_gaps == []
        assert result.skipped_reason is None

    def test_config_zero_disables_candidates_end_to_end(self):
        result, _client = _run({}, [_raw(_gap())], config={"max_gaps_per_document": 0})
        assert len(result.placements) == 1
        assert result.category_gaps == []

    def test_skipped_run_has_no_candidates(self):
        result, client = _run({"domains": []}, [_raw(_gap())])
        assert client.calls == 0
        assert result.category_gaps == []


# ---------------------------------------------------------------------------
# (g)(h) examples / シリアライズ契約
# ---------------------------------------------------------------------------


class TestExampleAndSerialization:
    def _example(self) -> dict:
        return json.loads(_EXAMPLE_PATH.read_text(encoding="utf-8"))

    def test_example_output_with_gaps_passes_validation(self):
        example = self._example()
        item = LandscapePlacementInput.from_dict(example["input"])

        result, errors, _warnings = VALIDATOR.validate(
            example["output"], item, cartridge_id=item.cartridge_id
        )

        assert errors == []
        assert len(result.category_gaps) == len(example["output"]["category_gaps"])
        assert result.category_gaps[0].layer in GAP_LAYERS
        assert result.category_gaps[0].parent_region_id == "cosmology"

    def test_example_input_carries_the_gap_cap(self):
        item = LandscapePlacementInput.from_dict(self._example()["input"])
        assert item.max_gaps_per_document == 3
        assert item.to_dict()["max_gaps_per_document"] == 3

    def test_result_serialization_includes_category_gaps(self):
        result, _errors, _warnings = _validate(_raw(_gap()))

        payload = result.to_dict()
        assert payload["category_gaps"] == [
            {
                "layer": "concept",
                "domain_key": "astrophysics",
                "parent_region_id": "cosmology",
                "proposed_label": "重力レンズ再構成",
                "reason": "論文が扱うレンズ効果の再構成に対応する概念が、この領域には並んでいない。",
                "evidence_quote": _CLAIM_1,
                "confidence": 0.5,
            }
        ]
        # JSON シリアライズ可能・round trip が安定（backend が読む側）
        again = LandscapePlacementResult.from_dict(json.loads(result.to_json()))
        assert again.to_dict() == payload

    def test_backend_reads_gaps_via_getattr_default(self):
        """backend 側は ``getattr(result, "category_gaps", [])`` で読む契約。"""
        empty = LandscapePlacementResult(document_id="doc-1")
        assert getattr(empty, "category_gaps", None) == []

    def test_skipped_results_carry_no_candidate(self):
        skipped = LandscapePlacementResult.make_skipped(
            _input(), "no_source_material", "no material"
        )
        assert skipped.category_gaps == []
        assert skipped.to_dict()["category_gaps"] == []
