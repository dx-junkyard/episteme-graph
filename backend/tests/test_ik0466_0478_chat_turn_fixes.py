"""学習チャットの往復の是正（IK-0466〜IK-0478・ペルソナ通し受講 第 11 周）。

- IK-0466 書き直しで取り除いた往復が出した出典番号を、別のチャンクへ振り直さない
- IK-0468 英語の鏡（"…" 引用）の抽出と、在処の事実文の英語版
- IK-0469 「はい、〜は読みました」を前提の確認として記帳し、topic_id の鍵でトピックを跨ぐ
- IK-0470 構造帰属の入力: 回答が本文で引用したチャンクだけ・雑談／お礼は帰属しない
- IK-0471 / IK-0473 未踏ガードはお礼・雑談に付けない・discuss では予想を先に求めない
- IK-0472 トピックの論文のチャンクを先に並べる
- IK-0474 引っかかりのヒント: お礼の発話には立てない・例の turn id を実会話と衝突させない
- IK-0476 一次判定: コースの題名・概念名と英語の学問一般語
- IK-0477 / IK-0478 ドリルダウンの入れ子の引用番号と生の LaTeX 制御綴り

DB・実 LLM には触れない（境界 monkeypatch）。
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

import routes.learning as learning_mod  # noqa: E402
from core.discuss.mirroring import MIRROR_MOVED_NOTE, MIRROR_MOVED_NOTE_EN, extract_mirror  # noqa: E402
from core.learning_stance.heuristic import prejudge  # noqa: E402
from core.learning_support_agent import extract_inline_actions  # noqa: E402
from core.tension import prompt as tension_prompt  # noqa: E402
from core.tension.prefilter import judge_tension_hint  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402

from tests.test_ik0432_0437_chat_turn_fixes import (  # noqa: E402,F401
    CURRENT_USER,
    _ask,
    _chunk,
    chat_env,
)

services = sys.modules[learning_mod.load_stored_chat_history.__module__]


# ---------------------------------------------------------------------------
# IK-0466
# ---------------------------------------------------------------------------


def _assistant(content, sources=None, citation_map=None):
    turn = {"role": "assistant", "content": content}
    if sources:
        turn["sources"] = sources
    if citation_map:
        turn["citation_map"] = citation_map
    return turn


class TestCitationNumbersMonotone:
    def test_removed_turn_numbers_are_reserved(self):
        kept = [
            {"role": "user", "content": "q1", "id": "u1"},
            _assistant("a1", citation_map={"c1": 1, "c17": 17}),
        ]
        removed = [
            {"role": "user", "content": "phantom", "id": "u2"},
            _assistant("a2", sources=[{"index": 18, "chunk_id": "f428"}],
                       citation_map={"c1": 1, "c17": 17, "f428": 18}),
        ]
        numbers = learning_mod._SessionCitationNumbers(kept, removed)
        assert numbers.assign("a89d") == 19
        assert numbers.assign("f428") == 18

    def test_number_owned_by_another_chunk_is_never_reassigned(self):
        history = [_assistant("a", sources=[{"index": 5, "chunk_id": "x"}, {"index": 5, "chunk_id": "y"}])]
        numbers = learning_mod._SessionCitationNumbers(history)
        assert numbers.assign("x") == 5
        assert numbers.assign("y") == 6

    def test_merged_citation_map_is_one_to_one(self):
        history = [
            _assistant("a", citation_map={"x": 1}),
            _assistant("b", sources=[{"index": 1, "chunk_id": "y"}, {"index": 2, "chunk_id": "z"}]),
        ]
        assert services.merged_citation_map(history) == {"x": 1, "z": 2}

    def test_truncated_history_carries_full_map(self):
        truncated = [{"role": "user", "content": "q"}, _assistant("a", citation_map={"x": 1})]
        carried = services._carry_citation_map_onto(truncated, {"x": 1, "f428": 18})
        assert carried[-1]["citation_map"] == {"x": 1, "f428": 18}
        assert "citation_map" in truncated[-1] and truncated[-1]["citation_map"] == {"x": 1}

    def test_rewrite_path_reserves_removed_numbers(self, chat_env):
        chat_env.monkeypatch.setattr(learning_mod, "truncate_chat_and_supersede", lambda *a, **k: {
            "truncated_history": [
                {"role": "user", "content": "q1", "id": "u1"},
                _assistant("a1", sources=[{"index": 1, "chunk_id": "chunk-1"}]),
            ],
            "removed_ids": ["u2"],
            "removed_count": 2,
            "removed_history": [
                {"role": "user", "content": "phantom", "id": "u2"},
                _assistant("a2", sources=[{"index": 2, "chunk_id": "chunk-9"}]),
            ],
        })
        chat_env.monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk(1), _chunk(2)],
        )
        response = _ask("rewritten", replace_message_id="u2")
        indices = {s.chunk_id: s.index for s in response.sources}
        assert indices["chunk-1"] == 1
        assert indices["chunk-2"] == 3  # 2 は取り除いた往復の chunk-9 のもの


# ---------------------------------------------------------------------------
# IK-0468
# ---------------------------------------------------------------------------


class TestEnglishMirror:
    def test_double_quoted_mirror_is_extracted(self):
        learner = "I read the main claim as: dark energy is consistent with a cosmological constant."
        answer = '〔鏡〕You read it as "dark energy is consistent with a cosmological constant" — is that right?〔/鏡〕 Body.'
        clean, mirror = extract_mirror(answer, learner)
        assert mirror is not None and "cosmological constant" in mirror["text"]
        assert clean == "Body."

    def test_curly_quotes_and_fabricated_quote_fails(self):
        learner = "mu > 1 comes from the phantom background"
        ok = "〔鏡〕You read it as “mu > 1 comes from the phantom background”.〔/鏡〕x"
        bad = "〔鏡〕You read it as “mu > 1 comes from shear”.〔/鏡〕x"
        assert extract_mirror(ok, learner)[1] is not None
        assert extract_mirror(bad, learner)[1] is None

    def test_english_note_constant_and_prompt(self):
        assert "restatement" in MIRROR_MOVED_NOTE_EN
        clean, _m = extract_mirror("Intro. 〔鏡〕You said \"phantom background\".〔/鏡〕 rest", "the phantom background")
        assert MIRROR_MOVED_NOTE in clean  # 呼び出し側が英語へ差し替える
        src = Path(learning_mod.__file__).read_text(encoding="utf-8")
        assert "clean_answer.replace(MIRROR_MOVED_NOTE, MIRROR_MOVED_NOTE_EN)" in src
        assert 'You read it as "' in src


# ---------------------------------------------------------------------------
# IK-0469
# ---------------------------------------------------------------------------


class TestPrerequisiteAcknowledgement:
    @pytest.mark.parametrize("msg", [
        "はい、主結果のトピックは読みました。暗黒エネルギーの「音速」というのは、何が何の中を伝わる速さですか？",
        "はい、学びました",
        "Yes, I read that topic.",
        "I have studied it already.",
    ])
    def test_affirmative_completion_is_recorded(self, msg):
        assert services._is_explicit_prerequisite_acknowledgement(msg)

    @pytest.mark.parametrize("msg", [
        "私は暗黒エネルギーの論文の一番の主張は「暗黒エネルギーは宇宙定数で矛盾しない」ことだと読みました。合っていますか？",
        "はい、まだ読んでいません",
        "Yes, but I have not read it.",
        "I read the paper differently.",
    ])
    def test_interpretation_or_negation_is_not_recorded(self, msg):
        assert not services._is_explicit_prerequisite_acknowledgement(msg)

    def test_topic_key_is_recorded_and_honoured(self, monkeypatch):
        course = {"topics": [
            {"id": "t1", "title": "主結果", "prerequisites": []},
            {"id": "t2", "title": "未検証点", "prerequisites": [{"name": "主結果の読み", "topic_id": "t1"}]},
            {"id": "t3", "title": "別の点", "prerequisites": [{"name": "主結果", "topic_id": "t1"}]},
        ]}
        recorded: list = []
        monkeypatch.setattr(services, "record_prerequisite_acknowledgement",
                            lambda u, c, names: recorded.extend(names))
        assert services.check_prerequisites("u", "c", course, "未検証点", "はい、読みました") is None
        assert "topic:t1" in recorded
        monkeypatch.setattr(services, "get_acknowledged_prerequisites",
                            lambda u, c: {services.normalize_prerequisite_name(n) for n in recorded})
        monkeypatch.setattr(services, "get_course_completion", lambda *a, **k: {})
        assert services.check_prerequisites("u", "c", course, "別の点", "音速とは何ですか") is None


# ---------------------------------------------------------------------------
# IK-0470 / IK-0471 / IK-0473
# ---------------------------------------------------------------------------


class TestAnchorInputAndGuard:
    def test_cited_chunk_ids_follow_answer_markers(self):
        sources = [{"index": 1, "chunk_id": "a"}, {"index": 2, "chunk_id": "b"}, {"index": 3, "chunk_id": "c"}]
        assert learning_mod._chunk_ids_cited_in_answer("x [出典3] y [出典1][出典3] [出典9]", sources) == ["c", "a"]
        assert learning_mod._chunk_ids_cited_in_answer("no markers", sources) == []

    @pytest.mark.parametrize("msg,expected", [
        ("ありがとうございました。これでゼミで説明できそうです。", True),
        ("Thank you, I will stop here.", True),
        ("ありがとう、でも µ はなぜ負ですか？", False),
        ("音速とは何ですか", False),
    ])
    def test_closing_led_statement(self, msg, expected):
        assert learning_mod._is_closing_led_statement(msg) is expected

    def test_thanks_turn_gets_no_guard_and_no_anchor(self, chat_env):
        chat_env.monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
        schedule = MagicMock()
        chat_env.monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", schedule)
        learning_mod.learning_chat(
            "course-1", "topic-1",
            LearningChatRequest(message="ありがとうございました。これでゼミで説明できそうです。", history=[]),
            current_user=CURRENT_USER,
        )
        system = chat_env.prompts[-1][0]["content"]
        assert "未踏ガード" not in system
        payload = learning_mod.record_interest_trace.call_args.kwargs["extra_payload"]
        assert payload["anchor_skip_reason"] == "not_a_content_question"
        assert payload["tension_hint"] is False
        schedule.assert_not_called()

    def test_content_question_keeps_guard(self, chat_env):
        chat_env.monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
        _ask("音速とは何ですか？")
        assert "未踏ガード" in chat_env.prompts[-1][0]["content"]

    def test_discuss_guard_does_not_ask_prediction(self, chat_env):
        chat_env.monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
        learning_mod.learning_chat(
            "course-1", "topic-1",
            LearningChatRequest(message="音速とは何ですか？", history=[], intent_mode="discuss"),
            current_user=CURRENT_USER,
        )
        system = chat_env.prompts[-1][0]["content"]
        assert "議論モード" in system
        assert "学習者自身の予想を一度引き出す" not in system

    def test_worker_pending_query_skips_marked_rows(self):
        src = (BACKEND / "core" / "structure_anchor" / "worker.py").read_text(encoding="utf-8")
        assert "payload->>'anchor_skip_reason' IS NULL" in src


# ---------------------------------------------------------------------------
# IK-0472
# ---------------------------------------------------------------------------


class TestTopicDocumentPrecedence:
    def test_topic_documents_first_but_adoptable_before_weak(self):
        results = [
            {"id": "o1", "document_id": "other", "score": 0.8},
            {"id": "t1", "document_id": "topic", "score": 0.5},
            {"id": "t_weak", "document_id": "topic", "score": 0.2},
            {"id": "o2", "document_id": "other", "score": 0.6},
            {"id": "t2", "document_id": "topic", "score": 0.7},
        ]
        ordered = [r["id"] for r in learning_mod._prefer_topic_documents(results, {"topic"})]
        assert ordered == ["t2", "t1", "o1", "o2", "t_weak"]

    def test_topic_source_document_ids_explicit(self):
        topic = {"document_id": "d1", "units": [{"document_id": "d2"}], "evidence_links": [{"document_id": "d3"}]}
        assert services.topic_source_document_ids(topic) == {"d1", "d2", "d3"}


# ---------------------------------------------------------------------------
# IK-0474
# ---------------------------------------------------------------------------


class TestTensionInputs:
    def test_thanks_never_gets_hint(self):
        assert judge_tension_hint("ありがとうございました。ゼミではこの前提のことを話してみます。", []) is False
        assert judge_tension_hint("そもそも前提がおかしくないですか？", []) is True

    def test_example_turn_ids_cannot_collide(self):
        assert "msg_00" not in tension_prompt._FEW_SHOT
        assert "example_a_1" in tension_prompt._FEW_SHOT


# ---------------------------------------------------------------------------
# IK-0476
# ---------------------------------------------------------------------------


class TestPrejudgeCourseTerms:
    def test_course_terms_from_titles(self):
        terms = learning_mod._course_content_terms({"topics": [{"title": "主結果：CMB・BAO・SN・宇宙シア"}]})
        assert "BAO" in terms and "SN" not in terms

    def test_english_question_with_course_term_prejudges(self):
        terms = learning_mod._CONTENT_QUESTION_TERMS + learning_mod._course_content_terms(
            {"topics": [{"title": "主結果：CMB・BAO"}]}
        )
        assert prejudge("How do redshift-space distortions enter the BAO measurement?", content_terms=terms) == "DOMAIN_RAG"
        assert prejudge("ありがとうございました。", content_terms=terms) is None


# ---------------------------------------------------------------------------
# IK-0477 / IK-0478
# ---------------------------------------------------------------------------


class TestDrilldownLabels:
    def test_nested_reference_number(self):
        clean, actions = extract_inline_actions("Body. [Ask more about what reference [98] found about μ]")
        assert clean == "Body."
        assert [a.label for a in actions] == ["Ask more about what reference 98 found about μ"]

    def test_bare_latex_to_plain(self):
        _c, actions = extract_inline_actions(r"x [Ask more about why \alpha_K>0 implies \mu\ge1]")
        assert actions[0].label == "Ask more about why α_K>0 implies μ≥1"
        _c, actions = extract_inline_actions(r"x [Ask more about $\Delta\chi^2_{\rm CMB}$]")
        assert actions[0].label == "Ask more about Δχ^2_CMB"

    def test_source_marker_dropped_from_japanese_label(self):
        _c, actions = extract_inline_actions("x [音速の定義[出典3]について詳しく聞く]")
        assert actions[0].label == "音速の定義について詳しく聞く"
