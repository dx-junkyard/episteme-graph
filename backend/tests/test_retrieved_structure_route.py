"""知識の転用層 P4-2 — RAG の構造 1 hop の route 配線のテスト。

正本: ``docs/features/knowledge_transfer_design.md`` §5 / SA層設計書 §11.16。

``test_assistant_context_learning_route.py`` と同型の手法: DB・実 LLM には触れず、
``routes.learning`` の境界関数を monkeypatch で差し替えて ``learning_chat`` を直接呼び、
**LLM 入力（messages）と保存・観測の振る舞い**で固定する。

固定する事実:

- 射影 SQL がスコープ2軸（``chunk_id`` と ``document_id = ANY(:doc_ids)``）を
  **SQL の中で**強制し、式由来の合成 claim を除く（KT6 / §5）
- ``screen_context`` が無いターンでも働く（入口は採用した出典 = 画面の申告ではない）
- モード別: casual なし / ``cycle_mode="elicit"`` なし / それ以外あり
- ``messages[-1]`` の順序は 画面文脈 → 検索構造 → 選択箇所 → 発話
- 保存 message・痕跡 payload は不変（SA6）
- 採用出典が無ければ DB を1本も引かない（クエリを増やさない）
- 解決の失敗はブロックだけ欠けて 200（fail-soft）
- 観測 ``structured_grounding_present`` は1件だけ（由来は payload に入れない）
"""

from __future__ import annotations

import inspect
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
from core.assistant_context.schema import (  # noqa: E402
    BLOCK_HEADER_LEARNING,
    BLOCK_HEADER_RETRIEVED,
    SELECTION_BLOCK_HEADER,
)
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402

from tests.test_assistant_context_learning_route import (  # noqa: E402
    COURSE_ID,
    CURRENT_USER,
    DOC_ID,
    MATERIAL_TEXT,
    TOPIC_ID,
    _course_data,
    _fake_settings,
    _screen_context,
)

CHUNK_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CHUNK_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
CLAIM_A = "cccccccc-cccc-cccc-cccc-cccccccccccc"
CLAIM_B = "dddddddd-dddd-dddd-dddd-dddddddddddd"

CLAIM_A_TEXT = "時間遅延はレンズポテンシャルの差で決まる。"
CLAIM_B_TEXT = "弱場近似のもとで像の歪みは線形になる。"


def _chunk(chunk_id: str, score: float = 0.8) -> dict:
    return {
        "id": chunk_id,
        "text": "採用されるチャンク本文。",
        "source_title": "テスト論文",
        "tier": "source",
        "score": score,
        "source_file": "",
        "material_id": "mat-1",
    }


def _claim_row(claim_id: str, chunk_id: str, text: str, agent_id: str = "") -> dict:
    return {
        "id": claim_id,
        "chunk_id": chunk_id,
        "document_id": DOC_ID,
        "text": text,
        "claim_type": "definition",
        "agent_claim_id": agent_id,
        "source_scope": {"legacy_ids": [agent_id]} if agent_id else {},
    }


def _graph_index(claim_id: str = CLAIM_A) -> dict:
    return {
        "nodes": [
            {
                "component_id": "theory_op_0001",
                "graph_layer": "main",
                "label": "Theory basis",
                "display_label": "Theory basis: 重力ポテンシャルの定義",
                "linked_claim_ids": [claim_id],
            },
            {
                "component_id": "eq_op_0007",
                "graph_layer": "equation_detail",
                "label": "Define eq_2_7",
                "display_label": "Define eq_2_7",
                "linked_claim_ids": [CLAIM_B],
            },
        ]
    }


def _make_body(message: str = "ここが分かりません", **overrides) -> LearningChatRequest:
    kwargs: dict = dict(message=message, history=[])
    kwargs.update(overrides)
    return LearningChatRequest(**kwargs)


class _FakeSession:
    """``_pg_session()`` の代役。実行された SQL と params を記録する。"""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.statements: list[str] = []
        self.params: list[dict] = []
        self.closed = False

    def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(dict(params or {}))
        rows = self.rows

        class _Result:
            def mappings(self_inner):
                return self_inner

            def fetchall(self_inner):
                return rows

        return _Result()

    def close(self):
        self.closed = True


