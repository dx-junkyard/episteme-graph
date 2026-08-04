"""LandscapePlacementValidator のハードルールと warning（設計書 §7.3）。"""
from __future__ import annotations

import pytest

from episteme_graph.agents.landscape_placement.schema import (
    ClaimSummary,
    DEFAULT_MAX_PLACEMENTS,
    DomainOption,
    LandscapePlacementInput,
    MAX_EVIDENCE_QUOTE_CHARS,
    MAX_REASON_CHARS,
    PERSPECTIVES,
    SkeletonNodeOption,
)
from episteme_graph.agents.landscape_placement.validator import (
    LandscapePlacementValidator,
)

VALIDATOR = LandscapePlacementValidator()

_THESIS = "A single consistency relation links the temperature and polarization amplitudes."
_GOAL = "Establish a consistency relation between the two measured amplitudes."
_QUESTION = "Can the two measured amplitudes be described by a single relation?"
_HEADLINE = "The two amplitudes agree once the same beam model is used."
_CLAIM_1 = "A quadratic estimator is applied to the foreground-cleaned temperature maps."
_CLAIM_2 = "The inferred amplitude constrains the sum of the neutrino masses."


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
        paper_goal=_GOAL,
        headline_claim=_HEADLINE,
        claim_summaries=[
            ClaimSummary("c_1", _CLAIM_1),
            ClaimSummary("c_2", _CLAIM_2),
        ],
        domains=_domains(),
        max_placements=DEFAULT_MAX_PLACEMENTS,
    )
    defaults.update(kwargs)
    return LandscapePlacementInput(**defaults)


def _placement(**kwargs) -> dict:
    defaults = dict(
        domain_key="astrophysics",
        node_id="cmb",
        perspective="subject",
        weight=0.8,
        reason="論文の対象は宇宙背景放射から再構成した振幅である。",
        evidence_quote=_THESIS,
        claim_id=None,
        confidence=0.7,
    )
    defaults.update(kwargs)
    return defaults


def _raw(*placements, **extra) -> dict:
    payload = {"placements": list(placements) or [_placement()]}
    payload.update(extra)
    return payload


def _validate(raw: dict, item: LandscapePlacementInput | None = None):
    return VALIDATOR.validate(raw, item or _input(), cartridge_id="astrophysics")


def _error_ids(errors: list[str]) -> set[str]:
    return {message.split(":", 1)[0] for message in errors}


# ---------------------------------------------------------------------------
# 1. happy path
# ---------------------------------------------------------------------------


class TestAccepted:
    def test_multi_domain_multi_perspective_is_the_default(self):
        raw = _raw(
            _placement(),
            _placement(node_id="cosmology", perspective="question", evidence_quote=_QUESTION),
            _placement(
                node_id="observation_methods",
                perspective="method",
                evidence_quote=_CLAIM_1,
                claim_id="c_1",
                weight=0.5,
            ),
        )
        result, errors, warnings = _validate(raw)

        assert errors == []
        assert result is not None
        assert len(result.placements) == 3
        assert {p.perspective for p in result.placements} == {
            "subject",
            "question",
            "method",
        }
        assert result.truncated is False
        assert result.cartridge_id == "astrophysics"
        assert result.skipped_reason is None
        # 同じノードに違う観点でも置ける
        assert result.placements[2].claim_id == "c_1"

    def test_same_node_with_two_perspectives_is_allowed(self):
        raw = _raw(
            _placement(perspective="subject"),
            _placement(perspective="theory", evidence_quote=_GOAL),
        )
        result, errors, _warnings = _validate(raw)
        assert errors == []
        assert len(result.placements) == 2

    def test_every_perspective_in_the_vocabulary_is_accepted(self):
        for perspective in PERSPECTIVES:
            result, errors, _w = _validate(_raw(_placement(perspective=perspective)))
            assert errors == [], perspective
            assert result.placements[0].perspective == perspective

    def test_unplaced_domains_are_carried_through(self):
        raw = _raw(
            _placement(),
            unplaced_domains=[
                {"domain_key": "particle_physics", "reason": "加速器実験の検出器を扱っていない。"}
            ],
        )
        result, errors, _warnings = _validate(raw)

        assert errors == []
        assert [u.domain_key for u in result.unplaced_domains] == ["particle_physics"]
        assert result.unplaced_domains[0].reason

    def test_quote_whitespace_differences_are_tolerated(self):
        """改行・連続空白のみの違いは verbatim 扱い（正規化は空白の畳み込みのみ）。"""
        noisy = _THESIS.replace(" ", "\n  ", 1)
        result, errors, _w = _validate(_raw(_placement(evidence_quote=noisy)))
        assert errors == []
        assert result is not None

    def test_quote_may_come_from_a_claim(self):
        result, errors, _w = _validate(
            _raw(_placement(evidence_quote=_CLAIM_2, claim_id="c_2"))
        )
        assert errors == []
        assert result.placements[0].claim_id == "c_2"


