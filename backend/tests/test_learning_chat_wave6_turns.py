"""学習チャットの往復まわりの是正（ペルソナ通し受講 第 8 周・2026-09-28）。

- IK-0378: 未踏ガードと注意書きは「採用した根拠が1つも無い（model_generated）」ときだけ。
  注意書きは履歴に保存しない・再注入もしない。
- IK-0394: 履歴ウィンドウは 1 件 4000 字・段落/文の境界で切る。出典の quote は数式を割らない。
- IK-0395: 内容語の無い追い発話は、直近の内容語のある学習者発話を足して検索する。
- IK-0396: 逆質問に「理解している」と答えたら、元の質問に答える（事実文 1 行付き）。
- IK-0397: 理解度の点数・割合の要求は非LLM の固定文で答える（quota 非消費）。
- IK-0398: コース画面の状態・進捗は本人の完了記録から導出する / 完了したトピックの前提は聞き直さない。
- IK-0409: 確認問題の並置プロンプトは回答の言語で書かせ、全要素に観点を付けさせる。

DB・実 LLM には触れない（境界 monkeypatch。test_chat_history_source_persistence.py と同型）。
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

from api import services  # noqa: E402
import routes.learning as learning_mod  # noqa: E402
import core.llm_policy as llm_policy_mod  # noqa: E402
from core import label_vocab  # noqa: E402
from core.learning_experience import out_of_source_notice  # noqa: E402
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from core.llm_worker.history import window_history  # noqa: E402
from core.text_excerpt import excerpt  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402

CURRENT_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}

_GATE = (
    "「テストトピック」を理解するには、まず以下の前提知識を押さえる必要があります：\n\n"
    "**観測データ**\n\nこの前提知識を理解していますか？\n"
    "理解できていない場合は、まず「観測データ」から説明します。"
)


def _course_data() -> dict:
    return {
        "id": "course-1",
        "title": "テストコース",
        "domain": "テスト分野",
        "chapters": [],
        "topics": [{
            "id": "topic-1", "title": "テストトピック", "chapter_index": 0,
            "prerequisites": ["観測データ"],
        }],
        "concepts": [],
        "sources": [{"material_id": "mat-1", "title": "このコースの論文"}],
    }


def _chunk(idx: int, *, score: float = 0.9, tier: str = "source") -> dict:
    return {
        "id": f"chunk-{idx}",
        "text": f"チャンク本文 {idx}",
        "score": score,
        "source_title": f"論文 {idx}",
        "source_file": f"paper{idx}.pdf",
        "material_id": "mat-1",
        "tier": tier,
    }


@pytest.fixture
def env(monkeypatch):
    settings = SimpleNamespace(
        learning_chat_max_calls_per_day=300,
        learning_chat_llm_model="",
        llm_analysis_model="analysis-model-x",
        llm_fast_model="fast-model-x",
    )
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_policy_mod, "get_settings", lambda: settings)
    gate = CostGate()
    monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", gate)
    monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
    monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda *a, **k: ["doc-1"])
    monkeypatch.setattr(learning_mod, "list_course_source_document_ids", lambda *a, **k: ["doc-1"])
    monkeypatch.setattr(learning_mod, "log_unanswered_query", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_prerequisites", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "_atlas_topic_attribution", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_and_count_confirm_prompt", lambda *a, **k: False)
    monkeypatch.setattr(learning_mod, "maybe_schedule_tension_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", lambda *a, **k: None)
    trace = MagicMock(return_value="trace-1")
    monkeypatch.setattr(learning_mod, "record_interest_trace", trace)
    monkeypatch.setattr(learning_mod, "detect_and_record_misconception", MagicMock(return_value=None))
    calls: list[list[dict]] = []

    def _generate_text(**kwargs):
        calls.append(kwargs.get("messages") or [])
        return "回答です [出典1]"

    monkeypatch.setattr(learning_mod, "generate_text", _generate_text)
    searches: list[str] = []

    def _search(query, *a, **k):
        searches.append(query)
        return [_chunk(1), _chunk(2, score=0.35, tier="out_of_source")]

    monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", _search)
    persist = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist)
    return SimpleNamespace(
        monkeypatch=monkeypatch, calls=calls, searches=searches, persist=persist,
        trace=trace, gate=gate,
    )


def _chat(message: str, **kwargs):
    return learning_mod.learning_chat(
        "course-1", "topic-1",
        LearningChatRequest(message=message, **kwargs),
        current_user=CURRENT_USER,
    )


# ---------------------------------------------------------------------------
# IK-0378: 未踏ガード・注意書き
# ---------------------------------------------------------------------------


class TestOutOfSourceGuardOnlyWithoutAdoptedSources:
    def test_sourced_answer_with_weak_source_has_no_guard_or_notice(self, env):
        """最弱集約で tier が out_of_source でも、出典を採用した回答にはガードも注意書きも付かない。"""
        resp = _chat("フィラメントの磁場の形態は？", support_action="ask_question")
        system = env.calls[-1][0]["content"]
        assert resp.overall_tier == "out_of_source"  # 格の表示（最弱集約）は変えない
        assert resp.content_grounding == "course_material"
        assert "未踏ガード" not in system
        assert not resp.answer.startswith(out_of_source_notice())

    def test_no_sources_gets_guard_with_precedence_and_notice_not_persisted(self, env):
        env.monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
        env.monkeypatch.setattr(learning_mod, "get_course_data", lambda *a: {
            **_course_data(), "topics": [{"id": "topic-1", "title": "テストトピック", "chapter_index": 0}],
        })
        resp = _chat("ジーンズ質量とは", support_action="ask_question")
        system = env.calls[-1][0]["content"]
        assert resp.content_grounding == "model_generated"
        assert "未踏ガード" in system
        assert "（優先順位）" in system
        assert resp.answer.startswith(out_of_source_notice())
        persisted_answer = env.persist.call_args.args[5]
        assert out_of_source_notice() not in persisted_answer

    def test_notice_is_stripped_from_reinjected_history(self, env):
        history = [
            {"role": "user", "content": "前の質問"},
            {"role": "assistant", "content": out_of_source_notice() + "\n\n前の回答本文"},
        ]
        _chat("フィラメントの磁場の形態は？", support_action="ask_question", history=history)
        joined = "\n".join(str(m.get("content")) for m in env.calls[-1])
        assert "出典が提示できない" not in joined
        assert "前の回答本文" in joined


# ---------------------------------------------------------------------------
# IK-0394: 履歴ウィンドウと quote
# ---------------------------------------------------------------------------


class TestHistoryWindowBoundary:
    def test_default_behaviour_unchanged(self):
        out = window_history([{"role": "assistant", "content": "あ" * 50}], max_chars=10)
        assert out[0]["content"] == "あ" * 10

    def test_boundary_trim_cuts_at_paragraph_with_ellipsis(self):
        text = "第一段落の文です。" * 30 + "\n\n" + "第二段落。" * 200
        out = window_history(
            [{"role": "assistant", "content": text}], max_chars=400, trim_at_boundary=True,
        )[0]["content"]
        assert len(out) <= 400
        assert out.endswith("…")
        assert out.rstrip("…").endswith("。")

    def test_boundary_trim_does_not_split_dollar_math(self):
        text = "い" * 30 + "$B = 153 \\pm 6\\,\\mu G$" + "う" * 40
        out = window_history(
            [{"role": "assistant", "content": text}], max_chars=40, trim_at_boundary=True,
        )[0]["content"]
        assert out.count("$") % 2 == 0

    def test_learning_chat_uses_4000_chars(self, env):
        long_answer = ("段落の文です。" * 100 + "\n\n") * 8  # 約 5,600 字
        history = [{"role": "user", "content": "質問"}, {"role": "assistant", "content": long_answer}]
        _chat("フィラメントの磁場の形態は？", support_action="ask_question", history=history)
        assistant_turns = [m for m in env.calls[-1] if m["role"] == "assistant"]
        reinjected = assistant_turns[-1]["content"]
        assert 2000 < len(reinjected) <= 4000
        assert reinjected.endswith("…")

    def test_quote_excerpt_keeps_dollar_math_whole(self):
        text = "磁場の強さは " + "$B = 153 \\pm 6\\,\\mu\\mathrm{G}$" + " と見積もられ、構造関数解析では別の値になる。" * 3
        quote = excerpt(text, 20, keep_dollar_math=True)
        assert quote.count("$") % 2 == 0


# ---------------------------------------------------------------------------
# IK-0395: 内容語の無い追い発話の検索
# ---------------------------------------------------------------------------


class TestContentlessFollowupRetrieval:
    def test_borrows_last_learner_message_with_content_words(self, env):
        history = [
            {"role": "user", "content": "暗黒エネルギーの音速は成長率に痕跡を残しますか"},
            {"role": "assistant", "content": "…"},
        ]
        learning_mod.learning_chat(
            "course-1", "_discussion",
            LearningChatRequest(
                message="はい、そう読みました。合っているんですか",
                intent_mode="discuss", history=history,
            ),
            current_user=CURRENT_USER,
        )
        assert len(env.searches) == 1
        assert env.searches[0].startswith("暗黒エネルギーの音速")
        assert env.searches[0].endswith("はい、そう読みました。合っているんですか")

    def test_message_with_content_words_is_searched_alone(self, env):
        history = [{"role": "user", "content": "暗黒エネルギーの音速"}]
        _chat("構造関数解析の比は？", support_action="ask_question", history=history)
        assert env.searches == ["構造関数解析の比は？"]

    def test_selection_does_not_borrow_history(self):
        body = LearningChatRequest(
            message="ここはどういう意味？", selection_text="選んだ箇所",
            history=[{"role": "user", "content": "暗黒エネルギーの音速"}],
        )
        assert learning_mod._retrieval_query_for_turn(body, body.message) == "ここはどういう意味？"


# ---------------------------------------------------------------------------
# IK-0396: 前提確認の往復で元の質問に答える
# ---------------------------------------------------------------------------


class TestPrerequisiteAckResumesOriginalQuestion:
    def test_ack_answers_the_original_question(self, env):
        history = [
            {"role": "user", "content": "「構造関数解析」ってどういう意味ですか。"},
            {"role": "assistant", "content": _GATE},
        ]
        resp = _chat("はい、理解しています", history=history)
        assert resp.answer.startswith(label_vocab.PREREQUISITE_ACK_RESUME_NOTICE)
        # 検索・生成は元の質問で（LLM は回答の1回だけ。意図分類は呼ばない）
        assert env.searches == ["「構造関数解析」ってどういう意味ですか。"]
        assert len(env.calls) == 1
        assert env.calls[-1][-1]["content"].endswith("「構造関数解析」ってどういう意味ですか。")
        # 保存する学習者発話は本人が打った文のまま
        assert env.persist.call_args.args[4] == "はい、理解しています"

    def test_question_before_gate_helper(self):
        history = [
            {"role": "user", "content": "元の質問"},
            {"role": "assistant", "content": _GATE},
        ]
        assert learning_mod._question_before_prerequisite_gate(history, "テストトピック") == "元の質問"
        assert learning_mod._question_before_prerequisite_gate(
            history + [{"role": "assistant", "content": "別の回答"}], "テストトピック",
        ) is None


# ---------------------------------------------------------------------------
# IK-0397: 点数・割合の要求
# ---------------------------------------------------------------------------


class TestScoreRequestPreRoute:
    @pytest.mark.parametrize("message", [
        "私の理解度を点数で教えて", "確認問題の点数を教えて", "理解度は何割くらい？",
        "What is my score?", "Can you grade my understanding?",
    ])
    def test_detects(self, message):
        assert learning_mod._is_understanding_score_request(message)

    @pytest.mark.parametrize("message", [
        "スコア関数の定義は？", "構造関数解析の比は？", "points of the field lines", "",
    ])
    def test_does_not_detect(self, message):
        assert not learning_mod._is_understanding_score_request(message)

    def test_fixed_reply_without_llm_or_quota(self, env):
        resp = _chat("私の理解度を点数で教えて")
        assert resp.answer == label_vocab.UNDERSTANDING_SCORE_REQUEST_REPLY
        assert resp.content_grounding == "model_generated"
        assert env.calls == []
        assert env.searches == []
        env.trace.assert_not_called()


# ---------------------------------------------------------------------------
# IK-0398: コース画面の進捗・前提ゲート
# ---------------------------------------------------------------------------


class TestCourseProgressOverlay:
    DATA = {
        "chapters": [
            {"title": "章1", "status": "in_progress", "progress_pct": 0},
            {"title": "章2", "status": "locked", "progress_pct": 0},
        ],
        "topics": [
            {"id": "t0", "title": "A", "chapter_index": 0, "status": "in_progress"},
            {"id": "t1", "title": "B", "chapter_index": 0, "status": "locked"},
            {"id": "t2", "title": "C", "chapter_index": 1, "status": "locked"},
        ],
    }

    def test_completion_reflected(self):
        out = learning_mod._overlay_learner_progress(self.DATA, ["t0"])
        statuses = [t["status"] for t in out["topics"]]
        assert statuses == ["completed", "in_progress", "locked"]
        assert out["chapters"][0]["status"] == "in_progress"
        assert out["chapters"][0]["progress_pct"] == 50
        assert out["chapters"][1]["status"] == "locked"
        # 保存データは書き換えない
        assert self.DATA["topics"][0]["status"] == "in_progress"

    def test_chapter_complete_opens_next_chapter_topic(self):
        out = learning_mod._overlay_learner_progress(self.DATA, ["t0", "t1"])
        assert [t["status"] for t in out["topics"]] == ["completed", "completed", "in_progress"]
        assert out["chapters"][0]["status"] == "completed"
        assert out["chapters"][0]["progress_pct"] == 100
        assert out["chapters"][1]["status"] == "in_progress"

    def test_no_completion_keeps_master_values(self):
        assert learning_mod._overlay_learner_progress(self.DATA, []) is self.DATA


class TestPrerequisiteGateSkipsCompletedTopic:
    def _course(self):
        return {
            "topics": [
                {"id": "t0", "title": "問題設定と観測データ"},
                {"id": "t1", "title": "フィラメントの形状", "prerequisites": [
                    {"name": "問題設定と観測データ", "topic_id": "t0"},
                ]},
            ],
        }

    def test_completed_linked_topic_is_not_reasked(self, monkeypatch):
        monkeypatch.setattr(services, "get_acknowledged_prerequisites", lambda *a: set())
        monkeypatch.setattr(
            services, "get_course_completion",
            lambda *a: {"completed_topic_ids": ["t0"], "course_completed": False},
        )
        assert services.check_prerequisites("u", "c", self._course(), "フィラメントの形状", "形状の意味は？") is None

    def test_uncompleted_topic_is_still_asked(self, monkeypatch):
        monkeypatch.setattr(services, "get_acknowledged_prerequisites", lambda *a: set())
        monkeypatch.setattr(
            services, "get_course_completion",
            lambda *a: {"completed_topic_ids": [], "course_completed": False},
        )
        out = services.check_prerequisites("u", "c", self._course(), "フィラメントの形状", "形状の意味は？")
        assert out and "問題設定と観測データ" in out["message"]


# ---------------------------------------------------------------------------
# IK-0409: 確認問題の並置プロンプト
# ---------------------------------------------------------------------------


class TestCheckJuxtapositionPrompt:
    def test_prompt_asks_learner_language_and_every_requirement(self):
        src = Path(learning_mod.__file__).read_text(encoding="utf-8")
        body = src.split("def check_topic_understanding(")[1].split("\ndef ")[0]
        assert "受講者の回答と同じ言語" in body
        assert "すべての要素について1件ずつ" in body
        assert "limit=max(check_review.MAX_OBSERVATIONS, len(answer_requirements))" in body


# ---------------------------------------------------------------------------
# 追補（wave6a の続き）: IK-0396 ボタン経路 / IK-0397 誤爆 / IK-0399 / IK-0409 /
# IK-0389 配信側 / IK-0411
# ---------------------------------------------------------------------------


class TestPrerequisiteAckViaButton:
    def test_continue_detail_button_also_resumes(self, env):
        """逆質問の「はい、理解しています」ボタン（typed action continue_detail）でも元の質問に答える。"""
        history = [
            {"role": "user", "content": "「構造関数解析」ってどういう意味ですか。"},
            {"role": "assistant", "content": _GATE},
        ]
        resp = _chat("はい、理解しています", history=history, support_action="continue_detail")
        assert resp.answer.startswith(label_vocab.PREREQUISITE_ACK_RESUME_NOTICE)
        assert env.searches == ["「構造関数解析」ってどういう意味ですか。"]

    def test_negative_answer_does_not_resume(self, env):
        history = [
            {"role": "user", "content": "「構造関数解析」ってどういう意味ですか。"},
            {"role": "assistant", "content": _GATE},
        ]
        resp = _chat("いいえ、理解していません", history=history)
        assert not resp.answer.startswith(label_vocab.PREREQUISITE_ACK_RESUME_NOTICE)


class TestScoreRequestNoFalsePositives:
    @pytest.mark.parametrize("message", [
        "スコア関数の理解が難しい", "点数分布の図は？", "I don't get the main points",
        "I understand the grade of the fit",
    ])
    def test_content_questions_are_not_routed(self, message):
        assert not learning_mod._is_understanding_score_request(message)

    @pytest.mark.parametrize("message", ["私の点数は？", "my score please", "grade me"])
    def test_requests_are_routed(self, message):
        assert learning_mod._is_understanding_score_request(message)


class TestSelfCheckDisagreedDoesNotComplete:
    def _setup(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "get_course_data", lambda *a: _course_data())
        passed = MagicMock(return_value={
            "topic_completed": True, "course_completed": False, "completed_topic_ids": ["topic-1"],
        })
        monkeypatch.setattr(learning_mod, "record_topic_check_pass", passed)
        monkeypatch.setattr(
            learning_mod, "get_course_completion",
            lambda *a: {"course_completed": False, "completed_topic_ids": []},
        )
        return passed

    def _self_check(self, value):
        from schemas import LearningCheckSelfCheckRequest

        return learning_mod.self_check_topic_understanding(
            "course-1", "topic-1", LearningCheckSelfCheckRequest(self_check=value),
            current_user=CURRENT_USER,
        )

    def test_disagreed_records_without_completing(self, monkeypatch):
        passed = self._setup(monkeypatch)
        resp = self._self_check("disagreed")
        passed.assert_not_called()
        assert resp.topic_completed is False
        assert resp.notice == label_vocab.CHECK_SELF_CHECK_DISAGREED_NOTICE
        assert not any(ch.isdigit() for ch in resp.notice)

    def test_agreed_completes(self, monkeypatch):
        passed = self._setup(monkeypatch)
        resp = self._self_check("agreed")
        passed.assert_called_once()
        assert resp.topic_completed is True
        assert resp.notice == ""


class TestCheckObservationsCoverEveryRequirement:
    REQS = ["要素A", "要素B", "要素C", "要素D", "要素E"]

    def test_fill_missing_requirements_backfills_in_order(self):
        from core import check_review

        observed = [
            {"requirement": "要素C", "status": "covered", "statement": "C に触れているようです。"},
            {"requirement": "要素A", "status": "not_mentioned", "statement": "A は見当たらないようです。"},
        ]
        out = check_review.fill_missing_requirements(observed, self.REQS)
        # 返った観点の並びは保ち、補った要素を出題の順で後ろに足す
        assert [o["requirement"] for o in out] == ["要素C", "要素A", "要素B", "要素D", "要素E"]
        filled = [o for o in out if o["requirement"] in ("要素B", "要素D", "要素E")]
        assert all(o["status"] == "unclear" for o in filled)
        assert all(o["statement"] == check_review.MISSING_OBSERVATION_STATEMENT for o in filled)

    def test_english_verdict_words_are_dropped(self):
        from core import check_review

        assert check_review.sanitize_statement("Your answer is correct.") == ""
        assert check_review.sanitize_statement("This would score 3 points.") == ""
        kept = "You seem to mention how the field lines bend."
        assert check_review.sanitize_statement(kept) == kept

    def test_language_line_for_english_answer_only(self):
        assert "英語" in learning_mod._answer_language_line(
            "The magnetic field is inferred from the polarization angle dispersion."
        )
        assert learning_mod._answer_language_line("磁場は偏光角の分散から推定されます。") == ""

    def test_route_returns_an_observation_per_requirement(self, monkeypatch):
        from schemas import LearningCheckQuestionRequest

        data = _course_data()
        data["topics"][0]["check_questions"] = [{
            "question": "要点は？", "answer_requirements": self.REQS,
        }]
        monkeypatch.setattr(learning_mod, "get_course_data", lambda *a: data)
        monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda *a: {})
        monkeypatch.setattr(
            learning_mod, "get_course_completion",
            lambda *a: {"course_completed": False, "completed_topic_ids": []},
        )
        prompts: list[str] = []

        def _json_call(prompt, **kwargs):
            prompts.append(prompt)
            return {"observations": [
                {"requirement": r, "status": "covered", "statement": f"You seem to mention {r}."}
                for r in self.REQS[:3]
            ]}

        monkeypatch.setattr(learning_mod, "json_call", _json_call)
        resp = learning_mod.check_topic_understanding(
            "course-1", "topic-1",
            LearningCheckQuestionRequest(
                answer="The magnetic field is inferred from the polarization angle dispersion.",
            ),
            current_user=CURRENT_USER,
        )
        assert [o.requirement for o in resp.observations] == self.REQS
        assert [o.status for o in resp.observations][3:] == ["unclear", "unclear"]
        assert "英語で書いてください" in prompts[-1]


class TestTopicMaterialUnresolvedFormulaPlaceholders:
    def test_saved_course_body_placeholders_are_replaced_on_delivery(self, monkeypatch):
        data = _course_data()
        data["topics"][0]["content"] = (
            "磁場は [[FORMULA_0]] で表され、分散は [[FORMULA_7]] で見積もられる。"
        )
        data["topics"][0]["content_blocks"] = [{
            "type": "equations",
            "items": [{"equation_id": "FORMULA_0", "latex": "B = 1"}],
        }]
        monkeypatch.setattr(learning_mod, "get_course_data", lambda *a: data)
        monkeypatch.setattr(learning_mod, "_load_course_figures_by_id", lambda *a, **k: {})
        monkeypatch.setattr(learning_mod, "_attach_figure_explanations", lambda *a, **k: None)
        resp = learning_mod.get_topic_material("course-1", "topic-1", current_user=CURRENT_USER)
        text = "".join(c.text for c in resp.chunks)
        assert "[[FORMULA_0]]" in text  # 引けるものには触れない
        assert "[[FORMULA_7]]" not in text
        assert label_vocab.UNRESOLVED_FORMULA_PLACEHOLDER_TEXT in text


def _walk_keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_keys(value)


_LEAKY_KEYS = {"confidence", "reason", "anchor_id", "detector_version", "evidence_quote",
               "assumption_id", "structure_anchor"}


class TestAnchorDecisionResponsesAreProjected:
    ANCHOR = {
        "anchor_type": "claim", "anchor_id": "3f9c0000-0000-0000-0000-000000000001",
        "anchor_label": "磁場の強さの見積もり", "doubt_type": "justification_gap",
        "attribution_source": "confirmed", "status": "active", "confidence": 0.88,
        "reason": "The learner asked about claim c_12 in block b_4.",
        "evidence_quote": "どうしてこの値になるの", "detector_version": "anchor-v1",
    }

    def test_confirm_returns_labels_only(self, monkeypatch):
        monkeypatch.setattr(
            learning_mod, "confirm_anchor_trace",
            lambda *a, **k: {"trace_id": "t-1", "structure_anchor": dict(self.ANCHOR)},
        )
        resp = learning_mod.confirm_anchor_route(
            "t-1", learning_mod.AnchorConfirmRequest(), current_user=CURRENT_USER,
        )
        assert resp["ok"] is True and resp["status"] == "confirmed"
        assert resp["anchor_label"] == "磁場の強さの見積もり"
        assert resp["doubt_type_label"]
        assert resp["notice"] == label_vocab.ANCHOR_CONFIRMED_NOTICE
        assert not (_LEAKY_KEYS & set(_walk_keys(resp)))

    def test_related_assumption_keeps_statement_only(self, monkeypatch):
        dto = learning_mod._learner_anchor_decision_dto(
            {"trace_id": "t-1", "structure_anchor": dict(self.ANCHOR),
             "related_assumption": {"assumption_id": "a-1", "statement": "磁場は一様"}},
            status="confirmed", notice=label_vocab.ANCHOR_CONFIRMED_NOTICE,
        )
        assert dto["related_assumption"] == {"statement": "磁場は一様"}

    def test_dismiss_returns_fact_line(self, monkeypatch):
        monkeypatch.setattr(
            learning_mod, "dismiss_anchor_trace",
            lambda *a, **k: {"trace_id": "t-1", "anchor_status": "dismissed"},
        )
        resp = learning_mod.dismiss_anchor_route("t-1", current_user=CURRENT_USER)
        assert resp == {
            "ok": True, "trace_id": "t-1", "status": "dismissed",
            "notice": label_vocab.ANCHOR_DISMISSED_NOTICE,
        }

    def test_tension_decisions_use_allowlist(self, monkeypatch):
        monkeypatch.setattr(
            learning_mod, "confirm_tension_trace",
            lambda *a, **k: {"trace_id": "t-2", "status": "open", "confidence": 0.6, "reason": "x"},
        )
        resp = learning_mod.confirm_tension_route(
            "t-2", learning_mod.TensionConfirmRequest(), current_user=CURRENT_USER,
        )
        assert resp == {"ok": True, "trace_id": "t-2", "status": "open"}


class TestDiscussMirrorPlacementRule:
    """IK-0400 追補: 〔鏡〕は返答の先頭に置き、前置き文を書かず、鏡の後に本文を続ける。"""

    def test_prompt_states_mirror_goes_first_and_body_follows(self):
        prompt = learning_mod._get_discuss_system_prompt("テスト分野", None)
        assert "〔鏡〕…〔/鏡〕 は返答の先頭に置いてください" in prompt
        assert "前置き文や予告文" in prompt
        assert "鏡の後に本文を続けてください" in prompt
