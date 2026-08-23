"""candidate → 人間確定 共通プリミティブ（``core/candidate_flow.py``）のテスト。

正本: ``docs/features/candidate_flow_design.md``（提案 §2-1）。

検査観点:
  1. 遷移マトリクス全網羅（許可3遷移以外は全て CandidateTransitionError）
  2. actor_id 必須（KN-3: 確定は人間）
  3. dismiss の理由必須と、構成による緩和
  4. select_supersedable が人間確定行（accepted / dismissed）を守る（LS3）
  5. 空集合入力で callable を一度も呼ばない（SQL 非発行の慣行）
  6. 監査 callable が entity_type / action 付きで呼ばれる（監査必須）
  7. 語彙構築の検証エラー
  8. ガードレール: fastapi / sqlalchemy 非 import・DELETE 文字列不在（P4）
"""

from __future__ import annotations

import dataclasses
import itertools
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.candidate_flow import (  # noqa: E402
    ACTION_CONFIRM,
    ACTION_DISMISS,
    ACTION_RESTORE,
    ACTION_SUPERSEDE,
    ACTIONS,
    CandidateFlow,
    CandidateFlowConfigError,
    CandidateTransitionError,
    CandidateVocabulary,
    resolve_transition,
)
from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
)

ACTOR = "11111111-1111-1111-1111-111111111111"

VOCAB = CandidateVocabulary(
    candidate="candidate",
    accepted="confirmed",
    dismissed="dismissed",
    superseded="superseded",
)
#: superseded の概念を持たない系統（W層 element_annotations 相当）
VOCAB_NO_SUPERSEDE = CandidateVocabulary(
    candidate="candidate",
    accepted="committed",
    dismissed="dismissed",
)


def make_flow(**kwargs) -> tuple[CandidateFlow, Mock, Mock]:
    """apply / audit を Mock にした CandidateFlow を組む。"""
    apply_status = Mock(return_value={"ok": True})
    record_audit = Mock(return_value=None)
    flow = CandidateFlow(
        vocab=kwargs.pop("vocab", VOCAB),
        audit_entity_type=kwargs.pop("audit_entity_type", "landscape_placement"),
        apply_status=apply_status,
        record_audit=record_audit,
        **kwargs,
    )
    return flow, apply_status, record_audit


# ---------------------------------------------------------------------------
# 1. 語彙
# ---------------------------------------------------------------------------


class TestCandidateVocabulary:
    def test_statuses_and_helpers(self):
        assert VOCAB.statuses == ("candidate", "confirmed", "dismissed", "superseded")
        assert VOCAB_NO_SUPERSEDE.statuses == ("candidate", "committed", "dismissed")
        assert VOCAB.human_decided == ("confirmed", "dismissed")
        assert VOCAB.is_candidate("candidate") is True
        assert VOCAB.is_candidate("confirmed") is False

    def test_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            VOCAB.candidate = "other"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"candidate": "", "accepted": "confirmed", "dismissed": "dismissed"},
            {"candidate": "candidate", "accepted": "   ", "dismissed": "dismissed"},
            {"candidate": "candidate", "accepted": "confirmed", "dismissed": ""},
            {"candidate": "candidate", "accepted": "confirmed", "dismissed": "d",
             "superseded": ""},
            {"candidate": " candidate", "accepted": "confirmed", "dismissed": "dismissed"},
            {"candidate": None, "accepted": "confirmed", "dismissed": "dismissed"},
        ],
    )
    def test_empty_or_padded_status_rejected(self, kwargs):
        with pytest.raises(CandidateFlowConfigError):
            CandidateVocabulary(**kwargs)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"candidate": "same", "accepted": "same", "dismissed": "dismissed"},
            {"candidate": "candidate", "accepted": "dup", "dismissed": "dup"},
            {"candidate": "candidate", "accepted": "confirmed",
             "dismissed": "dismissed", "superseded": "candidate"},
        ],
    )
    def test_duplicate_status_rejected(self, kwargs):
        with pytest.raises(CandidateFlowConfigError):
            CandidateVocabulary(**kwargs)


# ---------------------------------------------------------------------------
# 2. 遷移マトリクス
# ---------------------------------------------------------------------------


