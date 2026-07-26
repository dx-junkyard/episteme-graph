"""学習画面インスペクト・モード（Phase 2 バックエンド, §5.2/§5.4/§9）のテスト。

設計正本: ``docs/features/learning_ui_inspect_hover_design.md``。

対象:
  - ``backend/core/help_kb/ui_anchors.py``（UI アンカー表の正本、新規）
  - ``backend/core/help_kb/validator.py`` の ``check_ui_anchor_mappings`` /
    ``validate_manual`` への統合
  - ``backend/api/routes/learning.py`` の ``GET /help/ui-anchors`` /
    ``POST /help/ui-anchor-events`` / ``_usage_help_response`` の ``ui_anchor`` 優先
  - ``backend/api/schemas.py`` の ``LearningChatRequest.ui_anchor``
  - ``backend/core/admin_assistant/next_steps.py`` の
    ``manual.help_gaps_pending`` が ``ui:`` プレフィックスの anchor を素通しで
    集計対象に含めること（コード変更なし・既存 SQL の COALESCE 挙動の確認）
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.help_kb import manual as kb_manual  # noqa: E402
from core.help_kb import ui_anchors as kb_ui_anchors  # noqa: E402
from core.help_kb import validator as kb_validator  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
)

import routes.learning as learning_mod  # noqa: E402
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark_api = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入（full API は Docker 内で）")

_UID_STUDENT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def _clear_manual_cache():
    # 他テスト（test_help_kb_store 等）が合成ツリー・DB 状態で温めたモジュールキャッシュを
    # 持ち込まない／持ち出さない（実行順依存の flake 防止。sibling と同じ規約）。
    kb_manual.clear_manual_cache()
    yield
    kb_manual.clear_manual_cache()


def _headers(role: str = "STUDENT", sub: str = _UID_STUDENT):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    return {"Authorization": "Bearer " + jwt.encode(payload, "test-secret-key", algorithm="HS256")}


# ===========================================================================
# 1. ui_anchors.py — audience 越境禁止・FastAPI 非 import・実在解決
# ===========================================================================


class TestUiAnchorsModule:
    def test_does_not_import_fastapi(self):
        src = Path(kb_ui_anchors.__file__).read_text(encoding="utf-8")
        assert_source_does_not_import(src, ["fastapi"], context="ui_anchors.py")

    def test_all_mapped_values_reference_student_only(self):
        for anchor_id, ref in kb_ui_anchors.UI_ANCHORS.items():
            assert ref.startswith("student/"), f"{anchor_id} が student/ 以外を参照: {ref}"

    def test_known_ids_is_superset_of_mapped_ids(self):
        assert set(kb_ui_anchors.UI_ANCHORS.keys()) <= kb_ui_anchors.KNOWN_UI_ANCHOR_IDS

    def test_resolve_ui_anchors_against_real_docs_has_no_broken_mapping(self):
        """docs/manual/student/ の実データに対し、マップした全アンカーが解決できる。"""
        resolved = kb_ui_anchors.resolve_ui_anchors()
        assert set(resolved.keys()) == set(kb_ui_anchors.UI_ANCHORS.keys())
        for anchor_id, sec in resolved.items():
            assert sec["title"], f"{anchor_id} の title が空"
            assert sec["body"], f"{anchor_id} の body が空"
            assert sec["manual_anchor"] == kb_ui_anchors.UI_ANCHORS[anchor_id]

    def test_resolve_ui_anchor_mapped_matches_bulk_resolution(self):
        bulk = kb_ui_anchors.resolve_ui_anchors()
        single = kb_ui_anchors.resolve_ui_anchor("composer.send")
        assert single == bulk["composer.send"]

    def test_resolve_ui_anchor_known_but_unmapped_returns_none(self, monkeypatch):
        """2026-07-26 に KNOWN_UI_ANCHOR_IDS の全17種が UI_ANCHORS にマップ済みになった

        ため、実データでは「既知だが未マップ」のアンカーはもう存在しない。それでも
        resolve_ui_anchor が「KNOWN_UI_ANCHOR_IDS にはあるが UI_ANCHORS に無い」場合に
        None を返すロジック自体は独立した関心事なので、UI_ANCHORS を一時的に間引いて
        検証する。
        """
        assert "topbar.inspect" in kb_ui_anchors.KNOWN_UI_ANCHOR_IDS
        assert "topbar.inspect" in kb_ui_anchors.UI_ANCHORS  # 2026-07-26 時点で全17種マップ済み
        pruned = dict(kb_ui_anchors.UI_ANCHORS)
        del pruned["topbar.inspect"]
        monkeypatch.setattr(kb_ui_anchors, "UI_ANCHORS", pruned)
        assert kb_ui_anchors.resolve_ui_anchor("topbar.inspect") is None

    def test_resolve_ui_anchor_unknown_id_returns_none(self):
        assert kb_ui_anchors.resolve_ui_anchor("no.such.anchor") is None

    def test_split_manual_ref(self):
        assert kb_ui_anchors.split_manual_ref("student/02-student.md#ai-chat") == (
            "02-student.md", "ai-chat",
        )


# ===========================================================================
# 2. validator.py — check_ui_anchor_mappings / validate_manual 統合
# ===========================================================================


class TestValidatorIntegration:
    def test_check_ui_anchor_mappings_clean_against_real_docs(self):
        assert kb_ui_anchors.resolve_ui_anchors()  # 前提: 実データで何か解決できている
        assert kb_validator.check_ui_anchor_mappings() == []

    def test_validate_manual_unaffected_by_ui_anchor_check(self):
        """check_ui_anchor_mappings は validate_manual() に混ぜない（独立関数）。

        既存のガードレール（test_help_kb.py 等）が manual_root() を合成ツリーに
        差し替えて validate_manual() の戻り値を厳密にピン留めしているため、
        統合すると本機能と無関係なテストが壊れる。main.py lifespan からは
        別ブロックで呼ぶ（TestMainLifespanWiring 参照）。
        """
        assert "check_ui_anchor_mappings" not in _source_of(kb_validator.validate_manual)

    def test_non_student_reference_is_flagged(self, monkeypatch):
        monkeypatch.setattr(
            kb_ui_anchors, "UI_ANCHORS",
            {"bogus.anchor": "teacher/x.md#y"},
        )
        violations = kb_validator.check_ui_anchor_mappings()
        assert any("bogus.anchor" in v and "student/" in v for v in violations)

    def test_missing_section_is_flagged(self, monkeypatch):
        monkeypatch.setattr(
            kb_ui_anchors, "UI_ANCHORS",
            {"bogus.anchor": "student/does-not-exist.md#nope"},
        )
        violations = kb_validator.check_ui_anchor_mappings()
        assert any("bogus.anchor" in v for v in violations)

    def test_check_is_independent_of_manual_root_monkeypatch_used_by_other_tests(self, tmp_path, monkeypatch):
        """他ガードレール（test_help_kb.py 等）と同じ手法（manual_root 差し替え）で

        合成ツリーに切り替えても、check_ui_anchor_mappings 単体は「実docsに対して
        マップが正しいか」を見る独立した関心事であり、他テストのassert対象
        （validate_manual の戻り値）には影響しない、という設計上の分離を確認する。
        """
        monkeypatch.setattr(kb_validator, "manual_root", lambda: tmp_path / "nope")
        # validate_manual() 自体は合成ツリー（存在しない）に対して空リストのまま
        # （check_ui_anchor_mappings を含まないため、この差し替えの影響を受けない）。
        assert kb_validator.validate_manual() == []


def _source_of(fn) -> str:
    import inspect

    return inspect.getsource(fn)


class TestMainLifespanWiring:
    def test_main_calls_check_ui_anchor_mappings_in_separate_fail_open_block(self):
        main_src = Path(BACKEND / "api" / "main.py").read_text(encoding="utf-8")
        assert "check_ui_anchor_mappings" in main_src
        idx = main_src.find("check_ui_anchor_mappings")
        block = main_src[max(0, idx - 400): idx + 200]
        assert "except Exception" in block


# ===========================================================================
# 3. GET /api/learning/help/ui-anchors — 配信 API
# ===========================================================================


@pytestmark_api
class TestUiAnchorsApi:
    @pytest.fixture
    def client(self):
        from api.main import app

        return TestClient(app)

    def test_requires_auth(self, client):
        resp = client.get("/api/learning/help/ui-anchors")
        assert resp.status_code in (401, 403)

    def test_authenticated_returns_mapped_anchors(self, client):
        resp = client.get("/api/learning/help/ui-anchors", headers=_headers("STUDENT"))
        assert resp.status_code == 200
        data = resp.json()
        assert "anchors" in data
        anchors = data["anchors"]
        assert "composer.send" in anchors
        entry = anchors["composer.send"]
        assert set(entry.keys()) == {"title", "body", "manual_anchor"}
        assert entry["manual_anchor"] == "student/02-student.md#ai-chat"
        # 2026-07-26 に KNOWN_UI_ANCHOR_IDS の全17種がマップ済みになったため、
        # マップ未整備で欠けるものが無いことを確認する（かつては topbar.inspect 等
        # 6種が未整備で欠けていた）。
        assert set(anchors.keys()) == set(kb_ui_anchors.KNOWN_UI_ANCHOR_IDS)

    def test_does_not_record_any_trace(self, client, monkeypatch):
        def _unexpected(*a, **k):
            raise AssertionError("GET ui-anchors は痕跡を書いてはならない（読み取り専用）")

        monkeypatch.setattr(learning_mod, "record_interest_trace", _unexpected)
        resp = client.get("/api/learning/help/ui-anchors", headers=_headers("STUDENT"))
        assert resp.status_code == 200


# ===========================================================================
# 4. POST /api/learning/help/ui-anchor-events — no_hit 記録
# ===========================================================================


@pytestmark_api
class TestUiAnchorEventsApi:
    @pytest.fixture
    def client(self):
        from api.main import app

        return TestClient(app)

    def test_requires_auth(self, client):
        resp = client.post(
            "/api/learning/help/ui-anchor-events",
            json={"anchor_id": "topbar.inspect", "kind": "no_hit"},
        )
        assert resp.status_code in (401, 403)

    def test_unknown_anchor_id_is_422(self, client):
        resp = client.post(
            "/api/learning/help/ui-anchor-events",
            json={"anchor_id": "not.a.real.anchor", "kind": "no_hit"},
            headers=_headers("STUDENT"),
        )
        assert resp.status_code == 422

    def test_bad_kind_is_422(self, client):
        resp = client.post(
            "/api/learning/help/ui-anchor-events",
            json={"anchor_id": "topbar.inspect", "kind": "shown"},
            headers=_headers("STUDENT"),
        )
        assert resp.status_code == 422

    def test_no_hit_event_records_help_usage_trace_for_known_anchor(self, client, monkeypatch):
        """no_hit イベントは anchor_id が KNOWN_UI_ANCHOR_IDS に含まれてさえいれば

        documented=False で記録される。UI_ANCHORS でのマップ状態は問わない
        （実運用ではフロントが「まだ説明が無い」ツールチップを表示したときだけ
        このエンドポイントを呼ぶ想定だが、バックエンド側はそこまで検証しない —
        2026-07-26 に topbar.inspect 自体はマップ済みになったが、このテストは
        マップ状態に依存しないことの確認として残す）。
        """
        monkeypatch.setattr(learning_mod, "_recent_duplicate_ui_anchor_event", lambda *a, **k: False)
        captured: dict = {}

        def _fake_record(user_id, course_id, topic_id, **kwargs):
            captured["args"] = (user_id, course_id, topic_id)
            captured["kwargs"] = kwargs
            return "trace-1"

        monkeypatch.setattr(learning_mod, "record_interest_trace", _fake_record)
        resp = client.post(
            "/api/learning/help/ui-anchor-events",
            json={"anchor_id": "topbar.inspect", "kind": "no_hit"},
            headers=_headers("STUDENT"),
        )
        assert resp.status_code == 201
        assert resp.json() == {"ok": True, "recorded": True}
        assert captured["kwargs"]["kind"] == "help_usage"
        payload = captured["kwargs"]["extra_payload"]
        assert payload == {"help_anchor": "ui:topbar.inspect", "documented": False, "no_hit": True}
        # 逐語（自由文）を積まない — text はホバー元の anchor_id を含まない固定文言。
        assert captured["kwargs"]["text"] == "UI要素の使い方（未整備）"

    def test_known_mapped_anchor_is_also_accepted(self, client, monkeypatch):
        """マップ済みアンカーでも no_hit イベント自体は拒否しない（緩い妥当性検証）。"""
        monkeypatch.setattr(learning_mod, "_recent_duplicate_ui_anchor_event", lambda *a, **k: False)
        monkeypatch.setattr(learning_mod, "record_interest_trace", MagicMock(return_value="trace-1"))
        resp = client.post(
            "/api/learning/help/ui-anchor-events",
            json={"anchor_id": "composer.send", "kind": "no_hit"},
            headers=_headers("STUDENT"),
        )
        assert resp.status_code == 201

    def test_dedup_skips_recording(self, client, monkeypatch):
        monkeypatch.setattr(learning_mod, "_recent_duplicate_ui_anchor_event", lambda *a, **k: True)

        def _unexpected(*a, **k):
            raise AssertionError("直近重複がある場合は record_interest_trace を呼んではならない")

        monkeypatch.setattr(learning_mod, "record_interest_trace", _unexpected)
        resp = client.post(
            "/api/learning/help/ui-anchor-events",
            json={"anchor_id": "topbar.inspect", "kind": "no_hit"},
            headers=_headers("STUDENT"),
        )
        assert resp.status_code == 201
        assert resp.json() == {"ok": True, "recorded": False}


# ===========================================================================
# 5. _usage_help_response の ui_anchor 優先解決
# ===========================================================================


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


def _make_body(message: str = "画面の使い方を教えて", **overrides) -> LearningChatRequest:
    kwargs: dict = dict(message=message, history=[])
    kwargs.update(overrides)
    return LearningChatRequest(**kwargs)


@pytest.fixture
def help_env(monkeypatch):
    settings = _fake_settings()
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())
    monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())

    persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
    trace_mock = MagicMock(return_value="trace-1")
    monkeypatch.setattr(learning_mod, "record_interest_trace", trace_mock)

    def _unexpected_classify_intent(*a, **k):
        raise AssertionError("HELP pre-route ヒット時に _classify_intent が呼ばれてはならない")

    monkeypatch.setattr(learning_mod, "_classify_intent", _unexpected_classify_intent)
    monkeypatch.setattr(learning_mod, "_vector_search_manual", lambda *a, **k: [])

    return SimpleNamespace(settings=settings, persist_mock=persist_mock, trace_mock=trace_mock)


_UI_CURRENT_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}


class TestUsageHelpUiAnchorPriority:
    def test_mapped_ui_anchor_resolves_directly_without_search_manual(self, help_env, monkeypatch):
        def _unexpected_search(*a, **k):
            raise AssertionError("マップ済み ui_anchor があるとき search_manual を呼んではならない")

        monkeypatch.setattr(learning_mod, "_search_manual", _unexpected_search)
        body = _make_body(support_action="usage_help", ui_anchor="composer.send")

        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=_UI_CURRENT_USER)

        expected = kb_ui_anchors.resolve_ui_anchor("composer.send")
        assert expected is not None
        assert expected["body"] in resp.answer
        assert resp.manual_citations == [{
            "file": "02-student.md", "anchor": "ai-chat", "title": expected["title"],
        }]

    def test_mapped_ui_anchor_trace_uses_ui_prefixed_anchor(self, help_env):
        body = _make_body(
            message="この超長い質問文の逐語がpayloadに残ってはいけない",
            support_action="usage_help", ui_anchor="composer.send",
        )
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=_UI_CURRENT_USER)

        called_kwargs = help_env.trace_mock.call_args.kwargs
        assert called_kwargs["kind"] == "help_usage"
        payload = called_kwargs["extra_payload"]
        assert payload["help_anchor"] == "ui:composer.send"
        assert payload["documented"] is True
        assert payload["no_hit"] is False
        assert "この超長い質問文の逐語" not in called_kwargs.get("text", "")

    def test_unmapped_ui_anchor_falls_back_to_search_and_records_no_hit(self, help_env, monkeypatch):
        """2026-07-26 に KNOWN_UI_ANCHOR_IDS の全17種がマップ済みになり、実データには

        「既知だが未マップ」の ui_anchor がもう存在しない。それでも resolve が失敗する
        （まだマップされていない、または将来 KNOWN_UI_ANCHOR_IDS が先に増える）ケースの
        フォールバック挙動は独立した関心事なので、_resolve_ui_anchor を直接 None に
        差し替えて検証する。
        """
        monkeypatch.setattr(learning_mod, "_resolve_ui_anchor", lambda anchor_id: None)
        monkeypatch.setattr(learning_mod, "_search_manual", lambda *a, **k: [])
        body = _make_body(support_action="usage_help", ui_anchor="topbar.inspect")

        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=_UI_CURRENT_USER)

        assert "その使い方の説明はまだ整備されていません。" in resp.answer
        called_kwargs = help_env.trace_mock.call_args.kwargs
        payload = called_kwargs["extra_payload"]
        assert payload == {"help_anchor": "ui:topbar.inspect", "documented": False, "no_hit": True}

    def test_unmapped_ui_anchor_with_search_hit_still_tags_ui_anchor(self, help_env, monkeypatch):
        """未マップ ui_anchor でも通常検索がヒットすれば documented=True になるが、

        痕跡の anchor 値は検索ヒット自身の citation ではなく "ui:<anchor_id>" に
        一本化される（demand 追跡を UI 要素単位にする設計判断、§9-1）。2026-07-26 に
        全17種がマップ済みになったため、_resolve_ui_anchor を直接 None に差し替えて
        「未マップ」状態を再現する（上のテストと同じ理由）。
        """
        monkeypatch.setattr(learning_mod, "_resolve_ui_anchor", lambda anchor_id: None)
        hit = {
            "file": "getting_started.md", "anchor": "voice-mode", "title": "音声モードの使い方",
            "body": "マイクボタンを押すと音声入力が始まります。", "audience": "student",
            "citation": "manual/student/getting_started.md#voice-mode", "documented": True,
        }
        monkeypatch.setattr(learning_mod, "_search_manual", lambda *a, **k: [hit])
        body = _make_body(support_action="usage_help", ui_anchor="topbar.inspect")

        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=_UI_CURRENT_USER)

        assert hit["body"] in resp.answer
        called_kwargs = help_env.trace_mock.call_args.kwargs
        payload = called_kwargs["extra_payload"]
        assert payload["help_anchor"] == "ui:topbar.inspect"
        assert payload["documented"] is True

    def test_no_ui_anchor_keeps_legacy_citation_behavior(self, help_env, monkeypatch):
        """ui_anchor 未指定時は既存挙動（citation をそのまま anchor に使う）が不変。"""
        hit = {
            "file": "getting_started.md", "anchor": "voice-mode", "title": "音声モードの使い方",
            "body": "マイクボタンを押すと音声入力が始まります。", "audience": "student",
            "citation": "manual/student/getting_started.md#voice-mode", "documented": True,
        }
        monkeypatch.setattr(learning_mod, "_search_manual", lambda *a, **k: [hit])
        body = _make_body(support_action="usage_help")

        learning_mod.learning_chat("course-1", "topic-1", body, current_user=_UI_CURRENT_USER)

        called_kwargs = help_env.trace_mock.call_args.kwargs
        payload = called_kwargs["extra_payload"]
        assert payload["help_anchor"] == "manual/student/getting_started.md#voice-mode"

    def test_resolve_ui_anchor_exception_falls_back_to_search(self, help_env, monkeypatch):
        def _raise(anchor_id):
            raise RuntimeError("boom")

        monkeypatch.setattr(learning_mod, "_resolve_ui_anchor", _raise)
        monkeypatch.setattr(learning_mod, "_search_manual", lambda *a, **k: [])
        body = _make_body(support_action="usage_help", ui_anchor="composer.send")

        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=_UI_CURRENT_USER)

        assert "その使い方の説明はまだ整備されていません。" in resp.answer


# ===========================================================================
# 6. LearningChatRequest.ui_anchor フィールド
# ===========================================================================


class TestSchemaField:
    def test_ui_anchor_field_exists_and_defaults_to_none(self):
        req = LearningChatRequest(message="hi")
        assert req.ui_anchor is None

    def test_ui_anchor_can_be_set(self):
        req = LearningChatRequest(message="hi", ui_anchor="composer.send")
        assert req.ui_anchor == "composer.send"


# ===========================================================================
# 7. manual.help_gaps_pending — ui: プレフィックス anchor の集計（コード変更なし）
# ===========================================================================


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, stmt, params=None):
        return _FakeResult(self.rows)


class TestHelpGapsPendingUiAnchorAggregation:
    def test_ui_prefixed_bucket_is_aggregated_distinctly_from_generic_no_hit(self):
        from core.admin_assistant import next_steps as next_steps_mod

        session = _FakeSession([
            {"bucket_key": "ui:composer.send", "cnt": 4, "last_seen": "2026-07-26T00:00:00+00:00"},
        ])
        out = next_steps_mod._eval_manual_help_gaps_pending(session, "u1")
        assert len(out) == 1
        step, _sort_ts = out[0]
        assert step.rule_id == next_steps_mod.RULE_MANUAL_HELP_GAPS_PENDING
        assert step.step_key == "manual.help_gaps_pending:ui:composer.send"
        assert step.target == {"anchor": "ui:composer.send"}
        assert "3-5" in step.reason

    def test_ui_prefixed_bucket_below_k_anonymity_is_hidden(self):
        from core.admin_assistant import next_steps as next_steps_mod

        session = _FakeSession([
            {"bucket_key": "ui:topbar.inspect", "cnt": 2, "last_seen": "t"},
        ])
        out = next_steps_mod._eval_manual_help_gaps_pending(session, "u1")
        assert out == []
