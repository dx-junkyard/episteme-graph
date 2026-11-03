"""第 15 周: 本文が触れない設定値の式（N = 15）を「この節で使う数式」に貼らない。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import course_content_builder as ccb  # noqa: E402


def _topic(latex="N = 15"):
    return {
        "content_blocks": [{"type": "equations", "items": [
            {"equation_id": "eq_n", "latex": latex, "plain_text": "N equals 15"},
        ]}],
        "linked_equation_ids": ["eq_n"],
    }


def test_unmentioned_parameter_value_is_not_appended():
    result = {"student_material": {"source_text": "TOV 方程式の残差を損失に加える。"}}
    ccb._ensure_required_equations_in_material(result, _topic())
    assert ccb.GENERATED_EQUATIONS_HEADING not in result["student_material"]["source_text"]


def test_mentioned_parameter_value_is_kept():
    result = {"student_material": {"source_text": "ネットワークは N 個の種から学習する。"}}
    ccb._ensure_required_equations_in_material(result, _topic())
    assert "![[equation:eq_n]]" in result["student_material"]["source_text"]


def test_real_equation_is_still_appended():
    result = {"student_material": {"source_text": "音速を定義する。"}}
    ccb._ensure_required_equations_in_material(result, _topic(r"c_s^2 = dP/d\\epsilon"))
    assert "![[equation:eq_n]]" in result["student_material"]["source_text"]
