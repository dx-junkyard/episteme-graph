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

