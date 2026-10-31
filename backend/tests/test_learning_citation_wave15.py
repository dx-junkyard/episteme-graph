"""出典の番号・一覧・検索の混入・出典本文の開き方の是正（ペルソナ通し受講 第 14 周）。

- 同じ番号は会話の中で同じチャンクを指す（前の回答の引用を戻すときも番号を変えない）
- 学習者に見せる出典は番号順・本文で引用していない出典は ``cited: False`` で区別
- トピックの論文で答えられるときは、それより類似度の低い別論文のチャンクを外す
- 見出しだけ・ページ番号だけの極短チャンクを検索結果から外す
- 出典本文の途中始まり・途中終わりを「…」で示し、404 は日本語の事実文

DB・実 LLM には触れない（test_ik0432_0437_chat_turn_fixes.py の chat_env を再利用）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.learning as learning_mod  # noqa: E402
import services  # noqa: E402

from tests.test_ik0432_0437_chat_turn_fixes import (  # noqa: E402,F401
    CURRENT_USER,
    _ask,
    _chunk,
    chat_env,
)


def _search(chat_env, *chunks):
    chat_env.monkeypatch.setattr(
        learning_mod, "search_chunks_with_metadata", lambda *a, **k: [dict(c) for c in chunks],
    )


def _answer(chat_env, text: str):
    def _generate(**kwargs):
        chat_env.prompts.append(kwargs.get("messages"))
        return text

    chat_env.monkeypatch.setattr(learning_mod, "generate_text", _generate)


class TestSameNumberSameChunk:
    def test_numbers_fixed_across_turns_and_carry(self, chat_env):
        _search(chat_env, _chunk(1), _chunk(2), _chunk(3))
        _answer(chat_env, "a [出典2] b [出典3]")
        first = _ask("一つ目")
        by_first = {s.index: s.chunk_id for s in first.sources}
        history = [{"role": m["role"], "content": m["content"]} for m in chat_env.stored["history"]]
        rows = [_chunk(2), _chunk(3)]
        chat_env.monkeypatch.setattr(
            learning_mod, "get_chunks_for_prompt",
            lambda ids, *, allowed_document_ids: [dict(r) for r in rows if r["id"] in ids],
        )
        _search(chat_env, _chunk(4), _chunk(1))
        _answer(chat_env, "前の [出典2][出典3] と新しい [出典4]")
        second = _ask("二つ目", history=history)
        by_second = {s.index: s.chunk_id for s in second.sources}
        for index, chunk_id in by_first.items():
            assert by_second[index] == chunk_id
        context = str(chat_env.prompts[-1][1]["content"])
        assert "[出典2] 『論文 2』（前の回答で引用した箇所）\nチャンク本文 2" in context
        assert "[出典3] 『論文 3』（前の回答で引用した箇所）\nチャンク本文 3" in context


class TestDisplayedSources:
    def test_sorted_by_number(self):
        sources = [{"index": 9, "origin": "course_material"}, {"index": 3, "origin": "course_material"}]
        out = learning_mod._displayed_sources_for("x [出典9] y [出典3]", sources, "course_material")
        assert [s["index"] for s in out] == [3, 9]
        assert all(s["cited"] is True for s in out)

    def test_uncited_fallback_marked_unreferenced(self):
        sources = [{"index": 5, "origin": "course_material"}, {"index": 2, "origin": "course_material"}]
        out = learning_mod._displayed_sources_for("引用なし", sources, "course_material")
        assert [s["index"] for s in out] == [2, 5]
        assert all(s["cited"] is False for s in out)
        assert "cited" not in sources[0]  # 入力は変更しない

    def test_history_meta_keeps_unreferenced_flag(self):
        meta = learning_mod._history_source_meta([
            {"index": 1, "chunk_id": "a", "source_title": "t", "tier": "source", "cited": False},
            {"index": 2, "chunk_id": "b", "source_title": "t", "tier": "source", "cited": True},
        ])
        assert meta[0]["cited"] is False
        assert "cited" not in meta[1]


class TestOffTopicChunks:
    def _r(self, cid, doc, score):
        return {"id": cid, "document_id": doc, "score": score}

    def test_drops_lower_scoring_other_documents(self):
        rows = [self._r("t1", "topic", 0.6), self._r("o1", "other", 0.5), self._r("o2", "other", 0.7)]
        out = learning_mod._drop_off_topic_chunks(rows, {"topic"})
        assert [r["id"] for r in out] == ["t1", "o2"]

    def test_keeps_all_when_topic_has_no_adoptable_chunk(self):
        rows = [self._r("t1", "topic", 0.2), self._r("o1", "other", 0.5)]
        assert learning_mod._drop_off_topic_chunks(rows, {"topic"}) == rows

    def test_no_topic_documents_is_noop(self):
        rows = [self._r("o1", "other", 0.5)]
        assert learning_mod._drop_off_topic_chunks(rows, set()) == rows


class TestTooShortChunks:
    @pytest.mark.parametrize("text", ["2", "II. THEORY", "A. Background\n", "Table II"])
    def test_heading_or_page_number_only(self, text):
        assert services.non_content_chunk_reason(text) == "too_short"

    @pytest.mark.parametrize("text", [
        "磁場強度は約150マイクロガウスと見積もられる。",
        "We find that the sound speed of dark energy is constrained.",
    ])
    def test_real_sentences_kept(self, text):
        assert services.non_content_chunk_reason(text) is None


class TestSourcePassage:
    def test_mid_start_and_end_marked(self):
        out = learning_mod._learner_source_passage({"chunk_id": "c", "text": "s was assumed for th", "formulas": []})
        assert out["text"] == "…s was assumed for th…"

    def test_complete_sentence_untouched(self):
        out = learning_mod._learner_source_passage({"chunk_id": "c", "text": "We find this.", "formulas": []})
        assert out["text"] == "We find this."

    def test_not_found_detail_is_japanese_fact(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "get_accessible_course_data", lambda *a, **k: {"sources": []})
        monkeypatch.setattr(learning_mod, "list_course_source_document_ids", lambda *a, **k: [])
        monkeypatch.setattr(learning_mod, "get_chunk_passage", lambda *a, **k: None)
        with pytest.raises(HTTPException) as exc:
            learning_mod.get_source_chunk_route("course", "chunk", current_user=CURRENT_USER)
        assert exc.value.status_code == 404
        assert "Source chunk not found" not in exc.value.detail
        assert "開けません" in exc.value.detail
