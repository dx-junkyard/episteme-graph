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


def test_prerequisite_intervention_offers_yes_no_actions(monkeypatch):
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
    assert "[ACTION_BUTTON: はい、理解しています]" in response
    assert (
        "[ACTION_BUTTON: いいえ、理解できていないので"
        "客観的実在と物理的描像の区別について教えてください]"
    ) in response


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
