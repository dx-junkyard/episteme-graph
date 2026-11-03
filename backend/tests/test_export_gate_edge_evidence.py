"""export validation gate の誤警報2件（実データ由来の是正・2026-09-19）。

1. ``COMPONENT_GRAPH_EDGE_NO_EVIDENCE`` — gate は ``evidence.evidence_claims`` しか
   読まないが、GraphNormalizer が claim backing を書くのは ``evidence_claim_ids``。
   実測では 618/618 の辺が「根拠なし」と報告されていた（実際は全て非空）。
2. ``DSL_EDGE_DANGLING_EVIDENCE`` — DSLLinkingAgent は claim 参照を
   ``claim:{block_id}:{span_id}`` と書き、ClaimObjectBuilder の ID は接頭辞なし。
   素の文字列比較なので接頭辞付きの参照が全て宙吊りと報告されていた。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.document_pipeline.export_validation_gate import (  # noqa: E402
    ExportValidationGate,
    normalize_claim_ref,
)


def _codes(entries):
    return [e.code for e in entries]


class TestComponentGraphEdgeEvidence:
    @staticmethod
    def _run(edge):
        gate = ExportValidationGate()
        errors: list = []
        warnings: list = []
        artifacts = {
            "component_graph": {
                "nodes": [
                    {"component_id": "n1", "label": "A", "component_type": "TheoryOperationNode"},
                    {"component_id": "n2", "label": "B", "component_type": "TheoryOperationNode"},
                ],
                "edges": [edge],
            }
        }
        component_result = types.SimpleNamespace(components=[])
        gate._check_component_graph_artifact(artifacts, component_result, errors, warnings)
        return errors, warnings

    def test_evidence_claim_ids_count_as_evidence(self):
        _errors, warnings = self._run({
            "edge_id": "e1", "source": "n1", "target": "n2", "edge_type": "derives",
            "evidence": {"evidence_claim_ids": ["claim_a"], "evidence_claims": []},
        })
        assert "COMPONENT_GRAPH_EDGE_NO_EVIDENCE" not in _codes(warnings)

    def test_top_level_evidence_claim_ids_count_too(self):
        _errors, warnings = self._run({
            "edge_id": "e1", "source": "n1", "target": "n2", "edge_type": "derives",
            "evidence_claim_ids": ["claim_a"],
        })
        assert "COMPONENT_GRAPH_EDGE_NO_EVIDENCE" not in _codes(warnings)

    def test_an_edge_with_no_reference_at_all_is_still_reported(self):
        _errors, warnings = self._run({
            "edge_id": "e1", "source": "n1", "target": "n2", "edge_type": "derives",
            "evidence": {"reason": "…"},
        })
        assert "COMPONENT_GRAPH_EDGE_NO_EVIDENCE" in _codes(warnings)


class TestDslEdgeDanglingEvidence:
    @staticmethod
    def _run(claim_refs, known_claim_ids):
        gate = ExportValidationGate()
        errors: list = []
        warnings: list = []
        dsl = types.SimpleNamespace(
            nodes=[
                types.SimpleNamespace(node_id="n1"),
                types.SimpleNamespace(node_id="n2"),
            ],
            edges=[
                types.SimpleNamespace(
                    edge_id="e1",
                    edge_type="DEFINES",
                    from_node_id="n1",
                    to_node_id="n2",
                    evidence_refs={"claim_ids": claim_refs, "equation_ids": [], "thesis_refs": []},
                )
            ],
        )
        claim_objects = types.SimpleNamespace(
            claims=[types.SimpleNamespace(claim_id=cid) for cid in known_claim_ids]
        )
        evidence = types.SimpleNamespace(records=[])
        gate._check_dsl_edges(dsl, {}, claim_objects, evidence, errors, warnings)
        return errors, warnings

    def test_prefixed_reference_resolves(self):
        errors, _warnings = self._run(
            ["claim:blk_x:span_001"], ["blk_x:span_001"]
        )
        assert "DSL_EDGE_DANGLING_EVIDENCE" not in _codes(errors)

    def test_unprefixed_reference_still_resolves(self):
        errors, _warnings = self._run(["claim_span_001_3"], ["claim_span_001_3"])
        assert "DSL_EDGE_DANGLING_EVIDENCE" not in _codes(errors)

    def test_a_genuinely_unknown_reference_is_still_dangling(self):
        errors, _warnings = self._run(["claim:blk_other:span_009"], ["blk_x:span_001"])
        assert "DSL_EDGE_DANGLING_EVIDENCE" in _codes(errors)


class TestGateLoadsWithoutTheCorePackage:
    def test_normalize_claim_ref_is_available_in_the_gate_module(self):
        """A層のテストが importlib でこのモジュールを直接ロードしても落ちない。"""
        assert normalize_claim_ref("claim:blk_x:span_001") == "blk_x:span_001"
        assert normalize_claim_ref("claim_span_001") == "claim_span_001"


class TestReconstructedEquationSeverity:
    """復元由来の式の入力リンク欠落を error（= failed_validation）にしない。

    ``can_be_used_in_derivation`` が真になると review_item → error に上がる規則は、
    PDF 復元式が導出に使えるようになった分だけ解析を落としてしまう。復元由来
    （``must_not_treat_as_source_extracted``）の式は人が見る列（review_item）に残す。
    """

    @staticmethod
    def _run(policy):
        gate = ExportValidationGate()
        errors: list = []
        review_items: list = []
        artifacts = {
            "equation_semantics": {
                "equations": [
                    {
                        "equation_id": "eq_7",
                        "equation_type": "result",
                        "input_equation_ids": [],
                        "output_equation_ids": [],
                        "link_status": "",
                        "confidence_policy": policy,
                    }
                ]
            }
        }
        gate._check_equation_link_integrity(artifacts, errors, review_items)
        return errors, review_items

    def test_extracted_and_usable_equation_is_still_an_error(self):
        errors, review_items = self._run(
            {"can_be_used_in_derivation": True, "must_not_treat_as_source_extracted": False}
        )
        assert "EQ_RESULT_RELATION_WITHOUT_INPUT" in _codes(errors)
        assert "EQ_RESULT_RELATION_WITHOUT_INPUT" not in _codes(review_items)

    def test_reconstructed_equation_stays_a_review_item(self):
        errors, review_items = self._run(
            {"can_be_used_in_derivation": True, "must_not_treat_as_source_extracted": True}
        )
        assert "EQ_RESULT_RELATION_WITHOUT_INPUT" not in _codes(errors)
        # 情報は落とさない（人が見る列に残す）。
        assert "EQ_RESULT_RELATION_WITHOUT_INPUT" in _codes(review_items)

    def test_unusable_equation_is_a_review_item_as_before(self):
        errors, review_items = self._run({"can_be_used_in_derivation": False})
        assert "EQ_RESULT_RELATION_WITHOUT_INPUT" not in _codes(errors)
        assert "EQ_RESULT_RELATION_WITHOUT_INPUT" in _codes(review_items)