class TestResolveTransition:
    #: (current, action) -> 期待される新状態。ここに無い組み合わせは全て不正。
    ALLOWED = {
        ("candidate", ACTION_CONFIRM): "confirmed",
        ("candidate", ACTION_DISMISS): "dismissed",
        ("dismissed", ACTION_RESTORE): "candidate",
    }

    def test_allowed_transitions(self):
        for (current, action), expected in self.ALLOWED.items():
            assert (
                resolve_transition(
                    current, action, vocab=VOCAB, actor_id=ACTOR, reason="理由"
                )
                == expected
            )

    def test_full_matrix_rejects_everything_else(self):
        """語彙4状態 × アクション3種の全組み合わせを網羅する。"""
        checked = 0
        for current, action in itertools.product(VOCAB.statuses, ACTIONS):
            checked += 1
            if (current, action) in self.ALLOWED:
                continue
            with pytest.raises(CandidateTransitionError):
                resolve_transition(
                    current, action, vocab=VOCAB, actor_id=ACTOR, reason="理由"
                )
        assert checked == len(VOCAB.statuses) * len(ACTIONS) == 12

    def test_unknown_action_rejected(self):
        for action in ("delete", "purge", "supersede", "", "CONFIRM"):
            with pytest.raises(CandidateTransitionError):
                resolve_transition(
                    "candidate", action, vocab=VOCAB, actor_id=ACTOR, reason="理由"
                )

    def test_unknown_current_status_rejected(self):
        with pytest.raises(CandidateTransitionError):
            resolve_transition(
                "review_required", ACTION_CONFIRM, vocab=VOCAB, actor_id=ACTOR
            )

    @pytest.mark.parametrize("actor", [None, "", "   ", 0, 12345])
    def test_actor_required(self, actor):
        """KN-3: 確定は人間。actor 空・None・非文字列は拒否する。"""
        for action, current in (
            (ACTION_CONFIRM, "candidate"),
            (ACTION_DISMISS, "candidate"),
            (ACTION_RESTORE, "dismissed"),
        ):
            with pytest.raises(CandidateTransitionError):
                resolve_transition(
                    current, action, vocab=VOCAB, actor_id=actor, reason="理由"
                )

    def test_dismiss_requires_reason_by_default(self):
        for reason in ("", "   ", None):
            with pytest.raises(CandidateTransitionError):
                resolve_transition(
                    "candidate", ACTION_DISMISS, vocab=VOCAB, actor_id=ACTOR,
                    reason=reason,  # type: ignore[arg-type]
                )

    def test_dismiss_reason_can_be_relaxed(self):
        assert (
            resolve_transition(
                "candidate", ACTION_DISMISS, vocab=VOCAB, actor_id=ACTOR,
                reason="", require_dismiss_reason=False,
            )
            == "dismissed"
        )

    def test_confirm_and_restore_do_not_require_reason(self):
        assert resolve_transition(
            "candidate", ACTION_CONFIRM, vocab=VOCAB, actor_id=ACTOR
        ) == "confirmed"
        assert resolve_transition(
            "dismissed", ACTION_RESTORE, vocab=VOCAB, actor_id=ACTOR
        ) == "candidate"

    def test_vocab_without_superseded_matrix(self):
        with pytest.raises(CandidateTransitionError):
            resolve_transition(
                "superseded", ACTION_RESTORE, vocab=VOCAB_NO_SUPERSEDE, actor_id=ACTOR
            )
        assert resolve_transition(
            "candidate", ACTION_CONFIRM, vocab=VOCAB_NO_SUPERSEDE, actor_id=ACTOR
        ) == "committed"


# ---------------------------------------------------------------------------
# 3. supersede 対象抽出
# ---------------------------------------------------------------------------


class TestSelectSupersedable:
    ROWS = [
        {"id": "a", "status": "candidate"},
        {"id": "b", "status": "confirmed"},
        {"id": "c", "status": "dismissed"},
        {"id": "d", "status": "superseded"},
        {"id": "e", "status": "candidate"},
        {"id": "f", "status": "review_required"},
        {"id": "g", "status": None},
    ]

    def test_only_candidates_returned(self):
        from core.candidate_flow import select_supersedable

        got = select_supersedable(self.ROWS, vocab=VOCAB)
        assert [row["id"] for row in got] == ["a", "e"]

    def test_human_decided_rows_never_returned(self):
        """LS3: 人間が確定した行は再解析で置換・復活させられない。"""
        from core.candidate_flow import select_supersedable

        for status in VOCAB.human_decided:
            got = select_supersedable([{"id": "x", "status": status}], vocab=VOCAB)
            assert got == []

    def test_attribute_rows_and_custom_getter(self):
        from core.candidate_flow import select_supersedable

        class Row:
            def __init__(self, state):
                self.state = state

        rows = [Row("candidate"), Row("confirmed")]
        got = select_supersedable(rows, vocab=VOCAB, status_key="state")
        assert len(got) == 1 and got[0].state == "candidate"

        got2 = select_supersedable(
            rows, vocab=VOCAB, status_of=lambda r: r.state
        )
        assert len(got2) == 1

    def test_empty_input(self):
        from core.candidate_flow import select_supersedable

        assert select_supersedable([], vocab=VOCAB) == []

    def test_config_error_without_superseded_vocab(self):
        from core.candidate_flow import select_supersedable

        with pytest.raises(CandidateFlowConfigError):
            select_supersedable(self.ROWS, vocab=VOCAB_NO_SUPERSEDE)


