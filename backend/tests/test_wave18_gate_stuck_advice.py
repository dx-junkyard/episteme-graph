"""第 18 周: 前提ゲート後の詰まり・肯定＋中身・学習相談の平文・辺の confidence（G1〜G5）。

正本: docs/features/dialogue_response_shape_design.md §8.1 / docs/backend/rag-chat.md ①-b。
"""

from __future__ import annotations

from tests.test_ik0383_prerequisite_followup import (  # noqa: F401  (sys.path を整える)
    PREREQ,
    TOPIC_TITLE,
    _chat,
    _course_data,
    _gate_history,
    _gate_text,
    _topic_info,
    chat_env,
)

import routes.learning as learning_mod  # noqa: E402


def _answer_with(monkeypatch, chat_env, text):
    def _gen(**kwargs):
        chat_env.prompts.append(" ".join(str(m.get("content")) for m in kwargs.get("messages") or []))
        return text

    monkeypatch.setattr(learning_mod, "generate_text", _gen)


def _followup(message, history=None):
    return learning_mod._prerequisite_followup(
        message, history, _topic_info(), TOPIC_TITLE, _course_data()
    )


# ---------------------------------------------------------------------------
# G1: ゲートが出ている間の詰まり
# ---------------------------------------------------------------------------


class TestStuckAfterGate:
    def test_bare_stuck_after_gate_is_not_explain(self):
        for m in ("むずい", "わからない", "まだわからない"):
            assert _followup(m, _gate_history()) == ("after_gate", None)

    def test_explicit_no_still_explains(self):
        assert _followup("いいえ", _gate_history()) == ("explain", PREREQ)
        assert _followup("わからないので説明して", _gate_history()) == ("explain", PREREQ)

    def test_gate_stays_pending_through_a_stuck_exchange(self):
        history = _gate_history() + [
            {"role": "user", "content": "むずい"},
            {"role": "assistant", "content": "足場です。"},
        ]
        assert learning_mod._previous_turn_was_prerequisite_gate(history, TOPIC_TITLE) == PREREQ
        assert _followup("いいえ", history) == ("explain", PREREQ)
        # 中身のある往復が挟まれば保留は解ける。
        history2 = history + [
            {"role": "user", "content": "音速はどこに効きますか"},
            {"role": "assistant", "content": "回答です。"},
        ]
        assert learning_mod._previous_turn_was_prerequisite_gate(history2, TOPIC_TITLE) is None

    def test_route_stuck_after_gate_gives_foothold_only(self, chat_env, monkeypatch):
        _answer_with(
            monkeypatch, chat_env,
            "足場1。足場2。足場3。足場4。\n\n【しくみ】長い講義。\n\nどこがわかりにくかったですか。",
        )
        resp = _chat("むずい", _gate_history())
        assert learning_mod.PREREQUISITE_GATE_MARKER not in resp.answer
        assert resp.answer.endswith("足場1。足場2。足場3。")
        assert "足場4" not in resp.answer and "【しくみ】" not in resp.answer
        assert resp.shape == {"closing_question": None, "has_gate": False, "mirror_kept": False}
        assert resp.support_mode != "prerequisite_review"
        # 前提の説明プロンプトに流していない。
        assert f"前提知識「{PREREQ}」そのものを" not in chat_env.prompts[-1]
        assert resp.content_grounding  # 出所を空欄にしない（原則8）

    def test_route_explicit_no_explanation_goes_through_shape(self, chat_env, monkeypatch):
        _answer_with(
            monkeypatch, chat_env,
            "【ひとことで】前提の説明。\n\n【しくみ】要点。わかりましたか？ 試してみますか？",
        )
        resp = _chat("いいえ", _gate_history())
        assert resp.support_mode == "prerequisite_review"
        assert f"前提知識「{PREREQ}」そのものを" in chat_env.prompts[-1]
        # 問い返しは最後の 1 つだけ（骨格 RS3）。
        assert "わかりましたか？" not in resp.answer
        assert "試してみますか？" in resp.answer
        assert resp.content_grounding == "model_generated"


# ---------------------------------------------------------------------------
# G4: 「はい、…」＋中身
# ---------------------------------------------------------------------------

_ACK_WITH_QUESTION = (
    "はい、そうですね、分かりました。宇宙分散が装置の誤差ではないことは理解しました。"
    "だとすると、µ0 ≈ 1.04 になったのも観測がそう決めたからですよね？"
)


