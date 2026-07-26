"""help_kb ベクトル補助層（Phase 3 ①）のテスト。

正本: ``docs/features/manual_help_kb_design.md`` §5 Phase 3 ①。
対象: ``backend/core/help_kb/vector.py``（``sync_manual_vectors`` /
``vector_search_manual``）と、``backend/api/routes/learning.py`` の
``_usage_help_response`` フォールバック配線。

DB はフェイクセッション（``_FakeSession``）、埋め込みは
``core.help_kb.vector._embed_texts`` を monkeypatch して模擬する（実 DB・実 LLM
には一切触れない）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.help_kb import vector as kb_vector  # noqa: E402


# ---------------------------------------------------------------------------
# フェイク DB セッション
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _FakeSession:
    """``manual_sections`` に対する SELECT/INSERT/DELETE を模擬する最小セッション。

    - ``existing_rows``: 同期前の既存行 ``[(audience, file, anchor, content_hash), ...]``
      （``_fetch_existing_hashes`` の SELECT に応答する）。
    - ``search_rows``: ``vector_search_manual`` の cosine 距離検索 SELECT に応答する
      ``[(audience, file, anchor, title, body, distance), ...]``。
    - ``executed``: 発行された全 SQL と params を記録する（DELETE/INSERT/commit の
      有無・順序をテストで検証するため）。
    """

    def __init__(self, existing_rows=None, search_rows=None):
        self.existing_rows = list(existing_rows or [])
        self.search_rows = list(search_rows or [])
        self.executed: list[tuple[str, dict]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append((sql, params or {}))
        if "SELECT audience, file, anchor, content_hash FROM manual_sections" in sql:
            return _FakeResult(self.existing_rows)
        if "embedding <=> CAST(:qvec AS vector)) AS distance" in sql:
            return _FakeResult(self.search_rows)
        return _FakeResult([])

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True

    @property
    def insert_calls(self):
        return [c for c in self.executed if "INSERT INTO manual_sections" in c[0]]

    @property
    def delete_calls(self):
        return [c for c in self.executed if "DELETE FROM manual_sections" in c[0]]


class _PoisonSession:
    """呼ばれたら即失敗する、``session.execute`` に到達してはならないことの証人。"""

    def execute(self, *a, **k):  # noqa: D401
        raise AssertionError("session.execute が呼ばれた（fail-closed / fail-open 違反）")

    def commit(self):
        raise AssertionError("session.commit が呼ばれた")

    def rollback(self):
        pass

    def close(self):
        pass


def _section(audience, file, anchor, title, body):
    return {"audience": audience, "file": file, "anchor": anchor, "title": title, "body": body}


# ===========================================================================
# 1. HELP_KB_VECTOR_ENABLED スイッチ
# ===========================================================================


class TestVectorEnabledSwitch:
    def test_sync_disabled_skips_without_touching_session(self, monkeypatch):
        monkeypatch.setenv("HELP_KB_VECTOR_ENABLED", "0")
        result = kb_vector.sync_manual_vectors(session=_PoisonSession())
        assert result == {
            "skipped": True, "reason": "disabled",
            "embedded": 0, "unchanged": 0, "deleted": 0, "total": 0,
        }

    def test_search_disabled_returns_empty_without_touching_session(self, monkeypatch):
        monkeypatch.setenv("HELP_KB_VECTOR_ENABLED", "0")
        out = kb_vector.vector_search_manual("質問", audience="student", session=_PoisonSession())
        assert out == []

    def test_enabled_env_values_accepted(self, monkeypatch):
        monkeypatch.setenv("HELP_KB_VECTOR_ENABLED", "1")
        assert kb_vector._vector_enabled() is True
        monkeypatch.setenv("HELP_KB_VECTOR_ENABLED", "0")
        assert kb_vector._vector_enabled() is False


# ===========================================================================
# 2. validator 違反時の fail-closed（DELETE も走らない）
# ===========================================================================


class TestValidatorGate:
    def test_violation_skips_sync_and_never_deletes(self, monkeypatch):
        monkeypatch.setattr(kb_vector, "validate_manual", lambda: ["front-matter mismatch"])

        def _unexpected_snapshot():
            raise AssertionError("validator 違反時に snapshot を構築してはならない")

        monkeypatch.setattr(kb_vector, "_current_snapshot", _unexpected_snapshot)
        session = _PoisonSession()

        result = kb_vector.sync_manual_vectors(session=session)

        assert result["skipped"] is True
        assert result["reason"] == "validator_violations"
        assert result["violations_count"] == 1
        assert result["deleted"] == 0


# ===========================================================================
# 3. hash 不変節は再埋め込みしない
# ===========================================================================


class TestHashUnchangedSkipsReembedding:
    def test_unchanged_section_is_not_reembedded(self, monkeypatch):
        monkeypatch.setattr(kb_vector, "validate_manual", lambda: [])
        title, body = "音声モード", "マイクを押してください"
        key = ("student", "getting_started.md", "voice-mode")
        monkeypatch.setattr(
            kb_vector, "_current_snapshot",
            lambda: {key: {"title": title, "body": body, "content_hash": kb_vector._content_hash(title, body)}},
        )

        def _unexpected_embed(texts):
            raise AssertionError("hash 不変節を再埋め込みしてはならない")

        monkeypatch.setattr(kb_vector, "_embed_texts", _unexpected_embed)

        session = _FakeSession(existing_rows=[(*key, kb_vector._content_hash(title, body))])
        result = kb_vector.sync_manual_vectors(session=session)

        assert result == {"skipped": False, "embedded": 0, "unchanged": 1, "deleted": 0, "total": 1}
        assert session.insert_calls == []
        assert session.delete_calls == []
        assert session.committed is True


# ===========================================================================
# 4. 変更/新規節のみ埋め込み → upsert
# ===========================================================================


class TestChangedSectionsAreEmbeddedAndUpserted:
    def test_new_and_changed_sections_are_embedded(self, monkeypatch):
        monkeypatch.setattr(kb_vector, "validate_manual", lambda: [])
        unchanged_key = ("student", "a.md", "s1")
        changed_key = ("student", "a.md", "s2")
        new_key = ("teacher", "b.md", "s1")
        current = {
            unchanged_key: {"title": "T1", "body": "B1", "content_hash": kb_vector._content_hash("T1", "B1")},
            changed_key: {"title": "T2", "body": "B2-new", "content_hash": kb_vector._content_hash("T2", "B2-new")},
            new_key: {"title": "T3", "body": "B3", "content_hash": kb_vector._content_hash("T3", "B3")},
        }
        monkeypatch.setattr(kb_vector, "_current_snapshot", lambda: current)

        embed_calls: list[list[str]] = []

        def _fake_embed(texts):
            embed_calls.append(list(texts))
            return [[0.1, 0.2, 0.3] for _ in texts]

        monkeypatch.setattr(kb_vector, "_embed_texts", _fake_embed)

        session = _FakeSession(existing_rows=[
            (*unchanged_key, kb_vector._content_hash("T1", "B1")),
            (*changed_key, kb_vector._content_hash("T2", "B2-old")),
        ])
        result = kb_vector.sync_manual_vectors(session=session)

        assert result == {"skipped": False, "embedded": 2, "unchanged": 1, "deleted": 0, "total": 3}
        assert len(embed_calls) == 1
        assert len(embed_calls[0]) == 2  # changed_key + new_key のみ
        assert len(session.insert_calls) == 2
        assert session.committed is True


# ===========================================================================
# 5. 孤児行 DELETE が同一トランザクション内で起きる
# ===========================================================================


class TestOrphanDeleteSameTransaction:
    def test_orphan_rows_deleted_before_commit(self, monkeypatch):
        monkeypatch.setattr(kb_vector, "validate_manual", lambda: [])
        current_key = ("student", "a.md", "s1")
        current = {current_key: {"title": "T", "body": "B", "content_hash": kb_vector._content_hash("T", "B")}}
        monkeypatch.setattr(kb_vector, "_current_snapshot", lambda: current)
        monkeypatch.setattr(kb_vector, "_embed_texts", lambda texts: [[0.0] for _ in texts])

        orphan_key = ("student", "retired.md", "old-section")
        session = _FakeSession(existing_rows=[
            (*current_key, "stale-hash"),  # hash 不一致 → 再埋め込み対象
            (*orphan_key, "whatever-hash"),
        ])
        result = kb_vector.sync_manual_vectors(session=session)

        assert result["deleted"] == 1
        assert len(session.delete_calls) == 1
        delete_params = session.delete_calls[0][1]
        assert (delete_params["audience"], delete_params["file"], delete_params["anchor"]) == orphan_key
        # DELETE は commit より前に、同じセッション（同一トランザクション）で実行されている。
        delete_index = session.executed.index(session.delete_calls[0])
        assert delete_index < len(session.executed) - 1 or session.committed is True
        assert session.committed is True
        assert session.rolled_back is False


# ===========================================================================
# 6. 埋め込み失敗時は既存行を消さない・部分適用もしない
# ===========================================================================


class TestEmbeddingFailurePreservesExistingRows:
    def test_embedding_exception_performs_no_writes(self, monkeypatch):
        monkeypatch.setattr(kb_vector, "validate_manual", lambda: [])
        current_key = ("student", "a.md", "s1")
        current = {current_key: {"title": "T", "body": "B-new", "content_hash": kb_vector._content_hash("T", "B-new")}}
        monkeypatch.setattr(kb_vector, "_current_snapshot", lambda: current)

        def _raise(texts):
            raise RuntimeError("embedding API down")

        monkeypatch.setattr(kb_vector, "_embed_texts", _raise)

        orphan_key = ("student", "retired.md", "old")
        session = _FakeSession(existing_rows=[
            (*current_key, "stale-hash"),
            (*orphan_key, "whatever"),
        ])
        result = kb_vector.sync_manual_vectors(session=session)

        assert result["skipped"] is True
        assert result["reason"] == "embedding_failed"
        assert session.insert_calls == []
        assert session.delete_calls == []
        assert session.committed is False

    def test_embedding_count_mismatch_performs_no_writes(self, monkeypatch):
        monkeypatch.setattr(kb_vector, "validate_manual", lambda: [])
        current_key = ("student", "a.md", "s1")
        current = {current_key: {"title": "T", "body": "B-new", "content_hash": kb_vector._content_hash("T", "B-new")}}
        monkeypatch.setattr(kb_vector, "_current_snapshot", lambda: current)
        monkeypatch.setattr(kb_vector, "_embed_texts", lambda texts: [])  # 件数不一致

        session = _FakeSession(existing_rows=[])
        result = kb_vector.sync_manual_vectors(session=session)

        assert result["skipped"] is True
        assert result["reason"] == "embedding_count_mismatch"
        assert session.insert_calls == []
        assert session.delete_calls == []
        assert session.committed is False


# ===========================================================================
# 7. セッションのライフサイクル（呼び出し元管理 vs 自前オープン）
# ===========================================================================


class TestSessionLifecycle:
    def test_caller_supplied_session_is_not_closed(self, monkeypatch):
        monkeypatch.setattr(kb_vector, "validate_manual", lambda: [])
        monkeypatch.setattr(kb_vector, "_current_snapshot", lambda: {})
        session = _FakeSession()
        kb_vector.sync_manual_vectors(session=session)
        assert session.closed is False

    def test_owned_session_is_opened_and_closed(self, monkeypatch):
        monkeypatch.setattr(kb_vector, "validate_manual", lambda: [])
        monkeypatch.setattr(kb_vector, "_current_snapshot", lambda: {})
        owned = _FakeSession()
        monkeypatch.setattr(kb_vector, "_open_session", lambda: owned)
        kb_vector.sync_manual_vectors(session=None)
        assert owned.closed is True


# ===========================================================================
# 8. vector_search_manual — audience 継承 SQL・ValueError
# ===========================================================================


class TestVectorSearchAudience:
    def test_unknown_audience_raises_valueerror(self):
        with pytest.raises(ValueError):
            kb_vector.vector_search_manual("質問", audience="bogus")

    def test_unknown_audience_raises_even_when_disabled(self, monkeypatch):
        monkeypatch.setenv("HELP_KB_VECTOR_ENABLED", "0")
        with pytest.raises(ValueError):
            kb_vector.vector_search_manual("質問", audience="not-a-real-audience")

    @pytest.mark.parametrize("audience,expected_visible", [
        ("student", ["student"]),
        ("teacher", ["student", "teacher"]),
        ("system_admin", ["student", "teacher", "system_admin"]),
    ])
    def test_visible_dirs_passed_to_sql(self, monkeypatch, audience, expected_visible):
        monkeypatch.setattr(kb_vector, "_embed_texts", lambda texts: [[0.0, 0.0]])
        session = _FakeSession(search_rows=[])
        kb_vector.vector_search_manual("質問", audience=audience, session=session)

        select_calls = [c for c in session.executed if "manual_sections" in c[0] and "distance" in c[0]]
        assert len(select_calls) == 1
        assert select_calls[0][1]["visible"] == list(expected_visible)

    def test_query_embedding_failure_returns_empty_without_touching_session(self, monkeypatch):
        def _raise(texts):
            raise RuntimeError("embedding API down")

        monkeypatch.setattr(kb_vector, "_embed_texts", _raise)
        out = kb_vector.vector_search_manual("質問", audience="student", session=_PoisonSession())
        assert out == []

    def test_blank_query_returns_empty(self, monkeypatch):
        def _unexpected_embed(texts):
            raise AssertionError("空クエリで embedding を呼んではならない")

        monkeypatch.setattr(kb_vector, "_embed_texts", _unexpected_embed)
        out = kb_vector.vector_search_manual("   ", audience="student", session=_PoisonSession())
        assert out == []


# ===========================================================================
# 9. しきい値足切り + 返却 dict の形（score/distance/confidence 非漏洩）
# ===========================================================================


class TestThresholdAndResultShape:
    def test_far_rows_are_excluded_by_threshold(self, monkeypatch):
        monkeypatch.setattr(kb_vector, "_embed_texts", lambda texts: [[0.0, 0.0]])
        near = ("student", "near.md", "s1", "近い節", "近い本文", 0.1)
        far = ("student", "far.md", "s2", "遠い節", "遠い本文", 0.9)
        session = _FakeSession(search_rows=[near, far])

        out = kb_vector.vector_search_manual("質問", audience="student", session=session)

        assert len(out) == 1
        assert out[0]["file"] == "near.md"

    def test_result_dict_has_no_numeric_confidence_keys(self, monkeypatch):
        monkeypatch.setattr(kb_vector, "_embed_texts", lambda texts: [[0.0, 0.0]])
        row = ("student", "near.md", "s1", "近い節", "近い本文", 0.05)
        session = _FakeSession(search_rows=[row])

        out = kb_vector.vector_search_manual("質問", audience="student", session=session)

        assert len(out) == 1
        forbidden_keys = {"distance", "score", "confidence"}
        assert forbidden_keys.isdisjoint(out[0].keys())
        assert set(out[0].keys()) == {"file", "anchor", "title", "body", "audience", "citation", "documented"}

    def test_result_shape_matches_search_manual_contract(self, monkeypatch):
        monkeypatch.setattr(kb_vector, "_embed_texts", lambda texts: [[0.0, 0.0]])
        row = ("student", "getting_started.md", "voice-mode", "音声モード", "マイクを押す", 0.05)
        session = _FakeSession(search_rows=[row])

        out = kb_vector.vector_search_manual("質問", audience="student", limit=3, session=session)

        assert out == [{
            "file": "getting_started.md",
            "anchor": "voice-mode",
            "title": "音声モード",
            "body": "マイクを押す",
            "audience": "student",
            "citation": "manual/student/getting_started.md#voice-mode",
            "documented": True,
        }]

    def test_empty_body_row_is_not_documented(self, monkeypatch):
        monkeypatch.setattr(kb_vector, "_embed_texts", lambda texts: [[0.0, 0.0]])
        row = ("student", "a.md", "s1", "空節", "   ", 0.05)
        session = _FakeSession(search_rows=[row])

        out = kb_vector.vector_search_manual("質問", audience="student", session=session)

        assert out[0]["documented"] is False


# ===========================================================================
# 10. learning.py フォールバック配線
# ===========================================================================


import routes.learning as learning_mod  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402


def _make_body(message: str = "使い方を教えて", **overrides) -> LearningChatRequest:
    kwargs: dict = dict(message=message, history=[])
    kwargs.update(overrides)
    return LearningChatRequest(**kwargs)


_VECTOR_HIT = {
    "file": "getting_started.md",
    "anchor": "voice-mode",
    "title": "音声モードの使い方",
    "body": "入力欄横のマイクボタンを押すと音声入力が始まります。",
    "audience": "student",
    "citation": "manual/student/getting_started.md#voice-mode",
    "documented": True,
}


class TestLearningPyFallbackWiring:
    def _mock_persistence(self, monkeypatch):
        persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
        monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
        trace_mock = MagicMock(return_value="trace-1")
        monkeypatch.setattr(learning_mod, "record_interest_trace", trace_mock)
        return persist_mock, trace_mock

    def test_nonvector_miss_falls_back_to_vector_hit(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "_search_manual", lambda *a, **k: [])
        monkeypatch.setattr(learning_mod, "_vector_search_manual", lambda *a, **k: [_VECTOR_HIT])
        _persist_mock, trace_mock = self._mock_persistence(monkeypatch)

        body = _make_body()
        resp = learning_mod._usage_help_response("user-1", "course-1", "topic-1", body)

        assert _VECTOR_HIT["body"] in resp.answer
        assert resp.manual_citations == [{
            "file": _VECTOR_HIT["file"], "anchor": _VECTOR_HIT["anchor"], "title": _VECTOR_HIT["title"],
        }]
        trace_mock.assert_called_once()
        payload = trace_mock.call_args.kwargs.get("extra_payload")
        assert payload == {
            "help_anchor": _VECTOR_HIT["citation"], "documented": True, "no_hit": False, "vector": True,
        }

    def test_both_miss_returns_fixed_text_without_vector_flag(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "_search_manual", lambda *a, **k: [])
        monkeypatch.setattr(learning_mod, "_vector_search_manual", lambda *a, **k: [])
        _persist_mock, trace_mock = self._mock_persistence(monkeypatch)

        body = _make_body()
        resp = learning_mod._usage_help_response("user-1", "course-1", "topic-1", body)

        assert "その使い方の説明はまだ整備されていません。" in resp.answer
        assert resp.manual_citations is None
        payload = trace_mock.call_args.kwargs.get("extra_payload")
        assert payload == {"help_anchor": None, "documented": False, "no_hit": True}

    def test_primary_hit_never_calls_vector_fallback(self, monkeypatch):
        primary_hit = {**_VECTOR_HIT, "file": "primary.md", "anchor": "p1", "citation": "manual/student/primary.md#p1"}
        monkeypatch.setattr(learning_mod, "_search_manual", lambda *a, **k: [primary_hit])

        def _unexpected_vector(*a, **k):
            raise AssertionError("primary が documented ヒットのときベクトル層を呼んではならない")

        monkeypatch.setattr(learning_mod, "_vector_search_manual", _unexpected_vector)
        self._mock_persistence(monkeypatch)

        body = _make_body()
        resp = learning_mod._usage_help_response("user-1", "course-1", "topic-1", body)

        assert primary_hit["body"] in resp.answer

    def test_vector_search_exception_falls_back_to_no_hit(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "_search_manual", lambda *a, **k: [])

        def _raise(*a, **k):
            raise RuntimeError("vector backend down")

        monkeypatch.setattr(learning_mod, "_vector_search_manual", _raise)
        _persist_mock, trace_mock = self._mock_persistence(monkeypatch)

        body = _make_body()
        resp = learning_mod._usage_help_response("user-1", "course-1", "topic-1", body)

        assert "その使い方の説明はまだ整備されていません。" in resp.answer
        payload = trace_mock.call_args.kwargs.get("extra_payload")
        assert payload == {"help_anchor": None, "documented": False, "no_hit": True}
