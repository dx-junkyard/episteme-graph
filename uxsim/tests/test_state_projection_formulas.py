"""教材投影の [[FORMULA_N]] 解決（画面と同じ規則・引けないものは残す）。"""
from uxsim.runner.state import project_observation, resolve_formula_placeholders


def test_placeholders_resolved_from_formulas():
    text = "比 約 [[FORMULA_0]] と [[FORMULA_1]]"
    formulas = [{"latex": "B_{\\rm ord}/B_{\\rm turb} \\approx 2"}, {"id": "FORMULA_1", "latex": "B_{3D}"}]
    assert resolve_formula_placeholders(text, formulas) == "比 約 $B_{\\rm ord}/B_{\\rm turb} \\approx 2$ と $B_{3D}$"


def test_unresolvable_placeholder_is_left_visible():
    # 製品側が formulas を落としたまま本文に参照を残す欠陥（IK-0389）を投影で隠さない
    assert resolve_formula_placeholders("式 [[FORMULA_0]]", []) == "式 [[FORMULA_0]]"
    assert resolve_formula_placeholders("式 [[FORMULA_3]]", [{"latex": "x"}]) == "式 [[FORMULA_3]]"


def test_topic_open_projection_uses_formulas():
    body = {"topic_id": "t3", "chunks": [{"text": "強度 [[FORMULA_0]]", "formulas": [{"latex": "B=4\\mu G"}]}]}
    out = project_observation("learning.topic.open", 200, body)
    assert "$B=4\\mu G$" in out and "[[FORMULA_0]]" not in out


def test_landscape_projection_shows_provenance_per_placement():
    body = {"domains": [{"domain_name": "宇宙物理", "frozen_version": "1", "is_course_map": True, "facts": []}],
            "documents": [{"title": "P", "placements": [{"node_label": "中性子星", "perspective_label": "観測から",
                                                          "weight_label": "強い関連", "provenance_label": "AIによる推定（未確認）",
                                                          "reason": "r"}]}],
            "unplaced_documents": [{"title": "Q"}]}
    out = project_observation("learning.landscape.view", 200, body)
    assert "中性子星（観測から・強い関連・AIによる推定（未確認））" in out and "配置されていない論文: Q" in out
