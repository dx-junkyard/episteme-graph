"""学習チャットの往復の是正（IK-0492〜IK-0496・ペルソナ通し受講 第 12 周）。

- IK-0492 文脈に置くチャンク本文の写しから U+FFFD と arXiv の版の刻印を取り除く（保存データは不変）
- IK-0493 「なるほど、ありがとうございます。」等のお礼に出所の分類を付けない
- IK-0494 学習者に見せる出典は回答本文が引用したものだけ（番号は振り直さない）
- IK-0495 足場の直後に作り話の assistant ターン（「お答えします」）を置かない
- IK-0475 直前の回答が引用したチャンクを次の往復の文脈へ元の番号で戻す（上限3件・可視性は SQL 内）

DB・実 LLM には触れない（境界 monkeypatch。test_ik0432_0437_chat_turn_fixes.py の chat_env を再利用）。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.learning as learning_mod  # noqa: E402
from core.text_hygiene import sanitize_source_text_for_prompt  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402

from tests.test_ik0432_0437_chat_turn_fixes import (  # noqa: E402,F401
    CURRENT_USER,
    _ask,
    _chunk,
    chat_env,
)


def _search(chat_env, *chunks):
    chat_env.monkeypatch.setattr(
        learning_mod, "search_chunks_with_metadata", lambda *a, **k: list(chunks),
    )


def _answer(chat_env, text: str):
    def _generate(**kwargs):
        chat_env.prompts.append(kwargs.get("messages"))
        return text

    chat_env.monkeypatch.setattr(learning_mod, "generate_text", _generate)


def _prompt_text(messages: list) -> str:
    return "\n".join(str(m.get("content") or "") for m in messages)


# ---------------------------------------------------------------------------
# IK-0492
# ---------------------------------------------------------------------------


class TestSourceTextSanitizer:
    def test_strips_replacement_char(self):
        assert sanitize_source_text_for_prompt("κ(χ) = �χH dχ") == "κ(χ) = χH dχ"

    def test_strips_arxiv_version_stamp(self):
        text = "Uni-\n\n$arXiv:2606.00411v1 [astro-ph.CO] 29 May 2026$\n\n2\nverse"
        out = sanitize_source_text_for_prompt(text)
        assert "arXiv:2606.00411" not in out
        assert "Uni-" in out and "verse" in out
        assert "arXiv:2606.00411v1 [astro-ph.CO] 28 May 2026" not in sanitize_source_text_for_prompt(
            "Intro arXiv:2606.00411v1 [astro-ph.CO] 28 May 2026 body"
        )

    def test_keeps_reference_citations_without_date(self):
        text = "Journal 34, 49 (2022),\narXiv:2203.06142 [astro-ph.CO].\n[26] A. Amon"
        assert sanitize_source_text_for_prompt(text) == text

    def test_none_and_empty(self):
        assert sanitize_source_text_for_prompt(None) == ""
        assert sanitize_source_text_for_prompt("") == ""

    def test_chat_context_is_sanitized_but_quote_source_unchanged(self, chat_env):
        dirty = dict(_chunk(1))
        dirty["text"] = "本文 � の続き\n$arXiv:2606.00411v1 [astro-ph.CO] 28 May 2026$\n次"
        _search(chat_env, dirty)
        _answer(chat_env, "回答です [出典1]")
        resp = _ask("質問です")
        prompt = _prompt_text(chat_env.prompts[-1])
        assert "�" not in prompt
        assert "arXiv:2606.00411v1" not in prompt
        assert "の続き" in prompt
        # 検索結果（保存データの写し）そのものは書き換えない。
        assert "�" in dirty["text"]
        assert all("�" not in (s.quote or "") for s in resp.sources)


# ---------------------------------------------------------------------------
# IK-0493
# ---------------------------------------------------------------------------


class TestThanksHasNoProvenance:
    def test_aizuchi_plus_thanks_is_a_closing_utterance(self):
        assert learning_mod._is_closing_utterance("なるほど、ありがとうございます。")
        assert learning_mod._is_closing_utterance("わかりました、ありがとうございました！")
        assert learning_mod._is_closing_utterance("I see, thanks!")
        assert learning_mod._is_closing_utterance("Got it, thank you.")
        # 内容が残る発話は拾わない（狭く保つ）。
        assert not learning_mod._is_closing_utterance("なるほど、ありがとう。でも µ はなぜ負？")
        assert not learning_mod._is_closing_utterance("なるほど")

    def test_aizuchi_thanks_takes_fixed_reply_without_grounding(self, chat_env):
        _search(chat_env, _chunk(1))
        _answer(chat_env, "呼ばれてはいけない")
        resp = learning_mod.learning_chat(
            "course-1", "topic-1",
            LearningChatRequest(message="なるほど、ありがとうございます。", history=[]),
            current_user=CURRENT_USER,
        )
        assert resp.content_grounding is None
        assert chat_env.prompts == []

    def test_closing_led_statement_without_citation_has_no_grounding(self, chat_env):
        _search(chat_env)  # 採用した根拠なし → 旧来は model_generated
        _answer(chat_env, "こちらこそ。ゼミでの説明、うまくいくといいですね。")
        resp = _ask("ありがとうございました。これでゼミで説明できそうです。")
        assert resp.content_grounding is None
        last = [m for m in chat_env.stored["history"] if m["role"] == "assistant"][-1]
        assert "content_grounding" not in last

    def test_content_question_keeps_model_generated(self, chat_env):
        _search(chat_env)
        _answer(chat_env, "一般的な説明です。")
        resp = _ask("BAO とは何ですか？")
        assert resp.content_grounding == "model_generated"


# ---------------------------------------------------------------------------
# IK-0494
# ---------------------------------------------------------------------------


class TestDisplayedSourcesAreCitedOnly:
    def test_helper_keeps_order_and_numbers(self):
        sources = [{"index": 3, "chunk_id": "c"}, {"index": 1, "chunk_id": "a"}, {"index": 2, "chunk_id": "b"}]
        out = learning_mod._sources_cited_in_answer("x [出典2] y [出典3]", sources)
        assert out == [{"index": 3, "chunk_id": "c"}, {"index": 2, "chunk_id": "b"}]
        assert learning_mod._sources_cited_in_answer("引用なし", sources) == []

    def test_uncited_off_topic_chunk_not_listed(self, chat_env):
        _search(chat_env, _chunk(1), _chunk(2))
        _answer(chat_env, "BAO の説明です [出典1]。出典2 は無関係です。")
        resp = _ask("BAO について教えてください")
        assert [(s.index, s.chunk_id) for s in resp.sources] == [(1, "chunk-1")]
        last = [m for m in chat_env.stored["history"] if m["role"] == "assistant"][-1]
        assert [s["chunk_id"] for s in last["sources"]] == ["chunk-1"]
        # 採用しただけの chunk-2 の番号も対応表に控えられる（次の往復で別チャンクへ振り直さない）。
        assert last[learning_mod.CITATION_MAP_KEY] == {"chunk-1": 1, "chunk-2": 2}

    def test_no_citation_falls_back_to_sources_that_decided_grounding(self):
        sources = [
            {"index": 1, "chunk_id": "a", "origin": "other_material"},
            {"index": 2, "chunk_id": "b", "origin": "course_material"},
        ]
        f = learning_mod._displayed_sources_for
        # 引用が無く出所が教材 → 出所を決めた course_material の出典だけ並べる。
        # 第 14 周: 引用していない出典は cited=False で区別する。
        assert f("引用なし", sources, "course_material") == [dict(sources[1], cited=False)]
        assert f("引用なし", sources, "other_material") == [dict(sources[0], cited=False)]
        # 引用があれば引用したものだけ。
        assert f("x [出典1]", sources, "course_material") == [dict(sources[0], cited=True)]
        # 出所が無い・AI の説明なら空。
        assert f("引用なし", sources, None) == []
        assert f("引用なし", sources, "model_generated") == []

    def test_uncited_answer_keeps_badge_and_list_consistent(self, chat_env):
        _search(chat_env, _chunk(1), _chunk(2))
        _answer(chat_env, "引用を付けずに説明しました。")
        resp = _ask("BAO について教えてください")
        # TRIAGE14（裁定）: 本文が何も引用せず表示中教材も問いに関わらなければ model_generated。
        # 帯（model_generated）と一覧（空）は食い違わない。
        assert resp.content_grounding == "model_generated"
        assert list(resp.sources) == []

    def test_numbering_stays_stable_after_filtering(self, chat_env):
        _search(chat_env, _chunk(1), _chunk(2))
        _answer(chat_env, "一つ目 [出典1]")
        _ask("一つ目")
        client = [{"role": m["role"], "content": m["content"]} for m in chat_env.stored["history"]]
        _search(chat_env, _chunk(3), _chunk(2))
        _answer(chat_env, "二つ目 [出典2][出典3]")
        second = _ask("二つ目", history=client)
        assert sorted((s.index, s.chunk_id) for s in second.sources) == [(2, "chunk-2"), (3, "chunk-3")]


# ---------------------------------------------------------------------------
# IK-0495
# ---------------------------------------------------------------------------


class TestNoFabricatedAssistantTurn:
    def test_first_turn_has_no_assistant_message(self, chat_env):
        _search(chat_env, _chunk(1))
        _answer(chat_env, "回答 [出典1]")
        _ask("質問です")
        messages = chat_env.prompts[-1]
        assert [m["role"] for m in messages] == ["system", "user", "user"]
        assert "お答えします" not in _prompt_text(messages)

    def test_history_follows_scaffold_directly(self, chat_env):
        _search(chat_env, _chunk(1))
        _answer(chat_env, "回答 [出典1]")
        history = [
            {"role": "user", "content": "Hello, what is BAO?"},
            {"role": "assistant", "content": "BAO is ..."},
        ]
        _ask("And the sound horizon?", history=history)
        messages = chat_env.prompts[-1]
        assert messages[2] == {"role": "user", "content": "Hello, what is BAO?"}
        assert all(m["role"] != "assistant" or m["content"] == "BAO is ..." for m in messages)


# ---------------------------------------------------------------------------
# IK-0475
# ---------------------------------------------------------------------------


class TestPreviousCitationsCarried:
    def _first_turn(self, chat_env, answer="前の回答 [出典1]"):
        _search(chat_env, _chunk(1), _chunk(2))
        _answer(chat_env, answer)
        _ask("一つ目")
        return [{"role": m["role"], "content": m["content"]} for m in chat_env.stored["history"]]

    def _carry_rows(self, chat_env, calls: list, rows: list):
        def _get(chunk_ids, *, allowed_document_ids):
            calls.append((list(chunk_ids), list(allowed_document_ids)))
            return [dict(r) for r in rows if r["id"] in chunk_ids]

        chat_env.monkeypatch.setattr(learning_mod, "get_chunks_for_prompt", _get)

    def test_previous_cited_chunk_reinjected_with_original_number(self, chat_env):
        history = self._first_turn(chat_env)
        calls: list = []
        self._carry_rows(chat_env, calls, [_chunk(1)])
        _search(chat_env, _chunk(3))
        _answer(chat_env, "前の [出典1] と同じ箇所です。新しい [出典3]")
        resp = _ask("二つ目", history=history)
        assert calls == [(["chunk-1"], ["doc-1"])]
        context = str(chat_env.prompts[-1][1]["content"])
        assert "[出典1] 『論文 1』（前の回答で引用した箇所）" in context
        assert "[出典1]" in resp.answer  # 根拠のある番号として残る
        assert sorted((s.index, s.chunk_id) for s in resp.sources) == [(1, "chunk-1"), (3, "chunk-3")]

    def test_uncited_previous_chunk_not_reinjected(self, chat_env):
        history = self._first_turn(chat_env, answer="前の回答 [出典1]")
        calls: list = []
        self._carry_rows(chat_env, calls, [_chunk(1), _chunk(2)])
        _search(chat_env, _chunk(3))
        _answer(chat_env, "回答 [出典3]")
        _ask("二つ目", history=history)
        # chunk-2 は前の回答が引用していないので戻さない。
        assert calls == [(["chunk-1"], ["doc-1"])]

    def test_already_retrieved_chunk_not_duplicated(self, chat_env):
        history = self._first_turn(chat_env)
        calls: list = []
        self._carry_rows(chat_env, calls, [_chunk(1)])
        _search(chat_env, _chunk(1))
        _answer(chat_env, "回答 [出典1]")
        _ask("二つ目", history=history)
        # 第 15 周: 直前の引用は検索の前に id 指定で読み出す（議論中の論文の優先に使う）が、
        # 今回の検索に既にあるチャンクを文脈へ二重に置かない。
        assert str(chat_env.prompts[-1][1]["content"]).count("[出典1]") == 1

    def test_invisible_chunk_dropped(self, chat_env):
        history = self._first_turn(chat_env)
        calls: list = []
        self._carry_rows(chat_env, calls, [])  # 可視性で落ちた
        _search(chat_env, _chunk(3))
        _answer(chat_env, "回答 [出典1][出典3]")
        resp = _ask("二つ目", history=history)
        assert "前の回答で引用した箇所" not in str(chat_env.prompts[-1][1]["content"])
        # 文脈に無い番号は本文から除かれる（従来どおり）。
        assert "[出典1]" not in resp.answer

    def test_cap_and_number_mismatch(self):
        numbers = learning_mod._SessionCitationNumbers([
            {"role": "assistant", "content": "x", learning_mod.CITATION_MAP_KEY: {
                "a": 1, "b": 2, "c": 3, "d": 4, "e": 5,
            }},
        ])
        history = [{
            "role": "assistant",
            "content": "[出典1][出典2][出典3][出典4][出典9]",
            "sources": [
                {"index": 1, "chunk_id": "a"}, {"index": 2, "chunk_id": "b"},
                {"index": 3, "chunk_id": "c"}, {"index": 4, "chunk_id": "d"},
                {"index": 9, "chunk_id": "e"},  # 対応表と番号が食い違う → 戻さない
            ],
        }]
        seen: list = []
        orig = learning_mod.get_chunks_for_prompt
        try:
            learning_mod.get_chunks_for_prompt = lambda ids, *, allowed_document_ids: seen.append(ids) or []
            learning_mod._carry_previous_cited_sources(
                history, numbers, exclude_chunk_ids={"a"}, allowed_document_ids=["doc-1"],
            )
        finally:
            learning_mod.get_chunks_for_prompt = orig
        assert seen == [["b", "c", "d"]]

    def test_elicit_does_not_carry(self):
        source = Path(learning_mod.__file__).read_text(encoding="utf-8")
        idx = source.index("_carry_previous_cited_sources(\n            body.history")
        assert 'if _cycle_mode != "elicit"' in source[idx:idx + 400]


class TestGetChunksForPromptFailClosed:
    def test_empty_allowed_set_issues_no_sql(self, monkeypatch):
        import services as services_mod

        def _boom():
            raise AssertionError("SQL を発行してはいけない")

        monkeypatch.setattr(services_mod, "_pg_session", _boom)
        assert services_mod.get_chunks_for_prompt(["x"], allowed_document_ids=[]) == []
        assert services_mod.get_chunks_for_prompt([], allowed_document_ids=["d"]) == []

    def test_visibility_enforced_in_sql(self):
        import inspect

        import services as services_mod

        src = inspect.getsource(services_mod.get_chunks_for_prompt)
        assert "c.document_id = ANY(CAST(:doc_ids AS uuid[]))" in src
