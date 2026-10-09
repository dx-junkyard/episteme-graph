"""第 23 周の選別で見つかったハーネス側の偽所見の源（uxsim/runs/round23/harness_fixes.md 末尾の 7 件）。

外部呼び出しなし（製品は httpx.MockTransport）。
"""
from __future__ import annotations

import json

import httpx

from uxsim.runner import vision5_exec
from uxsim.runner.actions_exec import execute
from uxsim.runner.client import EpistemeClient
from uxsim.runner.digest import STRUCTURE_PERSONAL_KEYS
from uxsim.runner.state import (COURSE_GROUP_ENROLLABLE, COURSE_GROUP_MINE, CONTINUES_MARK, OBSERVATION_CHARS,
                                PERSONAL_MAP_EMPTY, PersonaSession, project_observation)


def _client(handler, seen):
    def wrap(request):
        seen.append(request)
        return handler(request)
    return EpistemeClient("http://sandbox.test", transport=httpx.MockTransport(wrap))


def _body(r):
    return json.loads(r.content.decode("utf-8")) if r.content else {}


def test_ask_selection_prefers_brain_text_and_uses_ref_only_for_segment():
    s = PersonaSession(persona_id="st", course_id="c1", topic_id="t1")
    s.material = {"chunks": [{"text": "前のトピックの要約です。次の話題へ進みます。"},
                             {"text": "事前分布は平坦にとる。尤度は正規分布で近似する。"}]}
    seen = []
    c = _client(lambda r: httpx.Response(200, json={"answer": "回答"}), seen)
    execute("learning.chat.ask_selection", {"message": "これは？", "selection_text": "事前分布は平坦にとる",
                                            "selection_ref": "0:1"}, s, c)
    body = _body(seen[-1])
    assert body["selection_text"] == "事前分布は平坦にとる"  # ref の文で上書きしない
    assert body["selection_segment_id"] == 1  # ref の区画に無い文なので本文の一致から引く
    assert any(n.startswith("runner_choice:") for n in s.scratch["runner_notes"])
    execute("learning.chat.ask_selection", {"message": "これは？", "selection_ref": "1:2"}, s, c)
    body = _body(seen[-1])
    assert body["selection_text"] == "尤度は正規分布で近似する。" and body["selection_segment_id"] == 1


def test_source_chunk_projection_is_not_cut_and_marks_continuation():
    text = "The lensing signal in the left panel of Figure 3 shows the excess. " * 5
    obs = project_observation("learning.source_chunk.open", 200, {"section": "3 Results", "text": text})
    assert obs.startswith("3 Results") and "left panel of Figure 3 shows the excess." in obs.splitlines()[-1]
    assert CONTINUES_MARK not in obs
    long = project_observation("learning.source_chunk.open", 200, {"text": "x" * (OBSERVATION_CHARS * 2)})
    assert long.endswith(CONTINUES_MARK) and len(long) <= OBSERVATION_CHARS


def test_personal_map_empty_state_and_course_list_headings():
    obs = project_observation("learning.personal_network.mine", 200, {"nodes": [], "edges": []})
    for tab, msg in PERSONAL_MAP_EMPTY:
        assert f"{tab}: {msg}" in obs
    courses = [{"id": "c1", "title": "受講中のコース", "is_enrollable": False},
               {"id": "c2", "title": "まだのコース", "is_enrollable": True}]
    obs = project_observation("learning.course.list", 200, courses)
    lines = obs.splitlines()
    assert lines.index(f"{COURSE_GROUP_MINE}:") < lines.index("- 受講中のコース id=c1")
    assert lines.index(f"{COURSE_GROUP_ENROLLABLE}:") < lines.index("- まだのコース id=c2")
    assert "（受講可能）" not in obs


def test_status_label_is_a_personal_key():
    assert "status_label" in STRUCTURE_PERSONAL_KEYS


def test_rejected_row_shows_screen_fact_not_identifier():
    s = PersonaSession(persona_id="te", role="TEACHER", course_id="c9")
    s.scratch["release_landscape"] = {"documents": [{"placements": [{"id": "p1", "node_label": "一",
                                                                      "status": "rejected"}]}]}
    seen = []
    traces = execute("admin.release_review.row_status", {"status": "却下", "placement_hint": "1"}, s,
                     _client(lambda r: httpx.Response(200, json={}), seen))
    assert not seen and traces[-1].error == "precondition:placement_row_rejected"
    obs = project_observation("admin.release_review.row_status", s.last_status, s.last_body)
    assert obs == vision5_exec.RR_ROW_REJECTED and "placement_row_rejected" not in obs


def test_course_open_projects_return_door_when_not_empty():
    s = PersonaSession(persona_id="st", course_id="c1")
    course = {"title": "宇宙論", "topics": [{"id": "t1", "title": "導入", "status": "in_progress"}]}
    door = {"leave_note": {"text": "明日は式 (3) から"}, "carryover": None, "last_tension": None}
    seen = []

    def handler(r):
        return httpx.Response(200, json=door if r.url.path.endswith("/return-door") else course)
    execute("learning.course.open", {"course_id": "c1"}, s, _client(handler, seen))
    assert seen[-1].url.path == "/api/learning/courses/c1/cycle/return-door"
    obs = project_observation("learning.course.open", s.last_status, s.last_body)
    assert obs.startswith("帰還の扉") and "あなたの書き置き: 明日は式 (3) から" in obs and "コース: 宇宙論" in obs
    s2 = PersonaSession(persona_id="st", course_id="c1")
    execute("learning.course.open", {"course_id": "c1"}, s2,
            _client(lambda r: httpx.Response(200, json={"empty": True} if r.url.path.endswith("/return-door")
                                             else course), []))
    assert "帰還の扉" not in project_observation("learning.course.open", s2.last_status, s2.last_body)


def test_screen_text_has_no_internal_names():
    for text in (vision5_exec.RR_NO_PENDING, vision5_exec.PRECISION_UI_ONLY, vision5_exec.RR_ROW_REJECTED):
        assert ".js" not in text and "acceptPlacements" not in text and "cycle_mode" not in text
