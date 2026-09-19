"""Issue #283: 学習ルートからの `student_stumble_events` 記帳範囲。

Issue #283 の時点では「確認問題の不合格だけが記帳する」だった。是正 F1
（`docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md` §4 第1波 #1）で
確認問題から合否が消えたため、**学習ルートからの記帳経路はゼロになった**
（AI の判定を学習者の属性として、回答逐語つきで書く経路を残さない）。
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from routes import learning

_LEARNING_PY = Path(__file__).resolve().parents[1] / "api" / "routes" / "learning.py"


def test_graph_element_explanation_never_records_a_stumble_event() -> None:
    source = inspect.getsource(learning._generate_graph_element_explanation)

    assert "record_student_stumble_event" not in source
    assert "clicked_explain" not in source
    assert "explanation_missing" not in source
    assert "generated_for_student" not in source


def test_topic_check_no_longer_records_a_stumble_event() -> None:
    """確認問題は合否を出さないので、回答逐語つきの誤解記帳もしない（是正 F1）。"""
    source = inspect.getsource(learning.check_topic_understanding)

    assert "record_student_stumble_event" not in source
    assert "passed" not in source


def test_learning_route_module_has_no_stumble_writer_left() -> None:
    """学習ルート全体に記帳の呼び出し口が無いこと（唯一の呼び出し元が消えた）。"""
    src = _LEARNING_PY.read_text(encoding="utf-8")

    assert "record_student_stumble_event" not in src
