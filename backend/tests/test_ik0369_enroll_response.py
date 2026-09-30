"""IK-0369: 受講登録 API の応答を一覧（GET /courses の受講中行）と同じ投影で埋める。

id / title だけ埋めて他の列を既定値（非公開・受講不可）で返すと、公開コースに登録した
直後の画面がそれを事実として読む。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_API_DIR = str(Path(__file__).resolve().parents[1] / "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

import routes.learning as learning_routes  # noqa: E402


def _session_with(record):
    session = MagicMock()
    session.execute.return_value.fetchone.return_value = record
    return session


def test_enroll_response_reflects_the_public_course_row(monkeypatch):
    monkeypatch.setattr(
        learning_routes, "_pg_session",
        lambda: _session_with(("宇宙物理入門", "public", None, True, True, "説明文")),
    )
    enrolled: list = []
    monkeypatch.setattr(learning_routes, "enroll_user_in_course", lambda u, c: enrolled.append((u, c)))

    out = learning_routes.enroll_course("c-1", current_user={"id": "u-1"})
    assert enrolled == [("u-1", "c-1")]
    assert out.id == "c-1" and out.title == "宇宙物理入門"
    assert out.is_published is True and out.is_template is True
    assert out.visibility == "public"
    assert out.description == "説明文"
    # 受講登録した後なので一覧の「受講中」行と同じく受講可能ではない。
    assert out.is_enrollable is False
    assert out.group_id is None


def test_enroll_response_carries_group_visibility(monkeypatch):
    monkeypatch.setattr(
        learning_routes, "_pg_session",
        lambda: _session_with(("ゼミ", "group", "g-1", False, False, "")),
    )
    monkeypatch.setattr(learning_routes, "user_can_access_group", lambda u, g: True)
    monkeypatch.setattr(learning_routes, "enroll_user_in_course", lambda u, c: None)

    out = learning_routes.enroll_course("c-2", current_user={"id": "u-1"})
    assert out.visibility == "group" and out.group_id == "g-1"
    assert out.is_published is False and out.is_enrollable is False


def test_enroll_response_states_success_explicitly(monkeypatch):
    """IK-0385: is_enrollable=False を「受講できなかった」と読まれないよう、成立の事実を添える。"""
    from core import label_vocab

    monkeypatch.setattr(
        learning_routes, "_pg_session",
        lambda: _session_with(("宇宙物理入門", "public", None, True, True, "")),
    )
    monkeypatch.setattr(learning_routes, "enroll_user_in_course", lambda u, c: None)

    out = learning_routes.enroll_course("c-1", current_user={"id": "u-1"})
    assert out.enrolled is True
    assert out.notice == label_vocab.COURSE_ENROLLED_NOTICE
    assert out.notice and not any(ch.isdigit() for ch in out.notice)
    # 既存フィールドは不変（追加のみ）。
    dumped = out.model_dump()
    for key in ("id", "title", "is_template", "is_published", "is_enrollable",
                "visibility", "group_id", "description"):
        assert key in dumped
    assert out.is_enrollable is False



def test_enroll_already_enrolled_notice(monkeypatch):
    """TRIAGE14: 既に受講中（新しい行が作られなかった）なら「受講を開始しました」と言わない。"""
    from core import label_vocab

    monkeypatch.setattr(
        learning_routes, "_pg_session",
        lambda: _session_with(("宇宙物理入門", "public", None, True, True, "")),
    )
    monkeypatch.setattr(learning_routes, "enroll_user_in_course", lambda u, c: False)
    out = learning_routes.enroll_course("c-1", current_user={"id": "u-1"})
    assert out.enrolled is True
    assert out.notice == label_vocab.COURSE_ALREADY_ENROLLED_NOTICE
    assert out.notice != label_vocab.COURSE_ENROLLED_NOTICE
