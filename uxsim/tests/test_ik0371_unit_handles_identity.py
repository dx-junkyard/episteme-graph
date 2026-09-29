"""IK-0371: uxsim の登録変換は admin.js::cbDraftUnitHandles と同じく参照キーを運ぶ。"""
from __future__ import annotations

from uxsim.runner.actions_exec import _draft_to_course_create, _unit_handles
from uxsim.runner.state import PersonaSession


def test_strings_pick_up_keys_from_the_draft_table_and_dicts_pass_through():
    draft = {
        "title": "T",
        "unit_candidate_keys": {"U1": "k1:a", "U3": "k1:c"},
        "chapters": [{"title": "c", "topics": [
            {"title": "a", "units": ["U3", "U9", {"handle": "U2", "stable_key": "k1:b"}, "u3"]},
        ]}],
    }
    s = PersonaSession("st-test")
    s.remember("materials", "m1")
    payload = _draft_to_course_create(draft, s)
    assert payload["topics"][0]["units"] == [
        {"handle": "U3", "stable_key": "k1:c"},
        "U9",
        {"handle": "U2", "stable_key": "k1:b"},
    ]


def test_without_a_table_handles_stay_strings():
    assert _unit_handles({"units": ["U1", {"handle": "U2"}]}) == ["U1", "U2"]
    assert _unit_handles({"units": "U1"}) == []
