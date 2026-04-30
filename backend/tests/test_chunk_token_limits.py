"""Embedding API のトークン上限を超えないチャンク化・バッチ化のテスト。

Vertex AI の text-embedding 系モデルは
  - 1 入力あたり ~2048 トークン
  - 1 リクエスト合計 ~20000 トークン
の制約を持つ。本テストでは services.py の chunk_text / chunk_pdf_pages /
_build_embedding_batches がこれらの制約を満たすことを保証する。
"""

from __future__ import annotations

import pytest

from api.services import (
    EMBEDDING_BATCH_MAX_ITEMS,
    EMBEDDING_MAX_BATCH_TOKENS,
    EMBEDDING_MAX_INPUT_TOKENS,
    _build_embedding_batches,
    _estimate_tokens,
    chunk_pdf_pages,
    chunk_text,
)


class TestSmartChunking:
    def test_short_text_returns_single_chunk(self):
        chunks = chunk_text("これは短い文章です。", chunk_size=1000)
        assert chunks == ["これは短い文章です。"]

    def test_empty_text_returns_empty(self):
        assert chunk_text("") == []

    def test_chunks_break_on_paragraph(self):
        text = "段落1の内容。" * 50 + "\n\n" + "段落2の内容。" * 50
        chunks = chunk_text(text, chunk_size=400, overlap=0)
        assert len(chunks) >= 2
        # 段落1が完結した位置で区切られていること (段落2の冒頭が混ざらない)。
        assert chunks[0].rstrip().endswith("段落1の内容。")

    def test_chunks_never_exceed_max_chars(self):
        # 改行も区切り文字もない長大なテキスト。
        text = "あ" * 10_000
        max_chars = EMBEDDING_MAX_INPUT_TOKENS * 2
        chunks = chunk_text(text, chunk_size=1000, overlap=0, max_chars=max_chars)
        for chunk in chunks:
            assert len(chunk) <= max_chars
            assert _estimate_tokens(chunk) <= EMBEDDING_MAX_INPUT_TOKENS

    def test_chunks_split_on_sentence_when_no_paragraph(self):
        sentences = ["これは文" + str(i) + "です。" for i in range(200)]
        text = "".join(sentences)
        chunks = chunk_text(text, chunk_size=300, overlap=0)
        # 各チャンクが「。」で終わる (文の途中で切れていない) こと。
        for chunk in chunks[:-1]:
            assert chunk.endswith("。"), f"Chunk does not end on sentence boundary: {chunk!r}"

    def test_overlap_carries_context(self):
        text = "段落1。" * 50 + "\n\n" + "段落2。" * 50
        chunks = chunk_text(text, chunk_size=200, overlap=50)
        # 隣接チャンクで何らかの文字列がオーバーラップすること (緩い検査)。
        if len(chunks) >= 2:
            tail = chunks[0][-30:]
            assert any(part in chunks[1] for part in [tail[-10:], tail[-5:]])

    def test_dense_japanese_chunk_token_estimate_fits(self):
        # 日本語密度の高い文字列でもトークン推定値が上限内に収まること。
        text = "粒子物理学における標準模型は量子場理論として記述される。" * 200
        chunks = chunk_text(text, chunk_size=1000, overlap=100)
        for chunk in chunks:
            assert _estimate_tokens(chunk) <= EMBEDDING_MAX_INPUT_TOKENS


class TestChunkPdfPages:
    def test_preserves_page_metadata(self):
        pages = [
            {"page": 1, "text": "ページ1の内容です。" * 30},
            {"page": 2, "text": "ページ2の内容です。" * 30},
        ]
        chunks = chunk_pdf_pages(pages, chunk_size=200, overlap=20)
        assert chunks
        for chunk in chunks:
            assert chunk["text"]
            assert chunk["page_start"] in (1, 2)
            assert chunk["page_end"] in (1, 2)
            assert chunk["page_start"] <= chunk["page_end"]

    def test_token_limit_per_chunk(self):
        pages = [{"page": 1, "text": "あ" * 20_000}]
        chunks = chunk_pdf_pages(pages, chunk_size=1000, overlap=100)
        for chunk in chunks:
            assert _estimate_tokens(chunk["text"]) <= EMBEDDING_MAX_INPUT_TOKENS

    def test_empty_pages_returns_empty(self):
        assert chunk_pdf_pages([{"page": 1, "text": ""}]) == []


class TestBatchBuilder:
    def test_total_tokens_per_batch_under_limit(self):
        # 各 1500 トークン相当 (3000 文字) のテキスト 30 件。
        texts = ["あ" * 3000 for _ in range(30)]
        batches = _build_embedding_batches(texts)
        for batch in batches:
            total = sum(_estimate_tokens(texts[i]) for i in batch)
            assert total <= EMBEDDING_MAX_BATCH_TOKENS

    def test_all_indices_covered_exactly_once(self):
        texts = ["短文" for _ in range(123)]
        batches = _build_embedding_batches(texts)
        flat = [i for b in batches for i in b]
        assert sorted(flat) == list(range(len(texts)))

    def test_max_items_respected(self):
        texts = ["x" for _ in range(EMBEDDING_BATCH_MAX_ITEMS * 3)]
        batches = _build_embedding_batches(texts)
        for batch in batches:
            assert len(batch) <= EMBEDDING_BATCH_MAX_ITEMS

    def test_oversize_single_input_isolated_in_own_batch(self):
        # 単独でバッチ上限を超える入力でも、1 件だけのバッチに分離されて送られる。
        huge = "あ" * (EMBEDDING_MAX_BATCH_TOKENS * 2 + 100) * 2
        texts = ["小さい"] * 5 + [huge] + ["小さい"] * 5
        batches = _build_embedding_batches(texts)
        # huge の index は 5。それを含むバッチが見つかる。
        huge_batch = [b for b in batches if 5 in b]
        assert huge_batch
        assert huge_batch[0] == [5]


class TestRegressionFromErrorLog:
    """ログにあった text_chunks=1115 / 20759 tokens のシナリオを再現。

    元の実装では 50 件固定バッチで 20000 トークンを超過していた。
    新実装ではトークン推定でバッチを分割することにより、
    バッチ合計が 20000 を下回ることを検証する。
    """

    def test_1115_chunks_each_within_input_limit(self):
        pages = [{"page": i + 1, "text": "標準模型の動力学" * 200} for i in range(50)]
        chunks = chunk_pdf_pages(pages, chunk_size=1000, overlap=100)
        for chunk in chunks:
            assert _estimate_tokens(chunk["text"]) <= EMBEDDING_MAX_INPUT_TOKENS

    def test_batches_under_request_limit(self):
        # チャンクの平均トークンが ~400 (元エラーの状況に近い) のケース。
        texts = ["あ" * 800 for _ in range(1115)]
        batches = _build_embedding_batches(texts)
        for batch in batches:
            total = sum(_estimate_tokens(texts[i]) for i in batch)
            assert total <= EMBEDDING_MAX_BATCH_TOKENS, (
                f"Batch token total {total} exceeds limit {EMBEDDING_MAX_BATCH_TOKENS}"
            )
