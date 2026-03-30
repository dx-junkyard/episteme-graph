"""Issue #36 で追加されたAPIスキーマの単体テスト。

Pydanticモデルのバリデーション・デフォルト値・シリアライズを検証する。
外部 API は一切呼び出さない。
"""

from __future__ import annotations

import pytest

from api.schemas import (
    ApproveWithScopeRequest,
    ReextractionJobOut,
    SchemaProposalItemOut,
    SchemaProposalOut,
    SchemaTypeCreateRequest,
    SchemaTypeOut,
    SimulationDocResult,
    SimulationResponse,
    SimulationResults,
    SimulationStats,
)


class TestSchemaTypeOut:
    """SchemaTypeOut モデルのテスト。"""

    def test_defaults(self):
        obj = SchemaTypeOut(id="Agent", label="Agent")
        assert obj.description == ""
        assert obj.is_builtin is False

    def test_full_construction(self):
        obj = SchemaTypeOut(
            id="MathematicalObject",
            label="MathematicalObject",
            description="テンソル、群、多様体",
            is_builtin=True,
        )
        assert obj.id == "MathematicalObject"
        assert obj.is_builtin is True

    def test_model_dump(self):
        obj = SchemaTypeOut(id="X", label="X", description="desc", is_builtin=False)
        d = obj.model_dump()
        assert set(d.keys()) == {"id", "label", "description", "is_builtin"}


class TestSchemaProposalItemOut:
    """SchemaProposalItemOut モデルのテスト。"""

    def test_ontology_type_item(self):
        obj = SchemaProposalItemOut(
            id="item-1",
            item_type="ontology_type",
            key="Experiment",
            label="Experiment",
            description="実験",
        )
        assert obj.item_type == "ontology_type"
        assert obj.key == "Experiment"

    def test_predicate_item(self):
        obj = SchemaProposalItemOut(
            id="item-2",
            item_type="predicate",
            key="PROVED_BY",
            label="PROVED_BY",
        )
        assert obj.description == ""

    def test_model_dump(self):
        obj = SchemaProposalItemOut(
            id="i", item_type="predicate", key="K", label="L",
        )
        d = obj.model_dump()
        assert "id" in d and "item_type" in d and "key" in d and "label" in d


class TestSchemaProposalOut:
    """SchemaProposalOut モデルのテスト。"""

    def test_defaults(self):
        obj = SchemaProposalOut(proposal_id="p-1")
        assert obj.status == "pending"
        assert obj.summary == ""
        assert obj.reasoning == ""
        assert obj.source_query_count == 0
        assert obj.items == []
        assert obj.created_at == ""
        assert obj.reviewed_at == ""

    def test_with_items(self):
        obj = SchemaProposalOut(
            proposal_id="p-1",
            status="approved",
            summary="テスト提案",
            items=[
                SchemaProposalItemOut(
                    id="i-1", item_type="ontology_type",
                    key="Exp", label="Exp", description="実験",
                ),
            ],
            source_query_count=42,
        )
        assert obj.status == "approved"
        assert len(obj.items) == 1
        assert obj.source_query_count == 42


class TestReextractionJobOut:
    """ReextractionJobOut モデルのテスト。"""

    def test_defaults(self):
        obj = ReextractionJobOut(job_id="j-1")
        assert obj.proposal_id == ""
        assert obj.status == "pending"
        assert obj.total_docs == 0
        assert obj.processed_docs == 0
        assert obj.error_message is None
        assert obj.started_at == ""
        assert obj.completed_at == ""

    def test_full_construction(self):
        obj = ReextractionJobOut(
            job_id="j-1",
            proposal_id="p-1",
            status="completed",
            total_docs=10,
            processed_docs=10,
            error_message=None,
            started_at="2026-03-28T10:00:00",
            completed_at="2026-03-28T10:30:00",
            created_at="2026-03-28T09:00:00",
        )
        assert obj.status == "completed"
        assert obj.total_docs == obj.processed_docs

    def test_with_error_message(self):
        obj = ReextractionJobOut(
            job_id="j-2",
            status="failed",
            error_message="doc-123: extraction failed",
        )
        assert obj.error_message is not None
        assert "doc-123" in obj.error_message


class TestSchemaTypeCreateRequest:
    """SchemaTypeCreateRequest モデルのテスト。"""

    def test_minimal_construction(self):
        obj = SchemaTypeCreateRequest(id="NewType", label="NewType")
        assert obj.description == ""

    def test_full_construction(self):
        obj = SchemaTypeCreateRequest(
            id="Experiment",
            label="Experiment",
            description="実験手法",
        )
        assert obj.id == "Experiment"
        assert obj.description == "実験手法"