# ---------------------------------------------------------------------------
# 2. hard errors
# ---------------------------------------------------------------------------


class TestOutputShape:
    def test_non_dict_output(self):
        result, errors, _w = _validate(["nope"])  # type: ignore[arg-type]
        assert result is None
        assert "output_not_object" in _error_ids(errors)

    def test_missing_placements_array(self):
        result, errors, _w = _validate({"unplaced_domains": []})
        assert result is None
        assert "placements_missing" in _error_ids(errors)

    def test_placements_not_a_list(self):
        result, errors, _w = _validate({"placements": {"domain_key": "astrophysics"}})
        assert result is None
        assert "placements_missing" in _error_ids(errors)

    def test_placement_entry_not_an_object(self):
        result, errors, _w = _validate({"placements": ["astrophysics/cmb"]})
        assert result is None
        assert "placement_not_object" in _error_ids(errors)


class TestNodeExistence:
    def test_unknown_node_is_a_hard_error(self):
        result, errors, _w = _validate(_raw(_placement(node_id="black_holes")))
        assert result is None
        assert "placement_unknown_node" in _error_ids(errors)

    def test_node_from_another_domain_is_a_hard_error(self):
        """`detector` は particle_physics のノードなので astrophysics では実在しない。"""
        result, errors, _w = _validate(_raw(_placement(node_id="detector")))
        assert result is None
        assert "placement_unknown_node" in _error_ids(errors)

    def test_unknown_domain_is_a_hard_error(self):
        result, errors, _w = _validate(_raw(_placement(domain_key="chemistry")))
        assert result is None
        assert "placement_unknown_domain" in _error_ids(errors)

    def test_missing_ids_are_hard_errors(self):
        result, errors, _w = _validate(_raw(_placement(domain_key="")))
        assert result is None
        assert "placement_domain_missing" in _error_ids(errors)

        result, errors, _w = _validate(_raw(_placement(node_id="")))
        assert result is None
        assert "placement_node_missing" in _error_ids(errors)


class TestVocabularyAndWeight:
    def test_unknown_perspective_is_a_hard_error(self):
        result, errors, _w = _validate(_raw(_placement(perspective="vibes")))
        assert result is None
        assert "placement_unknown_perspective" in _error_ids(errors)

    @pytest.mark.parametrize("weight", [-0.1, 1.5, "high", None])
    def test_weight_must_be_a_number_in_range(self, weight):
        result, errors, _w = _validate(_raw(_placement(weight=weight)))
        assert result is None
        assert "placement_weight_invalid" in _error_ids(errors)

    def test_missing_weight_key_is_a_hard_error(self):
        entry = _placement()
        entry.pop("weight")
        result, errors, _w = _validate(_raw(entry))
        assert result is None
        assert "placement_weight_invalid" in _error_ids(errors)

    def test_boundary_weights_are_accepted(self):
        for weight in (0.0, 1.0):
            _result, errors, _w = _validate(_raw(_placement(weight=weight)))
            assert errors == [], weight


