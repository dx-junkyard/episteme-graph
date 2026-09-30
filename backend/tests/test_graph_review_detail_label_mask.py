"""第 14 周: 式の詳細層ノードのラベルに内部 ID（eq_…）を出さない（PL7 / #308・読み時のみ）。"""

import sys
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
for _path in (str(BACKEND), str(BACKEND / "api"), str(BACKEND.parent / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from routes import theory_components as tc


RECORDS = {"eq_6": {"equation_id": "eq_6", "label": "(12)"}, "eq_blk_004_0084": {"equation_id": "eq_blk_004_0084"}}


def test_printed_number_replaces_equation_id():
    assert tc.mask_equation_ids_in_label("Derive result eq_6", RECORDS) == "Derive result 式 (12)"


def test_unnumbered_equation_is_not_shown_by_id():
    out = tc.mask_equation_ids_in_label("Define eq_blk_004_0084", RECORDS)
    assert "eq_" not in out and "番号なしの式" in out


def test_unknown_id_is_also_masked():
    out = tc.mask_equation_ids_in_label("Apply constraint eq_eqcand_inline_blk_3df32664_151_7dbcd66d", {})
    assert "eq_" not in out


def test_graph_nodes_are_masked_without_touching_ids():
    graph = {"nodes": [{"component_id": "eq_op_1", "label": "Derive result eq_6", "display_label": "", "visual_label": "Define eq_6"}]}
    with patch.object(tc, "document_run_artifacts", return_value={"equation_semantics": {"records": list(RECORDS.values())}}):
        tc._mask_node_equation_ids("d", graph)
    node = graph["nodes"][0]
    assert node["component_id"] == "eq_op_1"
    assert node["label"] == "Derive result 式 (12)"
    assert node["visual_label"] == "Define 式 (12)"
