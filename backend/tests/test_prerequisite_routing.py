"""前提知識チェックの対話分岐テスト。"""

from __future__ import annotations


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Session:
    def __init__(self, rows=()):
        self._rows = rows

    def execute(self, *_args, **_kwargs):
        return _Rows(self._rows)

    def close(self):
        pass


def _course_data():
    return {
        "topics": [
            {
                "id": "t0",
                "title": "理論の「正しさ」と「完全性」という二つの問い",
                "prerequisites": [
                    {"name": "客観的実在と物理的描像の区別", "status": "not_started"},
                ],
            },
            {
                "id": "t-prereq",
                "title": "客観的実在と物理的描像の区別",
                "prerequisites": [],
            },
        ],
    }


def test_prerequisite_intervention_returns_structured_payload(monkeypatch):
    """介入時は本文（ボタン記法なし）と first_prerequisite を構造化して返す。"""
    from api import services

    monkeypatch.setattr(services, "_pg_session", lambda: _Session())

    response = services.check_prerequisites(
        "user-1",
        "course-1",
        _course_data(),
        "理論の「正しさ」と「完全性」という二つの問い",
        "このトピックを始めたい",
    )

    assert response is not None
    # 本文には旧来の [ACTION_BUTTON: ...] マーカーを埋め込まない（型付き next_actions に集約）。
    assert "ACTION_BUTTON" not in response["message"]
    assert response["first_prerequisite"] == "客観的実在と物理的描像の区別"
    assert "客観的実在と物理的描像の区別" in response["unlearned"]


def test_prerequisite_choice_actions_offers_yes_and_no():
    """ルートが組み立てる前提確認の選択肢（はい/いいえ）が型付きで返ること。"""
    from core.learning_support_agent import LearningSupportAgent

    agent = LearningSupportAgent("course-1", _course_data())
    actions = agent.prerequisite_choice_actions("客観的実在と物理的描像の区別")

    labels = [a.label for a in actions]
    assert "はい、理解しています" in labels
    # 「いいえ」側は first_prerequisite の解説へ誘導する drilldown アクション。
    no_action = next(a for a in actions if a.label.startswith("いいえ"))
    assert no_action.type == "drilldown"
    assert no_action.message == "客観的実在と物理的描像の区別について教えてください"


def test_prerequisite_explanation_request_does_not_loop(monkeypatch):
    from api import services

    monkeypatch.setattr(services, "_pg_session", lambda: _Session())

    response = services.check_prerequisites(
        "user-1",
        "course-1",
        _course_data(),
        "理論の「正しさ」と「完全性」という二つの問い",
        "客観的実在と物理的描像の区別について教えてください",
    )

    assert response is None
