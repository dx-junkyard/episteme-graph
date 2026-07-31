"""LLM モデル選択（M層）Phase 3 のテスト — チャット型・単発 AI 操作の場面別モデル指定。

正本: docs/features/llm_model_selection_design.md §6.2〜§6.5・§7・§11。

対象:
  1. core.llm_policy.validate_model_for_scene（fail-closed 検証の単一の真実源）。
  2. core.course_data.course_llm_models アクセサ。
  3. コース設定保存 API（routes/learning.py::update_course）の llm_models 部分更新。
  4. 学習チャット本体・確認問題（understanding_check）へのコース単位モデル上書き
     （live 読み・版ピン独立）。
  4b. 教員向けコース一覧 API（routes/admin.py::list_teacher_courses）の llm_models 投影
      （フロントのコース設定 UI がこの値を初期表示に使う）。
  5. 教員向けセッション/操作単位の model パラメータ（course_builder / lecture_studio /
     atlas / deliberation）。
  6. M9（学生向けレスポンスにモデル名を漏らさない）。

実 DB・実 LLM には触れない。既存テストファイル（test_llm_model_policy_api.py /
test_learning_chat_infra.py / test_course_builder_infra.py / test_deliberation_api.py /
test_lecture_language.py）と同型の手法を踏襲する。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_HAS_FASTAPI = True
try:
    import fastapi  # noqa: F401
    from fastapi import HTTPException
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)

import core.llm_policy as llm_policy  # noqa: E402
from core.course_data import course_llm_models  # noqa: E402


_FAKE_CATALOG = {
    "models": [
        {"id": "gpt-5.2", "provider": "openai", "capabilities": ["text", "structured", "vision"],
         "default_effort": "medium", "cost_hint": "high", "note": ""},
        {"id": "gpt-5.4-mini", "provider": "openai", "capabilities": ["text", "structured"],
         "default_effort": "medium", "cost_hint": "medium", "note": ""},
        {"id": "gpt-4o", "provider": "openai", "capabilities": ["text", "structured", "vision"],
         "default_effort": None, "cost_hint": "medium", "note": ""},
    ]
}


def _fake_catalog_models(catalog=_FAKE_CATALOG):
    def _impl(*, capability=None):
        result = []
        for entry in catalog["models"]:
            if capability is not None and capability not in entry.get("capabilities", []):
                continue
            result.append(entry)
        return result

    return _impl


@pytest.fixture
def fake_catalog(monkeypatch):
    """全モジュールが同一の core.llm_policy を参照するため、ここでの monkeypatch は
    routes.learning / routes.admin / routes.atlas / routes.deliberation /
    routes.lecture_studio.{scripts,topics} のいずれから呼ばれても効く。"""
    monkeypatch.setattr(llm_policy, "load_catalog", lambda: _FAKE_CATALOG)
    monkeypatch.setattr(llm_policy, "catalog_models", _fake_catalog_models())
    return _FAKE_CATALOG


# ===========================================================================
# 1. core.llm_policy.validate_model_for_scene（正本の fail-closed 検証）
# ===========================================================================


class TestValidateModelForScene:
    def test_unknown_scene_key_is_rejected(self, fake_catalog):
        reason = llm_policy.validate_model_for_scene("no_such_scene", "gpt-5.2")
        assert reason is not None
        assert "no_such_scene" in reason

    def test_empty_model_is_rejected(self, fake_catalog):
        reason = llm_policy.validate_model_for_scene(llm_policy.SCENE_COURSE_BUILDER, "")
        assert reason == "model is required"

    def test_catalog_unavailable_is_rejected(self, monkeypatch):
        monkeypatch.setattr(llm_policy, "load_catalog", lambda: None)
        reason = llm_policy.validate_model_for_scene(llm_policy.SCENE_COURSE_BUILDER, "gpt-5.2")
        assert reason is not None
        assert "カタログ" in reason

    def test_model_not_in_catalog_is_rejected(self, fake_catalog):
        reason = llm_policy.validate_model_for_scene(llm_policy.SCENE_COURSE_BUILDER, "made-up-model")
        assert reason is not None

    def test_valid_model_is_accepted(self, fake_catalog):
        assert llm_policy.validate_model_for_scene(llm_policy.SCENE_COURSE_BUILDER, "gpt-5.4-mini") is None

    def test_vision_scene_rejects_non_vision_model(self, fake_catalog):
        reason = llm_policy.validate_model_for_scene(llm_policy.SCENE_PIPELINE_VISION, "gpt-5.4-mini")
        assert reason is not None
        assert "vision" in reason

    def test_vision_scene_accepts_vision_model(self, fake_catalog):
        assert llm_policy.validate_model_for_scene(llm_policy.SCENE_PIPELINE_VISION, "gpt-4o") is None

    def test_deliberation_vision_feature_key_requires_vision(self, fake_catalog):
        reason = llm_policy.validate_model_for_scene("deliberation:vision", "gpt-5.4-mini")
        assert reason is not None
        assert llm_policy.validate_model_for_scene("deliberation:vision", "gpt-4o") is None

    def test_required_capability_for_scene(self):
        assert llm_policy.required_capability_for_scene(llm_policy.SCENE_PIPELINE_VISION) == "vision"
        assert llm_policy.required_capability_for_scene(llm_policy.SCENE_DELIBERATION) is None

    def test_is_known_scene_key_accepts_scenes_and_features(self):
        assert llm_policy.is_known_scene_key(llm_policy.SCENE_COURSE_BUILDER)
        assert llm_policy.is_known_scene_key("pipeline:claim_qualification")
        assert not llm_policy.is_known_scene_key("totally_unknown")


@_skip_no_fastapi
class TestRoutesDelegateToValidateModelForScene:
    """routes/llm_models.py が独自ロジックを持たず正本へ委譲していることの回帰確認。"""

    def test_validate_model_choice_delegates(self, fake_catalog):
        import routes.llm_models as llm_models_routes

        with pytest.raises(HTTPException) as exc:
            llm_models_routes._validate_model_choice(llm_policy.SCENE_COURSE_BUILDER, "made-up-model")
        assert exc.value.status_code == 422

        # 正常系は例外を送出しない。
        llm_models_routes._validate_model_choice(llm_policy.SCENE_COURSE_BUILDER, "gpt-5.4-mini")


# ===========================================================================
# 2. core.course_data.course_llm_models アクセサ
# ===========================================================================


class TestCourseLlmModelsAccessor:
    def test_returns_empty_dict_for_missing_or_non_dict(self):
        assert course_llm_models(None) == {}
        assert course_llm_models({}) == {}
        assert course_llm_models({"llm_models": "not-a-dict"}) == {}

    def test_returns_dict_value(self):
        assert course_llm_models({"llm_models": {"learning_chat": "gpt-5.2"}}) == {
            "learning_chat": "gpt-5.2"
        }


# ===========================================================================
# 3. コース設定保存 API — PUT /api/learning/courses/{course_id}（llm_models 部分更新）
# ===========================================================================


@_skip_no_fastapi
class TestCourseUpdateLlmModels:
    CURRENT_USER = {"id": "teacher-1", "username": "t1", "email": "t1@test.local", "role": "TEACHER"}

    def _course_data(self, **overrides) -> dict:
        base = {"id": "course-1", "title": "コース", "topics": [], "chapters": [], "concepts": [], "sources": []}
        base.update(overrides)
        return base

    def test_set_valid_model(self, fake_catalog, monkeypatch):
        import routes.learning as learning_mod
        from schemas import CourseUpdateRequest

        data = self._course_data()
        monkeypatch.setattr(learning_mod, "get_editable_course_data", lambda uid, cid: data)
        saved = {}
        monkeypatch.setattr(learning_mod, "save_course_data", lambda uid, cid, d, **kw: saved.update(d))

        body = CourseUpdateRequest(llm_models={"learning_chat": "gpt-5.4-mini"})
        result = learning_mod.update_course("course-1", body, current_user=self.CURRENT_USER)

        assert saved["llm_models"] == {"learning_chat": "gpt-5.4-mini"}

    def test_invalid_model_returns_422(self, fake_catalog, monkeypatch):
        import routes.learning as learning_mod
        from schemas import CourseUpdateRequest

        data = self._course_data()
        monkeypatch.setattr(learning_mod, "get_editable_course_data", lambda uid, cid: data)

        def _unexpected_save(*a, **k):
            raise AssertionError("invalid model must not be persisted")

        monkeypatch.setattr(learning_mod, "save_course_data", _unexpected_save)

        body = CourseUpdateRequest(llm_models={"learning_chat": "made-up-model"})
        with pytest.raises(HTTPException) as exc:
            learning_mod.update_course("course-1", body, current_user=self.CURRENT_USER)
        assert exc.value.status_code == 422

    def test_unsupported_scene_key_returns_422(self, fake_catalog, monkeypatch):
        import routes.learning as learning_mod
        from schemas import CourseUpdateRequest

        data = self._course_data()
        monkeypatch.setattr(learning_mod, "get_editable_course_data", lambda uid, cid: data)
        monkeypatch.setattr(
            learning_mod, "save_course_data",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not save")),
        )

        body = CourseUpdateRequest(llm_models={"atlas": "gpt-5.2"})
        with pytest.raises(HTTPException) as exc:
            learning_mod.update_course("course-1", body, current_user=self.CURRENT_USER)
        assert exc.value.status_code == 422

    def test_empty_value_clears_key(self, fake_catalog, monkeypatch):
        """空/null は設定解除（キー削除）。"""
        import routes.learning as learning_mod
        from schemas import CourseUpdateRequest

        data = self._course_data(llm_models={"learning_chat": "gpt-5.2"})
        monkeypatch.setattr(learning_mod, "get_editable_course_data", lambda uid, cid: data)
        saved = {}
        monkeypatch.setattr(learning_mod, "save_course_data", lambda uid, cid, d, **kw: saved.update(d))

        body = CourseUpdateRequest(llm_models={"learning_chat": None})
        learning_mod.update_course("course-1", body, current_user=self.CURRENT_USER)

        assert "llm_models" not in saved or saved.get("llm_models") == {}

    def test_unspecified_llm_models_leaves_existing_untouched(self, fake_catalog, monkeypatch):
        """body.llm_models=None（未指定）は「変更しない」。"""
        import routes.learning as learning_mod
        from schemas import CourseUpdateRequest

        data = self._course_data(llm_models={"learning_chat": "gpt-5.2"})
        monkeypatch.setattr(learning_mod, "get_editable_course_data", lambda uid, cid: data)
        saved = {}
        monkeypatch.setattr(learning_mod, "save_course_data", lambda uid, cid, d, **kw: saved.update(d))

        body = CourseUpdateRequest(title="新タイトル")
        learning_mod.update_course("course-1", body, current_user=self.CURRENT_USER)

        assert saved["llm_models"] == {"learning_chat": "gpt-5.2"}


# ===========================================================================
# 4. 学習チャット本体・understanding_check へのコース単位モデル上書き
# ===========================================================================


CURRENT_STUDENT = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}


def _course_data(**topic_overrides) -> dict:
    topic = {"id": "topic-1", "title": "テストトピック", "chapter_index": 0, "prerequisites": []}
    topic.update(topic_overrides)
    return {
        "id": "course-1", "title": "テストコース", "domain": "テスト分野",
        "chapters": [], "topics": [topic], "concepts": [], "sources": [],
    }


def _fake_settings(**overrides) -> SimpleNamespace:
    base = dict(
        learning_chat_max_calls_per_day=300,
        learning_chat_llm_model="",
        llm_analysis_model="analysis-model-x",
        llm_fast_model="fast-model-x",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_body(message: str = "テストの質問です", history: list | None = None, **overrides):
    from schemas import LearningChatRequest

    kwargs: dict = dict(message=message, history=history or [], support_action="ask_question")
    kwargs.update(overrides)
    return LearningChatRequest(**kwargs)


@pytest.fixture
def chat_env(monkeypatch):
    """test_learning_chat_infra.py::chat_env と同型の最小フィクスチャ。"""
    import routes.learning as learning_mod
    from core.llm_worker.cost_gate import CostGate

    settings = _fake_settings()
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_policy, "get_settings", lambda: settings)
    monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())

    monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
    monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
    monkeypatch.setattr(learning_mod, "log_unanswered_query", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_prerequisites", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "_atlas_topic_attribution", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_and_count_confirm_prompt", lambda *a, **k: False)
    monkeypatch.setattr(learning_mod, "maybe_schedule_tension_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "persist_chat_history", lambda *a, **k: {"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "record_interest_trace", lambda *a, **k: "trace-1")
    monkeypatch.setattr(learning_mod, "detect_and_record_misconception", lambda *a, **k: None)
    # ライブ読みは既定で空（コース上書き無し）。個別テストで上書きする。
    monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda course_id: {})

    return SimpleNamespace(settings=settings, learning_mod=learning_mod)


# ===========================================================================
# 4b. 教員向けコース一覧 API の llm_models 投影
# ===========================================================================


@_skip_no_fastapi
class TestListTeacherCoursesLlmModelsProjection:
    """routes/admin.py::list_teacher_courses が course_llm_models(data) を投影する
    ことの確認（教員向けコース設定 UI が初期表示に使う。M9: 学生向け
    LearningCourseDetail には出さない — 別途 TestM9NoModelLeakToStudent で保証）。"""

    def _rows(self, data_by_id: dict[str, dict]):
        rows = []
        for course_id, data in data_by_id.items():
            rows.append((
                course_id, "タイトル", False, False, None, None, "owner", data,
                "private", None, "",
            ))
        return rows

    def test_projects_existing_course_override(self, monkeypatch):
        import routes.admin as admin_mod

        fake_session = MagicMock()
        fake_session.execute.return_value.fetchall.return_value = self._rows({
            "course-1": {"llm_models": {"learning_chat": "gpt-5.4-mini"}},
        })
        monkeypatch.setattr(admin_mod, "_pg_session", lambda: fake_session)

        result = admin_mod.list_teacher_courses(current_user={"id": "t1", "role": "TEACHER"})

        assert result[0]["llm_models"] == {"learning_chat": "gpt-5.4-mini"}

    def test_no_override_is_empty_dict_not_missing(self, monkeypatch):
        import routes.admin as admin_mod

        fake_session = MagicMock()
        fake_session.execute.return_value.fetchall.return_value = self._rows({"course-1": {}})
        monkeypatch.setattr(admin_mod, "_pg_session", lambda: fake_session)

        result = admin_mod.list_teacher_courses(current_user={"id": "t1", "role": "TEACHER"})

        assert result[0]["llm_models"] == {}


@_skip_no_fastapi
class TestLearningChatCourseOverride:
    def test_course_override_wins_over_env_and_tier_default(self, chat_env, monkeypatch):
        learning_mod = chat_env.learning_mod
        chat_env.settings.learning_chat_llm_model = "custom-learning-model"
        monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda course_id: {"learning_chat": "gpt-5.4-mini"})

        captured: dict = {}

        def _fake_generate_text(**kwargs):
            captured.update(kwargs)
            return "テスト応答"

        monkeypatch.setattr(learning_mod, "generate_text", _fake_generate_text)

        body = _make_body()
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_STUDENT)

        assert captured["model"] == "gpt-5.4-mini"

    def test_no_course_override_keeps_existing_resolution(self, chat_env, monkeypatch):
        """コース設定が無ければ、従来どおり resolve_model() の解決結果が使われる（挙動不変）。"""
        learning_mod = chat_env.learning_mod
        captured: dict = {}

        def _fake_generate_text(**kwargs):
            captured.update(kwargs)
            return "テスト応答"

        monkeypatch.setattr(learning_mod, "generate_text", _fake_generate_text)

        body = _make_body()
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_STUDENT)

        assert captured["model"] == chat_env.settings.llm_analysis_model

    def test_course_override_applies_to_casual_and_discuss_features_too(self, chat_env, monkeypatch):
        """_chat_feature が chat_casual/chat_discuss でも同じコース設定が効く
        （scene 単位の上書きのため feature に依存しない）。"""
        learning_mod = chat_env.learning_mod
        monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda course_id: {"learning_chat": "gpt-4o"})

        captured: dict = {}

        def _fake_generate_text(**kwargs):
            captured.update(kwargs)
            return "テスト応答"

        monkeypatch.setattr(learning_mod, "generate_text", _fake_generate_text)

        body = _make_body(intent_mode="casual")
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_STUDENT)

        assert captured["model"] == "gpt-4o"

    def test_course_override_is_read_live_independent_of_version_pinned_snapshot(self, chat_env, monkeypatch):
        """M層 Phase 3 §6.4: バージョンピン中の学習者にも live 設定が効く。

        get_course_data（版ピンが適用されうる読み取り経路）が llm_models を持たない
        スナップショットを返しても、get_course_live_llm_models（版ピン非適用の
        専用 SELECT）が別途 override を返せば、それが使われることを確認する。
        """
        learning_mod = chat_env.learning_mod
        # get_course_data はバージョンスナップショット（llm_models を含まない）を模す。
        monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
        # get_course_live_llm_models は所有者の live 設定を独立に返す。
        monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda course_id: {"learning_chat": "gpt-5.2"})

        captured: dict = {}
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: (captured.update(kwargs), "応答")[1])

        body = _make_body()
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_STUDENT)

        assert captured["model"] == "gpt-5.2"


@_skip_no_fastapi
class TestUnderstandingCheckCourseOverride:
    def test_course_override_applies_to_grading_call(self, monkeypatch):
        import routes.learning as learning_mod
        from schemas import LearningCheckQuestionRequest

        monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
        monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda course_id: {"learning_chat": "gpt-4o"})
        monkeypatch.setattr(
            learning_mod, "record_topic_check_pass",
            lambda *a, **k: {"topic_completed": True, "course_completed": False, "completed_topic_ids": []},
        )

        captured: dict = {}

        def _fake_generate_text(**kwargs):
            captured.update(kwargs)
            return '{"passed": true, "feedback": "ok", "model_answer": "ans", "explanation": ""}'

        monkeypatch.setattr(learning_mod, "generate_text", _fake_generate_text)

        body = LearningCheckQuestionRequest(answer="十分に具体的な回答をここに書きます" * 3)
        learning_mod.check_topic_understanding("course-1", "topic-1", body, current_user=CURRENT_STUDENT)

        assert captured["model"] == "gpt-4o"
        # model 明示時は reasoning_effort を強制しない（override 側の解決に委ねる）。
        assert captured.get("reasoning_effort") is None

    def test_no_override_keeps_fast_tier_default(self, monkeypatch):
        """未設定時は従来どおり fast tier 固定（挙動不変）— get_llm_params("fast") の
        結果がそのまま渡ることを確認する（core.llm.get_settings は core.config から
        別束縛されているため、ここでは get_llm_params 自体を差し替えて検証する）。"""
        import routes.learning as learning_mod
        from schemas import LearningCheckQuestionRequest

        monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
        monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda course_id: {})
        monkeypatch.setattr(
            learning_mod, "get_llm_params",
            lambda mode: {"model": "fast-tier-model", "reasoning_effort": "low"},
        )
        monkeypatch.setattr(
            learning_mod, "record_topic_check_pass",
            lambda *a, **k: {"topic_completed": True, "course_completed": False, "completed_topic_ids": []},
        )

        captured: dict = {}

        def _fake_generate_text(**kwargs):
            captured.update(kwargs)
            return '{"passed": true, "feedback": "ok", "model_answer": "ans", "explanation": ""}'

        monkeypatch.setattr(learning_mod, "generate_text", _fake_generate_text)

        body = LearningCheckQuestionRequest(answer="十分に具体的な回答をここに書きます" * 3)
        learning_mod.check_topic_understanding("course-1", "topic-1", body, current_user=CURRENT_STUDENT)

        assert captured["model"] == "fast-tier-model"
        assert captured["reasoning_effort"] == "low"


@_skip_no_fastapi
class TestGetCourseLiveLlmModelsFailOpen:
    def test_db_failure_returns_empty_dict(self, monkeypatch):
        """DB 取得に失敗してもチャットを止めない（fail-open, M2）。"""
        import services as services_mod

        def _raise_session(*a, **k):
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(services_mod, "_pg_session", _raise_session)
        assert services_mod.get_course_live_llm_models("course-1") == {}


# ===========================================================================
# 5. M9 — 学習者向けレスポンスにモデル名を出さない
# ===========================================================================


@_skip_no_fastapi
class TestM9NoModelLeakToStudent:
    def test_learning_chat_response_has_no_model_field(self, chat_env, monkeypatch):
        learning_mod = chat_env.learning_mod
        monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda course_id: {"learning_chat": "gpt-5.4-mini"})
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: "テスト応答")

        body = _make_body()
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_STUDENT)

        dumped = resp.model_dump()
        assert "model" not in dumped

    def test_understanding_check_response_has_no_model_field(self, monkeypatch):
        import routes.learning as learning_mod
        from schemas import LearningCheckQuestionRequest

        monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
        monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda course_id: {"learning_chat": "gpt-5.4-mini"})
        monkeypatch.setattr(
            learning_mod, "record_topic_check_pass",
            lambda *a, **k: {"topic_completed": True, "course_completed": False, "completed_topic_ids": []},
        )
        monkeypatch.setattr(
            learning_mod, "generate_text",
            lambda **kwargs: '{"passed": true, "feedback": "ok", "model_answer": "ans", "explanation": ""}',
        )

        body = LearningCheckQuestionRequest(answer="十分に具体的な回答をここに書きます" * 3)
        resp = learning_mod.check_topic_understanding("course-1", "topic-1", body, current_user=CURRENT_STUDENT)

        assert "model" not in resp.model_dump()

    def test_course_update_response_model_has_no_model_field(self, fake_catalog, monkeypatch):
        """CourseUpdateRequest.llm_models は保存はされるが、レスポンス
        (LearningCourseDetail) には現れない（構造上 extra キーは無視される）。"""
        import routes.learning as learning_mod
        from schemas import CourseUpdateRequest

        data = {"id": "course-1", "title": "コース", "topics": [], "chapters": [], "concepts": [], "sources": []}
        monkeypatch.setattr(learning_mod, "get_editable_course_data", lambda uid, cid: data)
        monkeypatch.setattr(learning_mod, "save_course_data", lambda uid, cid, d, **kw: None)

        body = CourseUpdateRequest(llm_models={"learning_chat": "gpt-5.4-mini"})
        result = learning_mod.update_course(
            "course-1", body, current_user={"id": "t1", "role": "TEACHER"},
        )
        assert "model" not in result.model_dump()
        assert "llm_models" not in result.model_dump()


# ===========================================================================
# 6. 教員向けセッション/操作単位の model パラメータ
# ===========================================================================


@_skip_no_fastapi
class TestCourseBuilderModelParam:
    TEACHER = {"id": "teacher-1", "username": "t1", "email": "t1@test.local", "role": "TEACHER"}

    @pytest.fixture(autouse=True)
    def _fresh_cost_gate(self):
        import routes.admin as admin_mod
        from core.llm_worker.cost_gate import CostGate

        admin_mod._course_builder_cost_gate = CostGate()
        yield

    def test_valid_model_is_passed_to_generate_text(self, fake_catalog, monkeypatch):
        import routes.admin as admin_mod
        from schemas import CourseBuilderChatRequest

        monkeypatch.setattr(admin_mod, "get_settings", lambda: SimpleNamespace(course_builder_max_calls_per_day=100))
        captured = {}

        def _fake_generate_text(**kwargs):
            captured.update(kwargs)
            return "普通の応答"

        monkeypatch.setattr(admin_mod, "generate_text", _fake_generate_text)

        body = CourseBuilderChatRequest(message="コースを作って", model="gpt-4o")
        admin_mod.course_builder_chat(body, current_user=self.TEACHER)

        assert captured["model"] == "gpt-4o"

    def test_invalid_model_returns_422_without_calling_llm(self, fake_catalog, monkeypatch):
        import routes.admin as admin_mod
        from schemas import CourseBuilderChatRequest

        monkeypatch.setattr(admin_mod, "get_settings", lambda: SimpleNamespace(course_builder_max_calls_per_day=100))

        def _unexpected(**kwargs):
            raise AssertionError("generate_text must not be called for invalid model")

        monkeypatch.setattr(admin_mod, "generate_text", _unexpected)

        body = CourseBuilderChatRequest(message="コースを作って", model="made-up-model")
        with pytest.raises(HTTPException) as exc:
            admin_mod.course_builder_chat(body, current_user=self.TEACHER)
        assert exc.value.status_code == 422

    def test_unspecified_model_keeps_existing_resolution(self, fake_catalog, monkeypatch):
        import routes.admin as admin_mod
        from schemas import CourseBuilderChatRequest

        monkeypatch.setattr(admin_mod, "get_settings", lambda: SimpleNamespace(course_builder_max_calls_per_day=100))
        captured = {}
        monkeypatch.setattr(admin_mod, "generate_text", lambda **kwargs: (captured.update(kwargs), "応答")[1])

        body = CourseBuilderChatRequest(message="コースを作って")
        admin_mod.course_builder_chat(body, current_user=self.TEACHER)

        # 環境変数/tier 既定に委ねられる（resolve_model の呼び出し引数どおり）。
        assert "model" in captured


@_skip_no_fastapi
class TestLectureStudioModelParam:
    TEACHER = {"id": "teacher-1", "username": "t1", "email": "t1@test.local", "role": "TEACHER"}

    def test_course_topic_draft_rewrite_uses_requested_model(self, fake_catalog, monkeypatch):
        import routes.lecture_studio._shared as shared_mod
        import routes.lecture_studio.topics as topics_mod

        course_data = {"id": "course-1", "title": "コース", "topics": [{"id": "topic-1", "title": "T"}]}
        # 権限ゲートの seam は _shared._course_data_for_studio_editable（教材図スタジオ §8 で共有化）。
        monkeypatch.setattr(shared_mod, "get_editable_course_data", lambda uid, cid: course_data)
        monkeypatch.setattr(topics_mod, "consume_lecture_rewrite_quota", lambda *a, **k: None)

        captured = {}

        def _fake_structured(*, messages, response_format, model):
            captured["model"] = model
            return topics_mod.CourseTopicDraftLLMResponse(key_concepts=["a"], spoken_script="s")

        monkeypatch.setattr(topics_mod, "generate_text_with_structured_output", _fake_structured)

        result = topics_mod.rewrite_lecture_studio_course_topic(
            "course-1", "topic-1",
            {"prompt": "教員の指示", "model": "gpt-5.4-mini"},
            current_user=self.TEACHER,
        )
        assert captured["model"] == "gpt-5.4-mini"
        assert result["key_concepts"] == ["a"]

    def test_course_topic_draft_rewrite_invalid_model_422(self, fake_catalog, monkeypatch):
        import routes.lecture_studio._shared as shared_mod
        import routes.lecture_studio.topics as topics_mod

        course_data = {"id": "course-1", "title": "コース", "topics": [{"id": "topic-1", "title": "T"}]}
        # 権限ゲートの seam は _shared._course_data_for_studio_editable（教材図スタジオ §8 で共有化）。
        monkeypatch.setattr(shared_mod, "get_editable_course_data", lambda uid, cid: course_data)
        monkeypatch.setattr(topics_mod, "consume_lecture_rewrite_quota", lambda *a, **k: None)

        def _unexpected(*a, **k):
            raise AssertionError("must not call LLM for invalid model")

        monkeypatch.setattr(topics_mod, "generate_text_with_structured_output", _unexpected)
        monkeypatch.setattr(topics_mod, "generate_text", _unexpected)

        with pytest.raises(HTTPException) as exc:
            topics_mod.rewrite_lecture_studio_course_topic(
                "course-1", "topic-1",
                {"prompt": "教員の指示", "model": "made-up-model"},
                current_user=self.TEACHER,
            )
        assert exc.value.status_code == 422

    def test_course_topic_draft_rewrite_unspecified_model_delegates_to_entry_resolution(
        self, fake_catalog, monkeypatch,
    ):
        """未指定なら model=None を渡し、入口（resolve_scene_model）に解決を委ねる。

        scripts.py の rewrite と同じ裁定（M層レビュー指摘 J3 同型）。以前は
        fast tier のモデルを明示引数で渡していたため解決順①（呼び出し時指定）に
        化けて、運用タブ・本人既定の設定が効かなかった。ポリシー行も env も無い
        環境では llm_policy._FEATURE_TIER_ONLY（admin:lecture_rewrite → fast）により
        従来と同じ fast tier に解決される。
        """
        import routes.lecture_studio._shared as shared_mod
        import routes.lecture_studio.topics as topics_mod

        course_data = {"id": "course-1", "title": "コース", "topics": [{"id": "topic-1", "title": "T"}]}
        # 権限ゲートの seam は _shared._course_data_for_studio_editable（教材図スタジオ §8 で共有化）。
        monkeypatch.setattr(shared_mod, "get_editable_course_data", lambda uid, cid: course_data)
        monkeypatch.setattr(topics_mod, "consume_lecture_rewrite_quota", lambda *a, **k: None)

        captured = {}

        def _fake_structured(*, messages, response_format, model):
            captured["model"] = model
            return topics_mod.CourseTopicDraftLLMResponse(key_concepts=["a"], spoken_script="s")

        monkeypatch.setattr(topics_mod, "generate_text_with_structured_output", _fake_structured)

        topics_mod.rewrite_lecture_studio_course_topic(
            "course-1", "topic-1", {"prompt": "教員の指示"}, current_user=self.TEACHER,
        )
        assert captured["model"] is None

    @patch("api.routes.lecture_studio.scripts.threading.Thread")
    @patch("api.routes.lecture_studio.scripts.create_background_task")
    @patch("api.routes.lecture_studio.scripts._get_course_chunks")
    @patch("api.routes.lecture_studio.scripts.get_editable_course_data")
    def test_batch_generate_scripts_threads_requested_model(
        self, mock_course, mock_chunks, mock_create, mock_thread, fake_catalog,
    ):
        from api.routes.lecture_studio import batch_generate_scripts
        from schemas import LectureScriptGenerateRequest

        mock_course.return_value = {"lecture_studio_settings": {}}
        mock_chunks.return_value = [{"id": "c1", "chunk_index": 0, "text": "t", "spoken_text": ""}]
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        resp = batch_generate_scripts(
            "course-1",
            LectureScriptGenerateRequest(model="gpt-5.4-mini"),
            self.TEACHER,
        )

        assert resp.status == "pending" if hasattr(resp, "status") else True
        thread_args = mock_thread.call_args.kwargs["args"]
        assert thread_args[-1] == "gpt-5.4-mini"
        mock_thread_instance.start.assert_called_once()

    @patch("api.routes.lecture_studio.scripts.threading.Thread")
    @patch("api.routes.lecture_studio.scripts.create_background_task")
    @patch("api.routes.lecture_studio.scripts._get_course_chunks")
    @patch("api.routes.lecture_studio.scripts.get_editable_course_data")
    def test_batch_generate_scripts_invalid_model_422_before_thread(
        self, mock_course, mock_chunks, mock_create, mock_thread, fake_catalog,
    ):
        from api.routes.lecture_studio import batch_generate_scripts
        from schemas import LectureScriptGenerateRequest

        mock_course.return_value = {"lecture_studio_settings": {}}
        mock_chunks.return_value = [{"id": "c1", "chunk_index": 0, "text": "t", "spoken_text": ""}]

        with pytest.raises(HTTPException) as exc:
            batch_generate_scripts(
                "course-1", LectureScriptGenerateRequest(model="made-up-model"), self.TEACHER,
            )
        assert exc.value.status_code == 422
        mock_thread.assert_not_called()

    @patch("api.routes.lecture_studio.scripts.consume_lecture_rewrite_quota")
    @patch("api.routes.lecture_studio.scripts._ensure_chunk_editable", return_value="material-1")
    @patch("api.routes.lecture_studio.scripts._pg_session")
    @patch("api.routes.lecture_studio.scripts.generate_text")
    def test_rewrite_lecture_script_uses_requested_model(
        self, mock_gen, mock_pg, mock_edit, mock_quota, fake_catalog,
    ):
        from api.routes.lecture_studio import scripts as scripts_mod
        from schemas import LectureScriptRewriteRequest

        fake_session = MagicMock()
        fake_session.execute.return_value.fetchone.return_value = (
            "source text", "display", "spoken", [], "", None, None,
        )
        mock_pg.return_value = fake_session
        mock_gen.return_value = json.dumps({"display_text": "d2", "spoken_text": "s2", "formulas": []})

        body = LectureScriptRewriteRequest(prompt="rewrite this", model="gpt-5.4-mini")
        scripts_mod.rewrite_lecture_script(
            "550e8400-e29b-41d4-a716-446655440000", body, self.TEACHER,
        )

        assert mock_gen.call_args.kwargs["model"] == "gpt-5.4-mini"
        assert mock_gen.call_args.kwargs["reasoning_effort"] is None


@_skip_no_fastapi
class TestAtlasSkeletonModelValidation:
    """既存の GenerateSkeletonRequest.model は Phase 3 以前から存在するが、これまで
    カタログ検証を経ていなかった。ここではその新規追加分（fail-closed 化）のみを検証する。"""

    def test_invalid_model_returns_422_without_reaching_generator(self, fake_catalog, monkeypatch):
        import routes.atlas as atlas_routes

        def _unexpected(*a, **k):
            raise AssertionError("must not touch DB/generator for invalid model")

        monkeypatch.setattr(atlas_routes, "_skeleton_session", _unexpected)

        body = atlas_routes.GenerateSkeletonRequest(model="made-up-model")
        with pytest.raises(HTTPException) as exc:
            atlas_routes.generate_atlas_skeleton(
                "particle_physics", body, current_user={"id": "t1", "role": "TEACHER"},
            )
        assert exc.value.status_code == 422

    def test_valid_model_passes_validation_gate(self, fake_catalog, monkeypatch):
        import routes.atlas as atlas_routes

        class _PassedValidation(Exception):
            pass

        def _marker(*a, **k):
            raise _PassedValidation("reached past model validation")

        monkeypatch.setattr(atlas_routes, "_skeleton_session", _marker)

        body = atlas_routes.GenerateSkeletonRequest(model="gpt-5.2")
        with pytest.raises(_PassedValidation):
            atlas_routes.generate_atlas_skeleton(
                "particle_physics", body, current_user={"id": "t1", "role": "TEACHER"},
            )

    def test_unspecified_model_skips_validation_entirely(self, monkeypatch):
        """model 未指定なら従来どおり検証をスキップし、既存の分岐へ直接進む。"""
        import routes.atlas as atlas_routes

        class _PassedValidation(Exception):
            pass

        def _marker(*a, **k):
            raise _PassedValidation("reached past model validation")

        monkeypatch.setattr(atlas_routes, "_skeleton_session", _marker)
        # カタログ未設定でも model 未指定なら 422 にならない（validate は model が truthy な時だけ走る）。
        monkeypatch.setattr(atlas_routes.llm_policy, "load_catalog", lambda: None)

        body = atlas_routes.GenerateSkeletonRequest()
        with pytest.raises(_PassedValidation):
            atlas_routes.generate_atlas_skeleton(
                "particle_physics", body, current_user={"id": "t1", "role": "TEACHER"},
            )


@_skip_no_fastapi
class TestDeliberationModelParam:
    OWNER_ID = "22222222-2222-2222-2222-222222222222"
    TEACHER = {"id": OWNER_ID, "username": "t1", "email": "t1@test.local", "role": "TEACHER"}

    def _patch_session(self, monkeypatch, route_mod, element_type="theory_claim"):
        from core.deliberation.schema import ElementRef, SCOPE_DOCUMENT

        monkeypatch.setattr(
            route_mod.delib_store, "get_session_by_id",
            lambda _id: {
                "id": _id, "created_by": self.OWNER_ID, "scope": "document", "element_type": element_type,
                "element_id": "el-1", "document_id": "doc-1", "domain_key": None, "title": "",
                "messages": [], "created_at": "2026-01-01T00:00:00",
            },
        )
        monkeypatch.setattr(
            route_mod.refs, "resolve",
            lambda *a, **k: ElementRef(
                scope=SCOPE_DOCUMENT, element_type=element_type, element_id="el-1", document_id="doc-1",
            ),
        )
        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.dialogue, "check_and_count_llm_call", lambda *a, **k: True)

    def test_invalid_model_returns_422_before_llm_call(self, fake_catalog, monkeypatch):
        import routes.deliberation as route_mod

        self._patch_session(monkeypatch, route_mod)

        def _unexpected(*a, **k):
            raise AssertionError("must not build grounding for invalid model")

        monkeypatch.setattr(route_mod.dialogue, "build_grounding", _unexpected)

        body = route_mod.MessageCreateRequest(content="質問です", model="made-up-model")
        with pytest.raises(HTTPException) as exc:
            route_mod.post_deliberation_message("s1", body, current_user=self.TEACHER)
        assert exc.value.status_code == 422

    def test_valid_text_model_is_passed_to_run_turn(self, fake_catalog, monkeypatch):
        import routes.deliberation as route_mod
        from core.deliberation.dialogue import DialogueTurnResult

        self._patch_session(monkeypatch, route_mod)
        monkeypatch.setattr(
            route_mod.dialogue, "build_grounding",
            lambda ref: {"breakdown": {}, "positioning": {"available": False}},
        )
        monkeypatch.setattr(route_mod.dialogue, "grounding_to_text", lambda g: "GROUNDING")
        monkeypatch.setattr(route_mod.delib_store, "append_messages", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.delib_annotations, "create_candidates_from_dialogue", lambda *a, **k: [])
        monkeypatch.setattr(route_mod, "record_review_event", lambda *a, **k: None)

        captured = {}

        def _fake_run_turn(*args, **kwargs):
            captured["model"] = kwargs.get("model")
            return DialogueTurnResult(reply="回答です", annotations=[], degraded=False)

        monkeypatch.setattr(route_mod.dialogue, "run_turn", _fake_run_turn)

        body = route_mod.MessageCreateRequest(content="質問です", model="gpt-5.4-mini")
        route_mod.post_deliberation_message("s1", body, current_user=self.TEACHER)

        assert captured["model"] == "gpt-5.4-mini"

    def test_figure_element_requires_vision_capability(self, fake_catalog, monkeypatch):
        """figure 要素への model 指定は vision capability が無いと 422（M5）。"""
        import routes.deliberation as route_mod
        from core.deliberation.schema import ELEMENT_FIGURE

        self._patch_session(monkeypatch, route_mod, element_type=ELEMENT_FIGURE)

        def _unexpected(*a, **k):
            raise AssertionError("must not build grounding for invalid (non-vision) model on figure")

        monkeypatch.setattr(route_mod.dialogue, "build_grounding", _unexpected)

        body = route_mod.MessageCreateRequest(content="この図について", model="gpt-5.4-mini")
        with pytest.raises(HTTPException) as exc:
            route_mod.post_deliberation_message("s1", body, current_user=self.TEACHER)
        assert exc.value.status_code == 422

    def test_figure_element_accepts_vision_model(self, fake_catalog, monkeypatch):
        import routes.deliberation as route_mod
        from core.deliberation.dialogue import DialogueTurnResult
        from core.deliberation.schema import ELEMENT_FIGURE

        self._patch_session(monkeypatch, route_mod, element_type=ELEMENT_FIGURE)
        monkeypatch.setattr(
            route_mod.dialogue, "build_grounding",
            lambda ref: {"breakdown": {}, "positioning": {"available": False}},
        )
        monkeypatch.setattr(route_mod.dialogue, "grounding_to_text", lambda g: "GROUNDING")
        monkeypatch.setattr(route_mod.dialogue, "figure_image_bytes", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.delib_store, "append_messages", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.delib_annotations, "create_candidates_from_dialogue", lambda *a, **k: [])
        monkeypatch.setattr(route_mod, "record_review_event", lambda *a, **k: None)

        captured = {}

        def _fake_run_turn(*args, **kwargs):
            captured["model"] = kwargs.get("model")
            return DialogueTurnResult(reply="回答です", annotations=[], degraded=False)

        monkeypatch.setattr(route_mod.dialogue, "run_turn", _fake_run_turn)

        body = route_mod.MessageCreateRequest(content="この図について", model="gpt-4o")
        route_mod.post_deliberation_message("s1", body, current_user=self.TEACHER)

        assert captured["model"] == "gpt-4o"

    def test_unspecified_model_keeps_existing_resolution(self, fake_catalog, monkeypatch):
        import routes.deliberation as route_mod
        from core.deliberation.dialogue import DialogueTurnResult

        self._patch_session(monkeypatch, route_mod)
        monkeypatch.setattr(
            route_mod.dialogue, "build_grounding",
            lambda ref: {"breakdown": {}, "positioning": {"available": False}},
        )
        monkeypatch.setattr(route_mod.dialogue, "grounding_to_text", lambda g: "GROUNDING")
        monkeypatch.setattr(route_mod.delib_store, "append_messages", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.delib_annotations, "create_candidates_from_dialogue", lambda *a, **k: [])
        monkeypatch.setattr(route_mod, "record_review_event", lambda *a, **k: None)

        captured = {}

        def _fake_run_turn(*args, **kwargs):
            captured["model"] = kwargs.get("model")
            return DialogueTurnResult(reply="回答です", annotations=[], degraded=False)

        monkeypatch.setattr(route_mod.dialogue, "run_turn", _fake_run_turn)

        body = route_mod.MessageCreateRequest(content="質問です")
        route_mod.post_deliberation_message("s1", body, current_user=self.TEACHER)

        assert captured["model"] is None
