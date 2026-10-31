"""IK-0588 / IK-0570: 部品の文脈で、移った先から元へ戻れること・中心命題に宛先なし id を描かないこと。

- 分割の子 → 上位に親（「の構成要素である」）、親 → 下位に子（「を構成要素として含む」）。
- 依存先 → 上位に依存元（「に利用される」）。
- 中心命題・支持構造は ``id`` キーを持たず、レーンに1回だけ開幕画面への事実文。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import component_context as cc  # noqa: E402

DOC = "33333333-3333-4333-8333-333333333333"
PARENT = "11111111-1111-4111-8111-111111111111"
CHILD_A = "22222222-2222-4222-8222-222222222222"
CHILD_B = "44444444-4444-4444-8444-444444444444"

# 同じ論文の live 行（legacy_ids = agent ID、parent_agent_component_id = 親の agent ID）。
ROWS = [
    {"id": PARENT, "name": "TOV residual constraint", "legacy": "comp_001", "parent": None},
    {"id": CHILD_A, "name": "Residual definition", "legacy": "comp_001__op0", "parent": "comp_001"},
    {"id": CHILD_B, "name": "Residual limit", "legacy": "comp_001__op1", "parent": "comp_001"},
]
ARTIFACTS = {
    "component_assembly": {
        "components": [
            {"component_id": "comp_001"},
            {"component_id": "comp_001__op0"},
            {"component_id": "comp_001__op1",
             "dependencies": [{"dependency_type": "depends_on", "component_refs": ["comp_001__op0"]}]},
        ],
    }
}


def _row(db_id):
    row = next(r for r in ROWS if r["id"] == db_id)
    return {"id": row["id"], "document_id": DOC, "name": row["name"],
            "source_scope": {"legacy_ids": [row["legacy"]]},
            "parent_agent_component_id": row["parent"]}


def _install(monkeypatch, upper=None):
    def resolve(doc, refs):
        assert doc == DOC
        return {r["legacy"]: {"id": r["id"], "name": r["name"]} for r in ROWS if r["legacy"] in refs}

    def children(doc, agent_ids):
        return [{"id": r["id"], "name": r["name"]} for r in ROWS if r["parent"] in agent_ids]

    monkeypatch.setattr(cc, "_resolve_live_components", resolve)
    monkeypatch.setattr(cc, "_resolve_child_components", children)
    monkeypatch.setattr(
        cc.context_lens_mod, "build",
        lambda ref: {"focus": {"label": "焦点"}, "upper": list(upper or []), "lower": []},
    )


def _graph(db_id, upper=None):
    row = _row(db_id)
    record = next(
        (c for c in ARTIFACTS["component_assembly"]["components"]
         if c["component_id"] == row["source_scope"]["legacy_ids"][0]), {},
    )
    return cc._build_graph(
        db_id, DOC, record, component_row=row,
        agent_ids=cc._agent_id_candidates(row), artifacts=ARTIFACTS,
    )


def _navigable(lane, relation):
    return [e["id"] for e in lane if e.get("navigable") and e["relation_label"] == relation]


def test_child_shows_parent_upper_and_parent_lists_children(monkeypatch):
    _install(monkeypatch)
    child = _graph(CHILD_A)
    assert _navigable(child["upper"], "の構成要素である") == [PARENT]
    parent = _graph(PARENT)
    assert set(_navigable(parent["lower"], "を構成要素として含む")) == {CHILD_A, CHILD_B}


def test_round_trip_child_parent_child(monkeypatch):
    _install(monkeypatch)
    child = _graph(CHILD_A)
    up = next(e for e in child["upper"] if e["relation_label"] == "の構成要素である")
    back = _graph(up["id"])
    assert CHILD_A in _navigable(back["lower"], "を構成要素として含む")


def test_dependency_target_shows_dependent_upper(monkeypatch):
    _install(monkeypatch)
    # CHILD_B は CHILD_A を前提とする → CHILD_A の上位に CHILD_B（に利用される）、
    # CHILD_B の下位に CHILD_A（を前提とする）で往復できる。
    target = _graph(CHILD_A)
    assert _navigable(target["upper"], "に利用される") == [CHILD_B]
    dependent = _graph(CHILD_B)
    assert CHILD_A in _navigable(dependent["lower"], "を前提とする")


def test_unpersisted_split_parent_shows_label_without_id(monkeypatch):
    _install(monkeypatch)
    artifacts = {"component_assembly": {
        "components": [],
        "refinement_report": {"split_actions": [
            {"parent_component_id": "comp_009", "parent_label": "Original bundle",
             "child_component_ids": ["comp_009__op0"]}]},
    }}
    row = {"id": CHILD_A, "document_id": DOC, "source_scope": {"legacy_ids": ["comp_009__op0"]},
           "parent_agent_component_id": "comp_009"}
    graph = cc._build_graph(CHILD_A, DOC, {}, component_row=row,
                            agent_ids=cc._agent_id_candidates(row), artifacts=artifacts)
    parent = next(e for e in graph["upper"] if e["relation_label"] == "の構成要素である")
    assert parent["label"] == "Original bundle"
    assert "id" not in parent and parent["navigable"] is False


def test_thesis_items_have_no_id_key_and_one_exit(monkeypatch):
    thesis = [
        {"element_id": None, "element_type": "thesis", "label": f"支持構造「{n}」",
         "relation_label": "（中心命題）を支持する", "relation_status": "source_backed"}
        for n in ("A", "B", "C")
    ]
    _install(monkeypatch, upper=thesis)
    graph = _graph(CHILD_A, upper=thesis)
    thesis_items = [e for e in graph["upper"] if e["element_type"] == "thesis"]
    assert len(thesis_items) == 3
    for e in thesis_items:
        assert "id" not in e and e["navigable"] is False
    assert graph["exits"] == {"upper": cc.THESIS_EXIT_FACT}


def test_no_exit_without_thesis(monkeypatch):
    _install(monkeypatch)
    assert "exits" not in _graph(PARENT)


def test_lane_cap_holds(monkeypatch):
    many = [{"id": f"55555555-5555-4555-8555-{i:012d}", "name": f"c{i}"} for i in range(40)]
    _install(monkeypatch)
    monkeypatch.setattr(cc, "_resolve_child_components", lambda doc, ids: many)
    assert len(_graph(PARENT)["lower"]) <= cc.learner_context_common.LANE_MAX


def test_hierarchy_failure_is_fail_soft(monkeypatch):
    _install(monkeypatch)

    def boom(*_a):
        raise RuntimeError("db")

    monkeypatch.setattr(cc, "_resolve_live_components", boom)
    monkeypatch.setattr(cc, "_resolve_child_components", boom)
    graph = _graph(CHILD_A)
    assert graph["upper"] == [] and graph["lower"] == []


def test_relation_words_come_from_the_context_lens_vocabulary():
    labels = cc.context_lens_mod.RELATION_LABELS
    assert cc._PART_OF_RELATION_LABEL == labels["member_of"]
    assert cc._CONTAINS_RELATION_LABEL == labels["contains"]
    assert cc._USED_BY_RELATION_LABEL == labels["used_by_component"]


def test_learner_ui_renders_server_exit_fact():
    src = (Path(__file__).resolve().parents[2] / "frontend/public/js/app.js").read_text(encoding="utf-8")
    assert "function componentContextExitsHtml" in src
    assert "議論の開幕画面" not in src  # 文言はサーバの正本をそのまま描く
