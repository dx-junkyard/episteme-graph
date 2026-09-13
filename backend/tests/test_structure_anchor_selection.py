"""学ぶ単位 P2-7 — 学習者の痕跡を構造に着地させる（設計 §8）。

正本: ``docs/features/learning_units_design.md`` §8（親文書
``docs/architecture/knowledge_structure_review_2026-09-12.md`` の C-11
「痕跡が ``seg_0`` 固定で構造に着地しない」）。

固定する事実:

- ``resolve_selection_segment`` は**一意に決まるときだけ**区画番号を返す
  （0 件一致・複数一致は ``None``。推測しない = LU3）。空白・改行・全角空白の違いと
  制御シーケンスで「不一致」にしない。
- ``_learner_selected_anchor`` の優先順は 要素タップ > テキスト選択 > 画面文脈の選択要素。
- 区画番号が申告されていないテキスト選択は教材本文との逐語一致で埋まり、埋まらなければ
  ``anchor_id=""``（``seg_0`` を既定にしない）。
- 画面で要素チップを選んだ状態の発話は ``learner_selected`` / ``reason="screen_selection"``
  で確定する（AI 候補 = 方法B に回さない）。course 不一致・未知 screen の画面文脈は無視。
- 生数値（confidence）を DTO へ持ち出さない（payload の既定形のみ）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import core.llm_policy as llm_policy_mod  # noqa: E402
import routes.learning as learning_mod  # noqa: E402
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from core.structure_anchor.selection_segment import (  # noqa: E402
    resolve_selection_segment,
)
from schemas import LearningChatRequest  # noqa: E402

LEARNING_PY = BACKEND / "api" / "routes" / "learning.py"
COURSE_ID = "course-1"
COMPONENT_DB_ID = "33333333-3333-3333-3333-333333333333"


def _body(**overrides) -> LearningChatRequest:
    kwargs: dict = dict(message="ここが分かりません", history=[])
    kwargs.update(overrides)
    return LearningChatRequest(**kwargs)


def _screen_context(**selection) -> dict:
    base = {
        "course_id": COURSE_ID,
        "topic_id": "topic-1",
        "kind": "element",
        "element_type": "component",
        "element_id": COMPONENT_DB_ID,
    }
    base.update(selection)
    return {
        "screen": "learning",
        "selection": base,
        "view": {"mode": "chat"},
        "visible_entities": [],
    }


# ===========================================================================
# 1. core — 区画番号の決定論的解決（非LLM・推測しない）
# ===========================================================================


class TestResolveSelectionSegment:
    def test_unique_match_returns_index(self):
        segments = ["重力レンズの基礎。", "時間遅延は観測量である。", "まとめ。"]
        assert resolve_selection_segment(segments, "時間遅延は観測量") == 1

    def test_first_segment_is_a_real_answer(self):
        """``0`` は「決まらなかった」の代用ではなく、実際に一致した区画番号。"""
        assert resolve_selection_segment(["重力レンズの基礎。", "まとめ。"], "重力レンズ") == 0

    def test_no_match_returns_none(self):
        assert resolve_selection_segment(["重力レンズの基礎。"], "存在しない引用") is None

    def test_ambiguous_match_returns_none(self):
        """同じ文が複数区画にあるときは**どちらかを選ばない**（推測しない = LU3）。"""
        segments = ["観測量は時間遅延である。", "再掲: 観測量は時間遅延である。"]
        assert resolve_selection_segment(segments, "観測量は時間遅延") is None

    def test_whitespace_and_newlines_do_not_break_the_match(self):
        segments = ["重力レンズは\n背景光源の像を  歪める。"]
        assert resolve_selection_segment(segments, "背景光源の 像を歪める") == 0

    def test_fullwidth_space_is_normalized(self):
        assert resolve_selection_segment(["観測量は　時間遅延である。"], "観測量は時間遅延") == 0

    def test_control_sequences_are_stripped_before_matching(self):
        segments = ["\x1b[0m観測量は時間遅延である。"]
        assert resolve_selection_segment(segments, "観測量は時間遅延") == 0

    def test_empty_inputs_are_none(self):
        assert resolve_selection_segment([], "何か") is None
        assert resolve_selection_segment(None, "何か") is None
        assert resolve_selection_segment(["本文"], "") is None
        assert resolve_selection_segment(["本文"], None) is None
        assert resolve_selection_segment(["本文"], "   ") is None

    def test_is_pure_and_fail_soft(self):
        """壊れた入力でも例外を外に出さない（fail-soft）。"""
        assert resolve_selection_segment([None, 3, "本文"], "本文") == 2

    def test_does_not_mutate_input(self):
        segments = ["重力レンズの基礎。", "まとめ。"]
        snapshot = list(segments)
        resolve_selection_segment(segments, "まとめ")
        assert segments == snapshot


class TestSelectionSegmentModulePurity:
    def test_core_module_does_not_import_fastapi_or_llm(self):
        tree = ast.parse(
            (BACKEND / "core" / "structure_anchor" / "selection_segment.py").read_text(
                encoding="utf-8"
            )
        )
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        for module in imported:
            for forbidden in ("fastapi", "sqlalchemy", "core.llm", "openai"):
                assert not module.startswith(forbidden), module


# ===========================================================================
# 2. route — 明示アンカーの優先順と着地
# ===========================================================================


class TestAnchorPriority:
    def test_element_tap_wins_over_selection_and_screen(self):
        anchor = learning_mod._learner_selected_anchor(
            _body(
                element_id="comp-1",
                element_type="concept",
                element_label="有効ポテンシャル",
                selection_text="観測量は時間遅延である",
            ),
            screen_selection=_screen_context()["selection"],
            segment_texts=["観測量は時間遅延である。"],
        )
        assert anchor["reason"] == "element_tap"
        assert anchor["anchor_id"] == "comp-1"

    def test_text_selection_wins_over_screen_selection(self):
        anchor = learning_mod._learner_selected_anchor(
            _body(selection_text="観測量は時間遅延である"),
            screen_selection=_screen_context()["selection"],
            segment_texts=["観測量は時間遅延である。"],
        )
        assert anchor["reason"] == "text_selection"
        assert anchor["anchor_type"] == "segment"

    def test_nothing_explicit_is_none(self):
        assert learning_mod._learner_selected_anchor(_body()) is None


class TestTextSelectionSegment:
    def test_client_declared_segment_is_kept(self):
        anchor = learning_mod._learner_selected_anchor(
            _body(selection_text="時間遅延", selection_segment_id=2),
            segment_texts=["時間遅延について。"],
        )
        assert anchor["anchor_id"] == "seg_2"

    def test_segment_is_resolved_from_material_when_not_declared(self):
        anchor = learning_mod._learner_selected_anchor(
            _body(selection_text="観測量は時間遅延である"),
            segment_texts=["導入。", "観測量は時間遅延である。"],
        )
        assert anchor["anchor_id"] == "seg_1"
        assert anchor["evidence_quote"] == "観測量は時間遅延である"

    def test_unresolvable_segment_is_empty_not_seg_0(self):
        """C-11 の是正: 決まらないときに ``seg_0`` を捏造しない。"""
        anchor = learning_mod._learner_selected_anchor(
            _body(selection_text="教材の外から貼り付けた文"),
            segment_texts=["導入。", "観測量は時間遅延である。"],
        )
        assert anchor["anchor_id"] == ""

    def test_no_material_means_no_segment_id(self):
        anchor = learning_mod._learner_selected_anchor(
            _body(selection_text="観測量は時間遅延である"), segment_texts=None
        )
        assert anchor["anchor_id"] == ""

    def test_backward_compatible_call_without_keywords(self):
        """既存の呼び出し（位置引数 1 つ）はそのまま動く。"""
        anchor = learning_mod._learner_selected_anchor(
            _body(selection_text="時間遅延", selection_segment_id=0)
        )
        assert anchor["anchor_id"] == "seg_0"
        assert anchor["attribution_source"] == "learner_selected"


class TestScreenSelectionAnchor:
    def test_component_selection_lands_as_concept(self):
        anchor = learning_mod._learner_selected_anchor(
            _body(), screen_selection=_screen_context()["selection"]
        )
        assert anchor["attribution_source"] == "learner_selected"
        assert anchor["reason"] == "screen_selection"
        assert anchor["anchor_type"] == "concept"
        assert anchor["anchor_id"] == COMPONENT_DB_ID
        assert anchor["doubt_type"] == "unclassified"
        # 逐語を伴わない帰属（要素そのものを選んだ）— 捏造した引用を入れない。
        assert anchor["evidence_quote"] == ""

    def test_claim_and_equation_keep_their_granularity(self):
        for element_type, expected in (("claim", "claim"), ("equation", "equation")):
            anchor = learning_mod._learner_selected_anchor(
                _body(),
                screen_selection=_screen_context(
                    element_type=element_type, element_id="x-1"
                )["selection"],
            )
            assert anchor["anchor_type"] == expected, element_type

    def test_formula_is_absorbed_into_equation(self):
        anchor = learning_mod._learner_selected_anchor(
            _body(),
            screen_selection=_screen_context(element_type="formula", element_id="eq-1")[
                "selection"
            ],
        )
        assert anchor["anchor_type"] == "equation"

    def test_figure_degrades_to_a_coarser_granularity(self):
        anchor = learning_mod._learner_selected_anchor(
            _body(),
            screen_selection=_screen_context(element_type="figure", element_id="fig-1")[
                "selection"
            ],
        )
        assert anchor["anchor_type"] == "chunk"

    def test_unknown_element_type_is_ignored(self):
        assert (
            learning_mod._learner_selected_anchor(
                _body(),
                screen_selection=_screen_context(
                    element_type="chapter", element_id="c-1"
                )["selection"],
            )
            is None
        )

    def test_element_id_without_type_is_ignored(self):
        assert (
            learning_mod._learner_selected_anchor(
                _body(),
                screen_selection={"course_id": COURSE_ID, "element_id": "x-1"},
            )
            is None
        )

    def test_label_falls_back_to_the_id(self):
        anchor = learning_mod._learner_selected_anchor(
            _body(), screen_selection=_screen_context()["selection"]
        )
        assert anchor["anchor_label"] == COMPONENT_DB_ID

    def test_declared_label_is_used_when_present(self):
        anchor = learning_mod._learner_selected_anchor(
            _body(),
            screen_selection=_screen_context(element_label="有効ポテンシャル")["selection"],
        )
        assert anchor["anchor_label"] == "有効ポテンシャル"


class TestScreenSelectionGate:
    def test_matching_course_is_passed_through(self):
        selection = learning_mod._screen_selection_for_anchor(
            _body(screen_context=_screen_context()), course_id=COURSE_ID
        )
        assert selection["element_id"] == COMPONENT_DB_ID

    def test_other_course_is_ignored(self):
        assert (
            learning_mod._screen_selection_for_anchor(
                _body(screen_context=_screen_context(course_id="other-course")),
                course_id=COURSE_ID,
            )
            is None
        )

    def test_unknown_screen_is_ignored(self):
        payload = _screen_context()
        payload["screen"] = "unknown_screen"
        assert (
            learning_mod._screen_selection_for_anchor(
                _body(screen_context=payload), course_id=COURSE_ID
            )
            is None
        )

    def test_absent_screen_context_is_none(self):
        assert (
            learning_mod._screen_selection_for_anchor(_body(), course_id=COURSE_ID)
            is None
        )


class TestSegmentTextMaterial:
    def test_topic_material_is_the_delivered_material(self):
        assert learning_mod._anchor_segment_texts(
            {"content": "観測量は時間遅延である。"}
        ) == ["観測量は時間遅延である。"]

    def test_student_material_wins(self):
        assert learning_mod._anchor_segment_texts(
            {"content": "旧本文", "student_material": {"source_text": "授業用教材"}}
        ) == ["授業用教材"]

    def test_empty_topic_is_none(self):
        assert learning_mod._anchor_segment_texts({}) is None
        assert learning_mod._anchor_segment_texts(None) is None


# ===========================================================================
# 3. 配線（ソース検査）— RAG 経路が両方の材料を渡す / 要素タップ経路は不変
# ===========================================================================


def _function_source(name: str) -> str:
    tree = ast.parse(LEARNING_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(LEARNING_PY.read_text(encoding="utf-8"), node) or ""
    raise AssertionError(name)


class TestWiring:
    def test_rag_path_passes_screen_selection_and_segment_texts(self):
        source = _function_source("_learning_chat_core")
        call = source.split("_sel_anchor = _learner_selected_anchor(")[1].split("\n    )")[0]
        assert "screen_selection=_screen_selection_for_anchor(" in call
        assert "course_id=course_id" in call
        assert "segment_texts=_anchor_segment_texts(topic_info)" in call

    def test_element_tap_path_is_unchanged(self):
        """要素タップ経路（EXPLAIN_GRAPH_ELEMENT）は従来どおり body だけで解決する。"""
        source = _function_source("_learning_chat_core")
        branch = source.split('if body.action == "EXPLAIN_GRAPH_ELEMENT":')[1].split(
            "# 学生 HELP ルート"
        )[0]
        assert "_tap_anchor = _learner_selected_anchor(body)" in branch

    def test_no_llm_call_in_the_anchor_path(self):
        """痕跡の着地は同期・非LLM（P6）。"""
        for name in (
            "_learner_selected_anchor",
            "_screen_selection_for_anchor",
            "_anchor_segment_texts",
            "_screen_selection_anchor_type",
            "_screen_selection_element_type",
        ):
            source = _function_source(name)
            for forbidden in ("generate_text", "generate_structured", "embed"):
                assert forbidden not in source, (name, forbidden)


# ===========================================================================
# 4. 実行時 — 痕跡に実際に着地する（DB / LLM 非接続）
# ===========================================================================
#
# ``test_assistant_context_learning_route.py`` と同型の手法: 境界関数を差し替えて
# ``learning_chat`` を直接呼び、**記録された痕跡 payload** で固定する。

TOPIC_ID = "topic-1"
DOC_ID = "22222222-2222-2222-2222-222222222222"
MATERIAL_TEXT = "重力レンズは背景光源の像を歪める。\n\n観測量は時間遅延である。"
CURRENT_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}


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


@pytest.fixture
def chat_env(monkeypatch):
    settings = SimpleNamespace(
        learning_chat_max_calls_per_day=300,
        learning_chat_llm_model="",
        llm_analysis_model="analysis-model-x",
        llm_fast_model="fast-model-x",
    )
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
    anchor_mining = MagicMock(return_value=None)
    monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", anchor_mining)
    monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda *a, **k: {DOC_ID})
    monkeypatch.setattr(learning_mod, "list_course_source_document_ids", lambda *a, **k: [DOC_ID])
    monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda *a, **k: {})
    monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")
    monkeypatch.setattr(learning_mod, "persist_chat_history", MagicMock(return_value={"user_message_id": "msg-1"}))
    trace_mock = MagicMock(return_value="trace-1")
    monkeypatch.setattr(learning_mod, "record_interest_trace", trace_mock)
    monkeypatch.setattr(learning_mod, "detect_and_record_misconception", MagicMock(return_value=None))
    monkeypatch.setattr(learning_mod, "build_component_context", MagicMock(return_value=None))
    monkeypatch.setattr(learning_mod, "build_element_context", MagicMock(return_value=None))
    monkeypatch.setattr(learning_mod, "learner_ledger_line", MagicMock(return_value=None))
    monkeypatch.setattr(learning_mod, "learner_landscape_for_documents", MagicMock(return_value=None))
    monkeypatch.setattr(learning_mod, "_first_approved_component_explanation", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "_pg_session", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(learning_mod, "_record_document_discuss_event", MagicMock(return_value=None))
    monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: "回答本体")
    return SimpleNamespace(trace_mock=trace_mock, anchor_mining=anchor_mining)


def _chat(**overrides):
    body = _body(**overrides)
    return learning_mod.learning_chat(COURSE_ID, TOPIC_ID, body, current_user=CURRENT_USER)


def _recorded_anchor(chat_env) -> dict | None:
    payload = chat_env.trace_mock.call_args.kwargs["extra_payload"] or {}
    return payload.get("structure_anchor")


class TestTraceLanding:
    def test_selection_without_declared_segment_lands_on_the_matching_segment(self, chat_env):
        _chat(message="ここが分かりません", selection_text="観測量は時間遅延である")

        anchor = _recorded_anchor(chat_env)
        assert anchor["anchor_type"] == "segment"
        # 教材区画は1つ（配信される chunks と同じ単位）なので seg_0 が正しい答え。
        assert anchor["anchor_id"] == "seg_0"
        assert anchor["attribution_source"] == "learner_selected"

    def test_selection_outside_the_material_leaves_the_place_unknown(self, chat_env):
        _chat(message="ここが分かりません", selection_text="教材の外から貼り付けた文です")

        assert _recorded_anchor(chat_env)["anchor_id"] == ""

    def test_screen_selection_is_recorded_as_learner_selected(self, chat_env):
        _chat(message="これは何ですか", screen_context=_screen_context())

        anchor = _recorded_anchor(chat_env)
        assert anchor["attribution_source"] == "learner_selected"
        assert anchor["reason"] == "screen_selection"
        assert anchor["anchor_id"] == COMPONENT_DB_ID
        # 明示アンカーのある問いは方法B（非同期LLM帰属）の対象にしない。
        chat_env.anchor_mining.assert_not_called()

    def test_screen_selection_from_another_course_is_ignored(self, chat_env):
        _chat(message="これは何ですか", screen_context=_screen_context(course_id="other-course"))

        assert _recorded_anchor(chat_env) is None
        chat_env.anchor_mining.assert_called_once()

    def test_plain_question_still_goes_to_method_b(self, chat_env):
        _chat(message="レンズ方程式とは何ですか")

        assert _recorded_anchor(chat_env) is None
        chat_env.anchor_mining.assert_called_once()
