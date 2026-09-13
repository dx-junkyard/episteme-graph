"""参照の健全性の core（知識の転用層 P4-3 / ``knowledge_transfer_design.md`` §6）。

固定するのは:

1. 破断が無ければ ``ok``・あれば ``broken``・材料が無ければ ``unchecked``。
2. 4 検査（グラフノード→主張 / コンポーネント→主張・式 / 主張→チャンク / 学ぶ単位→両者）。
3. 参照キーは DB UUID / agent 側 ID / ``source_scope.legacy_ids`` のいずれでも引ける
   （``_resolve_claim_reference_index`` と同じ流儀）。
4. **facts に数字を書かない**（T-3）。
5. **from_label に内部 ID を出さない**（ラベルが無ければ空文字。ID で埋めない）。
6. ``debug`` 層のノードは検査対象外。
7. DB 読み部は live ビューだけを SELECT し、**書き込まない**（fail-soft で unchecked）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for path in (str(BACKEND), os.path.join(str(BACKEND), "api"), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.reference_health import (  # noqa: E402
    FACT_NO_MATERIAL,
    FACT_NOT_CHECKED,
    FACT_OK,
    KIND_FACTS,
    STATUS_BROKEN,
    STATUS_OK,
    STATUS_UNCHECKED,
    build_reference_health,
    check_document_references,
)

CLAIM_UUID = "11111111-1111-4111-8111-111111111111"
COMP_UUID = "22222222-2222-4222-8222-222222222222"


def _claim(**over):
    row = {
        "id": CLAIM_UUID,
        "agent_claim_id": "span_0001",
        "source_scope": {"legacy_ids": ["blk_3:span_0001"]},
        "chunk_id": "33333333-3333-4333-8333-333333333333",
        "text": "観測量は温度に比例する。",
    }
    row.update(over)
    return row


def _component(**over):
    row = {
        "id": COMP_UUID,
        "agent_component_id": "comp_003",
        "name": "温度較正",
        "source_scope": {"legacy_ids": ["comp_003"]},
        "evidence_claims": [CLAIM_UUID],
        "linked_claim_ids": ["span_0001"],
        "linked_equation_ids": ["eq_2_7"],
    }
    row.update(over)
    return row


def _equation(**over):
    row = {"id": "44444444-4444-4444-8444-444444444444", "agent_equation_id": "eq_2_7", "label": "(7)"}
    row.update(over)
    return row


def _unit(**over):
    row = {
        "id": "55555555-5555-4555-8555-555555555555",
        "label": "温度較正の手順",
        "linked_claim_ids": [CLAIM_UUID],
        "linked_component_ids": [COMP_UUID],
    }
    row.update(over)
    return row


def _node(**over):
    node = {
        "component_id": "theory_op_0001",
        "label": "Theory basis",
        "graph_layer": "main",
        "linked_claim_ids": [CLAIM_UUID],
    }
    node.update(over)
    return node


class TestHealthyDocument:
    def test_all_references_resolve_is_ok(self):
        result = build_reference_health(
            claims=[_claim()],
            components=[_component()],
            equations=[_equation()],
            units=[_unit()],
            graph_nodes=[_node()],
        )
        assert result["status"] == STATUS_OK
        assert result["facts"] == [FACT_OK]
        assert result["details"] == {}
        assert result["checked_at"]

    def test_agent_ids_and_legacy_ids_resolve(self):
        """DB UUID でなく agent 側 ID / legacy_ids で参照していても切れ扱いにしない。"""
        result = build_reference_health(
            claims=[_claim()],
            components=[_component(evidence_claims=["blk_3:span_0001"], linked_claim_ids=[], linked_equation_ids=[])],
            graph_nodes=[_node(linked_claim_ids=["span_0001"])],
        )
        assert result["status"] == STATUS_OK

    def test_debug_layer_nodes_are_not_checked(self):
        result = build_reference_health(
            claims=[_claim()],
            graph_nodes=[_node(graph_layer="debug", linked_claim_ids=["missing_claim_id"])],
        )
        assert result["status"] == STATUS_OK

    def test_equation_detail_layer_is_checked(self):
        result = build_reference_health(
            claims=[_claim()],
            graph_nodes=[_node(graph_layer="equation_detail", linked_claim_ids=["ghost"])],
        )
        assert result["status"] == STATUS_BROKEN
        assert [e["ref"] for e in result["details"]["graph_node_claim"]] == ["ghost"]


class TestBrokenReferences:
    def test_graph_node_pointing_at_a_missing_claim(self):
        result = build_reference_health(claims=[], graph_nodes=[_node()])
        assert result["status"] == STATUS_BROKEN
        assert KIND_FACTS["graph_node_claim"] in result["facts"]
        assert result["details"]["graph_node_claim"] == [
            {"ref": CLAIM_UUID, "from_label": "Theory basis"}
        ]

    def test_component_claim_and_equation_breaks(self):
        result = build_reference_health(claims=[], components=[_component()], equations=[])
        assert result["status"] == STATUS_BROKEN
        refs = {e["ref"] for e in result["details"]["component_claim"]}
        assert refs == {CLAIM_UUID, "span_0001"}
        assert result["details"]["component_equation"] == [
            {"ref": "eq_2_7", "from_label": "温度較正"}
        ]

    def test_claim_without_chunk(self):
        result = build_reference_health(claims=[_claim(chunk_id=None)])
        assert result["status"] == STATUS_BROKEN
        assert KIND_FACTS["claim_without_chunk"] in result["facts"]
        entry = result["details"]["claim_without_chunk"][0]
        assert entry["ref"] == CLAIM_UUID
        assert entry["from_label"] == "観測量は温度に比例する。"

    def test_learning_unit_breaks(self):
        result = build_reference_health(
            claims=[], components=[], units=[_unit()]
        )
        assert result["status"] == STATUS_BROKEN
        assert result["details"]["unit_claim"][0]["from_label"] == "温度較正の手順"
        assert result["details"]["unit_component"][0]["ref"] == COMP_UUID

    def test_duplicate_broken_refs_are_deduplicated(self):
        node = _node(linked_claim_ids=["ghost", "ghost"])
        result = build_reference_health(claims=[], graph_nodes=[node, dict(node)])
        assert len(result["details"]["graph_node_claim"]) == 1

    def test_facts_are_ordered_and_not_repeated(self):
        result = build_reference_health(
            claims=[_claim(chunk_id=None)],
            components=[_component(evidence_claims=["ghost"], linked_claim_ids=[], linked_equation_ids=[])],
            graph_nodes=[_node(linked_claim_ids=["ghost"])],
        )
        assert result["facts"] == [
            KIND_FACTS["graph_node_claim"],
            KIND_FACTS["component_claim"],
            KIND_FACTS["claim_without_chunk"],
        ]


class TestNoNumbersAndNoInternalIds:
    def test_every_fact_is_free_of_digits(self):
        """T-3: 事実文に数字を書かない（件数を facts から読み取れないようにする）。"""
        for fact in list(KIND_FACTS.values()) + [FACT_OK, FACT_NO_MATERIAL, FACT_NOT_CHECKED]:
            assert not any(ch.isdigit() for ch in fact), fact

    def test_broken_result_facts_have_no_digits(self):
        result = build_reference_health(
            claims=[], components=[_component()], units=[_unit()], graph_nodes=[_node()]
        )
        for fact in result["facts"]:
            assert not any(ch.isdigit() for ch in fact), fact

    def test_from_label_never_falls_back_to_an_internal_id(self):
        """ラベルが無い / 裸の内部 ID のときは空文字（ID で埋めない）。"""
        result = build_reference_health(
            claims=[],
            components=[_component(name="comp_003")],
            graph_nodes=[_node(label="", description="")],
        )
        labels = {
            e["from_label"]
            for entries in result["details"].values()
            for e in entries
        }
        assert labels == {""}

    def test_uuid_label_is_not_displayed(self):
        result = build_reference_health(
            claims=[], graph_nodes=[_node(label=COMP_UUID)]
        )
        assert result["details"]["graph_node_claim"][0]["from_label"] == ""


class TestUnchecked:
    def test_no_material_is_unchecked(self):
        result = build_reference_health()
        assert result["status"] == STATUS_UNCHECKED
        assert result["facts"] == [FACT_NO_MATERIAL]
        assert result["details"] == {}

    def test_blank_document_id_is_unchecked(self):
        result = check_document_references(object(), "   ")
        assert result["status"] == STATUS_UNCHECKED
        assert result["facts"] == [FACT_NOT_CHECKED]


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """live ビューだけを答える最小フェイク（書き込みメソッドは持たない）。"""

    def __init__(self, *, by_table=None, fail=False):
        self.by_table = by_table or {}
        self.fail = fail
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if self.fail:
            raise RuntimeError("db down")
        for table, rows in self.by_table.items():
            if table in sql:
                return _FakeResult(rows)
        return _FakeResult([])


class TestDbLayer:
    def test_reads_live_views_only(self):
        session = FakeSession(
            by_table={
                "theory_claims_live": [_claim()],
                "theory_components_live": [_component()],
                "knowledge_equations": [_equation()],
                "learning_units_live": [_unit()],
                "theory_component_graphs": [({"nodes": [_node()]},)],
            }
        )
        result = check_document_references(session, "doc-uuid")
        assert result["status"] == STATUS_OK
        joined = "\n".join(session.statements)
        # KO5: 基表を FROM しない（live ビュー / 明示 superseded_at フィルタのみ）。
        assert "FROM theory_claims\n" not in joined
        assert "FROM theory_components\n" not in joined
        assert "superseded_at IS NULL" in joined

    def test_never_writes(self):
        session = FakeSession(by_table={"theory_claims_live": [_claim()]})
        check_document_references(session, "doc-uuid")
        joined = " ".join(session.statements).upper()
        for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
            assert verb not in joined

    def test_db_failure_degrades_to_unchecked(self):
        result = check_document_references(FakeSession(fail=True), "doc-uuid")
        assert result["status"] == STATUS_UNCHECKED
        assert result["facts"] == [FACT_NOT_CHECKED]

    def test_document_id_is_cast_to_uuid(self):
        session = FakeSession()
        check_document_references(session, "doc-uuid")
        for sql in session.statements:
            assert "CAST(:document_id AS uuid)" in sql
