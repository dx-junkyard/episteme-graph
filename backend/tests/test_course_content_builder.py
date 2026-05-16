"""Tests for course content builder helpers."""

from __future__ import annotations


def test_course_json_dumps_strips_nul_characters():
    from core.course_content_builder import _json_dumps

    dumped = _json_dumps({
        "title": "test",
        "topics": [
            {
                "spoken_script": "before\x00after",
                "content_blocks": [{"latex": r"$R_{\\" + "\x00" + "Lambda}$"}],
                "literal_escape": r"bad\u0000escape",
            }
        ],
    })

    assert "\x00" not in dumped
    assert "\\u0000" not in dumped
    assert "beforeafter" in dumped
