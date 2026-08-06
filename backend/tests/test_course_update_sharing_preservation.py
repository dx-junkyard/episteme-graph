"""レビュー確定の修正2（D-5）— `PUT /api/learning/courses/{id}` が共有設定を壊さないこと。

背景: `routes/learning.py::update_course` は `services.save_course_data(user_id, course_id,
data)` を共有設定の引数なしで呼んでいた。`save_course_data` の UPSERT は
`ON CONFLICT ... SET visibility = EXCLUDED.visibility, group_id = EXCLUDED.group_id,
description = EXCLUDED.description` を無条件に含むため、既定引数（private / NULL / ""）が
既存値を上書きしていた。結果、**公開コース**（visibility='public', is_published=true）で
「議論テーマ」モーダル（course_focus 保存）や「AIモデル」モーダル（llm_models 保存）を
使うと visibility が 'private' に落ち、`get_accessible_course_data` は
`visibility == "public" and is_published and is_template` を要求するため
**受講者全員がコースを開けなくなる**（is_published は SET 句に無いので残り、教員画面では
公開済み表示のまま = 気付けない）。group 共有は group_id の NULL 化で失効、description も消失。

修正: `save_course_data(..., preserve_sharing_fields=True)` を新設し、既存行がある場合に
visibility / group_id / description を ON CONFLICT の SET 句から外して温存する
（既存行の読み取りを挟まないので TOCTOU も起きない）。`update_course` はこれを渡す。
明示的に共有設定を指定する `create_course` 等の既定（False）は従来挙動のまま。

実 DB・実 LLM には触れない（SQL 文字列を捕まえるフェイクセッション + UPSERT の
SET 句セマンティクスの模擬適用）。
"""

from __future__ import annotations

import inspect
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from api import services

_BACKEND = Path(__file__).resolve().parents[1]
_LEARNING_PATH = _BACKEND / "api" / "routes" / "learning.py"

_SHARING_COLUMNS = ("visibility", "group_id", "description")


class _CapturingSession:
    def __init__(self):
        self.executed_sql: str | None = None
        self.executed_params: dict | None = None
        self.committed = False
        self.closed = False

    def execute(self, sql, params=None):
        self.executed_sql = str(sql)
        self.executed_params = params or {}
        return self

    def commit(self):
        self.committed = True

    def rollback(self):  # pragma: no cover — 例外経路のみ
        pass

    def close(self):
        self.closed = True


def _conflict_set_clause(sql: str) -> str:
    """`ON CONFLICT (id) DO UPDATE SET ...` の SET 句だけを取り出す。"""
    marker = "ON CONFLICT (id) DO UPDATE"
    assert marker in sql, sql
    tail = sql.split(marker, 1)[1]
    return tail.split("SET", 1)[1]


def _apply_conflict_update(existing_row: dict, sql: str, params: dict) -> dict:
    """UPSERT の SET 句に現れた列だけを既存行へ反映する（PostgreSQL の意味論の模擬）。

    `col = EXCLUDED.col` / `data = CAST(EXCLUDED.data AS jsonb)` の形のみを解釈し、
    `updated_at = now()` は無視する。SET 句に現れない列は既存値のまま
    （= 温存されていることを実行可能な形で検証できる）。
    """
    updated = dict(existing_row)
    for assignment in _conflict_set_clause(sql).split(","):
        match = re.search(r"(\w+)\s*=\s*(?:CAST\()?EXCLUDED\.(\w+)", assignment)
        if not match:
            continue
        column, excluded_column = match.group(1), match.group(2)
        updated[column] = params[excluded_column]
    return updated


_EXISTING_PUBLIC_COURSE_ROW = {
    "id": "course-1",
    "title": "公開コース",
    "data": json.dumps({"id": "course-1", "title": "公開コース"}, ensure_ascii=False),
    "visibility": "public",
    "group_id": None,
    "description": "受講者向けの説明文",
    "is_published": True,
    "is_template": True,
}


def _is_accessible_to_enrolled_learner(row: dict) -> bool:
    """`services.get_accessible_course_data` の非オーナー public 分岐と同じ条件。"""
    return bool(
        row.get("visibility") == "public"
        and row.get("is_published")
        and row.get("is_template")
    )


# ---------------------------------------------------------------------------
# 1. save_course_data のシグネチャと SQL
# ---------------------------------------------------------------------------


