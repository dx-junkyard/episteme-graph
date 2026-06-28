"""LearningSupportAgent structured navigation tests."""

from __future__ import annotations


def test_learning_support_agent_returns_path_action_first():
    from core.learning_support_agent import LearningSupportAgent

    course_data = {
        "chapters": [{"title": "第3章 微分和則から全崩壊率和則へ"}],
        "topics": [
            {
                "id": "topic-hqet",
                "title": "HQETにおける微分和則",
                "chapter_index": 0,
            }
        ],
    }
    topic = course_data["topics"][0]
    agent = LearningSupportAgent("course-1", course_data)

    result = agent.with_learning_actions(
        answer="前提知識を確認します。",
        mode="prerequisite_review",
        origin=agent.origin_for_topic("topic-hqet", topic),
    )
    dumped = result.model_dump()

    assert dumped["status_label"] == "詳細説明中"
    assert dumped["origin"]["topic_id"] == "topic-hqet"
    assert dumped["origin"]["chapter_title"] == "第3章 微分和則から全崩壊率和則へ"
    assert dumped["next_actions"][0]["type"] == "return_to_learning_path"
    assert dumped["next_actions"][0]["label"] == "学習パスに戻る"


def test_learning_support_agent_detects_prerequisite_request():
    from core.learning_support_agent import LearningSupportAgent

    assert LearningSupportAgent.is_prerequisite_request("このコースに必要な前提知識を確認する")
    assert not LearningSupportAgent.is_prerequisite_request("HQETについて説明して")


# ---------------------------------------------------------------------------
# extract_inline_actions: 本文マーカー → 構造化アクションの一元化（P1）
# ---------------------------------------------------------------------------


def _origin():
    from core.learning_support_agent import LearningSupportOrigin

    return LearningSupportOrigin(
        course_id="c1", topic_id="t1", topic_title="トピックA", chapter_title="第1章"
    )


class TestExtractInlineActions:
    def test_extracts_action_button_marker(self):
        from core.learning_support_agent import extract_inline_actions

        clean, actions = extract_inline_actions(
            "本文です。\n\n[ACTION_BUTTON: 発散について詳しく聞く]"
        )
        assert "ACTION_BUTTON" not in clean
        assert clean == "本文です。"
        assert len(actions) == 1
        assert actions[0].type == "drilldown"
        assert actions[0].label == "発散について詳しく聞く"
        assert actions[0].message == "発散について詳しく聞く"

    def test_extracts_drilldown_bracket_marker(self):
        from core.learning_support_agent import extract_inline_actions

        clean, actions = extract_inline_actions(
            "回答。\n[くりこみ群について詳しく聞く]"
        )
        assert "[" not in clean
        assert [a.label for a in actions] == ["くりこみ群について詳しく聞く"]

    def test_deduplicates_identical_markers(self):
        from core.learning_support_agent import extract_inline_actions

        _clean, actions = extract_inline_actions(
            "[ACTION_BUTTON: Xについて聞く] と [Xについて聞く]"
        )
        assert len(actions) == 1

    def test_no_marker_returns_text_unchanged(self):
        from core.learning_support_agent import extract_inline_actions

        clean, actions = extract_inline_actions("ただの本文です。")
        assert clean == "ただの本文です。"
        assert actions == []

    def test_handles_empty_and_none(self):
        from core.learning_support_agent import extract_inline_actions

        assert extract_inline_actions("") == ("", [])
        assert extract_inline_actions(None) == ("", [])


class TestWithLearningActionsExtras:
    def test_extra_actions_inserted_after_return(self):
        from core.learning_support_agent import (
            LearningSupportAction,
            LearningSupportAgent,
        )

        agent = LearningSupportAgent("c1", {})
        extra = [LearningSupportAction(type="drilldown", label="A", message="A")]
        result = agent.with_learning_actions(
            answer="本文",
            mode="detail_explanation",
            origin=_origin(),
            include_continue=False,
            extra_actions=extra,
        )
        types = [a["type"] for a in result.model_dump()["next_actions"]]
        # 先頭は必ず復帰導線、続いて extra、最後に質問。
        assert types[0] == "return_to_learning_path"
        assert "drilldown" in types
        assert types[-1] == "ask_question"

    def test_include_continue_false_drops_continue_action(self):
        from core.learning_support_agent import LearningSupportAgent

        agent = LearningSupportAgent("c1", {})
        result = agent.with_learning_actions(
            answer="本文", mode="detail_explanation", origin=_origin(),
            include_continue=False,
        )
        types = [a["type"] for a in result.model_dump()["next_actions"]]
        assert "continue_detail" not in types


class TestAdviceActions:
    def test_advice_actions_offer_prereq_and_start(self):
        from core.learning_support_agent import LearningSupportAgent

        agent = LearningSupportAgent("c1", {})
        actions = agent.advice_actions(_origin(), "トピックA", first_concept="概念X")
        types = [a.type for a in actions]
        assert "review_prerequisite" in types
        assert "start_topic" in types
        start = next(a for a in actions if a.type == "start_topic")
        assert "概念X" in start.label
        # パス上のアクションなので復帰導線は含めない。
        assert "return_to_learning_path" not in types

    def test_advice_actions_falls_back_to_topic_when_no_concept(self):
        from core.learning_support_agent import LearningSupportAgent

        agent = LearningSupportAgent("c1", {})
        actions = agent.advice_actions(_origin(), "トピックA", first_concept=None)
        start = next(a for a in actions if a.type == "start_topic")
        assert "トピックA" in start.label


class TestPrerequisiteChoiceActions:
    def test_offers_yes_and_no(self):
        from core.learning_support_agent import LearningSupportAgent

        agent = LearningSupportAgent("c1", {})
        actions = agent.prerequisite_choice_actions("線形代数")
        assert actions[0].label == "はい、理解しています"
        no = next(a for a in actions if a.label.startswith("いいえ"))
        assert no.type == "drilldown"
        assert no.message == "線形代数について教えてください"

    def test_no_first_prerequisite_only_yes(self):
        from core.learning_support_agent import LearningSupportAgent

        agent = LearningSupportAgent("c1", {})
        actions = agent.prerequisite_choice_actions("")
        assert len(actions) == 1
        assert actions[0].label == "はい、理解しています"

