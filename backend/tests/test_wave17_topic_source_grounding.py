"""第 17 周: 表示中の教材を説明した往復の出所と出典チップ（A1）・式の事実（A2 / IK-0577）・
学習相談の主要概念のトピック化（C-seq10）。

- A1: 検索が何も採用しない往復でも、表示中トピックの出典チャンク（``material_chunk_ids``）を
  番号付き出典として登録し、出典チップが出る（本文が引用すれば course_material）。
  メタ語（一言・根拠・短く…）は内容語に数えない（出所がターンごとに反転しない）。
- A2: 出典ポップアップが描く式の事実（印字番号・AI復元の印）を文脈に1行で添え、復元式が
  あるときだけ system に「原文の式として断定しない」1文を足す。
- C-seq10: LEARNING_ADVICE の主要概念・トピック一覧・ボタンは表示中トピック（現在の章）に
  寄せ、トピックに概念が無いときだけコース全体へ落とす。
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
from core.formula_facts import (  # noqa: E402
    RECONSTRUCTED_FORMULA_INSTRUCTION,
    formula_fact_line,
)
from core.label_vocab import RECONSTRUCTED_EQUATION_MARK, RECONSTRUCTED_EQUATION_NOTE  # noqa: E402
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402

CURRENT_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}

TOPIC_MATERIAL = "この節では、フィラメントの形成が三つの段階に分かれることを述べる。 ![[equation:eq_1]]"


def _course_data(*, chunk_ids=("chunk-t1", "chunk-t2")):
    return {
        "id": "course-1",
        "title": "テストコース",
        "domain": "テスト分野",
        "chapters": [],
        "concepts": [{"name": "別論文の概念A"}, {"name": "別論文の概念B"}],
        "sources": [{"material_id": "mat-course"}],
        "topics": [
            {
                "id": "topic-1",
                "title": "形成の三段階",
                "chapter_index": 0,
                "prerequisites": [],
                "material_chunk_ids": list(chunk_ids),
                "key_concepts": ["フィラメント形成", "x"],
                "student_material": {"source_text": TOPIC_MATERIAL},
                "content_blocks": [
                    {"type": "equations", "items": [
                        {"equation_id": "eq_1", "label": "(2)", "latex": "a=b", "reconstructed": True},
                    ]},
                ],
            }
        ],
    }


def _topic_row(chunk_id: str, *, facts: str = ""):
    return {
        "id": chunk_id,
        "text": f"{chunk_id} の本文。",
        "source_title": "資料",
        "source_file": "paper.pdf",
        "tier": "source",
        "material_id": "mat-course",
        "document_id": "d1",
        "formula_facts": facts,
    }


def _make_env(monkeypatch, *, answer: str, course=None, rows=None):
    settings = SimpleNamespace(
        learning_chat_max_calls_per_day=300,
        learning_chat_llm_model="",
        llm_analysis_model="analysis-model-x",
        llm_fast_model="fast-model-x",
    )
    course = course or _course_data()
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_policy_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())
    monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: course)
    monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
    monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda uid: ["d1"])
    monkeypatch.setattr(learning_mod, "log_unanswered_query", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_prerequisites", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "_atlas_topic_attribution", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_and_count_confirm_prompt", lambda *a, **k: False)
    monkeypatch.setattr(learning_mod, "maybe_schedule_tension_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "detect_and_record_misconception", MagicMock(return_value=None))
    monkeypatch.setattr(learning_mod, "record_interest_trace", MagicMock(return_value="trace-1"))
    calls: dict = {}

    def _get_chunks(ids, *, allowed_document_ids):
        calls["ids"] = list(ids)
        calls["allowed"] = allowed_document_ids
        base = rows if rows is not None else [_topic_row(c) for c in ids]
        return [r for r in base if r["id"] in ids]

    monkeypatch.setattr(learning_mod, "get_chunks_for_prompt", _get_chunks)
    captured: dict = {}

    def _generate_text(**kwargs):
        captured["messages"] = kwargs.get("messages")
        return answer

    monkeypatch.setattr(learning_mod, "generate_text", _generate_text)
    monkeypatch.setattr(
        learning_mod, "persist_chat_history", MagicMock(return_value={"user_message_id": "msg-1"})
    )
    return SimpleNamespace(captured=captured, calls=calls)


def _ask(message: str):
    body = LearningChatRequest(message=message, history=[], support_action="ask_question")
    return learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)


def _prompt_text(env) -> str:
    return "\n".join(str(m.get("content") or "") for m in env.captured["messages"])


class TestTopicSourcesBecomeChips:
    def test_no_retrieval_still_yields_course_material_source(self, monkeypatch):
        env = _make_env(monkeypatch, answer="三つの段階に分かれます [出典1]。")
        res = _ask("フィラメントの形成はどう分かれる？")
        assert res.content_grounding == "course_material"
        assert len(res.sources) >= 1
        assert res.sources[0].chunk_id == "chunk-t1"
        # 可視性は当該ターンの allowed_document_ids で強制（SQL 側）。
        assert env.calls["allowed"] == ["d1"]
        assert "[出典1] 『資料』（表示中の教材の出典）" in _prompt_text(env)
        # 未踏ガードは付かない（採用した根拠がある）。
        assert "未踏ガード" not in env.captured["messages"][0]["content"]

    def test_meta_word_utterance_keeps_course_material(self, monkeypatch):
        _make_env(monkeypatch, answer="三つに分かれます。")
        res = _ask("3つに分かれるという根拠はどこ？一言で。")
        assert res.content_grounding == "course_material"
        # 本文が引用していなくても、出所を決めた教材の出典を並べる（帯と一覧を揃える）。
        assert len(res.sources) >= 1
        assert res.sources[0].cited is False

    def test_meta_words_are_not_content_terms(self):
        for msg in ("むずい。一言で言うと？", "根拠はどこ？", "簡単に要約して", "つまり結局？"):
            assert learning_mod._grounding_content_terms(msg) == []

    def test_unrelated_uncited_answer_is_model_generated(self, monkeypatch):
        _make_env(monkeypatch, answer="一般的にはこう言えます。")
        res = _ask("ブラックホールの蒸発はどう起きる？")
        assert res.content_grounding == "model_generated"

    def test_cap_and_no_chunk_ids(self, monkeypatch):
        course = _course_data(chunk_ids=("c1", "c2", "c3", "c4", "c5"))
        env = _make_env(monkeypatch, answer="答え [出典1]", course=course)
        _ask("フィラメントの形成は？")
        assert _prompt_text(env).count("（表示中の教材の出典）") == learning_mod._TOPIC_SOURCE_CHUNK_MAX

    def test_topic_without_chunk_ids_reads_nothing(self, monkeypatch):
        env = _make_env(monkeypatch, answer="答え", course=_course_data(chunk_ids=()))
        _ask("フィラメントの形成は？")
        assert "ids" not in env.calls


class TestReconstructedFormulaFacts:
    def test_facts_and_instruction_present_when_reconstructed(self, monkeypatch):
        facts = formula_fact_line([{"id": "f0", "label": "(5)", "latex": "x", "reconstructed": True}])
        rows = [_topic_row("chunk-t1", facts=facts), _topic_row("chunk-t2")]
        env = _make_env(monkeypatch, answer="答え [出典1]", rows=rows)
        _ask("フィラメントの形成は？")
        text = _prompt_text(env)
        assert f"式 (5)［{RECONSTRUCTED_EQUATION_MARK}］" in text
        # 表示中の教材が参照する式の事実（topic の content_blocks）。
        assert f"式 (2)［{RECONSTRUCTED_EQUATION_MARK}］" in text
        assert RECONSTRUCTED_EQUATION_NOTE in text
        assert RECONSTRUCTED_FORMULA_INSTRUCTION in env.captured["messages"][0]["content"]

    def test_absent_when_nothing_reconstructed(self, monkeypatch):
        course = _course_data()
        course["topics"][0]["content_blocks"][0]["items"][0]["reconstructed"] = False
        env = _make_env(monkeypatch, answer="答え [出典1]", course=course)
        _ask("フィラメントの形成は？")
        text = _prompt_text(env)
        assert RECONSTRUCTED_EQUATION_NOTE not in text
        assert RECONSTRUCTED_FORMULA_INSTRUCTION not in env.captured["messages"][0]["content"]
        # 印字番号の事実は残る（数字は式番号だけ）。
        assert "式 (2)" in text

    def test_fact_line_drops_internal_labels(self):
        line = formula_fact_line([
            {"id": "a", "label": "eq_tex_b14", "latex": "x", "reconstructed": True},
            {"id": "b", "label": "(3)", "latex": "y"},
        ])
        assert "eq_tex" not in line
        # 第 25 周 第 5 波（IK-1009）: 番号の無い復元式は式本文の先頭で名指す。
        assert "$x$ の式［" in line and "式 (3)" in line
        assert "印の無い式は原文の数式です" in line
        assert formula_fact_line([{"id": "c", "latex": "z"}]) == ""


class TestLearningAdviceScopedToTopic:
    def test_concepts_from_topic(self):
        course = _course_data()
        topic = course["topics"][0]
        assert learning_mod._advice_concepts(topic, course) == ["フィラメント形成"]

    def test_falls_back_to_course_concepts(self):
        course = _course_data()
        topic = dict(course["topics"][0], key_concepts=[])
        assert learning_mod._advice_concepts(topic, course) == ["別論文の概念A", "別論文の概念B"]

    def test_topics_are_current_chapter(self):
        course = {"topics": [
            {"id": f"a{i}", "title": f"A{i}", "chapter_index": 0} for i in range(12)
        ] + [{"id": "b0", "title": "B0", "chapter_index": 1}, {"id": "b1", "title": "B1", "chapter_index": 1}]}
        picked = learning_mod._advice_topics({"id": "b1", "chapter_index": 1}, course)
        assert [t["id"] for t in picked] == ["b0", "b1"]

    def test_prompt_lists_topic_concepts_not_course(self, monkeypatch):
        captured = {}

        def _gen(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return "案内"

        monkeypatch.setattr(learning_mod, "generate_text", _gen)
        course = _course_data()
        learning_mod._generate_learning_advice_response(
            "テストコース", "形成の三段階", "どこから読めばいい？",
            topic_info=course["topics"][0], course_data=course,
        )
        assert "フィラメント形成" in captured["prompt"]
        assert "別論文の概念A" not in captured["prompt"]
