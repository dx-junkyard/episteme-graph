"""第 15 周: 学習チャット・discuss の検索採用と出典の持ち越し・前提ゲートの残り。"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.learning as L  # noqa: E402
from services import non_content_chunk_reason  # noqa: E402


def test_negated_citation_is_not_counted():
    ans = "中性子星の結果です[出典1]。[出典3]と[出典5]は無関係なので使っていません。"
    assert L._affirmative_citation_indices(ans) == [1]


def test_negated_citation_is_not_carried():
    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "答え[出典1]。[出典3]はこの問いと関係がありません。",
         "sources": [{"index": 1, "chunk_id": "c1"}, {"index": 3, "chunk_id": "c3"}]},
    ]
    refs = L._previous_turn_cited_refs(history)
    assert [r["chunk_id"] for r in refs] == ["c1"]


def test_uncited_flag_source_not_carried():
    history = [{"role": "assistant", "content": "答え[出典2]。",
                "sources": [{"index": 2, "chunk_id": "c2", "cited": False}]}]
    assert L._previous_turn_cited_refs(history) == []


def test_carried_refs_fall_back_to_citation_map():
    history = [{"role": "assistant", "content": "答え[出典4]。", "sources": [],
                L.CITATION_MAP_KEY: {"c4": 4}}]
    assert [r["chunk_id"] for r in L._previous_turn_cited_refs(history)] == ["c4"]


def test_sources_cited_ignores_negated():
    srcs = [{"index": 1}, {"index": 3}]
    out = L._sources_cited_in_answer("A[出典1]。[出典3]は使っていません。", srcs)
    assert [s["index"] for s in out] == [1]


def test_discussed_document_ids_and_drop():
    focus = L._discussed_document_ids([{"id": "c1", "document_id": "dA"}])
    assert focus == {"dA"}
    rows = [
        {"id": "a", "document_id": "dA", "score": 0.5},
        {"id": "b", "document_id": "dB", "score": 0.45},
        {"id": "c", "document_id": "dB", "score": 0.6},
    ]
    kept = L._drop_off_topic_chunks(L._prefer_topic_documents(rows, focus), focus)
    assert {r["id"] for r in kept} == {"a", "c"}
    assert L._discussed_document_ids([]) == set()


def test_scope_chunk_ids_to_documents():
    m = {"c1": "dA", "c2": "dB"}
    assert L._scope_chunk_ids_to_documents(["c2", "c1"], m, {"dA"}) == ["c1"]
    assert L._scope_chunk_ids_to_documents(["c2"], m, {"dA"}) == ["c2"]
    assert L._scope_chunk_ids_to_documents(["c2"], m, set()) == ["c2"]


def test_contentless_reply():
    assert L._is_contentless_reply("まだ")
    assert L._is_contentless_reply("わからん。")
    assert L._is_contentless_reply("むずい")
    assert not L._is_contentless_reply("なぜまだ収束しないのですか？")
    assert not L._is_contentless_reply("μとΣは何を測りますか")


def test_non_content_chunks():
    assert non_content_chunk_reason(
        "The authors are grateful to X for reviewing. This work is supported by the Department. "
        "We acknowledge the support under Project Identification No. RTI 4012."
    ) == "acknowledgments"
    assert non_content_chunk_reason(
        "Draft version June 2, 2026\nTypeset using LATEX twocolumn style in AASTeX7.0.1\n\nThe First Detection of X"
    ) == "title_block"
    assert non_content_chunk_reason(
        "This paper is organized as follows: we explain the method in Sec. 2. Then results in Sec. 3 and 4. "
        "Finally, in Sec. 5 we summarize."
    ) == "outline"
    assert non_content_chunk_reason(
        "We thank the referee. In this section we derive the growth equation from the Poisson equation."
    ) is None


def test_prereq_negative_includes_mada():
    assert "まだ" in L._PREREQ_NEGATIVE_MARKERS


def test_anchor_confirm_prompt_constant():
    assert L.ANCHOR_CONFIRM_PROMPT.startswith("この疑問はどれに近いですか")