@pytest.fixture
def chat_env(monkeypatch):
    """DB / LLM 非接続の学習チャット環境 + 採用出典ありの検索結果。"""
    settings = _fake_settings()
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_policy_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())

    monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
    monkeypatch.setattr(
        learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk(CHUNK_A)]
    )
    monkeypatch.setattr(learning_mod, "log_unanswered_query", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_prerequisites", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "_atlas_topic_attribution", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_and_count_confirm_prompt", lambda *a, **k: False)
    monkeypatch.setattr(learning_mod, "maybe_schedule_tension_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda *a, **k: {DOC_ID})
    monkeypatch.setattr(learning_mod, "list_course_source_document_ids", lambda *a, **k: [DOC_ID])
    monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda *a, **k: {})
    monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")

    persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
    trace_mock = MagicMock(return_value="trace-1")
    monkeypatch.setattr(learning_mod, "record_interest_trace", trace_mock)
    monkeypatch.setattr(
        learning_mod, "detect_and_record_misconception", MagicMock(return_value=None)
    )
    # 画面文脈（Phase 4）側の射影は本テストの主題ではないので黙らせる。
    monkeypatch.setattr(learning_mod, "build_component_context", MagicMock(return_value=None))
    monkeypatch.setattr(learning_mod, "build_element_context", MagicMock(return_value=None))
    monkeypatch.setattr(learning_mod, "learner_ledger_line", MagicMock(return_value=None))
    monkeypatch.setattr(
        learning_mod, "learner_landscape_for_documents", MagicMock(return_value=None)
    )

    session = _FakeSession([_claim_row(CLAIM_A, CHUNK_A, CLAIM_A_TEXT)])
    monkeypatch.setattr(learning_mod, "_pg_session", lambda: session)
    graph_mock = MagicMock(return_value=_graph_index())
    monkeypatch.setattr(learning_mod, "load_latest_graph", graph_mock)

    metric_mock = MagicMock(return_value=None)
    monkeypatch.setattr(learning_mod, "_record_document_discuss_event", metric_mock)

    return SimpleNamespace(
        settings=settings,
        session=session,
        graph_mock=graph_mock,
        persist_mock=persist_mock,
        trace_mock=trace_mock,
        metric_mock=metric_mock,
        monkeypatch=monkeypatch,
    )