class TestSaveCourseDataPreserveFlag:
    def test_flag_is_keyword_only_and_defaults_to_false(self):
        param = inspect.signature(services.save_course_data).parameters["preserve_sharing_fields"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is False

    def test_default_still_overwrites_sharing_fields(self, monkeypatch):
        """既定（False）は従来挙動 — create_course 等の「共有設定を明示指定する」
        呼び出し元の意味を変えていないことの回帰確認。"""
        session = _CapturingSession()
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        services.save_course_data(
            "teacher-1", "course-2", {"title": "新コース"},
            is_template=True, visibility="group", group_id="g-1", description="説明",
        )

        set_clause = _conflict_set_clause(session.executed_sql)
        for column in _SHARING_COLUMNS:
            assert f"{column} = EXCLUDED.{column}" in set_clause
        assert session.committed is True

    def test_preserve_flag_drops_sharing_fields_from_conflict_set(self, monkeypatch):
        session = _CapturingSession()
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        services.save_course_data(
            "teacher-1", "course-1", {"title": "公開コース"}, preserve_sharing_fields=True,
        )

        set_clause = _conflict_set_clause(session.executed_sql)
        for column in _SHARING_COLUMNS:
            assert f"{column} = EXCLUDED.{column}" not in set_clause
        # data / title / updated_at は常に更新される。
        assert "data = CAST(EXCLUDED.data AS jsonb)" in set_clause
        assert "title = EXCLUDED.title" in set_clause
        assert "updated_at = now()" in set_clause

    def test_preserve_flag_keeps_insert_values_for_new_rows(self, monkeypatch):
        """新規 INSERT の列・パラメータは変わらない（温存は ON CONFLICT 側だけの話）。"""
        session = _CapturingSession()
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        services.save_course_data(
            "teacher-1", "course-1", {"title": "公開コース"}, preserve_sharing_fields=True,
        )

        assert "visibility, group_id, description)" in session.executed_sql
        assert set(_SHARING_COLUMNS) <= set(session.executed_params)


# ---------------------------------------------------------------------------
# 2. UPSERT セマンティクスの模擬: 公開コースが private に落ちない
# ---------------------------------------------------------------------------


class TestPublicCourseSurvivesPartialUpdate:
    def _run_upsert(self, monkeypatch, **save_kwargs) -> dict:
        session = _CapturingSession()
        monkeypatch.setattr(services, "_pg_session", lambda: session)
        services.save_course_data(
            "teacher-1", "course-1",
            {"id": "course-1", "title": "公開コース", "course_focus": "議論テーマ"},
            **save_kwargs,
        )
        return _apply_conflict_update(
            _EXISTING_PUBLIC_COURSE_ROW, session.executed_sql, session.executed_params,
        )

    def test_preserve_keeps_visibility_group_and_description(self, monkeypatch):
        row = self._run_upsert(monkeypatch, preserve_sharing_fields=True)

        assert row["visibility"] == "public"
        assert row["group_id"] is None
        assert row["description"] == "受講者向けの説明文"
        # data 本体（course_focus）は更新されている。
        assert "議論テーマ" in row["data"]

    def test_preserve_keeps_course_open_for_enrolled_learners(self, monkeypatch):
        row = self._run_upsert(monkeypatch, preserve_sharing_fields=True)
        assert _is_accessible_to_enrolled_learner(row) is True

    def test_without_the_flag_the_bug_reproduces(self, monkeypatch):
        """回帰の起点を明示的に固定する: フラグ無しだと公開コースが private に落ち、
        受講者から見えなくなる（この振る舞いが update_course に戻らないよう
        下の配線テストと対で守る）。"""
        row = self._run_upsert(monkeypatch)

        assert row["visibility"] == "private"
        assert row["description"] == ""
        assert _is_accessible_to_enrolled_learner(row) is False

    def test_group_sharing_is_not_revoked(self, monkeypatch):
        session = _CapturingSession()
        monkeypatch.setattr(services, "_pg_session", lambda: session)
        services.save_course_data(
            "teacher-1", "course-1", {"id": "course-1", "title": "共有コース"},
            preserve_sharing_fields=True,
        )
        existing = dict(
            _EXISTING_PUBLIC_COURSE_ROW,
            visibility="group",
            group_id="11111111-1111-1111-1111-111111111111",
        )
        row = _apply_conflict_update(existing, session.executed_sql, session.executed_params)

        assert row["visibility"] == "group"
        assert row["group_id"] == "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# 3. ルート配線: update_course は preserve_sharing_fields=True を渡す
# ---------------------------------------------------------------------------


class TestUpdateCourseWiring:
    def test_static_wiring_in_learning_py(self):
        source = _LEARNING_PATH.read_text(encoding="utf-8")
        block = source.split("def update_course(")[1].split("\n@router")[0]
        assert "preserve_sharing_fields=True" in block
        # 共有設定を PUT 側で受け取って書き換えていないこと（この PUT の責務外）。
        for column in _SHARING_COLUMNS:
            assert f"{column}=" not in block.replace("preserve_sharing_fields=True", "")

    def test_create_course_still_passes_explicit_sharing_fields(self):
        """create_course は従来どおり明示指定（preserve は付けない）。"""
        source = _LEARNING_PATH.read_text(encoding="utf-8")
        block = source.split("def create_course(")[1].split("\n@router")[0]
        assert "visibility=body.visibility" in block
        assert "preserve_sharing_fields" not in block


try:  # pragma: no cover — FastAPI 未インストール環境ではルート実行テストを飛ばす
    import fastapi  # noqa: F401

    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False


@pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")
class TestUpdateCourseCallsSaveWithPreserveFlag:
    CURRENT_USER = {"id": "teacher-1", "username": "t1", "email": "t1@test.local", "role": "TEACHER"}

    def test_course_focus_update_passes_preserve_flag(self, monkeypatch):
        from api.routes import learning as learning_module
        from schemas import CourseUpdateRequest

        data = {
            "id": "course-1", "title": "公開コース",
            "topics": [], "chapters": [], "concepts": [], "sources": [],
        }
        captured: dict = {}
        monkeypatch.setattr(learning_module, "get_editable_course_data", lambda uid, cid: data)

        def _fake_save(user_id, course_id, payload, is_template=False, **kwargs):
            captured["kwargs"] = kwargs
            captured["payload"] = payload

        monkeypatch.setattr(learning_module, "save_course_data", _fake_save)

        learning_module.update_course(
            "course-1", CourseUpdateRequest(course_focus="この論文の適用範囲"),
            current_user=self.CURRENT_USER,
        )

        assert captured["kwargs"].get("preserve_sharing_fields") is True
        assert captured["payload"]["course_focus"] == "この論文の適用範囲"
        # 共有設定は引数として渡さない（= 既存値の温存に委ねる）。
        for column in _SHARING_COLUMNS:
            assert column not in captured["kwargs"]