# ---------------------------------------------------------------------------
# 4. CandidateFlow
# ---------------------------------------------------------------------------


class TestCandidateFlowConstruction:
    def test_audit_entity_type_required(self):
        for value in ("", "   "):
            with pytest.raises(CandidateFlowConfigError):
                CandidateFlow(
                    vocab=VOCAB,
                    audit_entity_type=value,
                    apply_status=Mock(),
                    record_audit=Mock(),
                )

    def test_callables_required(self):
        with pytest.raises(CandidateFlowConfigError):
            CandidateFlow(
                vocab=VOCAB,
                audit_entity_type="ledger",
                apply_status="not callable",  # type: ignore[arg-type]
                record_audit=Mock(),
            )
        with pytest.raises(CandidateFlowConfigError):
            CandidateFlow(
                vocab=VOCAB,
                audit_entity_type="ledger",
                apply_status=Mock(),
                record_audit=None,  # type: ignore[arg-type]
            )

    def test_no_delete_api_exposed(self):
        """P4: 行削除に相当する公開 API を持たない。"""
        flow, _, _ = make_flow()
        for name in dir(flow):
            assert "delete" not in name.lower()
            assert "purge" not in name.lower()
            assert "remove" not in name.lower()


class TestCandidateFlowTransitions:
    def test_confirm_applies_then_audits(self):
        flow, apply_status, record_audit = make_flow()
        result = flow.confirm(
            "p1", current_status="candidate", actor_id=ACTOR, metadata={"note": "x"}
        )

        assert result["old_status"] == "candidate"
        assert result["new_status"] == "confirmed"
        assert result["action"] == ACTION_CONFIRM
        assert result["entity_id"] == "p1"
        assert result["applied"] == {"ok": True}

        apply_status.assert_called_once_with(
            entity_id="p1",
            old_status="candidate",
            new_status="confirmed",
            actor_id=ACTOR,
            reason="",
            metadata={"note": "x"},
        )
        record_audit.assert_called_once_with(
            entity_type="landscape_placement",
            entity_id="p1",
            action=ACTION_CONFIRM,
            old_status="candidate",
            new_status="confirmed",
            actor_id=ACTOR,
            reason="",
            metadata={"note": "x"},
        )

    def test_audit_receives_entity_type_and_action_for_every_action(self):
        """監査必須: 3アクションいずれも entity_type / action 付きで記帳される。"""
        cases = [
            ("confirm", "candidate", ACTION_CONFIRM, "confirmed"),
            ("dismiss", "candidate", ACTION_DISMISS, "dismissed"),
            ("restore", "dismissed", ACTION_RESTORE, "candidate"),
        ]
        for method, current, action, expected in cases:
            flow, apply_status, record_audit = make_flow()
            getattr(flow, method)(
                "e1", current_status=current, actor_id=ACTOR, reason="理由"
            )
            kwargs = record_audit.call_args.kwargs
            assert kwargs["entity_type"] == "landscape_placement"
            assert kwargs["action"] == action
            assert kwargs["old_status"] == current
            assert kwargs["new_status"] == expected
            assert kwargs["actor_id"] == ACTOR
            assert apply_status.call_count == 1

    def test_invalid_transition_calls_nothing(self):
        flow, apply_status, record_audit = make_flow()
        with pytest.raises(CandidateTransitionError):
            flow.confirm("p1", current_status="confirmed", actor_id=ACTOR)
        with pytest.raises(CandidateTransitionError):
            flow.restore("p1", current_status="candidate", actor_id=ACTOR)
        with pytest.raises(CandidateTransitionError):
            flow.dismiss("p1", current_status="candidate", actor_id="")
        apply_status.assert_not_called()
        record_audit.assert_not_called()

    def test_dismiss_reason_required_then_relaxed(self):
        flow, apply_status, record_audit = make_flow()
        with pytest.raises(CandidateTransitionError):
            flow.dismiss("p1", current_status="candidate", actor_id=ACTOR)
        apply_status.assert_not_called()
        record_audit.assert_not_called()

        relaxed, apply2, audit2 = make_flow(require_dismiss_reason=False)
        result = relaxed.dismiss("p1", current_status="candidate", actor_id=ACTOR)
        assert result["new_status"] == "dismissed"
        assert apply2.call_count == 1
        assert audit2.call_count == 1

    def test_metadata_is_copied_not_shared(self):
        flow, apply_status, record_audit = make_flow()
        metadata = {"k": "v"}
        flow.confirm("p1", current_status="candidate", actor_id=ACTOR, metadata=metadata)
        passed = apply_status.call_args.kwargs["metadata"]
        assert passed == metadata
        assert passed is not metadata

    def test_apply_failure_skips_audit(self):
        """書き込めていない遷移を監査に載せない。"""
        apply_status = Mock(side_effect=RuntimeError("db down"))
        record_audit = Mock()
        flow = CandidateFlow(
            vocab=VOCAB,
            audit_entity_type="ledger",
            apply_status=apply_status,
            record_audit=record_audit,
        )
        with pytest.raises(RuntimeError):
            flow.confirm("p1", current_status="candidate", actor_id=ACTOR)
        record_audit.assert_not_called()

    def test_audit_failure_propagates(self):
        """監査必須: 監査 callable の例外は握らない。"""
        flow, _, record_audit = make_flow()
        record_audit.side_effect = RuntimeError("audit down")
        with pytest.raises(RuntimeError):
            flow.confirm("p1", current_status="candidate", actor_id=ACTOR)


