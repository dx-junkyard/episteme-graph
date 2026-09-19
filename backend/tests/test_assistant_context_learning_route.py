"""画面文脈アダプター Phase 4 — 学習チャット route 側の配線のテスト。

正本: ``docs/features/assistant_screen_adapter_design.md`` §11（特に §11.3 権限3段 /
§11.4 選択テキスト注入 / §11.5 合流点とモード別表 / §11.6 CostGate 位置 /
§11.7 観測 / §11.9 ガードレール案）。

``test_learning_stance_routing.py`` と同型の手法: DB・実 LLM には触れず、
``routes.learning`` の境界関数を monkeypatch で差し替えて ``learning_chat`` を直接呼び、
**LLM 入力（messages）と保存・痕跡・観測の振る舞い**で固定する。

固定する事実:

- ``screen_context`` / ``selection_text`` 無しの LLM 入力は従来とバイト等価（後方互換）
- 選択逐語は第2ブロックとして注入され、本文との一致有無をサーバが明示する（§11.4）
- 画面文脈ブロック → 選択箇所ブロック → 発話 の順（§11.5）
- 台帳の事実が載ったときだけ system 末尾に閉世界の出力拘束が付く（§11.13-2）
- ``selection.course_id`` 不一致・未知 screen は丸ごと無視（§11.3 / fail-closed）
- casual は構造ブロックなし / elicit は表示モードの事実1行だけ / diff・楽屋はあり（§11.5）
- 保存 content・痕跡 payload に画面文脈が入らない（SA6）
- CostGate（429）では射影を1本も引かない（§11.6）
- 射影の例外はそのブロックだけ欠けて 200（SA2 fail-soft）
- grounding が空集合なら射影を引かない（fail-closed）。discuss ``all_visible`` でも
  コース sources の集合で解決する（画面文脈が範囲を広げない = DM1）
- R層の伏せフィールドを読む経路が新規ヘルパに無い（ソース検査）
- ``structured_grounding_present`` はブロック非空のときだけ1件記録される（§11.7）
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
from core.text_hygiene import UNTRUSTED_SOURCE_NOTICE  # noqa: E402
import core.llm_policy as llm_policy_mod  # noqa: E402
from core.assistant_context.schema import (  # noqa: E402
    BLOCK_HEADER_LEARNING,
    LEARNING_VERIFICATION_OUTPUT_CONSTRAINT,
    SELECTION_BLOCK_HEADER,
    SELECTION_MATCH_CONFIRMED,
    SELECTION_MATCH_UNCONFIRMED,
)
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402


CURRENT_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}

COURSE_ID = "course-1"
TOPIC_ID = "topic-1"
DOC_ID = "22222222-2222-2222-2222-222222222222"
COMPONENT_DB_ID = "33333333-3333-3333-3333-333333333333"
MATERIAL_TEXT = "重力レンズは背景光源の像を歪める。観測量は時間遅延である。"


def _course_data() -> dict:
    return {
        "id": COURSE_ID,
        "title": "テストコース",
        "domain": "テスト分野",
        "chapters": [],
        "topics": [{
            "id": TOPIC_ID,
            "title": "テストトピック",
            "chapter_index": 0,
            "prerequisites": [],
            "content": MATERIAL_TEXT,
        }],
        "concepts": [],
        "sources": [],
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


def _component_context() -> dict:
    return {
        "component_id": COMPONENT_DB_ID,
        "instance": {
            "component": {
                "label": "有効ポテンシャル",
                "summary": "レンズ方程式の有効ポテンシャル。",
                "component_type": "concept",
                "teaching_takeaway": "",
            },
            "in_paper": {
                "document": {"id": DOC_ID, "title": "テスト論文", "section": "2.1"},
                "narrative_role": "観測量の構成",
                "graph_summary_excerpt": "",
            },
            "supports": {
                "preconditions": [{"text": "弱場近似が成り立つこと"}],
                "equations": [{"id": "eq_2_7", "label": "式 (12)"}],
                "claims": [{"excerpt": "時間遅延はポテンシャル差で決まる"}],
            },
            "provenance": "course_freeze",
        },
        "shared_part": None,
        "graph": None,
    }


def _ledger_line() -> dict:
    return {
        "target_id": COMPONENT_DB_ID,
        "target_type": "component",
        "verification_status": "untested",
        "verification_status_label": "未検証",
        "scopes": [],
        "scope_coverage": "none",
        "fact_line": "この内容の検証スコープはまだ記帳されていません。",
        "falsification_conditions": [],
    }


def _landscape_dto() -> dict:
    return {
        "course_domain_key": "astrophysics",
        "domains": [
            {
                "domain_key": "astrophysics",
                "domain_name": "宇宙物理",
                "frozen_version": "v3",
                "is_course_map": True,
            }
        ],
        "documents": [
            {
                "document_id": DOC_ID,
                "title": "テスト論文",
                "placements": [
                    {
                        "domain_key": "astrophysics",
                        "node_id": "concept_lensing",
                        "node_label": "重力レンズ",
                        "region_id": "region_obs",
                        "node_kind": "concept",
                        "perspective_label": "観測",
                        "weight_label": "強い",
                        "status": "inferred",
                        "provenance_label": "AIによる推定（未確認）",
                        "reason": "",
                        "evidence": [],
                    }
                ],
            }
        ],
        "corpus": {},
    }


def _screen_context(**overrides) -> dict:
    payload = {
        "screen": "learning",
        "selection": {
            "course_id": COURSE_ID,
            "topic_id": TOPIC_ID,
            "kind": "element",
            "element_type": "component",
            "element_id": COMPONENT_DB_ID,
        },
        "view": {"mode": "chat", "precision_reading": False},
        "visible_entities": [],
    }
    for key, value in overrides.items():
        if key in ("selection", "view") and isinstance(value, dict):
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    return payload


def _make_body(message: str = "ここが分かりません", **overrides) -> LearningChatRequest:
    kwargs: dict = dict(message=message, history=[])
    kwargs.update(overrides)
    return LearningChatRequest(**kwargs)


@pytest.fixture
def chat_env(monkeypatch):
    """DB / LLM 非接続の学習チャット環境 + 学習者射影の fake（呼び出しを記録する）。"""
    settings = _fake_settings()
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_policy_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())

    monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
    monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
    monkeypatch.setattr(learning_mod, "log_unanswered_query", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_prerequisites", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "_atlas_topic_attribution", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_and_count_confirm_prompt", lambda *a, **k: False)
    monkeypatch.setattr(learning_mod, "maybe_schedule_tension_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda *a, **k: {DOC_ID})
    monkeypatch.setattr(learning_mod, "list_course_source_document_ids", lambda *a, **k: [DOC_ID])
    monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda *a, **k: {})
    # 非LLM 一次判定が働く発話でも常に同じ経路を通す（本テストの主題は入力の組み立て）。
    monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")

    persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
    trace_mock = MagicMock(return_value="trace-1")
    monkeypatch.setattr(learning_mod, "record_interest_trace", trace_mock)
    monkeypatch.setattr(learning_mod, "detect_and_record_misconception", MagicMock(return_value=None))

    # 学習者射影（権限ゲート付きの既存経路）を fake にする。route 側は「渡された DTO を
    # 解決器へ流す」だけで、遮断そのものは射影の責務（§11.3）。
    component_mock = MagicMock(return_value=_component_context())
    monkeypatch.setattr(learning_mod, "build_component_context", component_mock)
    element_mock = MagicMock(return_value=None)
    monkeypatch.setattr(learning_mod, "build_element_context", element_mock)
    monkeypatch.setattr(
        learning_mod, "_first_approved_component_explanation", lambda *a, **k: None
    )
    ledger_mock = MagicMock(return_value=_ledger_line())
    monkeypatch.setattr(learning_mod, "learner_ledger_line", ledger_mock)
    landscape_mock = MagicMock(return_value=_landscape_dto())
    monkeypatch.setattr(learning_mod, "learner_landscape_for_documents", landscape_mock)
    monkeypatch.setattr(
        learning_mod, "_pg_session", lambda: SimpleNamespace(close=lambda: None)
    )

    metric_mock = MagicMock(return_value=None)
    monkeypatch.setattr(learning_mod, "_record_document_discuss_event", metric_mock)

    return SimpleNamespace(
        settings=settings,
        persist_mock=persist_mock,
        trace_mock=trace_mock,
        component_mock=component_mock,
        element_mock=element_mock,
        ledger_mock=ledger_mock,
        landscape_mock=landscape_mock,
        metric_mock=metric_mock,
    )


def _set_generation(monkeypatch, captured: dict | None = None, answer: str = "回答本体"):
    state = {"calls": 0, "kwargs": {}}

    def _fake(**kwargs):
        state["calls"] += 1
        state["kwargs"] = kwargs
        if captured is not None:
            captured.update(kwargs)
        return answer

    monkeypatch.setattr(learning_mod, "generate_text", _fake)
    return state


def _chat(body: LearningChatRequest):
    return learning_mod.learning_chat(COURSE_ID, TOPIC_ID, body, current_user=CURRENT_USER)


def _messages(captured: dict) -> list[dict]:
    return captured["messages"]


def _last_user(captured: dict) -> str:
    return _messages(captured)[-1]["content"]


def _system(captured: dict) -> str:
    return _messages(captured)[0]["content"]


def _trace_payload(chat_env) -> dict:
    return chat_env.trace_mock.call_args.kwargs["extra_payload"]


# ===========================================================================
# 1. 後方互換 — 送ってこないクライアントのプロンプトは1バイトも変わらない（§11.10）
# ===========================================================================


class TestBackwardCompatibility:
    def test_no_screen_context_and_no_selection_is_byte_identical(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(_make_body("レンズ方程式とは何ですか"))

        assert _last_user(captured) == "レンズ方程式とは何ですか"
        assert BLOCK_HEADER_LEARNING not in _last_user(captured)
        assert SELECTION_BLOCK_HEADER not in _last_user(captured)
        assert LEARNING_VERIFICATION_OUTPUT_CONSTRAINT not in _system(captured)

    def test_no_screen_context_does_not_touch_the_learner_projections(
        self, chat_env, monkeypatch
    ):
        _set_generation(monkeypatch)

        _chat(_make_body("レンズ方程式とは何ですか"))

        chat_env.component_mock.assert_not_called()
        chat_env.ledger_mock.assert_not_called()
        chat_env.landscape_mock.assert_not_called()

    def test_unknown_screen_is_ignored(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(_make_body(screen_context=_screen_context(screen="unknown_screen")))

        assert _last_user(captured) == "ここが分かりません"
        chat_env.component_mock.assert_not_called()

    def test_course_id_mismatch_is_ignored_entirely(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(
            _make_body(
                screen_context=_screen_context(selection={"course_id": "another-course"})
            )
        )

        assert _last_user(captured) == "ここが分かりません"
        chat_env.component_mock.assert_not_called()
        chat_env.ledger_mock.assert_not_called()
        chat_env.landscape_mock.assert_not_called()


# ===========================================================================
# 2. 選択逐語の注入（§11.4）
# ===========================================================================


class TestSelectionBlock:
    def test_selection_text_is_quoted_with_a_confirmed_match(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(_make_body("ここが分かりません", selection_text="観測量は時間遅延である"))

        content = _last_user(captured)
        assert SELECTION_BLOCK_HEADER in content
        assert SELECTION_MATCH_CONFIRMED in content
        assert "> 観測量は時間遅延である" in content
        assert content.endswith("ここが分かりません")

    def test_selection_text_not_in_material_is_kept_but_marked_unconfirmed(
        self, chat_env, monkeypatch
    ):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(_make_body("ここが分かりません", selection_text="どこにも無い文字列です"))

        content = _last_user(captured)
        # **落とさない**（学習者が選んだ事実は情報 = 原則3）。正直に未確認と書く。
        assert "> どこにも無い文字列です" in content
        assert SELECTION_MATCH_UNCONFIRMED in content

    def test_untrusted_notice_accompanies_the_blocks_when_the_scaffold_lacks_it(
        self, chat_env, monkeypatch
    ):
        """TB1〜TB4（§11.4）: 選択逐語・画面文脈は PDF 由来の untrusted 入力なので、足場ターンに
        注意書きが無いターン（cited_chunks 空）では当該ターンに注意書きを添える。"""
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        # トピック教材も検索ヒットも無い（cited_chunks 空）ターン = 足場に注意書きが無い。
        monkeypatch.setattr(learning_mod, "_topic_student_material", lambda *_a, **_k: "")

        _chat(_make_body("ここが分かりません", selection_text="観測量は時間遅延である"))

        content = _last_user(captured)
        scaffold = _messages(captured)[1]["content"]
        assert UNTRUSTED_SOURCE_NOTICE not in scaffold
        assert content.count(UNTRUSTED_SOURCE_NOTICE) == 1
        assert content.index(UNTRUSTED_SOURCE_NOTICE) < content.index(SELECTION_BLOCK_HEADER)
        assert content.endswith("ここが分かりません")

    def test_untrusted_notice_is_not_duplicated_when_the_scaffold_has_it(
        self, chat_env, monkeypatch
    ):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        monkeypatch.setattr(
            learning_mod,
            "search_chunks_with_metadata",
            lambda *a, **k: [{
                "id": "chunk-1", "text": "本文", "source_title": "論文A", "score": 0.9,
                "tier": "source", "material_id": "m1", "source_file": "",
            }],
        )

        _chat(_make_body("ここが分かりません", selection_text="観測量は時間遅延である"))

        assert UNTRUSTED_SOURCE_NOTICE in _messages(captured)[1]["content"]
        assert UNTRUSTED_SOURCE_NOTICE not in _last_user(captured)

    def test_selection_segment_id_wins_over_the_screen_declared_segment(
        self, chat_env, monkeypatch
    ):
        """§11.2: 画面の segment_id と selection_segment_id が食い違えば後者（サーバが痕跡で
        既に信頼している明示アンカー）を事実文に使う。"""
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        ctx = _screen_context()
        ctx["selection"] = {**ctx["selection"], "kind": "segment", "segment_id": "9"}
        ctx["selection"].pop("element_id", None)
        ctx["selection"].pop("element_type", None)

        _chat(_make_body(selection_text="観測量は時間遅延である", selection_segment_id=3,
                         screen_context=ctx))

        content = _last_user(captured)
        assert "スライド3" in content
        assert "スライド9" not in content

    def test_selection_text_alone_does_not_add_the_verification_constraint(
        self, chat_env, monkeypatch
    ):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(_make_body(selection_text="観測量は時間遅延である"))

        assert LEARNING_VERIFICATION_OUTPUT_CONSTRAINT not in _system(captured)


# ===========================================================================
# 3. 画面文脈ブロック（§11.3 / §11.5）
# ===========================================================================


class TestScreenContextBlock:
    def test_element_facts_are_prepended_before_the_selection_and_the_message(
        self, chat_env, monkeypatch
    ):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(
            _make_body(
                "ここが分かりません",
                selection_text="観測量は時間遅延である",
                screen_context=_screen_context(),
            )
        )

        content = _last_user(captured)
        assert content.startswith(BLOCK_HEADER_LEARNING)
        assert content.index(BLOCK_HEADER_LEARNING) < content.index(SELECTION_BLOCK_HEADER)
        assert content.index(SELECTION_BLOCK_HEADER) < content.index("ここが分かりません")
        assert content.endswith("ここが分かりません")
        assert "有効ポテンシャル" in content

    def test_projections_are_called_within_the_course_source_scope(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        _chat(_make_body(screen_context=_screen_context()))

        args, _kwargs = chat_env.component_mock.call_args
        assert args[0] == COMPONENT_DB_ID
        assert args[1] == COURSE_ID
        assert args[2] == {DOC_ID}
        chat_env.ledger_mock.assert_called_once()
        assert chat_env.ledger_mock.call_args.args[1:] == ("component", COMPONENT_DB_ID)
        assert chat_env.landscape_mock.call_args.args[1] == [DOC_ID]

    def test_ledger_facts_add_the_closed_world_output_constraint(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(_make_body(screen_context=_screen_context()))

        assert "このコーパスの中では検証記録がありません" in _system(captured)
        assert _system(captured).endswith(LEARNING_VERIFICATION_OUTPUT_CONSTRAINT)

    def test_without_a_ledger_row_the_constraint_is_not_added(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        chat_env.ledger_mock.return_value = None

        _chat(_make_body(screen_context=_screen_context()))

        assert BLOCK_HEADER_LEARNING in _last_user(captured)
        assert LEARNING_VERIFICATION_OUTPUT_CONSTRAINT not in _system(captured)

    def test_placement_keeps_the_provenance_label(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(_make_body(screen_context=_screen_context()))

        assert "AIによる推定（未確認）" in _last_user(captured)

    def test_landscape_is_skipped_when_the_document_is_outside_the_scope(
        self, chat_env, monkeypatch
    ):
        _set_generation(monkeypatch)
        monkeypatch.setattr(
            learning_mod, "list_course_source_document_ids", lambda *a, **k: ["other-doc"]
        )
        # component 射影自体はスコープ強制を持つ（fake なので返るが）、配置は
        # grounding 集合に属する document_id が取れないので引かない。
        _chat(_make_body(screen_context=_screen_context()))

        chat_env.landscape_mock.assert_not_called()

    def test_empty_grounding_scope_resolves_no_corpus_facts(self, chat_env, monkeypatch):
        """fail-closed: 資料由来の事実は1つも出ない（表示モードの申告だけが残る）。"""
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        monkeypatch.setattr(learning_mod, "list_course_source_document_ids", lambda *a, **k: [])

        _chat(_make_body(screen_context=_screen_context()))

        content = _last_user(captured)
        assert "有効ポテンシャル" not in content
        assert "テスト論文" not in content
        assert "検証" not in content
        chat_env.component_mock.assert_not_called()
        chat_env.ledger_mock.assert_not_called()
        chat_env.landscape_mock.assert_not_called()

    def test_discuss_all_visible_does_not_widen_the_screen_context_scope(
        self, chat_env, monkeypatch
    ):
        """DM1: 画面文脈が範囲を広げない（解決は常にコース sources の集合）。"""
        _set_generation(monkeypatch)
        visible = MagicMock(return_value={DOC_ID, "another-doc"})
        monkeypatch.setattr(learning_mod, "list_visible_document_ids", visible)

        _chat(
            _make_body(
                intent_mode="discuss",
                discuss_scope="all_visible",
                screen_context=_screen_context(),
            )
        )

        assert chat_env.component_mock.call_args.args[2] == {DOC_ID}


# ===========================================================================
# 4. モード別の扱い（§11.5 の表）
# ===========================================================================


class TestModeMatrix:
    def test_casual_gets_no_structure_block_but_keeps_the_selection(
        self, chat_env, monkeypatch
    ):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(
            _make_body(
                intent_mode="casual",
                selection_text="観測量は時間遅延である",
                screen_context=_screen_context(),
            )
        )

        content = _last_user(captured)
        assert BLOCK_HEADER_LEARNING not in content
        assert SELECTION_BLOCK_HEADER in content
        chat_env.component_mock.assert_not_called()

    def test_elicit_gets_only_the_view_fact(self, chat_env, monkeypatch):
        """Elicit に主張本文・検証事実を渡すと問いの答えを手渡すことになる（§11.5）。"""
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(
            _make_body(
                cycle_mode="elicit",
                selection_text="観測量は時間遅延である",
                screen_context=_screen_context(view={"mode": "chat"}),
            )
        )

        content = _last_user(captured)
        assert BLOCK_HEADER_LEARNING in content
        assert "通常のチャット画面" in content
        assert "有効ポテンシャル" not in content
        assert "検証" not in content.split(SELECTION_BLOCK_HEADER)[0]
        assert SELECTION_BLOCK_HEADER in content
        assert LEARNING_VERIFICATION_OUTPUT_CONSTRAINT not in _system(captured)
        # elicit では射影を1本も引かない（渡さない事実のために DB を叩かない）。
        chat_env.component_mock.assert_not_called()
        chat_env.ledger_mock.assert_not_called()
        chat_env.landscape_mock.assert_not_called()

    def test_diff_gets_the_full_structure_block(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(_make_body(cycle_mode="diff", screen_context=_screen_context()))

        assert "有効ポテンシャル" in _last_user(captured)

    def test_backstage_gets_the_full_structure_block(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(_make_body(backstage=True, screen_context=_screen_context()))

        assert "有効ポテンシャル" in _last_user(captured)

    def test_check_scaffold_gets_the_full_structure_block(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        _chat(_make_body(check_scaffold=True, screen_context=_screen_context()))

        assert "有効ポテンシャル" in _last_user(captured)


# ===========================================================================
# 5. 保存・痕跡は不変（SA6）
# ===========================================================================


class TestPersistenceIsUnchanged:
    def test_saved_message_is_the_raw_utterance(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        _chat(
            _make_body(
                "ここが分かりません",
                selection_text="観測量は時間遅延である",
                screen_context=_screen_context(),
            )
        )

        saved = chat_env.persist_mock.call_args.args[4]
        assert saved == "ここが分かりません"
        assert BLOCK_HEADER_LEARNING not in saved
        assert SELECTION_BLOCK_HEADER not in saved

    def test_trace_payload_has_no_screen_context_key(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        _chat(_make_body(screen_context=_screen_context()))

        payload = _trace_payload(chat_env)
        assert "screen_context" not in payload
        assert BLOCK_HEADER_LEARNING not in repr(payload)


# ===========================================================================
# 6. CostGate（§11.6）と fail-soft（SA2）
# ===========================================================================


class TestCostGateAndFailSoft:
    def test_quota_exhausted_does_not_resolve_anything(self, chat_env, monkeypatch):
        from fastapi import HTTPException

        _set_generation(monkeypatch)
        chat_env.settings.learning_chat_max_calls_per_day = 0

        with pytest.raises(HTTPException) as exc:
            _chat(_make_body(screen_context=_screen_context()))

        assert exc.value.status_code == 429
        chat_env.component_mock.assert_not_called()
        chat_env.ledger_mock.assert_not_called()
        chat_env.landscape_mock.assert_not_called()

    def test_projection_failure_degrades_to_the_plain_prompt(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        chat_env.component_mock.side_effect = RuntimeError("boom")

        resp = _chat(_make_body(screen_context=_screen_context()))

        assert resp.answer == "回答本体"
        # 射影1本の失敗はその事実だけが欠ける（対話は止まらない・「見えないものがある」
        # とも言わない）。
        content = _last_user(captured)
        assert content.endswith("ここが分かりません")
        assert "有効ポテンシャル" not in content
        assert LEARNING_VERIFICATION_OUTPUT_CONSTRAINT not in _system(captured)

    def test_ledger_failure_keeps_the_element_facts(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        chat_env.ledger_mock.side_effect = RuntimeError("boom")

        _chat(_make_body(screen_context=_screen_context()))

        assert "有効ポテンシャル" in _last_user(captured)
        assert LEARNING_VERIFICATION_OUTPUT_CONSTRAINT not in _system(captured)


# ===========================================================================
# 7. 観測（§11.7）
# ===========================================================================


class TestObservation:
    def test_structured_grounding_present_is_recorded_once(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        _chat(_make_body(screen_context=_screen_context()))

        events = [call.args[0] for call in chat_env.metric_mock.call_args_list]
        assert events.count("structured_grounding_present") == 1

    def test_no_event_when_the_block_is_empty(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        _chat(_make_body("ここが分かりません", selection_text="観測量は時間遅延である"))

        events = [call.args[0] for call in chat_env.metric_mock.call_args_list]
        assert "structured_grounding_present" not in events


# ===========================================================================
# 8. R層の伏せフィールドは route の新規ヘルパに届かない（§11.9）
# ===========================================================================


class TestReconstructionHiddenFieldsAreUnreachable:
    _HELPERS = (
        "_learning_screen_sources",
        "_learning_screen_context_block",
        "_screen_element_sources",
        "_screen_element_document_id",
        "_component_context_with_explanation",
    )

    def test_helpers_do_not_read_reconstruction_tables(self):
        banned = ("reconstruction_item", "learner_reconstruction", "response_space", "expected")
        for name in self._HELPERS:
            source = inspect.getsource(getattr(learning_mod, name))
            for needle in banned:
                assert needle not in source, f"{name} が R層の語彙 {needle!r} に触れている"

    def test_helpers_only_go_through_learner_projections(self):
        """生 SQL を書かない（射影が持つ遮断を再実装しない = §11.3）。"""
        for name in self._HELPERS:
            source = inspect.getsource(getattr(learning_mod, name))
            for needle in ("SELECT", "sa_text", "theory_components", "epistemic_ledger",
                           "landscape_placements"):
                assert needle not in source, f"{name} が生テーブル/SQL に触れている（{needle!r}）"
