"""学ぶ単位の永続化（learning_units_design.md §5 / §5.1・LU2 / LU4）。

固定する契約:

①``persist_learning_units`` は DELETE を発行せず、``stable_key`` 一致で UUID を保ち、
  一致しない live 行を supersede する（Phase 1 と同じ規則）
②``review_status`` / ``teacher_notes``（人間の確定列）を再解析で上書きしない
③素材が 5 種別とも無いときは SQL を一切発行しない
④保存は run 単位の監査 1 行（``AUDIT_ENTITY_KNOWLEDGE_OBJECT``）に相乗りする
⑤``persist_components`` が子行に ``parent_agent_component_id`` を埋め、
  子の ``name`` / ``stable_key`` は変えない（P2-2）

DB も LLM も使わない（fake セッション）。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline import persistence  # noqa: E402
from core.knowledge_objects import stable_key as ko_keys  # noqa: E402
from core.schema import AUDIT_ENTITY_KNOWLEDGE_OBJECT  # noqa: E402
from tests.knowledge_object_fakes import FakeKnowledgeSession  # noqa: E402

DOC = "22222222-2222-2222-2222-222222222222"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _skeleton(label="Slow-roll assumption"):
    return types.SimpleNamespace(logical_blocks=[
        types.SimpleNamespace(
            block_id="lb_1", block_type="assumptions", label=label,
            section_ids=["sec2"], evidence_block_ids=["b1"],
            summary="The field rolls slowly.", reason="", confidence=0.9,
        ),
    ])


def _section_block_key(label="Slow-roll assumption"):
    return ko_keys.learning_unit_stable_key(
        DOC, "section_block", f"assumptions {label}", ["b1"]
    )


def _run_units(session=None, **kwargs):
    session = session or FakeKnowledgeSession(id_prefix="unit")
    params = {"skeleton": _skeleton()}
    params.update(kwargs)
    with patch.object(persistence, "_pg_session", return_value=session):
        summary = persistence.persist_learning_units(
            document_id=DOC, **params
        )
    return summary, session


def _unit_rows(session):
    return session.inserted_into("learning_units")


# ---------------------------------------------------------------------------
# ① DELETE しない / supersede で表す
# ---------------------------------------------------------------------------


def test_persist_learning_units_never_deletes():
    _summary, session = _run_units()
    assert not any("DELETE" in sql.upper() for sql in session.sql)


def test_new_units_are_inserted_with_their_kind_and_key():
    summary, session = _run_units(run_id="run-1")
    rows = _unit_rows(session)
    assert len(rows) == 1
    assert rows[0]["unit_kind"] == "section_block"
    assert rows[0]["stable_key"] == _section_block_key()
    assert rows[0]["agent_unit_id"] == "lb_1"
    assert summary["inserted"] == 1
    assert summary["units"] == 1


def test_matching_stable_key_keeps_the_same_uuid_and_supersedes_the_rest():
    session = FakeKnowledgeSession(live_rows=[
        {"id": "kept-uuid", "stable_key": _section_block_key(), "agent_id": "lb_1",
         "review_status": "confirmed", "teacher_notes": "keep me"},
        {"id": "stale-uuid", "stable_key": "k1:gone", "agent_id": "lb_old",
         "review_status": "candidate", "teacher_notes": ""},
    ])
    _summary, session = _run_units(session=session, run_id="run-2")
    assert _unit_rows(session) == []
    assert session.superseded == ["stale-uuid"]


# ---------------------------------------------------------------------------
# ② 人間の確定列を触らない（LU2）
# ---------------------------------------------------------------------------


def test_review_status_and_teacher_notes_are_preserved_on_match():
    session = FakeKnowledgeSession(live_rows=[
        {"id": "kept-uuid", "stable_key": _section_block_key(), "agent_id": "lb_1",
         "review_status": "confirmed", "teacher_notes": "keep me"},
    ])
    _summary, session = _run_units(session=session)
    updated = session.updated_in("learning_units")
    assert updated
    for values in updated:
        assert "review_status" not in values
        assert "teacher_notes" not in values


def test_content_columns_are_refreshed_on_match():
    session = FakeKnowledgeSession(live_rows=[
        {"id": "kept-uuid", "stable_key": _section_block_key(), "agent_id": "lb_1",
         "review_status": "candidate", "teacher_notes": ""},
    ])
    _summary, session = _run_units(session=session)
    values = session.updated_in("learning_units")[0]
    assert values["label"] == "Slow-roll assumption"
    assert session.json_of(values, "source_block_ids") == ["b1"]


def test_new_rows_start_as_candidates():
    _summary, session = _run_units()
    assert _unit_rows(session)[0]["review_status"] == "candidate"


# ---------------------------------------------------------------------------
# ③ 素材ゼロでは何もしない
# ---------------------------------------------------------------------------


def test_no_material_at_all_issues_no_sql():
    session = FakeKnowledgeSession()
    with patch.object(persistence, "_pg_session", return_value=session):
        summary = persistence.persist_learning_units(document_id=DOC)
    assert session.sql == []
    assert summary["units"] == 0
    assert summary["skipped_kinds"] == [
        "section_block", "thesis_support", "parent_component", "dsl_node", "figure",
    ]


def test_partially_missing_material_is_reported_honestly():
    summary, _session = _run_units()
    assert summary["skipped_kinds"] == [
        "thesis_support", "parent_component", "dsl_node", "figure",
    ]


# ---------------------------------------------------------------------------
# ④ 監査（新 entity_type を作らない）
# ---------------------------------------------------------------------------


def test_audit_reuses_the_knowledge_object_entity_type():
    _summary, session = _run_units(run_id="run-3")
    events = session.inserted_into("theory_review_events")
    assert len(events) == 1
    assert events[0]["entity_type"] == AUDIT_ENTITY_KNOWLEDGE_OBJECT
    metadata = json.loads(events[0]["metadata"])
    assert metadata["learning_units"]["inserted"] == 1
    assert metadata["run_id"] == "run-3"


def test_audit_metadata_has_no_body_text():
    _summary, session = _run_units()
    metadata = session.inserted_into("theory_review_events")[0]["metadata"]
    assert "Slow-roll" not in metadata


# ---------------------------------------------------------------------------
# ⑤ P2-2: 子行の親参照
# ---------------------------------------------------------------------------


def _component(component_id, label, **extra):
    data = {
        "component_id": component_id, "component_type": "theory", "label": label,
        "summary": "", "inputs": [], "outputs": [], "preconditions": [], "cautions": [],
        "dependencies": [], "evidence_refs": {}, "reason": "", "confidence": 0.5,
        "review_notes": [], "linked_claim_ids": [], "linked_evidence_ids": [],
        "operation": "linearize", "maturity_source": "llm_proposed",
    }
    data.update(extra)
    return types.SimpleNamespace(**data)


def _component_result_with_split():
    return types.SimpleNamespace(
        components=[
            _component("cmp_a", "Linearize: Bias correction"),
            _component("cmp_solo", "Noise model", operation="define"),
        ],
        refinement_report={"split_actions": [
            {"parent_component_id": "cmp_parent", "parent_label": "Bias correction",
             "child_component_ids": ["cmp_a"]},
            {"parent_component_id": "cmp_solo", "parent_label": "Noise model",
             "child_component_ids": ["cmp_solo"]},
        ]},
    )


def _run_components(session=None, component_result=None):
    session = session or FakeKnowledgeSession(id_prefix="cmp")
    with patch.object(persistence, "_pg_session", return_value=session):
        id_map = persistence.persist_components(
            document_id=DOC,
            component_result=component_result or _component_result_with_split(),
            run_id="run-4",
        )
    return id_map, session


def test_split_children_carry_the_parent_agent_id():
    _id_map, session = _run_components()
    rows = {r["name"]: r for r in session.inserted_into("theory_components")}
    assert rows["Linearize: Bias correction"]["parent_agent_component_id"] == "cmp_parent"


def test_unsplit_component_has_no_parent():
    """子 ID と親 ID が同じ組（分割されなかった原案）は親子関係を作らない。"""
    _id_map, session = _run_components()
    rows = {r["name"]: r for r in session.inserted_into("theory_components")}
    assert rows["Noise model"]["parent_agent_component_id"] is None


def test_parent_component_id_uuid_column_is_left_null():
    """v1 では原案を component 行にしないので UUID 列は書かない（設計書 §5.1）。"""
    _id_map, session = _run_components()
    for row in session.inserted_into("theory_components"):
        assert "parent_component_id" not in row


def test_child_name_and_stable_key_are_unchanged_by_the_parent_link():
    without_parent = types.SimpleNamespace(
        components=[_component("cmp_a", "Linearize: Bias correction")],
        refinement_report={},
    )
    _id_map, plain = _run_components(component_result=without_parent)
    _id_map2, linked = _run_components()
    plain_row = plain.inserted_into("theory_components")[0]
    linked_row = {
        r["name"]: r for r in linked.inserted_into("theory_components")
    }["Linearize: Bias correction"]
    assert plain_row["name"] == linked_row["name"]
    assert plain_row["stable_key"] == linked_row["stable_key"]


def test_parent_index_ignores_malformed_actions():
    result = types.SimpleNamespace(
        components=[_component("cmp_a", "Child")],
        refinement_report={"split_actions": [
            "not-a-dict",
            {"parent_component_id": "", "child_component_ids": ["cmp_a"]},
        ]},
    )
    _id_map, session = _run_components(component_result=result)
    assert session.inserted_into("theory_components")[0]["parent_agent_component_id"] is None