class TestSupersedeCandidates:
    ROWS = [
        {"id": "a", "status": "candidate"},
        {"id": "b", "status": "confirmed"},
        {"id": "c", "status": "dismissed"},
        {"id": "d", "status": "candidate"},
    ]

    def test_only_candidates_superseded(self):
        flow, apply_status, record_audit = make_flow()
        result = flow.supersede_candidates(self.ROWS)

        assert result["action"] == ACTION_SUPERSEDE
        assert result["entity_ids"] == ["a", "d"]
        assert result["count"] == 2
        assert apply_status.call_count == 2
        for call in apply_status.call_args_list:
            assert call.kwargs["old_status"] == "candidate"
            assert call.kwargs["new_status"] == "superseded"
        # 既定では supersede は監査記帳しない（detect 側で記帳する慣行）
        record_audit.assert_not_called()

    def test_empty_input_is_noop_without_calling_callables(self):
        """空集合で SQL を発行しない慣行を Mock 未呼び出しで検証する。"""
        flow, apply_status, record_audit = make_flow()
        result = flow.supersede_candidates([])
        assert result["count"] == 0 and result["entity_ids"] == []
        apply_status.assert_not_called()
        record_audit.assert_not_called()

    def test_all_human_decided_is_noop(self):
        flow, apply_status, record_audit = make_flow()
        rows = [{"id": "b", "status": "confirmed"}, {"id": "c", "status": "dismissed"}]
        result = flow.supersede_candidates(rows)
        assert result["count"] == 0
        apply_status.assert_not_called()
        record_audit.assert_not_called()

    def test_audit_supersede_opt_in(self):
        flow, apply_status, record_audit = make_flow(audit_supersede=True)
        flow.supersede_candidates(self.ROWS, actor_id=None, reason="再解析")
        assert record_audit.call_count == 2
        kwargs = record_audit.call_args.kwargs
        assert kwargs["action"] == ACTION_SUPERSEDE
        assert kwargs["entity_type"] == "landscape_placement"
        assert kwargs["actor_id"] is None

    def test_custom_key_getters(self):
        flow, apply_status, _ = make_flow()
        rows = [{"placement_id": "z", "state": "candidate"}]
        flow.supersede_candidates(rows, status_key="state", id_key="placement_id")
        assert apply_status.call_args.kwargs["entity_id"] == "z"

    def test_config_error_without_superseded_vocab(self):
        flow, apply_status, record_audit = make_flow(vocab=VOCAB_NO_SUPERSEDE)
        with pytest.raises(CandidateFlowConfigError):
            flow.supersede_candidates(self.ROWS)
        apply_status.assert_not_called()
        record_audit.assert_not_called()


# ---------------------------------------------------------------------------
# 5. ガードレール
# ---------------------------------------------------------------------------


class TestGuardrails:
    SOURCE_PATH = BACKEND / "core" / "candidate_flow.py"

    @property
    def source(self) -> str:
        return self.SOURCE_PATH.read_text(encoding="utf-8")

    def test_does_not_import_fastapi_sqlalchemy_or_llm(self):
        """core 共通ルール: FastAPI 非 import。DB へは一切触らない純 Python。"""
        assert_source_does_not_import(
            self.source,
            ["fastapi", "sqlalchemy", "psycopg2", "core.postgres", "core.llm", "openai"],
            context=str(self.SOURCE_PATH),
        )

    def test_no_delete_statements(self):
        """P4: 行削除しない（削除 SQL / 削除 API を持たない）。"""
        assert_source_forbids(
            self.source,
            ["DELETE", "TRUNCATE", "def delete", "def purge", "def remove"],
            context=str(self.SOURCE_PATH),
        )

    def test_invariants_documented_in_module_docstring(self):
        import core.candidate_flow as mod

        doc = mod.__doc__ or ""
        for term in ("P4", "KN-3", "監査必須", "LS3"):
            assert term in doc, f"module docstring must state invariant {term}"