class TestAckWithContent:
    def test_classification(self):
        kind, core = _followup(_ACK_WITH_QUESTION, _gate_history())
        assert kind == "ack_content"
        assert core.startswith("宇宙分散") and "はい" not in core[:3]

    def test_bare_yes_is_unchanged(self):
        assert _followup("はい", _gate_history()) == ("after_gate", None)

    def test_contrast_is_not_ack(self):
        kind, _ = _followup("はい、でもDCF法がわからないので説明して", _gate_history())
        assert kind == "explain"

    def test_route_answers_content_and_records_ack(self, chat_env, monkeypatch):
        def _unexpected(*a, **k):
            raise AssertionError("肯定＋中身で意図分類の LLM を呼ばない")

        monkeypatch.setattr(learning_mod, "_classify_intent", _unexpected)
        resp = _chat(_ACK_WITH_QUESTION, _gate_history())
        assert learning_mod.PREREQUISITE_GATE_MARKER not in resp.answer
        assert resp.support_mode != "prerequisite_review"
        assert f"前提知識「{PREREQ}」そのものを" not in chat_env.prompts[-1]
        assert "観測がそう決めたからですよね" in chat_env.prompts[-1]
        # 肯定は定型の答えとして記帳に渡る（素の「はい」と同じ経路）。
        assert chat_env.gate_calls == ["はい、理解しています"]

    def test_route_bare_yes_is_ack_only(self, chat_env):
        resp = _chat("はい", _gate_history())
        assert chat_env.gate_calls == ["はい、理解しています"]
        assert learning_mod.PREREQUISITE_GATE_MARKER not in resp.answer


# ---------------------------------------------------------------------------
# G2: 学習相談は見出しなしの短い平文・表示中トピックの出典
# ---------------------------------------------------------------------------


class TestAdvicePlain:
    def test_prompt_has_no_template_headings_and_names_next_topic(self, monkeypatch):
        captured = {}

        def _gen(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return "案内"

        monkeypatch.setattr(learning_mod, "generate_text", _gen)
        course = {"topics": [
            {"id": "a", "title": "導入", "chapter_index": 0},
            {"id": "b", "title": "主結果", "chapter_index": 0},
        ]}
        learning_mod._generate_learning_advice_response(
            "コース", "導入", "どこから読めばいい？", topic_info=course["topics"][0], course_data=course,
        )
        p = captured["prompt"]
        for h in ("【歓迎と目標】", "【構成要素】", "【前提知識の確認】", "【次の一歩】"):
            assert h not in p
        assert "章の次のトピック: 主結果" in p
        assert "5文以内" in p

    def test_fallback_is_plain(self, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("x")

        monkeypatch.setattr(learning_mod, "generate_text", _boom)
        out = learning_mod._generate_learning_advice_response("コース", "導入", "どこから？")
        assert "【" not in out and "ようこそ" not in out

    def test_shape_strips_headings_and_caps_sentences(self):
        raw = "## 【歓迎と目標】\nA。B。\n\n1. 【構成要素】C。D。E。F。G。"
        out = learning_mod._shape_advice_answer(raw, "どこから読めばいい？", plain=True)
        assert "【" not in out and "#" not in out
        assert out.count("。") == 5

    def test_route_topic_source_chip_and_grounding(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "prejudge_stance_route", lambda *a, **k: None)
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "LEARNING_ADVICE")
        monkeypatch.setattr(
            learning_mod, "_topic_source_rows",
            lambda *a, **k: [{
                "id": "chunk-topic", "text": "表示中の教材の本文。", "source_title": "論文A",
                "material_id": "mat-course", "tier": "source", "document_id": "doc-1",
            }],
        )
        _answer_with(
            monkeypatch, chat_env,
            "【歓迎と目標】まず表示中の教材の冒頭から読むとよいです [出典1]。次の節へ進めます。",
        )
        resp = _chat("どこから読めばいい？")
        assert "【" not in resp.answer
        assert resp.content_grounding == "course_material"
        assert resp.sources and resp.sources[0].chunk_id == "chunk-topic"

    def test_route_without_citation_is_model_generated(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "prejudge_stance_route", lambda *a, **k: None)
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "LEARNING_ADVICE")
        _answer_with(monkeypatch, chat_env, "冒頭から読むとよいです。")
        resp = _chat("どこから読めばいい？")
        assert resp.content_grounding == "model_generated"


# ---------------------------------------------------------------------------
# G5: 確からしさを数値で示さない
# ---------------------------------------------------------------------------


def test_tutor_and_casual_text_prompts_forbid_numeric_confidence():
    sentence = "自分の確からしさを数値・割合・点数で示さない（「論文に明記 / 論文から推定 / 一般知識」の3段の言葉で示す）。"
    assert sentence in learning_mod._get_integrated_tutor_system_prompt("")
    assert sentence in learning_mod._get_casual_teacher_system_prompt("", spoken=False)


# ---------------------------------------------------------------------------
# G3: 辺の confidence を既定値で埋め戻さない
# ---------------------------------------------------------------------------


def test_component_graph_edge_confidence_stays_absent_after_projection():
    from api.schemas import ComponentGraphResponse
    from api.routes.theory_components import project_teacher_dto

    payload = {
        "graph_id": "g", "document_id": "d",
        "nodes": [],
        "edges": [{"source_component_id": "a", "target_component_id": "b", "confidence": 0.5}],
    }
    projected = project_teacher_dto("d", payload)
    resp = ComponentGraphResponse(**projected)
    edge = resp.model_dump()["edges"][0]
    assert "confidence" not in edge
    assert "confidence" not in resp.model_dump_json()
