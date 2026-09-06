"""コーパス回遊 Phase B — コース無し論文議論（document 直付け discuss）の API テスト。

正本設計書: ``docs/features/corpus_roaming_design.md`` §5（CR1/CR2/CR8/CR9）。
親層: ``docs/features/discussion_mode_design.md``（DM1〜DM8）。

対象:
- ``backend/core/discuss/context.py``（センチネルの正本）
- ``backend/api/routes/learning.py`` の4エンドポイント
  （opening / chat / history / messages DELETE）と共通コア ``_learning_chat_core``

手法は ``test_anchor_ladder_hint.py`` / ``test_learning_chat_infra.py`` と同型:
DB・実 LLM には触れず、``routes.learning`` モジュールの境界関数を monkeypatch で
差し替えたうえで、ルート関数を FastAPI を経由せず直接呼び出す。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.learning as learning_mod  # noqa: E402
import core.llm_policy as llm_policy_mod  # noqa: E402
from core.discuss import context as discuss_context  # noqa: E402
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402
from services import DocumentAccess  # noqa: E402

DOC_ID = "11111111-1111-1111-1111-111111111111"
OTHER_DOC_ID = "22222222-2222-2222-2222-222222222222"
USER = {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "role": "STUDENT"}


def _fake_settings(**overrides) -> SimpleNamespace:
    base = dict(
        learning_chat_max_calls_per_day=300,
        learning_chat_llm_model="",
        llm_analysis_model="analysis-model-x",
        llm_fast_model="fast-model-x",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def viewable(monkeypatch):
    """DOC_ID は閲覧可能・OTHER_DOC_ID は閲覧不可（= 存在しても 404）にする。"""

    def _resolve(user_id, ref):
        if ref in (DOC_ID, "paper-a.pdf"):
            return DocumentAccess(
                document_id=DOC_ID,
                source_path="paper-a.pdf",
                uploaded_by=USER["id"],
                is_owner=True,
                can_view=True,
                can_edit=True,
            )
        if ref in (OTHER_DOC_ID, "paper-b.pdf"):
            # 他人の private 論文: 行は見つかるが can_view=False
            return DocumentAccess(
                document_id=OTHER_DOC_ID,
                source_path="paper-b.pdf",
                uploaded_by="someone-else",
                is_owner=False,
                can_view=False,
                can_edit=False,
            )
        return DocumentAccess(document_id=None)

    monkeypatch.setattr(learning_mod, "resolve_document_access", _resolve)
    # タイトル解決（core.personal_graph.queries.fetch_document_titles）は関数内 import
    # のため、モジュール属性を差し替える。
    import core.personal_graph.queries as pg_queries

    monkeypatch.setattr(
        pg_queries, "fetch_document_titles", lambda ids: {DOC_ID: "Paper A のタイトル"}
    )
    events: list[dict] = []
    monkeypatch.setattr(
        learning_mod.discuss_observation,
        "insert_metric_events",
        lambda uid, evs: events.extend(evs) or len(evs),
    )
    return SimpleNamespace(events=events)


@pytest.fixture
def chat_env(monkeypatch, viewable):
    """document 直付け chat をフル実行できるよう DB/LLM 境界だけモックする。"""
    settings = _fake_settings()
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_policy_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())

    course_data_mock = MagicMock(side_effect=AssertionError("コース解決へ流入した"))
    monkeypatch.setattr(learning_mod, "get_course_data", course_data_mock)
    live_models_mock = MagicMock(side_effect=AssertionError("learning_courses を引いた"))
    monkeypatch.setattr(learning_mod, "get_course_live_llm_models", live_models_mock)

    search_mock = MagicMock(return_value=[])
    monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", search_mock)
    monkeypatch.setattr(learning_mod, "log_unanswered_query", lambda *a, **k: None)
    monkeypatch.setattr(
        learning_mod, "check_prerequisites",
        MagicMock(side_effect=AssertionError("前提知識ゲートに入った")),
    )
    monkeypatch.setattr(learning_mod, "_atlas_topic_attribution", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_and_count_confirm_prompt", lambda *a, **k: False)
    monkeypatch.setattr(learning_mod, "maybe_schedule_tension_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", lambda *a, **k: None)
    visible_mock = MagicMock(return_value={DOC_ID, OTHER_DOC_ID})
    monkeypatch.setattr(learning_mod, "list_visible_document_ids", visible_mock)
    monkeypatch.setattr(
        learning_mod, "list_course_source_document_ids",
        MagicMock(side_effect=AssertionError("コース sources 解決に入った")),
    )
    monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: "AI の応答本文")

    persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
    trace_mock = MagicMock(return_value="trace-1")
    monkeypatch.setattr(learning_mod, "record_interest_trace", trace_mock)
    monkeypatch.setattr(
        learning_mod, "detect_and_record_misconception", MagicMock(return_value=None)
    )

    return SimpleNamespace(
        settings=settings,
        search_mock=search_mock,
        visible_mock=visible_mock,
        persist_mock=persist_mock,
        trace_mock=trace_mock,
        events=viewable.events,
        get_course_data=course_data_mock,
        get_course_live_llm_models=live_models_mock,
    )


def _body(message: str = "この論文の主張はどこが弱いですか", **overrides) -> LearningChatRequest:
    kwargs: dict = dict(message=message, history=[])
    kwargs.update(overrides)
    return LearningChatRequest(**kwargs)


# ===========================================================================
# 1. センチネル（設計 §5.1）
# ===========================================================================


class TestDocumentContextSentinel:
    def test_prefix_and_roundtrip(self):
        cid = discuss_context.document_context_id(DOC_ID)
        assert cid == f"_doc:{DOC_ID}"
        assert cid.startswith(discuss_context.DOCUMENT_CONTEXT_PREFIX)
        assert discuss_context.parse_document_context(cid) == DOC_ID
        assert discuss_context.is_document_context(cid) is True

    def test_non_sentinel_returns_none(self):
        for value in ("", None, "course-uuid-1234", "_discussion", "_doc", "doc:x"):
            assert discuss_context.parse_document_context(value) is None
            assert discuss_context.is_document_context(value) is False

    def test_empty_document_id_is_rejected(self):
        """解決漏れをセンチネルとして黙って通さない（fail-closed）。"""
        for bad in ("", "   ", None):
            with pytest.raises(ValueError):
                discuss_context.document_context_id(bad)  # type: ignore[arg-type]

    def test_prefix_only_sentinel_parses_to_none(self):
        assert discuss_context.parse_document_context("_doc:") is None
        assert discuss_context.parse_document_context("_doc:   ") is None

    def test_sentinel_never_collides_with_a_course_uuid(self):
        """実在コース id（uuid4 由来）は接頭辞と衝突しない。"""
        import uuid as _uuid

        for _ in range(20):
            assert discuss_context.parse_document_context(str(_uuid.uuid4())) is None


# ===========================================================================
# 2. 可視性 fail-closed（CR1）
# ===========================================================================


class TestVisibilityGate:
    def test_opening_rejects_non_viewable_document_with_404(self, viewable, monkeypatch):
        monkeypatch.setattr(
            learning_mod, "build_discussion_opening",
            MagicMock(side_effect=AssertionError("ゲートを抜けて opening が走った")),
        )
        with pytest.raises(HTTPException) as exc:
            learning_mod.get_document_discussion_opening(OTHER_DOC_ID, USER)
        assert exc.value.status_code == 404

    def test_opening_rejects_unknown_document_with_404(self, viewable, monkeypatch):
        monkeypatch.setattr(learning_mod, "build_discussion_opening", MagicMock())
        with pytest.raises(HTTPException) as exc:
            learning_mod.get_document_discussion_opening("no-such-doc", USER)
        assert exc.value.status_code == 404

    def test_not_viewable_and_not_found_are_indistinguishable(self, viewable, monkeypatch):
        """存在推測をさせない: 閲覧不可も不在も同じ 404 + 同じ detail。"""
        monkeypatch.setattr(learning_mod, "build_discussion_opening", MagicMock())
        codes = []
        for ref in (OTHER_DOC_ID, "no-such-doc"):
            with pytest.raises(HTTPException) as exc:
                learning_mod.get_document_discussion_opening(ref, USER)
            codes.append((exc.value.status_code, exc.value.detail))
        assert codes[0] == codes[1]

    def test_chat_rejects_non_viewable_document(self, chat_env):
        with pytest.raises(HTTPException) as exc:
            learning_mod.document_discuss_chat(OTHER_DOC_ID, _body(), USER)
        assert exc.value.status_code == 404
        chat_env.search_mock.assert_not_called()

    def test_history_rejects_non_viewable_document(self, viewable):
        with pytest.raises(HTTPException) as exc:
            learning_mod.get_document_discussion_history(OTHER_DOC_ID, USER)
        assert exc.value.status_code == 404

    def test_delete_rejects_non_viewable_document(self, viewable, monkeypatch):
        monkeypatch.setattr(
            learning_mod, "truncate_chat_and_supersede",
            MagicMock(side_effect=AssertionError("ゲートを抜けて truncate が走った")),
        )
        with pytest.raises(HTTPException) as exc:
            learning_mod.delete_document_discussion_message_from(OTHER_DOC_ID, "msg-1", USER)
        assert exc.value.status_code == 404

    def test_source_path_ref_is_accepted(self, viewable, monkeypatch):
        """document_ref は documents.id / source_path(material_id) の両対応。"""
        monkeypatch.setattr(
            learning_mod, "build_discussion_opening",
            lambda cid, ids, course_focus="": {"course_id": cid, "available": True, "documents": []},
        )
        result = learning_mod.get_document_discussion_opening("paper-a.pdf", USER)
        assert result["document_context"]["document_id"] == DOC_ID


# ===========================================================================
# 3. opening（設計 §5.3）
# ===========================================================================


class TestDocumentOpening:
    def test_calls_build_opening_with_sentinel_and_single_document(self, viewable, monkeypatch):
        calls: list[tuple] = []

        def _fake(course_id, document_ids, course_focus=""):
            calls.append((course_id, list(document_ids), course_focus))
            return {"course_id": course_id, "available": True, "documents": [], "fragile_points": []}

        monkeypatch.setattr(learning_mod, "build_discussion_opening", _fake)
        result = learning_mod.get_document_discussion_opening(DOC_ID, USER)

        assert calls == [(f"_doc:{DOC_ID}", [DOC_ID], "")]
        ctx = result["document_context"]
        assert ctx["document_id"] == DOC_ID
        assert ctx["context_id"] == f"_doc:{DOC_ID}"
        assert ctx["topic_id"] == learning_mod.DISCUSSION_TOPIC_ID
        assert ctx["title"] == "Paper A のタイトル"
        assert ctx["label"] == "論文との議論（コース外）"

    def test_opening_makes_no_llm_call(self, viewable, monkeypatch):
        """CR9: 開幕は LLM 0回（build_opening 自体が非LLM。ルートも呼ばない）。"""
        monkeypatch.setattr(
            learning_mod, "generate_text",
            MagicMock(side_effect=AssertionError("opening が LLM を呼んだ")),
        )
        monkeypatch.setattr(
            learning_mod, "build_discussion_opening",
            lambda cid, ids, course_focus="": {"course_id": cid, "available": False, "documents": []},
        )
        learning_mod.get_document_discussion_opening(DOC_ID, USER)

    def test_opening_does_not_resolve_course(self, viewable, monkeypatch):
        monkeypatch.setattr(
            learning_mod, "get_course_data",
            MagicMock(side_effect=AssertionError("コース解決へ流入した")),
        )
        monkeypatch.setattr(
            learning_mod, "build_discussion_opening",
            lambda cid, ids, course_focus="": {"course_id": cid, "available": False, "documents": []},
        )
        learning_mod.get_document_discussion_opening(DOC_ID, USER)

    def test_records_document_discuss_opened_event(self, viewable, monkeypatch):
        monkeypatch.setattr(
            learning_mod, "build_discussion_opening",
            lambda cid, ids, course_focus="": {"course_id": cid, "available": False, "documents": []},
        )
        learning_mod.get_document_discussion_opening(DOC_ID, USER)
        assert [e["event"] for e in viewable.events] == ["document_discuss_opened"]
        assert viewable.events[0]["course_id"] == f"_doc:{DOC_ID}"
        assert viewable.events[0]["payload"] == {}

    def test_metric_failure_does_not_break_opening(self, viewable, monkeypatch):
        """DO6: 計測失敗で UX を止めない。"""
        monkeypatch.setattr(
            learning_mod.discuss_observation, "insert_metric_events",
            MagicMock(side_effect=RuntimeError("db down")),
        )
        monkeypatch.setattr(
            learning_mod, "build_discussion_opening",
            lambda cid, ids, course_focus="": {"course_id": cid, "available": True, "documents": []},
        )
        result = learning_mod.get_document_discussion_opening(DOC_ID, USER)
        assert result["available"] is True


# ===========================================================================
# 4. chat（設計 §5.2）
# ===========================================================================


class TestDocumentChat:
    def test_rag_scope_is_the_single_document(self, chat_env):
        learning_mod.document_discuss_chat(DOC_ID, _body(), USER)
        assert chat_env.search_mock.call_count == 1
        kwargs = chat_env.search_mock.call_args.kwargs
        assert kwargs["allowed_document_ids"] == {DOC_ID}
        chat_env.visible_mock.assert_not_called()

    def test_all_visible_scope_widens_to_personal_visible_set(self, chat_env):
        learning_mod.document_discuss_chat(DOC_ID, _body(discuss_scope="all_visible"), USER)
        kwargs = chat_env.search_mock.call_args.kwargs
        assert kwargs["allowed_document_ids"] == {DOC_ID, OTHER_DOC_ID}

    def test_invalid_discuss_scope_is_422(self, chat_env):
        with pytest.raises(HTTPException) as exc:
            learning_mod.document_discuss_chat(DOC_ID, _body(discuss_scope="everything"), USER)
        assert exc.value.status_code == 422

    def test_history_persisted_under_sentinel_key(self, chat_env):
        learning_mod.document_discuss_chat(DOC_ID, _body(), USER)
        args = chat_env.persist_mock.call_args.args
        assert args[1] == f"_doc:{DOC_ID}"
        assert args[2] == learning_mod.DISCUSSION_TOPIC_ID

    def test_trace_recorded_under_sentinel_key_with_out_of_course_label(self, chat_env):
        learning_mod.document_discuss_chat(DOC_ID, _body(), USER)
        args = chat_env.trace_mock.call_args.args
        kwargs = chat_env.trace_mock.call_args.kwargs
        assert args[1] == f"_doc:{DOC_ID}"
        assert args[2] == learning_mod.DISCUSSION_TOPIC_ID
        assert kwargs["context_label"] == "論文との議論（コース外）"
        assert kwargs["extra_payload"]["entry_mode"] == "discuss"

    def test_course_resolution_is_never_touched(self, chat_env):
        learning_mod.document_discuss_chat(DOC_ID, _body(), USER)
        chat_env.get_course_data.assert_not_called()
        chat_env.get_course_live_llm_models.assert_not_called()

    def test_forces_discuss_mode_and_drops_course_only_payloads(self, chat_env):
        body = _body(
            intent_mode="explore",
            action="EXPLAIN_GRAPH_ELEMENT",
            atlas_context={"node_id": "n1"},
            cycle_mode="elicit",
        )
        learning_mod.document_discuss_chat(DOC_ID, body, USER)
        assert body.intent_mode == "discuss"
        assert body.action is None
        assert body.atlas_context is None
        assert body.cycle_mode is None

    def test_response_is_not_detour_and_has_discuss_shape(self, chat_env):
        result = learning_mod.document_discuss_chat(DOC_ID, _body(), USER)
        # discuss は「寄り道」化しない（DM5）
        assert result.origin is None
        assert result.mock is False
        assert "AI の応答本文" in result.answer

    def test_records_document_discuss_turn_event(self, chat_env):
        learning_mod.document_discuss_chat(DOC_ID, _body(), USER)
        assert [e["event"] for e in chat_env.events] == ["document_discuss_turn"]
        assert chat_env.events[0]["course_id"] == f"_doc:{DOC_ID}"
        assert chat_env.events[0]["payload"] == {}

    def test_llm_failure_degrades_but_keeps_200(self, chat_env, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("llm down")

        monkeypatch.setattr(learning_mod, "generate_text", _boom)
        result = learning_mod.document_discuss_chat(DOC_ID, _body(), USER)
        assert result.degraded is True
        chat_env.persist_mock.assert_called_once()

    def test_replace_message_id_truncates_under_sentinel_key(self, chat_env, monkeypatch):
        trunc = MagicMock(return_value={"truncated_history": [], "removed_count": 2})
        monkeypatch.setattr(learning_mod, "truncate_chat_and_supersede", trunc)
        learning_mod.document_discuss_chat(DOC_ID, _body(replace_message_id="msg-9"), USER)
        assert trunc.call_args.args[1] == f"_doc:{DOC_ID}"
        assert trunc.call_args.args[2] == learning_mod.DISCUSSION_TOPIC_ID
        assert trunc.call_args.args[3] == "msg-9"

    def test_empty_hit_uses_no_expansion_notice_not_a_wider_search(self, chat_env):
        """DM1: 該当チャンクゼロでも他スコープへ無断で広げない。"""
        learning_mod.document_discuss_chat(DOC_ID, _body(), USER)
        assert chat_env.search_mock.call_count == 1
        chat_env.visible_mock.assert_not_called()


# ===========================================================================
# 5. history / delete（設計 §5.3・CR8）
# ===========================================================================


class TestDocumentHistoryAndDelete:
    def test_history_reads_sentinel_key(self, viewable, monkeypatch):
        seen: dict = {}

        def _fake_get_chat_history(course_id, topic_id, current_user):
            seen["key"] = (course_id, topic_id)
            from schemas import LearningChatHistoryResponse

            return LearningChatHistoryResponse(history=[{"role": "user", "content": "x"}])

        monkeypatch.setattr(learning_mod, "get_chat_history", _fake_get_chat_history)
        out = learning_mod.get_document_discussion_history(DOC_ID, USER)
        assert seen["key"] == (f"_doc:{DOC_ID}", learning_mod.DISCUSSION_TOPIC_ID)
        assert len(out.history) == 1

    def test_delete_uses_truncate_semantics_under_sentinel_key(self, viewable, monkeypatch):
        trunc = MagicMock(return_value={"removed_count": 3})
        monkeypatch.setattr(learning_mod, "truncate_chat_and_supersede", trunc)
        out = learning_mod.delete_document_discussion_message_from(DOC_ID, "msg-7", USER)
        assert trunc.call_args.args[:4] == (
            USER["id"], f"_doc:{DOC_ID}", learning_mod.DISCUSSION_TOPIC_ID, "msg-7",
        )
        assert out == {"status": "deleted", "removed_count": 3}

    def test_delete_unknown_message_is_404(self, viewable, monkeypatch):
        monkeypatch.setattr(
            learning_mod, "truncate_chat_and_supersede", MagicMock(return_value=None)
        )
        with pytest.raises(HTTPException) as exc:
            learning_mod.delete_document_discussion_message_from(DOC_ID, "nope", USER)
        assert exc.value.status_code == 404


# ===========================================================================
# 6. 既存コース discuss の挙動不変（CR2）
# ===========================================================================


class TestCourseDiscussUnchanged:
    def test_course_path_still_resolves_course_and_uses_course_sources(self, monkeypatch):
        settings = _fake_settings()
        monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
        monkeypatch.setattr(llm_policy_mod, "get_settings", lambda: settings)
        monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())

        course_data = {
            "title": "コースA", "topics": [{"id": "t1", "title": "トピック1"}],
            "concepts": [], "sources": [{"material_id": "paper-a.pdf"}],
        }
        get_course = MagicMock(return_value=course_data)
        monkeypatch.setattr(learning_mod, "get_course_data", get_course)
        monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda cid: {})
        course_sources = MagicMock(return_value={DOC_ID})
        monkeypatch.setattr(learning_mod, "list_course_source_document_ids", course_sources)
        search_mock = MagicMock(return_value=[])
        monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", search_mock)
        monkeypatch.setattr(learning_mod, "log_unanswered_query", lambda *a, **k: None)
        monkeypatch.setattr(learning_mod, "check_prerequisites", lambda *a, **k: None)
        monkeypatch.setattr(learning_mod, "_atlas_topic_attribution", lambda *a, **k: None)
        monkeypatch.setattr(learning_mod, "check_and_count_confirm_prompt", lambda *a, **k: False)
        monkeypatch.setattr(learning_mod, "maybe_schedule_tension_mining", lambda *a, **k: None)
        monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", lambda *a, **k: None)
        monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda *a, **k: set())
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: "コース側の回答")
        persist = MagicMock(return_value={"user_message_id": "m1"})
        monkeypatch.setattr(learning_mod, "persist_chat_history", persist)
        trace = MagicMock(return_value="tr")
        monkeypatch.setattr(learning_mod, "record_interest_trace", trace)
        monkeypatch.setattr(
            learning_mod, "detect_and_record_misconception", MagicMock(return_value=None)
        )

        result = learning_mod.learning_chat(
            "course-1", learning_mod.DISCUSSION_TOPIC_ID,
            _body(intent_mode="discuss"), USER,
        )
        get_course.assert_called_once_with(USER["id"], "course-1")
        course_sources.assert_called_once()
        assert search_mock.call_args.kwargs["allowed_document_ids"] == {DOC_ID}
        # コース経路のラベルは従来どおり（「（コース外）」を名乗らない）
        assert trace.call_args.kwargs["context_label"] == "論文との議論"
        assert "コース側の回答" in result.answer

    def test_course_not_found_still_404(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "get_course_data", lambda uid, cid: None)
        with pytest.raises(HTTPException) as exc:
            learning_mod.learning_chat("missing", "t1", _body(), USER)
        assert exc.value.status_code == 404

    def test_sentinel_pushed_through_the_course_route_is_404(self, monkeypatch):
        """センチネルを course 経路に直接投げても、実在コース行が無いので 404。

        設計 §11「センチネル course_id がコース解決へ流入しない」を、ファサードの
        配線だけでなく **course 経路側の構造** でも確認する（learning_courses.id は
        uuid4 由来のため `_doc:` 接頭辞の行は存在し得ない）。
        """
        seen: list[str] = []

        def _fake_get_course_data(uid, cid):
            seen.append(cid)
            return None  # 実在しない = 404

        monkeypatch.setattr(learning_mod, "get_course_data", _fake_get_course_data)
        with pytest.raises(HTTPException) as exc:
            learning_mod.learning_chat(
                f"_doc:{DOC_ID}", learning_mod.DISCUSSION_TOPIC_ID, _body(), USER
            )
        assert exc.value.status_code == 404
        assert seen == [f"_doc:{DOC_ID}"]
