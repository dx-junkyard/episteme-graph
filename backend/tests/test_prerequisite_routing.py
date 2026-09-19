"""前提知識チェックの対話分岐テスト。"""

from __future__ import annotations


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Session:
    """`learning_states.progress_data` を1行だけ返す最小のフェイクセッション。

    是正 F4（2026-09-10）以降、`check_prerequisites` が読むのは本人の明示的な
    「理解している」の記帳（progress_data.acknowledged_prerequisites）だけで、
    `learning_chat_history` は読まない。
    """

    def __init__(self, rows=()):
        self._rows = rows
        self.executed: list[str] = []

    def execute(self, statement, *_args, **_kwargs):
        self.executed.append(str(statement))
        return _Rows(self._rows)

    def commit(self):
        pass

    def rollback(self):
        pass

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


# ---------------------------------------------------------------------------
# 是正 F4（2026-09-10）: 履歴による自動スキップの撤去と、明示的な答えの記帳
# ---------------------------------------------------------------------------


def test_chat_history_does_not_count_as_mastery(monkeypatch):
    """前提と同名トピックにチャット履歴があっても習得扱いにしない（接触≠理解）。

    記帳（acknowledged_prerequisites）が空である以上、介入は出続ける。
    """
    from api import services

    session = _Session(rows=[({},)])
    monkeypatch.setattr(services, "_pg_session", lambda: session)

    response = services.check_prerequisites(
        "user-1",
        "course-1",
        _course_data(),
        "理論の「正しさ」と「完全性」という二つの問い",
        "このトピックを始めたい",
    )

    assert response is not None
    assert "learning_chat_history" not in " ".join(session.executed)


def test_acknowledged_prerequisite_is_not_asked_again(monkeypatch):
    """本人が「理解している」と答えた前提は、記帳から解決されて再度問われない。"""
    from api import services

    acknowledged = {
        services.PROGRESS_ACKNOWLEDGED_PREREQUISITES_KEY: {
            "客観的実在と物理的描像の区別": "2026-09-10T00:00:00+00:00",
        }
    }
    monkeypatch.setattr(services, "_pg_session", lambda: _Session(rows=[(acknowledged,)]))

    response = services.check_prerequisites(
        "user-1",
        "course-1",
        _course_data(),
        "理論の「正しさ」と「完全性」という二つの問い",
        "このトピックを始めたい",
    )

    assert response is None


def test_explicit_acknowledgement_is_recorded(monkeypatch):
    """「はい、理解しています」は記帳され、以後の判定の根拠になる。"""
    from api import services

    recorded: list[tuple[str, str, list[str]]] = []
    monkeypatch.setattr(services, "_pg_session", lambda: _Session(rows=[({},)]))
    monkeypatch.setattr(
        services,
        "record_prerequisite_acknowledgement",
        lambda uid, cid, names: recorded.append((uid, cid, list(names))),
    )

    response = services.check_prerequisites(
        "user-1",
        "course-1",
        _course_data(),
        "理論の「正しさ」と「完全性」という二つの問い",
        "はい、理解しています",
    )

    assert response is None
    assert recorded == [("user-1", "course-1", ["客観的実在と物理的描像の区別"])]


def test_negated_understanding_is_not_recorded(monkeypatch):
    """「理解していません」のような否定形は恒久的な記帳の根拠にしない。"""
    from api import services

    recorded: list[str] = []
    monkeypatch.setattr(services, "_pg_session", lambda: _Session(rows=[({},)]))
    monkeypatch.setattr(
        services,
        "record_prerequisite_acknowledgement",
        lambda *a, **k: recorded.append("written"),
    )

    services.check_prerequisites(
        "user-1",
        "course-1",
        _course_data(),
        "理論の「正しさ」と「完全性」という二つの問い",
        "理解していません",
    )

    assert recorded == []
