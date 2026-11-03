"""agent 側の参照 ID の綴りゆれ（``claim:`` 接頭辞 / 合成 step ID）の正規化。

正本: ``backend/core/knowledge_objects/references.py``（claim 参照）と
``backend/core/knowledge_objects/stable_key.py::derivation_step_agent_id``（step）。
A層は backend を import できないので、``component_graph`` 側に同じ規則の実装があり、
ここでその2つが一致していることを固定する。
"""
from __future__ import annotations


from pathlib import Path

import pytest

from core.knowledge_objects.references import (
    CLAIM_REF_PREFIX,
    claim_ref_variants,
    expand_claim_refs,
    normalize_claim_ref,
)
from core.knowledge_objects.stable_key import derivation_step_agent_id

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent


class TestNormalizeClaimRef:
    def test_strips_the_agent_prefix(self):
        assert normalize_claim_ref("claim:blk_9fd917ff:span_001") == "blk_9fd917ff:span_001"

    def test_leaves_unprefixed_ids_alone(self):
        for value in ("blk_x:span_001", "claim_span_001_3_sub02", "synth_claim_0001"):
            assert normalize_claim_ref(value) == value

    def test_does_not_touch_claim_underscore_ids(self):
        """``claim_...`` は接頭辞ではない（``claim:`` だけを剥がす）。"""
        assert normalize_claim_ref("claim_span_001") == "claim_span_001"

    def test_prefix_only_value_is_kept(self):
        assert normalize_claim_ref(CLAIM_REF_PREFIX) == CLAIM_REF_PREFIX

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_empty_values(self, value):
        assert normalize_claim_ref(value) == ""


class TestVariants:
    def test_original_spelling_comes_first(self):
        assert claim_ref_variants("claim:blk_x:span_001") == [
            "claim:blk_x:span_001",
            "blk_x:span_001",
        ]

    def test_unprefixed_value_has_one_variant(self):
        assert claim_ref_variants("blk_x:span_001") == ["blk_x:span_001"]

    def test_empty_value_has_no_variant(self):
        assert claim_ref_variants("") == []

    def test_expand_keeps_order_and_dedups(self):
        assert expand_claim_refs(["claim:a", "a", "b", ""]) == ["claim:a", "a", "b"]


class TestPurity:
    def test_module_is_stdlib_only(self):
        src = (BACKEND / "core" / "knowledge_objects" / "references.py").read_text("utf-8")
        for banned in ("fastapi", "sqlalchemy", "core.llm", "openai"):
            assert banned not in src


class TestDerivationStepRefMirrorsBackend:
    """A層の ``derivation_step_ref`` と backend の ``derivation_step_agent_id`` は同じ規則。"""

    @staticmethod
    def _agent_schema():
        # backend から A層を import するのは既存の作法（stable_key.py も
        # ``episteme_graph.agents.content_normalization`` を読む）。逆向き
        # （A層 → backend）だけが禁止。
        from episteme_graph.agents.component_graph import schema

        return schema

    @pytest.mark.parametrize(
        ("derivation_id", "step_id"),
        [
            ("derivation_claim_0001", "step_001"),
            ("deriv", "step_012"),
            ("", "step_001"),
            ("chain", ""),
            ("  chain  ", "  step_002  "),
            ("", ""),
        ],
    )
    def test_same_output(self, derivation_id, step_id):
        agent = self._agent_schema()
        assert agent.derivation_step_ref(derivation_id, step_id) == \
            derivation_step_agent_id(derivation_id, step_id)
