"""学習者のコース入場判定を 1 つにする（第 25 周 第 3 波・IK-0945）。

受講中のコースを所有者が非公開にしても、受講者は教材・講義・チャット・記号・⚓・帰還の扉を
同じく読み続けられる。新規の受講開始だけが止まり、一覧には事実文が出る。
入場の条件式は ``services.resolve_course_entry`` の 1 箇所だけに置く。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_API_DIR = str(Path(__file__).resolve().parents[1] / "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

import services  # noqa: E402

DATA = {"title": "C", "topics": []}


def _session(course_row, enrolled: bool):
    session = MagicMock()
    results = [course_row, (1,) if enrolled else None]

    def _execute(*_a, **_k):
        r = MagicMock()
        r.fetchone.return_value = results.pop(0) if results else None
        return r

    session.execute.side_effect = _execute
    return session


def _private_row(owner="owner-1"):
    return (DATA, owner, "private", None, False, False)


@pytest.fixture
def no_version(monkeypatch):
    monkeypatch.setattr(services, "_apply_course_version_view", lambda cid, uid, data: data)
    monkeypatch.setattr(services, "user_can_view_course", lambda u, c: False)
    monkeypatch.setattr(services, "user_can_access_group", lambda u, g: False)


def test_enrolled_learner_reads_private_course_on_both_gates(monkeypatch, no_version):
    monkeypatch.setattr(services, "_pg_session", lambda: _session(_private_row(), enrolled=True))
    assert services.get_accessible_course_data("learner", "c1") == DATA
    monkeypatch.setattr(services, "_pg_session", lambda: _session(_private_row(), enrolled=True))
    assert services.get_course_data("learner", "c1") == DATA


def test_stranger_is_closed_on_both_gates(monkeypatch, no_version):
    monkeypatch.setattr(services, "_pg_session", lambda: _session(_private_row(), enrolled=False))
    assert services.get_accessible_course_data("stranger", "c1") is None
    monkeypatch.setattr(services, "_pg_session", lambda: _session(_private_row(), enrolled=False))
    assert services.get_course_data("stranger", "c1") is None


def test_entry_reports_closed_enrollment_for_enrolled(monkeypatch, no_version):
    monkeypatch.setattr(services, "_pg_session", lambda: _session(_private_row(), enrolled=True))
    entry = services.resolve_course_entry("learner", "c1", check_open=True)
    assert entry.readable and entry.is_enrolled and not entry.open_for_enrollment


def test_object_group_viewer_is_open(monkeypatch, no_version):
    monkeypatch.setattr(services, "user_can_view_course", lambda u, c: True)
    monkeypatch.setattr(services, "_pg_session", lambda: _session(_private_row(), enrolled=False))
    assert services.get_accessible_course_data("viewer", "c1") == DATA


def test_gates_delegate_to_single_entry_resolver():
    for fn in (services.get_course_data, services.get_accessible_course_data):
        src = inspect.getsource(fn)
        doc = inspect.getdoc(fn) or ""
        src = src.replace(fn.__doc__ or "", "") if doc else src
        assert "resolve_course_entry(" in src, fn.__name__
        assert "FROM learning_states" not in src and "is_published" not in src, fn.__name__


def test_enroll_route_reads_entry_resolver():
    src = (Path(__file__).resolve().parents[1] / "api/routes/learning.py").read_text(encoding="utf-8")
    body = src[src.index("def enroll_course("):src.index("# ----", src.index("def enroll_course("))]
    assert "resolve_course_entry(" in body
    assert "user_can_access_group(" not in body and "user_can_view_course(" not in body
