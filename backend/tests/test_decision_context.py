"""確定文脈の記帳（``core/decision_context.py``）— プリミティブの単体テスト。

正本: ``docs/features/decision_context_design.md``（不変条項 DC1〜DC4）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import decision_context as dc  # noqa: E402


def _ctx(**overrides):
    kwargs = {
        "basis": dc.BASIS_RELEASE_REVIEW_PLACEMENTS,
        "presented_ids": ["p2", "p1"],
        "applied_ids": ["p1", "p2"],
        "alternatives": (dc.ALT_REJECT, dc.ALT_RECONSIDER),
        "reopen_path": "PATCH /api/admin/landscape/placements/{placement_id}",
    }
    kwargs.update(overrides)
    return dc.build_decision_context(**kwargs)


class TestShape:
    def test_exact_key_set(self):
        assert set(_ctx()) == {
            "basis",
            "presented",
            "applied",
            "presented_matches_applied",
            "alternatives_available",
            "decline_possible",
            "reopen",
            "evidence_shown",
            "client_reported",
        }

    def test_id_blocks_have_count_ids_truncated(self):
        ctx = _ctx()
        assert set(ctx["presented"]) == {"count", "ids", "truncated"}
        assert ctx["presented"] == {"count": 2, "ids": ["p1", "p2"], "truncated": False}

    def test_reopen_block(self):
        ctx = _ctx(reopen_statuses=("rejected", "review_required", "", None))
        assert ctx["reopen"] == {
            "path": "PATCH /api/admin/landscape/placements/{placement_id}",
            "statuses": ["rejected", "review_required"],
            "actor": dc.REOPEN_ACTOR_TEACHER,
        }

    def test_decline_possible_is_always_true_and_derived(self):
        """DC3: 引数ではない（「断れなかった確定」を本プリミティブで表現しない）。"""
        assert _ctx()["decline_possible"] is True
        with pytest.raises(TypeError):
            dc.build_decision_context(
                basis="x",
                presented_ids=[],
                applied_ids=[],
                alternatives=(dc.ALT_REJECT,),
                reopen_path="PATCH /x",
                decline_possible=False,  # type: ignore[call-arg]
            )

    def test_json_serialisable(self):
        payload = json.dumps(_ctx(client_reported={"sort_order": "load"}))
        assert '"basis"' in payload

    def test_no_forbidden_numeric_keys(self):
        """原則4: 数値（confidence / weight / score）は載せない。"""
        payload = json.dumps(
            _ctx(client_reported={"sort_order": "load"}), ensure_ascii=False
        )
        for forbidden in ('"confidence"', '"weight"', '"score"'):
            assert forbidden not in payload


class TestPresentedAndApplied:
    def test_match_flag_is_set_equality_not_order(self):
        assert _ctx(presented_ids=["a", "b"], applied_ids=["b", "a"])[
            "presented_matches_applied"
        ] is True

    def test_mismatch_is_reported_honestly(self):
        ctx = _ctx(presented_ids=["a", "b"], applied_ids=["a"])
        assert ctx["presented_matches_applied"] is False
        assert ctx["presented"]["count"] == 2
        assert ctx["applied"]["count"] == 1

    def test_ids_are_normalised_and_deduped(self):
        ctx = _ctx(presented_ids=[" a ", "a", "", None, "b"], applied_ids=["a", "b"])
        assert ctx["presented"]["ids"] == ["a", "b"]
        assert ctx["presented_matches_applied"] is True

    def test_cap_and_truncated_flag(self):
        many = [f"p{i:04d}" for i in range(dc.PRESENTED_IDS_MAX + 5)]
        ctx = _ctx(presented_ids=many, applied_ids=many)
        assert ctx["presented"]["count"] == dc.PRESENTED_IDS_MAX + 5
        assert len(ctx["presented"]["ids"]) == dc.PRESENTED_IDS_MAX
        assert ctx["presented"]["truncated"] is True
        # DC2: 切り詰めは表示上の都合であって、一致判定は切り詰め前の集合で行う。
        assert ctx["presented_matches_applied"] is True

    def test_empty_applied_is_allowed(self):
        ctx = _ctx(presented_ids=["a"], applied_ids=[])
        assert ctx["applied"] == {"count": 0, "ids": [], "truncated": False}
        assert ctx["presented_matches_applied"] is False


class TestValidation:
    def test_empty_alternatives_raises(self):
        """DC3: 代替の無い確定はゴム印であり、判断として記帳しない。"""
        with pytest.raises(ValueError):
            _ctx(alternatives=())
        with pytest.raises(ValueError):
            _ctx(alternatives=("", "  ", None))

    def test_unknown_alternative_raises(self):
        with pytest.raises(ValueError):
            _ctx(alternatives=(dc.ALT_REJECT, "rubber_stamp"))

    def test_empty_basis_raises(self):
        with pytest.raises(ValueError):
            _ctx(basis="")
        with pytest.raises(ValueError):
            _ctx(basis="   ")

    def test_empty_reopen_path_raises(self):
        with pytest.raises(ValueError):
            _ctx(reopen_path="")

    def test_alternatives_are_sorted_and_deduped(self):
        ctx = _ctx(alternatives=(dc.ALT_RECONSIDER, dc.ALT_REJECT, dc.ALT_REJECT))
        assert ctx["alternatives_available"] == sorted(
            [dc.ALT_RECONSIDER, dc.ALT_REJECT]
        )

    def test_vocabulary_is_closed(self):
        assert set(dc.ALTERNATIVES) == {
            dc.ALT_DESELECT,
            dc.ALT_DISMISS,
            dc.ALT_EDIT,
            dc.ALT_RECONSIDER,
            dc.ALT_REJECT,
            dc.ALT_SKIP_STEP,
        }


class TestEvidenceAndClientReported:
    def test_unknown_evidence_stays_none(self):
        assert _ctx()["evidence_shown"] is None

    def test_evidence_flags_are_kept_as_given(self):
        assert _ctx(evidence_shown=True)["evidence_shown"] is True
        assert _ctx(evidence_shown=False)["evidence_shown"] is False

    def test_client_reported_is_isolated_and_absent_by_default(self):
        """DC4: 来歴申告はサーバ導出値と混ぜず、未指定なら None（偽装しない）。"""
        assert _ctx()["client_reported"] is None
        assert _ctx(client_reported={})["client_reported"] is None
        ctx = _ctx(client_reported={"sort_order": "load", "evidence_shown": True})
        assert ctx["client_reported"] == {"sort_order": "load", "evidence_shown": True}
        # トップレベルのサーバ導出キーは申告に汚染されない。
        assert ctx["evidence_shown"] is None

    def test_client_reported_is_copied_not_aliased(self):
        reported = {"sort_order": "load"}
        ctx = _ctx(client_reported=reported)
        reported["sort_order"] = "tampered"
        assert ctx["client_reported"]["sort_order"] == "load"


class TestAttach:
    def test_attach_returns_new_dict_and_does_not_mutate(self):
        metadata = {"action": "accept_on_release"}
        ctx = _ctx()
        merged = dc.attach_decision_context(metadata, ctx)
        assert merged["action"] == "accept_on_release"
        assert merged[dc.DECISION_CONTEXT_KEY] is ctx
        assert dc.DECISION_CONTEXT_KEY not in metadata

    def test_attach_accepts_empty_metadata(self):
        merged = dc.attach_decision_context({}, _ctx())
        assert set(merged) == {dc.DECISION_CONTEXT_KEY}

    def test_key_constant(self):
        assert dc.DECISION_CONTEXT_KEY == "decision_context"


class TestStagedBases:
    """段階適用の第2波（2026-09-10・是正 A-04 / F-18）で足した basis の不変条項。

    どの basis でも DC1〜DC4 は同じ形で成り立つ（basis ごとの例外を作らない）。
    """

    @pytest.mark.parametrize("basis", dc.BASIS_VALUES)
    def test_every_basis_satisfies_the_invariants(self, basis):
        ctx = _ctx(basis=basis)
        assert ctx["basis"] == basis
        # DC3: 断れない確定は表現しない（導出値）。
        assert ctx["decline_possible"] is True
        # DC2: 一致は導出（同じ集合を渡したので True）。
        assert ctx["presented_matches_applied"] is True
        # DC4: 申告が無ければ載せない。
        assert ctx["client_reported"] is None
        # 数値（confidence / weight / score）は載せない（原則4）。
        payload = json.dumps(ctx, ensure_ascii=False)
        for banned in ("confidence", "weight", "score"):
            assert banned not in payload

    @pytest.mark.parametrize("basis", dc.BASIS_VALUES)
    def test_every_basis_still_requires_alternatives(self, basis):
        """DC3: basis ごとの抜け道を作らない（一括でも単発でも同じ）。"""
        with pytest.raises(ValueError):
            _ctx(basis=basis, alternatives=())

    def test_single_confirmation_shape(self):
        """単発の確定は presented = applied = そのオブジェクト（DC2 を守る形）。"""
        ctx = _ctx(
            basis=dc.BASIS_COMPONENT_REVIEW_SINGLE,
            presented_ids=["comp-1"],
            applied_ids=["comp-1"],
            alternatives=(dc.ALT_REJECT, dc.ALT_SKIP_STEP),
            reopen_path="POST /api/admin/theory-components/{component_id}/reject",
            reopen_statuses=("rejected",),
        )
        assert ctx["presented"]["ids"] == ["comp-1"]
        assert ctx["applied"]["ids"] == ["comp-1"]
        assert ctx["presented_matches_applied"] is True
        assert ctx["reopen"]["statuses"] == ["rejected"]
        assert ctx["reopen"]["actor"] == dc.REOPEN_ACTOR_TEACHER

    def test_freeze_detects_a_diff_between_preview_and_frozen_nodes(self):
        """骨格の凍結は「プレビューの対象」と「版に入った node」を本当に比べる。"""
        ctx = _ctx(
            basis=dc.BASIS_ATLAS_SKELETON_FREEZE,
            presented_ids=["r1", "c1", "c2"],
            applied_ids=["r1", "c1"],
            alternatives=(dc.ALT_EDIT, dc.ALT_SKIP_STEP),
            reopen_path=(
                "POST /api/admin/cartridges/{cartridge_id}/atlas/skeleton/draft/from-frozen"
            ),
        )
        assert ctx["presented_matches_applied"] is False
        # 凍結版に「戻せる status」は無い（偽らない）。
        assert ctx["reopen"]["statuses"] == []
