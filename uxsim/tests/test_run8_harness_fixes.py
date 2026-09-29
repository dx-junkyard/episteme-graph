"""第 8 周で見つかったハーネス側の欠陥の是正: 材料に合うトピック / 全トピックの投影 / 確認問題の提示 / 任意材料 / persona_stop。"""
from uxsim.runner.actions_exec import _topic_by_hint
from uxsim.runner.scenario import ScenarioParams
from uxsim.runner.state import PersonaSession, project_observation


def _session():
    s = PersonaSession(persona_id="p")
    s.topics = {"t0": {"id": "t0", "title": "問題設定と観測データ", "chapter_index": 0},
                "t15": {"id": "t15", "title": "問題設定：音速で模型を見分ける", "chapter_index": 3}}
    s.topic_ids = list(s.topics)
    s.scratch["chapter_titles"] = ["第1章 Cep B", "第2章", "第3章", "第4章 動的暗黒エネルギーと修正重力の音速"]
    return s


def test_topic_by_hint_matches_title_or_chapter():
    s = _session()
    assert _topic_by_hint(s, ["暗黒エネルギー"]) == "t15"      # 章題で当たる
    assert _topic_by_hint(s, "音速、宇宙論") == "t15"           # 文字列は「、」区切り・題名で当たる
    assert _topic_by_hint(s, ["存在しない語"]) == ""
    assert _topic_by_hint(s, None) == ""


def test_course_open_projection_lists_every_topic_with_chapter_and_check_marker():
    body = {"master_course": {"title": "C", "chapters": [{"title": "第1章"}, {"title": "第2章"}],
                              "topics": [{"id": f"t{i}", "title": f"題{i}", "chapter_index": i // 6, "status": "locked",
                                          "check_questions": [{"question": "Q"}] if i == 9 else []} for i in range(12)]}}
    out = project_observation("learning.course.open", 200, body)
    assert "t11 題11" in out and "…ほか" not in out
    assert "第2章:" in out and "t9 題9 [locked]（確認問題あり）" in out


def test_optional_material_is_dropped_not_raised():
    r = ScenarioParams({"seed_questions": ["a"]}, persona={})
    args, exhausted = r.render_args({"message": "{{seed_questions | next}}", "topic_hint_terms": "{{topic_hint_terms}}"})
    assert args == {"message": "a"} and "topic_hint_terms" in exhausted


def test_check_take_picks_the_matching_question_and_self_check_maps_labels():
    from uxsim.runner.actions_exec import _SELF_CHECK_LABELS, execute
    from uxsim.runner.state import PersonaSession

    calls = []

    class _Client:
        def call(self, method, path, json=None, params=None, **kw):
            calls.append((method, path, json))
            return 200, {"ok": True}, 1

        def take_traces(self):
            return []

    s = PersonaSession(persona_id="p", course_id="c1", topic_id="t0")
    s.topics = {"t0": {"id": "t0", "check_questions": [{"question": "Q1?", "answer_requirements": ["a"]},
                                                        {"question": "Q2?", "answer_requirements": ["b"]}]}}
    execute("learning.check.take", {"answer": "x", "question": "Q2?"}, s, _Client())
    assert calls[-1][2]["check_question"]["question"] == "Q2?"
    execute("learning.check.self_check", {"self_check": "違っていた"}, s, _Client())
    assert calls[-1][2] == {"self_check": "disagreed"}
    assert _SELF_CHECK_LABELS["観点がおかしい"] == "verdict_wrong"


def test_campaign_pinned_course_wins_over_first_enrolled():
    from uxsim.runner.actions_exec import prefetch_courses
    from uxsim.runner.state import PersonaSession

    class _Client:
        def call(self, method, path, json=None, params=None, **kw):
            return 200, [{"id": "aaa", "title": "同名", "is_enrollable": False},
                         {"id": "bbb", "title": "同名", "is_enrollable": False}], 1

        def take_traces(self):
            return []

    s = PersonaSession(persona_id="p"); s.scratch["pinned_course_id"] = "bbb"
    prefetch_courses(s, _Client())
    assert s.course_id == "bbb"
    s2 = PersonaSession(persona_id="p")
    prefetch_courses(s2, _Client())
    assert s2.course_id == "aaa"


def test_cycle_anchor_maps_button_labels():
    from uxsim.runner.actions_exec import execute
    from uxsim.runner.state import PersonaSession

    calls = []

    class _Client:
        def call(self, method, path, json=None, params=None, **kw):
            calls.append(json); return 201, {"ok": True}, 1

        def take_traces(self):
            return []

    s = PersonaSession(persona_id="p", course_id="c1", topic_id="t0")
    execute("learning.cycle.anchor", {"quick_label": "気になる"}, s, _Client())
    assert calls[-1]["quick_label"] == "curious"


def test_source_chunk_open_resolves_by_source_number():
    from uxsim.runner.actions_exec import execute
    from uxsim.runner.state import PersonaSession

    calls = []

    class _Client:
        def call(self, method, path, json=None, params=None, **kw):
            calls.append(path); return 200, {"text": "x"}, 1

        def take_traces(self):
            return []

    s = PersonaSession(persona_id="p", course_id="c1", topic_id="t0")
    s.scratch["last_sources"] = {"9": "chunk-nine", "10": "chunk-ten"}
    execute("learning.source_chunk.open", {"chunk_id": "出典10"}, s, _Client())
    assert calls[-1].endswith("/source-chunk/chunk-ten")
