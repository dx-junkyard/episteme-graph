from __future__ import annotations

import pytest

from uxsim.runner.scenario import (EXHAUSTED, ScenarioError, ScenarioParams, evaluate_until, load_params, load_scenario,
                                   parse_scenario)


def test_placeholders_and_next_iterator():
    p = ScenarioParams({"goal_text": "ゼミ発表", "topic_hint": "第 2 章", "seed_questions": ["Q1", "Q2"]})
    assert p.render("{{goal_text}} のために {{topic_hint}} を読む") == "ゼミ発表 のために 第 2 章 を読む"
    assert p.render({"message": "{{seed_questions | next}}"}) == {"message": "Q1"}
    assert p.render({"message": "{{ seed_questions|next }}"}) == {"message": "Q2"}
    assert p.render("{{seed_questions | current}}") == "Q2"
    assert p.render("{{seed_questions | next}}") is EXHAUSTED  # 尽きたらペルソナが作る
    args, exhausted = p.render_args({"message": "{{seed_questions | next}}", "x": "{{goal_text}}"})
    assert args == {"x": "ゼミ発表"} and exhausted == ["message"]
    assert p.render("{{seed_questions}}") == ["Q1", "Q2"]  # 全体が穴なら型を保つ


def test_missing_param_raises():
    with pytest.raises(ScenarioError):
        ScenarioParams({}).render("{{nope}}")


def test_persona_fields():
    p = ScenarioParams({}, persona={"display_name": "高梨", "teaching": {"course_subject": "宇宙論"}})
    assert p.render("{{persona.display_name}} さん") == "高梨 さん"
    assert p.render("{{persona.teaching.course_subject}} のコース") == "宇宙論 のコース"


def test_until_expressions():
    assert evaluate_until("friction in [confused, gave_up] or steps > 12", {"friction": "confused", "steps": 1})
    assert evaluate_until("friction in [confused, gave_up] or steps > 12", {"friction": "none", "steps": 13})
    assert not evaluate_until("friction in [confused, gave_up] or steps > 12", {"friction": "none", "steps": 3})
    assert evaluate_until("status >= 400 and steps >= 2", {"status": 429, "steps": 2})
    assert evaluate_until("gave_up", {"friction": "gave_up"})
    assert evaluate_until("response is 429 or friction in [blocked]", {"status": 429})
    assert evaluate_until("persona_decides_to_stop or steps > 5", {"persona_decides_to_stop": True, "steps": 1})
    assert evaluate_until("goal_reached", {"goal_reached": True})


def test_scenario_file_and_domain_params(tmp_path):
    (tmp_path / "scenarios" / "goal" / "student").mkdir(parents=True)
    (tmp_path / "scenarios" / "goal" / "student" / "s-x.yaml").write_text(
        "id: s-x\npopulation: students\ngoal: '{{goal_text}}'\nparams_required: [goal_text, seed_questions]\n"
        "steps:\n  - do: learning.topic.open\n"
        "  - repeat: {until: 'friction in [confused] or steps > 3', do: learning.chat.ask,"
        " with: {message: '{{seed_questions | next}}'}}\n  - choose: [learning.symbol.lookup, learning.descent.ladder]\n",
        encoding="utf-8")
    (tmp_path / "domains" / "astro" / "scenario_params").mkdir(parents=True)
    (tmp_path / "domains" / "astro" / "scenario_params" / "s-x.yaml").write_text(
        "scenario: s-x\ndomain: astro\nparams:\n  goal_text: 発表\n  seed_questions: [a, b]\n"
        "per_archetype:\n  non_native: {seed_questions: [en]}\npersonas:\n  st-1: {goal_text: 研究}\n",
        encoding="utf-8")
    sc = load_scenario("s-x", tmp_path)
    assert [s.kind for s in sc.steps] == ["do", "repeat", "choose"]
    assert sc.steps[1].max == 12 and sc.steps[1].actions == ["learning.chat.ask"]
    params = load_params("astro", "s-x", "st-1", tmp_path, "students/non_native")
    assert params["goal_text"] == "研究" and params["seed_questions"] == ["en"]
    assert sc.missing_params(params) == []
    assert sc.missing_params({}) == ["goal_text", "seed_questions"]


def test_unknown_action_rejected():
    with pytest.raises(ScenarioError):
        parse_scenario({"id": "s-bad", "steps": [{"do": "learning.not_a_thing"}]})


def test_choose_options_and_repeat_block():
    sc = parse_scenario({"id": "s-y", "steps": [
        {"choose": [{"do": "learning.chat.rewrite", "with": {"message": "m"}, "note": "言い換える"},
                    {"do": "learning.help.inspect"}], "optional": True},
        {"repeat": {"until": "goal_reached or steps > 2", "max": 3,
                    "steps": [{"choose": "all"}, {"do": "learning.records.mine", "note": "記録を見る"}]}},
    ]})
    first, rep = sc.steps
    assert first.optional and first.actions == ["learning.chat.rewrite", "learning.help.inspect"]
    assert first.option_for("learning.chat.rewrite").with_ == {"message": "m"}
    assert rep.kind == "repeat" and [c.kind for c in rep.children] == ["choose", "do"]
    assert rep.children[0].actions == ["*"] and len(rep.children[0].allowed("learning")) > 10
