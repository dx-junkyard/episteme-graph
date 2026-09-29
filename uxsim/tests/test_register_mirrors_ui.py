"""admin.course_builder.register は画面（admin.js::approveCourse）と同じ変換を通す（PE9）。

第 1 周（run 20260927T044913Z）で草案を素通しし、章 4・トピック 0 のコースが登録された回帰。
"""
from __future__ import annotations

from uxsim.runner.actions_exec import _draft_to_course_create
from uxsim.runner.state import PersonaSession


def _session() -> PersonaSession:
    s = PersonaSession("st-test")
    s.remember("materials", "m1")
    s.remember("materials", "m2")
    setattr(s, "cb_selected", ["m2"])
    setattr(s, "material_titles", {"m2": "論文 B"})
    return s


def test_nested_topics_are_flattened_like_admin_js():
    draft = {
        "title": "T",
        "chapters": [
            {"title": "第1章", "topics": [{"title": "a", "prerequisites": ["p1", {"name": "p2"}], "units": ["U1", {"handle": "U2"}, "U1"]}, "b"]},
            {"title": "第2章", "topics": [{"title": "c"}]},
        ],
        "concepts": ["赤方偏移", {"name": "距離尺度", "children": ["光度距離"]}],
    }
    payload = _draft_to_course_create(draft, _session())
    assert [c["title"] for c in payload["chapters"]] == ["第1章", "第2章"]
    assert [t["title"] for t in payload["topics"]] == ["a", "b", "c"]
    assert [t["chapter_index"] for t in payload["topics"]] == [0, 0, 1]
    assert payload["topics"][0]["id"] == "t0" and payload["topics"][0]["status"] == "in_progress"
    assert payload["topics"][1]["status"] == "locked"
    assert payload["topics"][0]["prerequisites"] == [{"name": "p1", "status": "not_started"}, {"name": "p2", "status": "not_started"}]
    assert payload["topics"][0]["units"] == ["U1", "U2"]
    assert payload["concepts"][1]["children"] == ["光度距離"]
    # sources は選んだ教材（画面の選択）から組む
    assert payload["sources"] == [{"title": "論文 B", "subtitle": "", "license": "", "used_section": "", "material_id": "m2"}]


def test_sources_fall_back_to_all_listed_then_draft():
    s = PersonaSession("st-test")
    s.remember("materials", "m1")
    payload = _draft_to_course_create({"title": "T", "chapters": []}, s)
    assert [x["material_id"] for x in payload["sources"]] == ["m1"]
    payload2 = _draft_to_course_create({"title": "T", "chapters": [], "sources": ["論文 A"]}, PersonaSession("st-test"))
    assert payload2["sources"][0]["title"] == "論文 A" and payload2["sources"][0]["material_id"] == ""