class TestReasonAndEvidence:
    def test_reason_is_required(self):
        result, errors, _w = _validate(_raw(_placement(reason="")))
        assert result is None
        assert "placement_reason_missing" in _error_ids(errors)

    def test_reason_length_is_bounded(self):
        result, errors, _w = _validate(_raw(_placement(reason="あ" * (MAX_REASON_CHARS + 1))))
        assert result is None
        assert "placement_reason_too_long" in _error_ids(errors)

    def test_evidence_quote_is_required(self):
        result, errors, _w = _validate(_raw(_placement(evidence_quote="")))
        assert result is None
        assert "placement_evidence_quote_missing" in _error_ids(errors)

    def test_non_verbatim_quote_is_a_hard_error(self):
        """言い換え・翻訳された「引用」は捏造ガードで弾く（LS4）。"""
        result, errors, _w = _validate(
            _raw(_placement(evidence_quote="単一の整合関係が2つの振幅を結び付ける。"))
        )
        assert result is None
        assert "placement_evidence_quote_not_verbatim" in _error_ids(errors)

    def test_quote_must_come_from_the_untruncated_material(self):
        """切り詰め後のプロンプト本文ではなく元テキストが照合対象（誤検出しない）。"""
        long_thesis = "Start. " + ("filler word " * 200) + "The tail sentence matters."
        item = _input(central_thesis=long_thesis)
        _result, errors, _w = _validate(
            _raw(_placement(evidence_quote="The tail sentence matters.")), item
        )
        assert errors == []

    def test_too_short_quote_is_a_hard_error(self):
        result, errors, _w = _validate(_raw(_placement(evidence_quote="A")))
        assert result is None
        assert "placement_evidence_quote_too_short" in _error_ids(errors)

    def test_too_long_quote_is_a_hard_error(self):
        quote = _THESIS + " " + ("x" * MAX_EVIDENCE_QUOTE_CHARS)
        item = _input(central_thesis=quote)
        result, errors, _w = _validate(_raw(_placement(evidence_quote=quote)), item)
        assert result is None
        assert "placement_evidence_quote_too_long" in _error_ids(errors)

    def test_paper_title_is_not_a_valid_quote_source(self):
        """タイトルはヘイスタックに入れない（根拠として弱いため）。"""
        result, errors, _w = _validate(
            _raw(
                _placement(
                    evidence_quote="A consistency relation for the lensing amplitude"
                )
            )
        )
        assert result is None
        assert "placement_evidence_quote_not_verbatim" in _error_ids(errors)


class TestClaimReference:
    def test_unknown_claim_id_is_a_hard_error(self):
        result, errors, _w = _validate(_raw(_placement(claim_id="c_999")))
        assert result is None
        assert "placement_unknown_claim" in _error_ids(errors)

    def test_null_claim_id_is_fine_for_paper_level_quotes(self):
        _result, errors, _w = _validate(_raw(_placement(claim_id=None)))
        assert errors == []

    def test_empty_string_claim_id_is_normalized_to_none(self):
        result, errors, _w = _validate(_raw(_placement(claim_id="")))
        assert errors == []
        assert result.placements[0].claim_id is None


class TestSilentEmptiness:
    def test_empty_placements_without_unplaced_declaration_is_a_hard_error(self):
        """LS10: 「置けない」は正当だが、黙って空は答えではない。"""
        result, errors, _w = _validate({"placements": []})
        assert result is None
        assert "no_placement_and_no_unplaced_declaration" in _error_ids(errors)

    def test_empty_placements_with_unplaced_declaration_is_accepted(self):
        raw = {
            "placements": [],
            "unplaced_domains": [
                {"domain_key": "astrophysics", "reason": "対象が天体現象ではない。"},
                {"domain_key": "particle_physics", "reason": "加速器実験を扱っていない。"},
            ],
        }
        result, errors, _w = _validate(raw)

        assert errors == []
        assert result.placements == []
        assert len(result.unplaced_domains) == 2
        # 配置ゼロは skipped ではない（正直な「置けない」の記録）
        assert result.skipped_reason is None

    def test_no_material_document_may_return_nothing(self):
        item = _input(
            central_question="",
            central_thesis="",
            paper_goal="",
            headline_claim="",
            claim_summaries=[],
        )
        result, errors, _w = _validate({"placements": []}, item)
        assert errors == []
        assert result is not None
        assert result.placements == []


# ---------------------------------------------------------------------------
# 3. warnings（ブロックしない）
# ---------------------------------------------------------------------------