def _set_generation(monkeypatch, captured: dict | None = None, answer: str = "回答本体"):
    def _fake(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return answer

    monkeypatch.setattr(learning_mod, "generate_text", _fake)


def _chat(body: LearningChatRequest):
    return learning_mod.learning_chat(COURSE_ID, TOPIC_ID, body, current_user=CURRENT_USER)


def _last_user(captured: dict) -> str:
    return captured["messages"][-1]["content"]


# ===========================================================================
# 1. 射影 SQL（§5 / KT6）
# ===========================================================================


class TestProjectionSql:
    def test_scope_is_enforced_inside_the_sql(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        _chat(_make_body())

        sql = chat_env.session.statements[0]
        assert "theory_claims_live" in sql
        assert "chunk_id = ANY(CAST(:chunk_ids AS uuid[]))" in sql
        assert "document_id = ANY(CAST(:doc_ids AS uuid[]))" in sql
        params = chat_env.session.params[0]
        assert params["chunk_ids"] == [CHUNK_A]
        assert params["doc_ids"] == [DOC_ID]
        assert chat_env.session.closed is True

    def test_equation_synthesis_claims_are_excluded(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        _chat(_make_body())

        assert "'equation_synthesis'" in chat_env.session.statements[0]

    def test_discuss_all_visible_does_not_widen_the_structure_scope(
        self, chat_env, monkeypatch
    ):
        """検索範囲と同一（構造側で広げない = KT6）。"""
        _set_generation(monkeypatch)
        monkeypatch.setattr(
            learning_mod, "list_visible_document_ids", lambda *a, **k: {DOC_ID, "other-doc"}
        )

        _chat(_make_body(intent_mode="discuss", discuss_scope="all_visible"))

        doc_ids = chat_env.session.params[0]["doc_ids"]
        assert sorted(doc_ids) == sorted([DOC_ID, "other-doc"])

    def test_the_theory_graph_is_read_once_per_document(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)
        chat_env.session.rows = [
            _claim_row(CLAIM_A, CHUNK_A, CLAIM_A_TEXT),
            _claim_row(CLAIM_B, CHUNK_A, CLAIM_B_TEXT),
        ]

        _chat(_make_body())

        assert chat_env.graph_mock.call_count == 1
        assert chat_env.graph_mock.call_args.args[0] == DOC_ID


# ===========================================================================
# 2. ブロックの内容と位置（§5 の合流点）
# ===========================================================================


class TestBlockPlacement:
    def test_block_is_added_without_any_screen_context(self, chat_env, monkeypatch):
        """入口は採用した出典であって画面の申告ではない。"""
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(_make_body("ここが分かりません"))

        content = _last_user(captured)
        assert BLOCK_HEADER_RETRIEVED in content
        assert BLOCK_HEADER_LEARNING not in content
        assert CLAIM_A_TEXT in content
        assert "理論の土台" in content
        assert content.endswith("ここが分かりません")

    def test_order_is_screen_then_retrieved_then_selection_then_message(
        self, chat_env, monkeypatch
    ):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        monkeypatch.setattr(
            learning_mod,
            "build_component_context",
            MagicMock(return_value={
                "component_id": "33333333-3333-3333-3333-333333333333",
                "instance": {"component": {"label": "有効ポテンシャル"}},
            }),
        )

        _chat(
            _make_body(
                "ここが分かりません",
                selection_text=MATERIAL_TEXT[:10],
                screen_context=_screen_context(),
            )
        )

        content = _last_user(captured)
        assert content.index(BLOCK_HEADER_LEARNING) < content.index(BLOCK_HEADER_RETRIEVED)
        assert content.index(BLOCK_HEADER_RETRIEVED) < content.index(SELECTION_BLOCK_HEADER)
        assert content.index(SELECTION_BLOCK_HEADER) < content.index("ここが分かりません")

    def test_citation_numbers_match_the_cited_sources(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        monkeypatch.setattr(
            learning_mod,
            "search_chunks_with_metadata",
            lambda *a, **k: [_chunk(CHUNK_A), _chunk(CHUNK_B, 0.6)],
        )
        chat_env.session.rows = [_claim_row(CLAIM_B, CHUNK_B, CLAIM_B_TEXT)]

        resp = _chat(_make_body())

        content = _last_user(captured)
        assert "[出典2] の箇所には次の主張が構造化されています" in content
        assert [s.index for s in resp.sources] == [1, 2]

    def test_claim_matched_by_the_agent_id_reaches_the_graph_node(
        self, chat_env, monkeypatch
    ):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        chat_env.session.rows = [
            _claim_row(CLAIM_A, CHUNK_A, CLAIM_A_TEXT, agent_id="claim_span_001")
        ]
        chat_env.graph_mock.return_value = _graph_index("claim_span_001")

        _chat(_make_body())

        assert "理論の土台" in _last_user(captured)

    def test_no_cited_sources_means_no_database_read(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])

        _chat(_make_body("ここが分かりません"))

        assert chat_env.session.statements == []
        assert _last_user(captured) == "ここが分かりません"

    def test_no_claim_rows_means_no_block(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        chat_env.session.rows = []

        _chat(_make_body("ここが分かりません"))

        assert BLOCK_HEADER_RETRIEVED not in _last_user(captured)


# ===========================================================================
# 3. モード別（§5）
# ===========================================================================


class TestModeMatrix:
    def test_casual_gets_no_retrieved_block(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(_make_body(intent_mode="casual"))

        assert BLOCK_HEADER_RETRIEVED not in _last_user(captured)
        assert chat_env.session.statements == []

    def test_elicit_gets_no_retrieved_block(self, chat_env, monkeypatch):
        """主張本文を渡すと問いの答えの手渡しになる（UC2 / §11.5 の考え方を継承）。"""
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(_make_body(cycle_mode="elicit"))

        assert BLOCK_HEADER_RETRIEVED not in _last_user(captured)
        assert chat_env.session.statements == []

    @pytest.mark.parametrize(
        "overrides",
        [
            {"cycle_mode": "diff"},
            {"backstage": True},
            {"check_scaffold": True},
            {"intent_mode": "discuss"},
        ],
    )
    def test_other_modes_get_the_retrieved_block(self, chat_env, monkeypatch, overrides):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(_make_body(**overrides))

        assert BLOCK_HEADER_RETRIEVED in _last_user(captured)


# ===========================================================================
# 4. 保存・痕跡・観測（SA6 / §11.7）
# ===========================================================================


class TestPersistenceAndObservation:
    def test_saved_message_is_the_raw_utterance(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        _chat(_make_body("ここが分かりません"))

        saved = chat_env.persist_mock.call_args.args[4]
        assert saved == "ここが分かりません"
        assert BLOCK_HEADER_RETRIEVED not in saved

    def test_trace_payload_is_not_polluted(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        _chat(_make_body("ここが分かりません"))

        payload = chat_env.trace_mock.call_args.kwargs["extra_payload"]
        assert "retrieved_structure" not in payload
        assert BLOCK_HEADER_RETRIEVED not in str(payload)

    def test_structured_grounding_present_is_recorded_once(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        _chat(_make_body())

        events = [call.args[0] for call in chat_env.metric_mock.call_args_list]
        assert events.count("structured_grounding_present") == 1

    def test_no_event_without_a_block(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)
        chat_env.session.rows = []

        _chat(_make_body())

        events = [call.args[0] for call in chat_env.metric_mock.call_args_list]
        assert "structured_grounding_present" not in events


# ===========================================================================
# 5. fail-soft / CostGate
# ===========================================================================


class TestFailSoft:
    def test_claim_projection_failure_degrades_to_the_plain_prompt(
        self, chat_env, monkeypatch
    ):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        monkeypatch.setattr(
            learning_mod,
            "_retrieved_structure_claims",
            MagicMock(side_effect=RuntimeError("boom")),
        )

        resp = _chat(_make_body("ここが分かりません"))

        assert resp.answer == "回答本体"
        assert _last_user(captured) == "ここが分かりません"

    def test_graph_failure_keeps_the_claim_facts(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        chat_env.graph_mock.side_effect = RuntimeError("boom")

        _chat(_make_body())

        content = _last_user(captured)
        assert CLAIM_A_TEXT in content
        assert "理論の土台" not in content

    def test_quota_exhausted_does_not_read_the_database(self, chat_env, monkeypatch):
        from fastapi import HTTPException

        _set_generation(monkeypatch)
        chat_env.settings.learning_chat_max_calls_per_day = 0

        with pytest.raises(HTTPException) as exc:
            _chat(_make_body())

        assert exc.value.status_code == 429
        assert chat_env.session.statements == []


# ===========================================================================
# 6. ヘルパの規律（KT3 / KT7）
# ===========================================================================


class TestHelperDiscipline:
    _HELPERS = (
        "_retrieved_structure_claims",
        "_retrieved_structure_node_index",
        "_retrieved_structure_sources",
        "_learning_retrieved_structure_block",
    )

    def test_helpers_do_not_call_an_llm(self):
        for name in self._HELPERS:
            source = inspect.getsource(getattr(learning_mod, name))
            for needle in ("generate_text", "generate_embeddings", "usage_context"):
                assert needle not in source, f"{name} が LLM 経路に触れている（{needle!r}）"

    def test_helpers_do_not_write(self):
        for name in self._HELPERS:
            source = inspect.getsource(getattr(learning_mod, name))
            for needle in ("INSERT INTO", "UPDATE ", "DELETE FROM", "commit()"):
                assert needle not in source, f"{name} が書き込みに触れている（{needle!r}）"

    def test_the_projection_reads_the_live_view_only(self):
        source = inspect.getsource(learning_mod._retrieved_structure_claims)
        assert "theory_claims_live" in source
        assert "FROM theory_claims\n" not in source
