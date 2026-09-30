"""第 15 周: 部品の文脈から同じ論文の依存先 component へ 1 hop で辿れること。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import component_context as cc  # noqa: E402

DB_A = "11111111-1111-4111-8111-111111111111"
DB_B = "22222222-2222-4222-8222-222222222222"
DOC = "33333333-3333-4333-8333-333333333333"


def _record(*refs):
    return {"dependencies": [{"dependency_type": "requires", "component_refs": list(refs)}]}


def test_resolved_dependency_is_navigable(monkeypatch):
    monkeypatch.setattr(
        cc, "_resolve_live_components",
        lambda doc, refs: {"comp_002": {"id": DB_B, "name": "Residual constraint"}},
    )
    items, all_resolved = cc._dependency_lane_items(DB_A, DOC, _record("comp_002"))
    assert all_resolved is True
    assert items == [{
        "id": DB_B, "element_type": "theory_component", "label": "Residual constraint",
        "relation_label": "を前提とする", "relation_status": "source_backed", "navigable": True,
    }]


def test_self_reference_is_skipped(monkeypatch):
    monkeypatch.setattr(cc, "_resolve_live_components", lambda doc, refs: {"comp_001": {"id": DB_A, "name": "x"}})
    items, _ = cc._dependency_lane_items(DB_A, DOC, _record("comp_001"))
    assert items == []


def test_resolve_failure_is_fail_soft(monkeypatch):
    def boom(doc, refs):
        raise RuntimeError("db")
    monkeypatch.setattr(cc, "_resolve_live_components", boom)
    assert cc._dependency_lane_items(DB_A, DOC, _record("comp_002")) == ([], False)


def _unresolved(label="関連する論理要素"):
    return {"id": None, "element_type": "theory_component", "label": label,
            "relation_label": "を前提とする", "relation_status": "source_backed", "navigable": False}


def test_merge_drops_unresolved_duplicates_only_when_all_resolved():
    extra = [{"id": DB_B, "element_type": "theory_component", "label": "R",
              "relation_label": "を前提とする", "relation_status": "source_backed", "navigable": True}]
    lane = [_unresolved(), _unresolved(), _unresolved()]
    merged = cc._merge_lane(lane, extra, drop_unresolved_components=True)
    assert merged == extra
    kept = cc._merge_lane(lane, extra, drop_unresolved_components=False)
    # 一部しか解けないときは残すが、同名の重複は1つに畳む。
    assert kept == extra + [_unresolved()]


def test_merge_keeps_claims_and_dedupes_same_id():
    claim = {"id": DB_A, "element_type": "theory_claim", "label": "c",
             "relation_label": "を根拠として持つ", "relation_status": "source_backed", "navigable": True}
    extra = [{"id": DB_B, "element_type": "theory_component", "label": "R",
              "relation_label": "を前提とする", "relation_status": "source_backed", "navigable": True}]
    w_item = dict(extra[0], label="R (W)")
    merged = cc._merge_lane([claim, w_item], extra, drop_unresolved_components=True)
    assert merged == extra + [claim]