class TestTruncation:
    def _many(self, count: int) -> list[dict]:
        nodes = ["cmb", "cosmology", "observation_methods"]
        out = []
        for index in range(count):
            out.append(
                _placement(
                    node_id=nodes[index % len(nodes)],
                    perspective=PERSPECTIVES[index % len(PERSPECTIVES)],
                    weight=round(0.1 * (index + 1), 2),
                )
            )
        return out

    def test_over_cap_truncates_by_descending_weight(self):
        item = _input(max_placements=2)
        result, errors, warnings = _validate(_raw(*self._many(5)), item)

        assert errors == []
        assert result.truncated is True
        assert result.truncated_count == 3
        assert len(result.placements) == 2
        # 最も weight が高い2件が残る（0.5 / 0.4）
        assert sorted(p.weight for p in result.placements) == [0.4, 0.5]
        assert any("placements_over_cap" in w for w in warnings)
        assert result.review_notes

    def test_truncation_is_deterministic_on_ties(self):
        item = _input(max_placements=2)
        raw = _raw(
            _placement(node_id="cmb", perspective="subject", weight=0.5),
            _placement(node_id="cosmology", perspective="question", weight=0.5),
            _placement(node_id="observation_methods", perspective="method", weight=0.5),
        )
        first, _e, _w = _validate(raw, item)
        second, _e2, _w2 = _validate(raw, item)

        # weight が同値なら入力順で先勝ち（毎回同じ結果）
        assert [p.node_id for p in first.placements] == ["cmb", "cosmology"]
        assert [p.node_id for p in first.placements] == [
            p.node_id for p in second.placements
        ]

    def test_under_cap_does_not_set_truncated(self):
        result, _errors, warnings = _validate(_raw(*self._many(2)))
        assert result.truncated is False
        assert result.truncated_count == 0
        assert not any("placements_over_cap" in w for w in warnings)


class TestDeduplication:
    def test_duplicate_key_is_first_wins_and_warned(self):
        raw = _raw(
            _placement(reason="最初の理由。", weight=0.9),
            _placement(reason="二番目の理由。", weight=0.2),
        )
        result, errors, warnings = _validate(raw)

        assert errors == []
        assert len(result.placements) == 1
        assert result.placements[0].reason == "最初の理由。"
        assert any("placement_duplicate_key" in w for w in warnings)

    def test_duplicate_only_when_all_three_match(self):
        raw = _raw(
            _placement(perspective="subject"),
            _placement(perspective="theory", evidence_quote=_GOAL),
        )
        result, _errors, warnings = _validate(raw)
        assert len(result.placements) == 2
        assert not any("placement_duplicate_key" in w for w in warnings)


class TestSoftWarnings:
    def test_single_placement_is_warned_but_kept(self):
        result, errors, warnings = _validate(_raw(_placement()))
        assert errors == []
        assert len(result.placements) == 1
        assert any("fewer_placements_than_target" in w for w in warnings)

    def test_unplaced_entry_for_unknown_domain_is_warned_and_dropped(self):
        raw = _raw(
            _placement(),
            unplaced_domains=[{"domain_key": "chemistry", "reason": "対象外。"}],
        )
        result, errors, warnings = _validate(raw)
        assert errors == []
        assert result.unplaced_domains == []
        assert any("unplaced_unknown_domain" in w for w in warnings)

    def test_unplaced_entry_without_reason_is_kept_with_a_warning(self):
        raw = _raw(
            _placement(),
            unplaced_domains=[{"domain_key": "particle_physics"}],
        )
        result, errors, warnings = _validate(raw)
        assert errors == []
        assert [u.domain_key for u in result.unplaced_domains] == ["particle_physics"]
        assert any("unplaced_reason_missing" in w for w in warnings)

    def test_duplicate_unplaced_entries_are_collapsed(self):
        raw = _raw(
            _placement(),
            unplaced_domains=[
                {"domain_key": "particle_physics", "reason": "一つ目。"},
                {"domain_key": "particle_physics", "reason": "二つ目。"},
            ],
        )
        result, errors, _w = _validate(raw)
        assert errors == []
        assert len(result.unplaced_domains) == 1
        assert result.unplaced_domains[0].reason == "一つ目。"

    def test_non_list_unplaced_domains_is_ignored(self):
        result, errors, _w = _validate(_raw(_placement(), unplaced_domains="none"))
        assert errors == []
        assert result.unplaced_domains == []


# ---------------------------------------------------------------------------
# 4. すべての issue が保持される（P4）
# ---------------------------------------------------------------------------


class TestIssueRetention:
    def test_warnings_are_recorded_on_the_result(self):
        result, _errors, _warnings = _validate(_raw(_placement()))
        assert any(issue.severity == "warning" for issue in result.validation_issues)

    def test_error_messages_are_prefixed_with_the_rule_id(self):
        _result, errors, _w = _validate(_raw(_placement(perspective="vibes")))
        assert errors
        assert all(":" in message for message in errors)
