"""Issue #283: only failed topic checks are stumble events."""

from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from routes import learning


def test_graph_element_explanation_never_records_a_stumble_event() -> None:
    source = inspect.getsource(learning._generate_graph_element_explanation)

    assert "record_student_stumble_event" not in source
    assert "clicked_explain" not in source
    assert "explanation_missing" not in source
    assert "generated_for_student" not in source


def test_failed_topic_check_remains_the_only_learning_route_writer() -> None:
    source = inspect.getsource(learning.check_topic_understanding)

    assert "if not passed:" in source
    assert source.count("record_student_stumble_event(") == 1
