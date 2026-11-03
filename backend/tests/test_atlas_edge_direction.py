"""IK-1149（第 25 周 第 11 波）: 有向の関係（依存）の向きを教員が採用のときに選び、判断の行に記録する。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import atlas_lifecycle, label_vocab
from core.atlas_edges import schema
from core.atlas_edges import store

ROOT = Path(__file__).resolve().parents[2]
KEY = schema.build_edge_key("astro", "molecular_clouds", "planet_formation")


def test_edge_key_stays_undirected():
    assert KEY == schema.build_edge_key("astro", "planet_formation", "molecular_clouds")


def test_oriented_pair_accepts_only_the_two_endpoints():
    assert schema.oriented_pair(KEY, "planet_formation") == ("planet_formation", "molecular_clouds")
    assert schema.oriented_pair(KEY, "molecular_clouds") == ("molecular_clouds", "planet_formation")
    assert schema.oriented_pair(KEY, "other") is None
    assert schema.oriented_pair(KEY, "") is None


def test_decision_direction_prefers_recorded_and_says_when_unrecorded():
    chosen = {"from_node_id": "planet_formation", "to_node_id": "molecular_clouds"}
    assert schema.decision_direction(KEY, chosen) == ("planet_formation", "molecular_clouds", True)
    assert schema.decision_direction(KEY, {"from_node_id": "", "to_node_id": ""}) == (
        "molecular_clouds", "planet_formation", False,
    )
    assert schema.decision_direction(KEY, None)[2] is False


def test_decide_rejects_direction_outside_the_pair_and_on_non_accept():
    with pytest.raises(ValueError):
        store.decide(object(), edge_key=KEY, action="accept", actor_id="u", edge_kind="depends",
                     from_node_id="nope", record_audit=lambda **k: None)
    with pytest.raises(ValueError):
        store.decide(object(), edge_key=KEY, action="dismiss", actor_id="u", review_note="r",
                     from_node_id="planet_formation", record_audit=lambda **k: None)


def test_decision_row_carries_direction_columns():
    row = ("id", KEY, "accepted", "depends", "", "", None, None, None, None,
           "planet_formation", "molecular_clouds")
    d = store._decision_row_to_dict(row)
    assert d["from_node_id"] == "planet_formation" and d["to_node_id"] == "molecular_clouds"
    legacy = store._decision_row_to_dict(row[:10])
    assert legacy["from_node_id"] == "" and legacy["to_node_id"] == ""


def test_unrecorded_suffix_on_learner_and_freeze_phrases():
    line = label_vocab.atlas_panel_relations_line([("depends", "分子雲", "惑星形成", "", True)])
    assert label_vocab.EDGE_DIRECTION_UNRECORDED_SUFFIX in line
    assert label_vocab.EDGE_DIRECTION_UNRECORDED_SUFFIX not in label_vocab.atlas_panel_relations_line(
        [("depends", "分子雲", "惑星形成", "", False)]
    )
    # 向きの無い種別には添えない
    assert label_vocab.EDGE_DIRECTION_UNRECORDED_SUFFIX not in label_vocab.atlas_panel_relations_line(
        [("related", "a", "b", "", True)]
    )
    assert label_vocab.EDGE_DIRECTION_UNRECORDED_SUFFIX in label_vocab.atlas_l2_directed_relations_line(
        [("depends", "a", "b", True)]
    )
    assert label_vocab.EDGE_DIRECTION_UNRECORDED_SUFFIX in atlas_lifecycle.edge_change_label(
        "a", "b", "depends", direction_unrecorded=True
    )


class _E:
    def __init__(self, a, b, kind="depends"):
        self.from_id, self.to_id, self.kind = a, b, kind


class _S:
    def __init__(self, edges):
        self.edges = edges
        self.regions = []


def test_freeze_impact_lists_redirected_edges():
    changes = atlas_lifecycle.edge_changes(_S([_E("b", "a")]), _S([_E("a", "b")]))
    assert changes["added_edges"] == [] and changes["removed_edges"] == []
    assert changes["redirected_edges"] == ["b — a（依存: 「b」が「a」に依存する）"]


def test_admin_ui_has_direction_choice_and_mirrors_phrase():
    js = (ROOT / "frontend/public/js/admin.js").read_text(encoding="utf-8")
    assert 'data-ui-anchor="atlas.edge-direction"' in js
    assert f'var EDGE_DIRECTION_PHRASE_DEPENDS = "{label_vocab.EDGE_DIRECTION_PHRASE_DEPENDS}";' in js
    assert "body.from_node_id = fromNode" in js


def test_migration_096_is_additive():
    sql = (ROOT / "backend/db/096_atlas_edge_decision_direction.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS from_node_id" in sql
    assert "ADD COLUMN IF NOT EXISTS to_node_id" in sql
    assert "DELETE" not in sql.upper().replace("DELETE は", "")
