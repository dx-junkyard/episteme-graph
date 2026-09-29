"""IK-0422〜IK-0426: 前提確認の逆質問は (トピック, セッション) につき1回・言い換えと英語・開幕 DTO。

ペルソナ通し受講 第 9 周（砂場の再演）で残った欠陥:
- IK-0422: 1問目に答えたあと同じトピックの次の問いでまた逆質問が返った。逆質問の往復を
  書き直す（replace_message_id が逆質問の往復を切り詰める）と、同じ逆質問が3回続いた。
- IK-0423: 前提「GRとMGでの観測量への影響」を言い換えた問い
  「GR と修正重力（MG）で、観測量への音速の影響はどう違うのか」が説明ではなく逆質問に吸い込まれた。
- IK-0424: 英語の受講者に逆質問が日本語だけで返り、「Yes, I understand it」が記帳されず後で
  同じ逆質問が戻った。
- IK-0425: 英語の回答の `[Ask more about …]` がドリルダウンとして認識されず本文に残った。
- IK-0426: discuss 開幕 DTO のバックボーンで stage / stage_label が空、label が英語の stage 名。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.learning as learning_mod  # noqa: E402
from core import label_vocab  # noqa: E402

from tests.test_ik0383_prerequisite_followup import (  # noqa: E402,F401
    PREREQ,
    TOPIC_TITLE,
    _chat,
    _course_data,
    _gate_history,
    _gate_text,
    _topic_info,
    chat_env,
)

_SKIPPED = label_vocab.PREREQUISITE_GATE_SKIPPED_NOTICE.format(prerequisite=PREREQ)


def _gate_then_answer_history():
    return _gate_history() + [
        {"role": "user", "content": "出典はどこですか"},
        {"role": "assistant", "content": "出典は…です"},
    ]


# ---------------------------------------------------------------------------
# IK-0422: 逆質問は (トピック, セッション) につき1回
# ---------------------------------------------------------------------------


class TestGateOncePerSession:
    def test_gate_earlier_in_history_is_gated_before(self):
        kind, target = learning_mod._prerequisite_followup(
            "主結果の数式はどう読むのですか", _gate_then_answer_history(),
            _topic_info(), TOPIC_TITLE, _course_data(),
        )
        assert (kind, target) == ("gated_before", None)

    def test_gate_for_another_topic_does_not_count(self):
        history = [{"role": "assistant", "content": _gate_text().replace(TOPIC_TITLE, "別の題")}]
        assert not learning_mod._prerequisite_gate_asked_in_history(history, TOPIC_TITLE)

    def test_later_question_is_answered_with_fact_line_not_regated(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")
        resp = _chat("主結果の数式はどう読むのですか", _gate_then_answer_history())
        assert learning_mod.PREREQUISITE_GATE_MARKER not in resp.answer
        assert _SKIPPED + "\n\n説明本文" in resp.answer
        assert resp.answer.endswith("説明本文")
        # 記帳の責務は check_prerequisites に残る（呼ばれている）。
        assert chat_env.gate_calls == ["主結果の数式はどう読むのですか"]

    def test_skipped_notice_only_on_first_skipped_turn(self, chat_env, monkeypatch):
        """IK-0446: 履歴に既にこの1行があれば、次の往復では添えない。"""
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")
        history = _gate_then_answer_history() + [
            {"role": "user", "content": "主結果の数式はどう読むのですか"},
            {"role": "assistant", "content": _SKIPPED + "\n\n説明本文"},
        ]
        resp = _chat("音速はどこに効きますか", history)
        assert _SKIPPED not in resp.answer
        assert learning_mod.PREREQUISITE_GATE_MARKER not in resp.answer
        assert resp.answer.endswith("説明本文")

    def test_rewrite_of_gated_question_is_not_regated(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")
        # 書き直しで逆質問の往復が切り詰められ、履歴から逆質問が消えている。
        monkeypatch.setattr(
            learning_mod, "truncate_chat_and_supersede",
            lambda *a, **k: {"truncated_history": [], "removed_ids": ["m1", "m2"], "removed_count": 2},
        )
        resp = _chat("主結果をもう一度教えて", [], replace_message_id="m1")
        assert learning_mod.PREREQUISITE_GATE_MARKER not in resp.answer
        assert _SKIPPED + "\n\n説明本文" in resp.answer

    def test_first_gate_still_appears_without_history(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")
        resp = _chat("主結果を教えて")
        assert learning_mod.PREREQUISITE_GATE_MARKER in resp.answer
        assert label_vocab.PREREQUISITE_GATE_EN.split("{", 1)[0] not in resp.answer

    def test_skipped_notice_has_no_numbers(self):
        for text in (
            label_vocab.PREREQUISITE_GATE_SKIPPED_NOTICE,
            label_vocab.PREREQUISITE_GATE_SKIPPED_NOTICE_EN,
            label_vocab.PREREQUISITE_GATE_EN,
        ):
            assert not any(ch.isdigit() for ch in text)


# ---------------------------------------------------------------------------
# IK-0423: 前提名の言い換え（内容語の重なり）
# ---------------------------------------------------------------------------

_MG_PREREQ = "GRとMGでの観測量への影響"
_MG_TOPIC = "主結果：CMB・BAO・SN・宇宙シアによる制約"


def _mg_course():
    return {
        "topics": [
            {"id": "t17", "title": _MG_PREREQ},
            {
                "id": "t18",
                "title": _MG_TOPIC,
                "prerequisites": [{"name": _MG_PREREQ, "topic_id": "t17"}],
            },
        ]
    }


def _mg_followup(message, history=None):
    course = _mg_course()
    return learning_mod._prerequisite_followup(
        message, history, course["topics"][1], _MG_TOPIC, course
    )


class TestParaphraseOverlap:
    def test_paraphrase_with_request_is_explain(self):
        msg = "GR と修正重力（MG）で、観測量への音速の影響はどう違うのか説明してください。"
        assert _mg_followup(msg) == ("explain", _MG_PREREQ)

    def test_paraphrase_question_without_request_is_explain(self):
        assert _mg_followup("GR と修正重力（MG）で、観測量への音速の影響はどう違うのか") == (
            "explain", _MG_PREREQ,
        )

    def test_short_acronym_does_not_match_inside_words(self):
        # GR は gravity の途中に当たらない（語境界付き）。重なりは「観測量」だけ。
        assert _mg_followup("gravity の観測量はどう違うのですか") == (None, None)

    def test_single_overlap_is_left_alone(self):
        assert _mg_followup("BAO は具体的にどの量を制約しているんですか？") == (None, None)

    def test_english_paraphrase_question_is_explain(self):
        assert _mg_followup("Why does that happen only in the MG case and not in GR?") == (
            "explain", _MG_PREREQ,
        )


# ---------------------------------------------------------------------------
# IK-0424: 英語の受講者
# ---------------------------------------------------------------------------


class TestEnglishAcknowledgement:
    @pytest.mark.parametrize(
        "message",
        [
            "I think I understand the previous topic",
            "I understand",
            "Yes, I understand it. Please continue in English.",
            "I know that",
            "I'm familiar with it",
            "I am familiar with this",
            "はい、理解しています",
        ],
    )
    def test_positive_forms_are_acknowledgements(self, message):
        from api import services

        assert services._is_explicit_prerequisite_acknowledgement(message)

    @pytest.mark.parametrize(
        "message",
        [
            "I don't understand",
            "I do not understand it",
            "No, I understand only part of it",
            "I'm not familiar with it",
            "please explain it, I understand nothing",
            "理解していません",
        ],
    )
    def test_negative_forms_are_not_acknowledgements(self, message):
        from api import services

        assert not services._is_explicit_prerequisite_acknowledgement(message)

    def _after_gate(self, message):
        return learning_mod._prerequisite_followup(
            message, _gate_history(), _topic_info(), TOPIC_TITLE, _course_data()
        )

    @pytest.mark.parametrize("message", ["yes", "Yes.", "はい"])
    def test_bare_yes_after_gate_is_answer_to_gate(self, message):
        assert self._after_gate(message) == ("after_gate", None)

    @pytest.mark.parametrize(
        "message", ["no", "No, please explain it first", "I'm not familiar with it", "I don't know it"]
    )
    def test_english_negative_after_gate_is_explain(self, message):
        assert self._after_gate(message) == ("explain", PREREQ)

    def test_bare_yes_is_not_an_acknowledgement_without_gate(self):
        assert learning_mod._prerequisite_followup(
            "yes", [], _topic_info(), TOPIC_TITLE, _course_data()
        ) == (None, None)


class TestEnglishRoute:
    def test_english_question_gets_english_gate_sentence(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")
        resp = _chat("What exactly is oscillating in baryon acoustic oscillations?")
        assert learning_mod.PREREQUISITE_GATE_MARKER in resp.answer
        assert label_vocab.PREREQUISITE_GATE_EN.format(prerequisite=PREREQ) in resp.answer
        # 追記しても逆質問の判定（直前が逆質問か・最初の前提名）は保たれる。
        assert learning_mod._previous_turn_was_prerequisite_gate(
            [{"role": "assistant", "content": resp.answer}], TOPIC_TITLE
        ) == PREREQ

    def test_bare_yes_after_gate_is_recorded_and_resumes(self, chat_env):
        resp = _chat("yes", _gate_history())
        # check_prerequisites には記帳判定が読む定型の答えを渡す。
        assert chat_env.gate_calls == ["はい、理解しています"]
        # IK-0450: 英語の答え（かな・漢字を含まない）には英語の1行を添える。
        assert label_vocab.PREREQUISITE_ACK_RESUME_NOTICE_EN in resp.answer
        assert "主結果を教えて" in chat_env.prompts[-1]

    def test_english_ack_with_new_question_keeps_both_questions(self, chat_env):
        resp = _chat(
            "Yes, I understand it. Which part of the deviation does mu(a) measure?",
            _gate_history(),
        )
        assert label_vocab.PREREQUISITE_ACK_RESUME_NOTICE_EN in resp.answer
        assert "主結果を教えて" in chat_env.prompts[-1]
        assert "Which part of the deviation does mu(a) measure?" in chat_env.prompts[-1]

    def test_english_later_question_gets_english_fact_line(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")
        resp = _chat(
            "Where exactly does the sound speed enter the constraints?", _gate_then_answer_history()
        )
        assert label_vocab.PREREQUISITE_GATE_SKIPPED_NOTICE_EN.format(prerequisite=PREREQ) in resp.answer
        assert _SKIPPED not in resp.answer


# ---------------------------------------------------------------------------
# IK-0425: 英語のドリルダウン目印
# ---------------------------------------------------------------------------


class TestEnglishDrilldownMarkers:
    @pytest.mark.parametrize(
        "marker",
        ["Ask more about the sound speed", "Ask about BAO", "Tell me more about mu(a)", "ask MORE about GR"],
    )
    def test_english_markers_become_drilldown_actions(self, marker):
        from core.learning_support_agent import extract_inline_actions

        clean, actions = extract_inline_actions(f"Answer body.\n\n[{marker}]")
        assert clean == "Answer body."
        assert [(a.type, a.label, a.message) for a in actions] == [("drilldown", marker, marker)]

    def test_japanese_markers_still_work(self):
        from core.learning_support_agent import extract_inline_actions

        clean, actions = extract_inline_actions("本文。\n[音速について詳しく聞く]")
        assert clean == "本文。"
        assert [a.label for a in actions] == ["音速について詳しく聞く"]

    def test_unrelated_brackets_are_left(self):
        from core.learning_support_agent import extract_inline_actions

        clean, actions = extract_inline_actions("See [1] and [Asking price]")
        assert actions == []
        assert "[1]" in clean and "[Asking price]" in clean

    def test_tutor_prompt_states_english_marker_form(self):
        prompt = learning_mod._get_integrated_tutor_system_prompt("宇宙物理")
        assert "[Ask more about X]" in prompt
        assert "〇〇について詳しく聞く" in prompt


# ---------------------------------------------------------------------------
# IK-0426: discuss 開幕 DTO のバックボーン
# ---------------------------------------------------------------------------


class TestOpeningBackbone:
    def _nodes(self):
        return [
            {
                "component_id": "theory_op_0002",
                "label": "Equation system",
                "graph_layer": "main",
                "source_backing_status": "review_required",
                "review_status": "review_required",
            },
            {
                "component_id": "theory_op_0001",
                "label": "Theory basis",
                "graph_layer": "main",
                "source_backing_status": "source_backed",
            },
            {"component_id": "theory_op_0009", "label": "", "graph_layer": "main"},
        ]

    def test_stage_is_derived_from_the_english_stage_label(self):
        from core.discuss.opening import project_backbone

        projected, _ = project_backbone(self._nodes())
        by_id = {n["node_id"]: n for n in projected}
        assert by_id["theory_op_0001"]["stage"] == "theory_basis"
        assert by_id["theory_op_0001"]["stage_label"] == "理論の土台"
        assert by_id["theory_op_0001"]["label"] == "理論の土台"
        assert by_id["theory_op_0002"]["stage_label"] == "式の体系"
        # stage 順（理論の土台 → 式の体系）。
        assert [n["node_id"] for n in projected][:2] == ["theory_op_0001", "theory_op_0002"]

    def test_label_never_falls_back_to_the_internal_id(self):
        from core.discuss.opening import project_backbone, project_fragile_points

        projected, _ = project_backbone(self._nodes())
        for node in projected:
            assert not node["label"].startswith("theory_op_")
        points, _ = project_fragile_points([], {"doc-1": projected})
        assert points and all(not p["label"].startswith("theory_op_") for p in points)
        assert points[0]["label"] == "式の体系"
