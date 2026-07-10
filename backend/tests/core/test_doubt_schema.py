"""D1-1 — D層スキーマ語彙と台帳データモデルのテスト。"""

from __future__ import annotations

import json

import pytest

from core.doubt.schema import (
    HUMAN_ONLY_VERIFICATION_STATUSES,
    LEDGER_REVIEW_ENTITY_TYPES,
    AssumptionStatus,
    ChallengeType,
    EpistemicLedgerRecord,
    ScopeCandidate,
    TargetType,
    VerificationScope,
    VerificationStatus,
    count_range_label,
    load_level_for_score,
    scope_coverage_level,
)


class TestVocabulary:
    def test_target_types(self):
        assert {t.value for t in TargetType} == {"claim", "assumption", "equation", "component"}

    def test_verification_statuses(self):
        assert {s.value for s in VerificationStatus} == {
            "directly_verified", "indirectly_supported", "untested", "refuted", "unknown",
        }

    def test_directly_verified_is_human_only(self):
        assert VerificationStatus.DIRECTLY_VERIFIED in HUMAN_ONLY_VERIFICATION_STATUSES

    def test_review_entity_types_extended(self):
        for entity in ("ledger", "assumption", "challenge",
                       "verification_proposal", "counterfactual_session"):
            assert entity in LEDGER_REVIEW_ENTITY_TYPES

    def test_challenge_types(self):
        assert {t.value for t in ChallengeType} == {
            "scope_extrapolation", "untested_in_domain", "definitional", "hidden_lemma",
        }

    def test_assumption_statuses_keep_dismissed(self):
        # P4: dismissed は正式な語彙（削除ではなく保持）
        assert AssumptionStatus.DISMISSED.value == "dismissed"


class TestLedgerRecord:
    def test_empty_scopes_is_normal_state(self):
        """スコープ 0 件（空欄）が正常状態として表現できる（受け入れ条件）。"""
        record = EpistemicLedgerRecord(target_id="c1", target_type=TargetType.CLAIM)
        assert record.verification_scopes == []
        assert not record.has_scopes()
        assert record.verification_status == VerificationStatus.UNKNOWN

    def test_serialization_roundtrip(self):
        record = EpistemicLedgerRecord(
            target_id="c1",
            target_type=TargetType.EQUATION,
            verification_status=VerificationStatus.INDIRECTLY_SUPPORTED,
            verification_scopes=[VerificationScope(condition="低エネルギー極限で",
                                                   evidence_ids=["e1"],
                                                   recorded_by="u1", reason="r")],
        )
        data = json.loads(record.model_dump_json())
        assert data["target_type"] == "equation"
        assert data["verification_scopes"][0]["condition"] == "低エネルギー極限で"

    def test_invalid_vocab_rejected(self):
        with pytest.raises(Exception):
            EpistemicLedgerRecord(target_id="x", target_type="paper")  # type: ignore[arg-type]
        with pytest.raises(Exception):
            EpistemicLedgerRecord(
                target_id="x", target_type=TargetType.CLAIM,
                verification_status="proven",  # type: ignore[arg-type]
            )


class TestScopeModels:
    def test_scope_has_axis(self):
        assert not VerificationScope().has_axis()
        assert VerificationScope(domain="電磁相互作用で").has_axis()

    def test_candidate_default_status_is_candidate(self):
        # AIに疑わせない: LLM 候補の既定 status は candidate
        assert ScopeCandidate().status == "candidate"


class TestLabels:
    def test_count_range_label_k_anonymity(self):
        # k=3 未満は空（呼び出し側で行ごと除外）
        assert count_range_label(0) == ""
        assert count_range_label(2) == ""
        assert count_range_label(3) == "3-5"
        assert count_range_label(5) == "3-5"
        assert count_range_label(6) == "6-10"
        assert count_range_label(11) == "11+"

    def test_load_level_for_score(self):
        assert load_level_for_score(None, 1, 5, 10) == ""
        assert load_level_for_score(0.5, 1, 5, 10) == "low"
        assert load_level_for_score(2, 1, 5, 10) == "medium"
        assert load_level_for_score(6, 1, 5, 10) == "high"
        assert load_level_for_score(12, 1, 5, 10) == "highest"

    def test_scope_coverage_level(self):
        assert scope_coverage_level(0) == "none"
        assert scope_coverage_level(1) == "single"
        assert scope_coverage_level(3) == "several"
        assert scope_coverage_level(5) == "broad"