# ---------------------------------------------------------------------------
# Shadow Testing / Simulation (Issue #45)
# ---------------------------------------------------------------------------


class TestSimulationDocResult:
    """SimulationDocResult モデルのテスト。"""

    def test_defaults(self):
        obj = SimulationDocResult(doc_id="doc-1")
        assert obj.title == ""
        assert obj.added_concepts == []
        assert obj.removed_concepts == []
        assert obj.added_relations == []
        assert obj.removed_relations == []
        assert obj.reclassified_nodes == []
        assert obj.summary == ""

    def test_full_construction(self):
        obj = SimulationDocResult(
            doc_id="doc-1",
            title="Test Paper",
            added_concepts=[{"name": "X", "type": "Experiment"}],
            removed_concepts=[{"name": "Y", "type": "Agent"}],
            added_relations=[{"source": "A", "target": "B", "predicate": "CAUSES"}],
            removed_relations=[],
            reclassified_nodes=[{"name": "Z", "old_type": "Event", "new_type": "Experiment"}],
            summary="実験カテゴリが追加",
        )
        assert len(obj.added_concepts) == 1
        assert len(obj.reclassified_nodes) == 1
        assert obj.summary == "実験カテゴリが追加"


class TestSimulationStats:
    """SimulationStats モデルのテスト。"""

    def test_defaults(self):
        obj = SimulationStats()
        assert obj.target_doc_count == 0
        assert obj.total_added_concepts == 0
        assert obj.total_reclassified_nodes == 0

    def test_with_values(self):
        obj = SimulationStats(
            target_doc_count=3,
            similar_doc_count=2,
            control_doc_count=1,
            total_added_concepts=5,
            total_removed_concepts=2,
            total_reclassified_nodes=1,
        )
        assert obj.target_doc_count == 3
        assert obj.total_added_concepts == 5


class TestSimulationResults:
    """SimulationResults モデルのテスト。"""

    def test_defaults(self):
        obj = SimulationResults()
        assert obj.target == []
        assert obj.similar == []
        assert obj.control == []

    def test_with_docs(self):
        obj = SimulationResults(
            target=[SimulationDocResult(doc_id="d1", title="Paper A")],
            similar=[SimulationDocResult(doc_id="d2")],
            control=[],
        )
        assert len(obj.target) == 1
        assert len(obj.similar) == 1
        assert obj.target[0].title == "Paper A"


class TestSimulationResponse:
    """SimulationResponse モデルのテスト。"""

    def test_defaults(self):
        obj = SimulationResponse(proposal_id="p1")
        assert obj.proposal_summary == ""
        assert obj.proposal_items == []
        assert obj.results.target == []
        assert obj.stats.target_doc_count == 0

    def test_full_construction(self):
        obj = SimulationResponse(
            proposal_id="p1",
            proposal_summary="テスト提案",
            proposal_items=[
                SchemaProposalItemOut(
                    id="i1", item_type="ontology_type",
                    key="Exp", label="Exp", description="実験",
                ),
            ],
            results=SimulationResults(
                target=[SimulationDocResult(doc_id="d1", title="Paper")],
            ),
            stats=SimulationStats(target_doc_count=1, total_added_concepts=2),
        )
        assert obj.proposal_id == "p1"
        assert len(obj.proposal_items) == 1
        assert len(obj.results.target) == 1
        assert obj.stats.total_added_concepts == 2

    def test_model_dump_structure(self):
        obj = SimulationResponse(proposal_id="p1")
        d = obj.model_dump()
        assert "proposal_id" in d
        assert "results" in d
        assert "stats" in d
        assert "target" in d["results"]
        assert "similar" in d["results"]
        assert "control" in d["results"]


class TestApproveWithScopeRequest:
    """ApproveWithScopeRequest モデルのテスト。"""

    def test_defaults(self):
        obj = ApproveWithScopeRequest()
        assert obj.scope == "full"
        assert obj.course_ids == []

    def test_canary_scope(self):
        obj = ApproveWithScopeRequest(
            scope="canary",
            course_ids=["course-1", "course-2"],
        )
        assert obj.scope == "canary"
        assert len(obj.course_ids) == 2

    def test_model_dump(self):
        obj = ApproveWithScopeRequest(scope="full")
        d = obj.model_dump()
        assert d["scope"] == "full"
        assert d["course_ids"] == []
