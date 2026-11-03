"""学習チャットの往復の是正（IK-0444〜IK-0451・ペルソナ通し受講 第 10 周）。

- IK-0444 クライアントが履歴を窓で切り・本文を変えて・sources を落として送っても、出典番号は
  会話の中で最初の番号のまま（サーバが保存する累積の対応表 ``citation_map``）
- IK-0445 書誌の連なり・所属機関の区画・先頭の非本文見出しを検索結果から外す
- IK-0446 「前提の確認はまだ記録していません」の1行はトピックで最初の往復だけ
- IK-0447 締めくくりの定型文に出所の分類を付けない
- IK-0448 出典ポップアップの DTO を描画に使うキーに絞る
- IK-0450 定型文の英語版（理解度の点数・前提確認の記録・「違っていた」）
- IK-0451 ドリルダウンのボタンの文字から数式の区切りを外す

DB・実 LLM には触れない（境界 monkeypatch）。
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
from api import services  # noqa: E402
from core import label_vocab  # noqa: E402
from core.learning_support_agent import extract_inline_actions  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402

from tests.test_ik0432_0437_chat_turn_fixes import (  # noqa: E402,F401
    CURRENT_USER,
    _ask,
    _chunk,
    chat_env,
)


def _search(chat_env, *ids):
    chat_env.monkeypatch.setattr(
        learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk(i) for i in ids],
    )


def _cite_all(chat_env):
    """回答が文脈の [出典N] をすべて引用する LLM（IK-0494 以降、見せる出典は引用したものだけ）。"""
    import re as _re

    def _generate(**kwargs):
        messages = kwargs.get("messages") or []
        chat_env.prompts.append(messages)
        context = str(messages[1].get("content") or "") if len(messages) > 1 else ""
        labels = list(dict.fromkeys(_re.findall(r"\[出典\d+\]", context)))
        return "回答です " + "".join(labels)

    chat_env.monkeypatch.setattr(learning_mod, "generate_text", _generate)


def _client_copy(stored: list, *, window: int | None = None, mutate: bool = False) -> list:
    """uxsim の API runner と同じ形: {role, content} だけ・窓で切る・（任意で）本文を変える。"""
    turns = [{"role": m["role"], "content": m["content"]} for m in stored]
    if mutate:
        turns = [
            {"role": t["role"], "content": ("⚠️ 表示用の前置き\n\n" + t["content"] + "\n（整形済み）")}
            if t["role"] == "assistant" else t
            for t in turns
        ]
    if window is not None:
        turns = turns[-window:]
    return turns


# ---------------------------------------------------------------------------
# IK-0444 出典番号は会話の中で最初の番号のまま
# ---------------------------------------------------------------------------


class TestCitationMapSurvivesClientCopy:
    def test_modified_windowed_history_keeps_first_numbers(self, chat_env):
        _cite_all(chat_env)
        _search(chat_env, 1, 2)
        first = _ask("一つ目")
        assert [(s.index, s.chunk_id) for s in first.sources] == [(1, "chunk-1"), (2, "chunk-2")]

        # 2往復目: 本文を変え、sources を落とした履歴（照合で戻せない）。
        _search(chat_env, 3)
        second = _ask("二つ目", history=_client_copy(chat_env.stored["history"], mutate=True))
        assert [(s.index, s.chunk_id) for s in second.sources] == [(3, "chunk-3")]

        # 3往復目: 直前の1往復だけを送る窓（1往復目は届かない）。
        _search(chat_env, 1, 4, 2)
        third = _ask("三つ目", history=_client_copy(chat_env.stored["history"], window=2, mutate=True))
        assert [(s.index, s.chunk_id) for s in third.sources] == [
            (1, "chunk-1"), (2, "chunk-2"), (4, "chunk-4"),  # 番号順（第 14 周）
        ]
        # 保存された最新の assistant ターンに会話全体の対応表が残る。
        last = [m for m in chat_env.stored["history"] if m["role"] == "assistant"][-1]
        assert last[learning_mod.CITATION_MAP_KEY] == {
            "chunk-1": 1, "chunk-2": 2, "chunk-3": 3, "chunk-4": 4,
        }

    def test_turn_without_sources_carries_map_forward(self, chat_env):
        _cite_all(chat_env)
        _search(chat_env, 1)
        _ask("一つ目")
        # 締めくくりの定型文（出典なしの経路）を挟んでも対応表は失われない。
        _ask("ありがとうございます", history=_client_copy(chat_env.stored["history"], mutate=True))
        _search(chat_env, 5, 1)
        third = _ask("三つ目", history=_client_copy(chat_env.stored["history"], window=2))
        assert [(s.index, s.chunk_id) for s in third.sources] == [(1, "chunk-1"), (2, "chunk-5")]  # 番号順（第 14 周）

    def test_rehydrate_matches_text_without_drilldown_markers(self):
        stored = [{
            "role": "assistant",
            "content": "回答です [出典1]\n\n[音速について詳しく聞く]",
            "sources": [{"index": 1, "chunk_id": "a"}],
        }]
        client = [{"role": "assistant", "content": "回答です [出典1]"}]
        out = learning_mod._rehydrate_history_sources(client, stored)
        assert out[0]["sources"] == [{"index": 1, "chunk_id": "a"}]

    def test_history_api_does_not_return_citation_map(self, monkeypatch):
        class _Rec:
            def fetchone(self):
                return ([
                    {"role": "user", "content": "q"},
                    {"role": "assistant", "content": "a", "citation_map": {"c": 1}, "sources": []},
                ],)

        class _Sess:
            def execute(self, *a, **k):
                return _Rec()

            def close(self):
                pass

        monkeypatch.setattr(learning_mod, "_pg_session", lambda: _Sess())
        resp = learning_mod.get_chat_history("course-1", "topic-1", current_user=CURRENT_USER)
        assert all("citation_map" not in m for m in resp.history)


# ---------------------------------------------------------------------------
# IK-0445 本文ではない区画
# ---------------------------------------------------------------------------

_NUMBERED_REFS = (
    "¨O. Akarsu, et al., Cosmology intertwined: A review of\n"
    "the particle physics, astrophysics, and cosmology asso-\n"
    "ciated with the cosmological tensions and anomalies,\n"
    "Journal of High Energy Astrophysics 34, 49 (2022),\n"
    "arXiv:2203.06142 [astro-ph.CO].\n"
    "[26] A. Amon and G. Efstathiou, A non-linear solution to\n"
    "the S8 tension?, Mon. Not. of the Roy. Astron. Soc.\n"
    "516, 5355 (2022), arXiv:2206.11794 [astro-ph.CO].\n"
    "[27] I. G. McCarthy, J. Salcido, J. Schaye, J. Kwan, W. Elbers, et al., The FLAMINGO\n"
    "project, Mon. Not. R. Astron. Soc. 526, 5494 (2023), arXiv:2309.07959 [astro-ph.CO].\n"
    "[28] J. Carron, A. Lewis, and G. Fabbian, Planck integrated Sachs-Wolfe-lensing likelihood,\n"
    "Phys. Rev. D 106, 103507 (2022), arXiv:2209.07395 [astro-ph.CO].\n"
)
_APPENDIX_THEN_REFS = (
    "Parameterization\n\nIn this Appendix, we show contours plots for the posteriors of the\n"
    "k-essence-like parametrization.\n\n" + _NUMBERED_REFS.replace("¨O. Akarsu", "[25] O. Akarsu")
)
_TITLE_BLOCK = (
    "The sound of dynamical dark energy and modified gravity\n\n"
    "A. Author,1, 2 B. Author,2 and C. Author3\n\n"
    "1Department of Astronomy/Steward Observatory, University of Arizona,\n"
    "933 North Cherry Avenue, Tucson, AZ 85721, USA\n"
    "2CBPF - Brazilian Center for Research in Physics,\n"
    "150, zip 22290-180, Rio de Janeiro, RJ, Brazil\n"
    "3C. N. Yang Institute for Theoretical Physics, Stony Brook University, NY, 11794, USA\n"
    "(Dated: June 2, 2026)\n\n"
    "Different candidate models are able to reproduce the dynamical dark energy signal.\n"
)
_BODY_WITH_BRACKET_CITATIONS = (
    "I.\nINTRODUCTION\n\nOne of the main scientific goals of modern cosmology is to uncover the\n"
    "nature of dark energy. LCDM provides a good fit to CMB anisotropies [1-6], galaxy\n"
    "correlation functions [7-10] and supernova distances [11, 12], see e.g. Akarsu et al. (2022).\n"
    "[13] shows a similar tension. Recent work (Houde et al. 2009; Pattle et al. 2017) agrees.\n"
)


class TestNonContentChunks:
    @pytest.mark.parametrize("text", [_NUMBERED_REFS, _APPENDIX_THEN_REFS])
    def test_bibliography_runs(self, text):
        assert services.non_content_chunk_reason(text) == "bibliography"

    def test_title_affiliation_block(self):
        assert services.non_content_chunk_reason(_TITLE_BLOCK) == "affiliation"

    @pytest.mark.parametrize("text", [
        "V.\nACKNOWLEDGMENTS\n\nWe thank the referee for useful comments.",
        "8 DATA AVAILABILITY\n\nAll data supporting the conclusions are public.",
        "Software\nWe used numpy and CAMB.",
        "6.1 Data Availability Statement: the data are available on request.",
    ])
    def test_non_content_heading_at_head(self, text):
        assert services.non_content_chunk_reason(text) == "non_content_heading"

    @pytest.mark.parametrize("text", [
        _BODY_WITH_BRACKET_CITATIONS,
        "V.\nCONCLUSIONS\n\nHints for dynamical dark energy from recent data are discussed.",
        "C.\nSound Speed and Growth\n\nWhile standard analyses assume a unit sound speed, we vary it.",
        "The software used to reduce the data is described in Section 2 of the paper.",
    ])
    def test_body_is_kept(self, text):
        assert services.non_content_chunk_reason(text) is None


# ---------------------------------------------------------------------------
# IK-0446 「確認はまだ記録していません」はトピックで最初の往復だけ
# ---------------------------------------------------------------------------


class TestGateSkippedNoticeOnce:
    def test_detects_notice_in_history(self):
        notice = label_vocab.PREREQUISITE_GATE_SKIPPED_NOTICE.format(prerequisite="前提A")
        history = [{"role": "assistant", "content": notice + "\n\n本文"}]
        assert learning_mod._history_has_gate_skipped_notice(history)
        en = label_vocab.PREREQUISITE_GATE_SKIPPED_NOTICE_EN.format(prerequisite="Prereq A")
        assert learning_mod._history_has_gate_skipped_notice([{"role": "assistant", "content": en}])
        assert not learning_mod._history_has_gate_skipped_notice([{"role": "assistant", "content": "本文"}])
        # 学習者の発話に同じ文があっても数えない。
        assert not learning_mod._history_has_gate_skipped_notice([{"role": "user", "content": notice}])


# ---------------------------------------------------------------------------
# IK-0447 締めくくりの定型文に出所の分類を付けない
# ---------------------------------------------------------------------------


class TestClosingReplyHasNoOrigin:
    def test_closing_reply_has_no_grounding(self, chat_env):
        resp = _ask_plain("ありがとうございました、今日はここまでにします")
        assert resp.answer == label_vocab.CLOSING_UTTERANCE_REPLY
        assert resp.content_grounding is None
        last = chat_env.stored["history"][-1]
        assert "content_grounding" not in last


def _ask_plain(message: str, history: list | None = None):
    return learning_mod.learning_chat(
        "course-1", "topic-1",
        LearningChatRequest(message=message, history=history or []),
        current_user=CURRENT_USER,
    )


# ---------------------------------------------------------------------------
# IK-0448 出典ポップアップの学習者向け DTO
# ---------------------------------------------------------------------------


class TestLearnerSourcePassage:
    def test_projects_to_render_keys(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "get_accessible_course_data", lambda uid, cid: {"sources": []})
        monkeypatch.setattr(learning_mod, "list_course_source_document_ids", lambda cd: {"doc-a"})
        monkeypatch.setattr(learning_mod, "get_chunk_passage", lambda *a, **k: {
            "chunk_id": "c1", "text": "本文 [[FORMULA_0]]", "section": "3.1 Map",
            "source_title": "論文", "source_file": "paper.pdf",
            "formulas": [{
                "id": "[[FORMULA_0]]", "latex": "I \\geq 10", "raw_text": "I ≥10", "block_id": "blk_1",
                "section_id": "sec_3", "review_reason": ["pdf_text_layer_untrusted"],
                "source_image": {"bbox": [1, 2, 3, 4], "data_base64": "iVBOR"},
                "source_location": {"bbox": [1, 2, 3, 4], "page": 3}, "reconstructed": True,
                "is_display": True, "latex_source": "reconstruction",
            }],
        })
        out = learning_mod.get_source_chunk_route("course-1", "c1", current_user=CURRENT_USER)
        assert set(out) == {"chunk_id", "text", "section", "source_title", "formulas"}
        formula = out["formulas"][0]
        assert set(formula) == {
            "id", "latex", "raw_text", "reconstructed", "reconstructed_mark", "reconstructed_note",
        }
        assert formula["reconstructed_mark"] == label_vocab.RECONSTRUCTED_EQUATION_MARK
        assert "iVBOR" not in repr(out) and "bbox" not in repr(out)


# ---------------------------------------------------------------------------
# IK-0450 定型文の英語版
# ---------------------------------------------------------------------------


class TestEnglishFixedLines:
    def test_score_request_in_english(self, chat_env):
        resp = _ask_plain("Can you tell me my score?")
        assert resp.answer == label_vocab.UNDERSTANDING_SCORE_REQUEST_REPLY_EN

    def test_score_request_in_japanese(self, chat_env):
        resp = _ask_plain("私の理解度を点数で教えてください")
        assert resp.answer == label_vocab.UNDERSTANDING_SCORE_REQUEST_REPLY

    def test_english_constants_have_no_numbers(self):
        for text in (
            label_vocab.UNDERSTANDING_SCORE_REQUEST_REPLY_EN,
            label_vocab.PREREQUISITE_ACK_RESUME_NOTICE_EN,
            label_vocab.CHECK_SELF_CHECK_DISAGREED_NOTICE_EN,
        ):
            assert not any(ch.isdigit() for ch in text)
            assert not any("぀" <= ch <= "鿿" for ch in text)

    @pytest.mark.parametrize(("last_user", "expected"), [
        ("Why is mu negative here?", label_vocab.CHECK_SELF_CHECK_DISAGREED_NOTICE_EN),
        ("µ はなぜ負ですか", label_vocab.CHECK_SELF_CHECK_DISAGREED_NOTICE),
        (None, label_vocab.CHECK_SELF_CHECK_DISAGREED_NOTICE),
    ])
    def test_disagreed_notice_follows_learner_language(self, monkeypatch, last_user, expected):
        history = [] if last_user is None else [
            {"role": "user", "content": last_user}, {"role": "assistant", "content": "a"},
        ]
        monkeypatch.setattr(learning_mod, "load_stored_chat_history", lambda *a, **k: history)
        assert learning_mod._self_check_disagreed_notice("u", "c", "t") == expected

    @pytest.mark.parametrize(("text", "expected"), [
        ("Thanks!", True), ("Can you tell me my score?", True), ("はい", False),
        ("µ はなぜ", False), ("12345", False), ("", False),
    ])
    def test_kana_kanji_free(self, text, expected):
        assert learning_mod._is_kana_kanji_free(text) is expected


# ---------------------------------------------------------------------------
# IK-0451 ドリルダウンのボタンの文字
# ---------------------------------------------------------------------------


class TestDrilldownLabelMath:
    @pytest.mark.parametrize(("text", "label"), [
        (r"本文 [Ask more about $\mu$]", "Ask more about μ"),  # IK-0478: 生の制御綴りも平文に直す
        (r"本文 [\(w_0\)について詳しく聞く]", "w_0について詳しく聞く"),
        (r"本文 [Tell me more about $$c_s^2$$]", "Tell me more about c_s^2"),
        ("本文 [音速について詳しく聞く]", "音速について詳しく聞く"),
    ])
    def test_math_delimiters_are_removed(self, text, label):
        clean, actions = extract_inline_actions(text)
        assert clean == "本文"
        assert [a.label for a in actions] == [label]
        assert actions[0].message == label


# ---------------------------------------------------------------------------
# IK-0449 前提の説明の出所: course_material はコース内トピックの教材を渡したときだけ
# ---------------------------------------------------------------------------


class TestPrerequisiteGroundingRule:
    def _course(self, material: str) -> dict:
        topic = {"id": "t17", "title": "前提A"}
        if material:
            topic["student_material"] = {"source_text": material}
        return {"topics": [topic, {"id": "t18", "title": "本題"}], "sources": []}

    def test_topic_material_without_numbered_sources_is_course_material(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
        out = learning_mod._resolve_prerequisite_context("u", self._course("前提Aの教材本文"), ["前提A"])
        assert out["content_grounding"] == "course_material"
        assert out["cited_sources"] == []
        assert "[コース内トピック『前提A』の教材]" in out["context_block"]

    def test_title_match_without_material_is_model_generated(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda uid: [])
        monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
        out = learning_mod._resolve_prerequisite_context("u", self._course(""), ["前提A"])
        assert out["content_grounding"] == "model_generated"
        assert out["context_block"] is None
        assert learning_mod.PREREQUISITE_CLOSED_WORLD_FACT in (
            learning_mod._prerequisite_closed_world_note(out["unresolved"])
        )
