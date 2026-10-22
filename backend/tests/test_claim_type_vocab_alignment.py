"""claim 種別の語彙の整合（2026-10-17・頭脳 W1 seq 3283）。

claim_qualification のプロンプトが LLM に許す型（CORE_CLAIM_TYPE_FALLBACKS）が
claim_object_builder の正規化で unknown に丸められ、保存側の正本
core/schema.py::CLAIM_TYPES にも在る型（relation / diagnostic_claim 等）が消えていた。
プロンプトが許す型 ⊆ claim_object_builder の語彙 ⊆ 正本、を固定する。
"""
from __future__ import annotations

from core.schema import CLAIM_TYPES
from episteme_graph.agents.claim_object_builder.builder import ClaimObjectBuilder
from episteme_graph.agents.claim_object_builder.schema import CLAIM_TYPE_ONTOLOGY
from episteme_graph.agents.claim_qualification.schema import CORE_CLAIM_TYPE_FALLBACKS


def test_claim_object_ontology_is_within_the_canonical_vocabulary():
    assert set(CLAIM_TYPE_ONTOLOGY) <= set(CLAIM_TYPES)


def test_prompt_claim_types_are_not_rounded_to_unknown():
    for value in CORE_CLAIM_TYPE_FALLBACKS:
        if value == "unknown":
            continue
        normalized = ClaimObjectBuilder._normalize_claim_type(value)
        assert normalized != "unknown", value
        assert normalized in CLAIM_TYPES, (value, normalized)
