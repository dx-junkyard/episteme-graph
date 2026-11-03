"""第 25 周 第 5 周の前提スクリプト（runs/round25/r5_prereqs.py）の静的検査（HTTP なし）。"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROUND = Path(__file__).resolve().parents[1] / "runs" / "round25"


def _load():
    spec = importlib.util.spec_from_file_location("r5_prereqs", ROUND / "r5_prereqs.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["r5_prereqs"] = mod  # dataclass の注釈解決に要る
    spec.loader.exec_module(mod)
    return mod


def _doc_items() -> list[str]:
    text = (ROUND / "r5_prereqs.md").read_text(encoding="utf-8")
    section = text.split("## 周の前の一括実行", 1)[1]
    return re.findall(r"^\| `([a-z0-9_]+)` \|", section, re.MULTILINE)


def test_importable_and_items_match_doc():
    mod = _load()
    assert len(mod.ITEM_NAMES) == 13
    assert list(mod.ITEM_NAMES) == _doc_items()
    assert [i.name for i in mod.ITEMS] == list(mod.ITEM_NAMES)


def test_parser_and_grant_is_separate():
    mod = _load()
    a = mod.build_parser().parse_args(["--dry-run", "--only", "pinn_node1_ledger"])
    assert a.dry_run and a.only == ["pinn_node1_ledger"] and not a.grant_te02
    assert "grant" not in " ".join(mod.ITEM_NAMES)  # te-02 への共有は項目に入れない（--grant-te02 だけ）


def test_no_direct_db_or_docker():
    src = (ROUND / "r5_prereqs.py").read_text(encoding="utf-8")
    for bad in ("psycopg2", "sqlalchemy", "create_engine", "subprocess", "docker ", "sandbox_database_url"):
        assert bad not in src


def test_pure_helpers():
    mod = _load()
    mats = [{"document_id": "a", "title": "Neutron Star Equation of State via Physics Informed Neural Network",
             "filename": "x.tar.gz"},
            {"document_id": "b", "title": "Neutron Star Equation of State via Physics Informed Neural Network",
             "filename": "x.pdf"}]
    v = mod.split_versions(mats, "Neutron Star Equation of State")
    assert [m["document_id"] for m in v["tex"]] == ["a"] and [m["document_id"] for m in v["pdf"]] == ["b"]
    nodes = mod.graph_nodes({"graph": {"nodes": [{"id": "d1", "graph_layer": "equation_detail", "label": "x"},
                                                 {"id": "m1", "component_id": "u1", "label": "Theory basis"}]}})
    assert nodes[0]["id"] == "u1"
    pend = mod.accepted_unapplied([{"decision": {"status": "accepted", "applied_version": ""}},
                                   {"decision": {"status": "accepted", "applied_version": "2026.2"}},
                                   {"decision": {"status": "dismissed"}}])
    assert len(pend) == 1
    fp = mod.topic_fingerprint({"id": "t16", "title": "T", "student_material": {"content": "a\n===\nb"}})
    assert fp["traces"] and "content" not in fp
