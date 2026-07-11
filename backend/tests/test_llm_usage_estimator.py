"""core/llm_usage/estimator.py — 決定論的トークン推計のテスト。

観点: 決定性 / CJK・ASCII 式の検算 / messages 框 / schema 加算 / vision（寸法あり無し混在）/
レンジ境界 / tiktoken 不在時のフォールバック。
"""

from __future__ import annotations

import json
import math
import sys

import pytest

from core.llm_usage import estimator

# --- 独立実装した期待値計算（設計書 §4.2 の式をテスト側でも再実装し検算する） ---

_CJK_RANGES = (
    (0x3000, 0x303F),
    (0x3040, 0x309F),
    (0x30A0, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),
)


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _CJK_RANGES)


def _expected_heuristic_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return math.ceil(cjk * 1.0 + other / 4.0)


@pytest.fixture(autouse=True)
def _force_tiktoken_absent(monkeypatch):
    """このテストファイルは常にヒューリスティック経路を検証するため、
    実行環境に tiktoken がインストールされていても常に ImportError を再現する。
    """
    monkeypatch.setitem(sys.modules, "tiktoken", None)
    yield


class TestHeuristicFormula:
    def test_pure_ascii(self):
        text = "a" * 40
        est = estimator.estimate_text_tokens(text)
        assert est.tokens == _expected_heuristic_tokens(text) == math.ceil(40 / 4.0)
        assert est.usage_source == "estimated_heuristic"

    def test_pure_cjk_hiragana(self):
        text = "あ" * 10
        est = estimator.estimate_text_tokens(text)
        assert est.tokens == _expected_heuristic_tokens(text) == 10

    def test_pure_cjk_kanji_and_fullwidth_punct(self):
        text = "日本語、テスト。"
        est = estimator.estimate_text_tokens(text)
        assert est.tokens == _expected_heuristic_tokens(text)

    def test_mixed_cjk_and_ascii(self):
        text = "あ" * 3 + "a" * 8
        est = estimator.estimate_text_tokens(text)
        assert est.tokens == _expected_heuristic_tokens(text) == math.ceil(3 * 1.0 + 8 / 4.0) == 5

    def test_empty_text(self):
        est = estimator.estimate_text_tokens("")
        assert est.tokens == 0
        assert est.low == 0
        assert est.high == 0

    def test_none_text_is_treated_as_empty(self):
        est = estimator.estimate_text_tokens(None)  # type: ignore[arg-type]
        assert est.tokens == 0


class TestDeterminism:
    def test_same_text_same_result(self):
        text = "テスト用のテキストです。Some ascii mixed in too."
        first = estimator.estimate_text_tokens(text)
        second = estimator.estimate_text_tokens(text)
        assert first == second

    def test_same_messages_same_result(self):
        messages = [
            {"role": "system", "content": "あなたは親切なアシスタントです。"},
            {"role": "user", "content": "こんにちは、質問があります。"},
        ]
        first = estimator.estimate_messages_tokens(messages, model="gpt-4o")
        second = estimator.estimate_messages_tokens(messages, model="gpt-4o")
        assert first == second

    def test_same_vision_same_result(self):
        dims = [(1024, 1024), None, (2048, 4096)]
        first = estimator.estimate_vision_tokens(dims)
        second = estimator.estimate_vision_tokens(dims)
        assert first == second


class TestMessagesFraming:
    def test_overhead_and_priming(self):
        messages = [
            {"role": "system", "content": "a" * 40},
            {"role": "user", "content": "あ" * 10},
        ]
        est = estimator.estimate_messages_tokens(messages)
        expected = (
            _expected_heuristic_tokens("a" * 40)
            + _expected_heuristic_tokens("あ" * 10)
            + estimator.PER_MESSAGE_OVERHEAD * len(messages)
            + estimator.PRIMING_TOKENS
        )
        assert est.tokens == expected == 31

    def test_empty_messages_only_priming(self):
        est = estimator.estimate_messages_tokens([])
        assert est.tokens == estimator.PRIMING_TOKENS

    def test_vision_style_list_content_only_counts_text_parts(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
                ],
            }
        ]
        est = estimator.estimate_messages_tokens(messages)
        expected = (
            _expected_heuristic_tokens("hello")
            + estimator.PER_MESSAGE_OVERHEAD * 1
            + estimator.PRIMING_TOKENS
        )
        assert est.tokens == expected

    def test_schema_tokens_are_added(self):
        messages = [{"role": "user", "content": "hello"}]
        schema = {"type": "object", "properties": {"foo": {"type": "string"}}}

        without_schema = estimator.estimate_messages_tokens(messages)
        with_schema = estimator.estimate_messages_tokens(messages, schema=schema)

        schema_text = json.dumps(schema, ensure_ascii=False)
        expected_schema_tokens = _expected_heuristic_tokens(schema_text)

        assert with_schema.tokens == without_schema.tokens + expected_schema_tokens
        assert with_schema.tokens > without_schema.tokens


class TestVisionEstimate:
    def test_known_dims_1024_square_matches_openai_reference(self):
        # OpenAI 公式ドキュメントの参照値: 1024x1024 (detail=high) = 765 トークン
        est = estimator.estimate_vision_tokens([(1024, 1024)])
        assert est.tokens == 765
        assert est.usage_source == "estimated_heuristic"

    def test_known_dims_tall_image_matches_openai_reference(self):
        # OpenAI 公式ドキュメントの参照値: 2048x4096 (detail=high) = 1105 トークン
        est = estimator.estimate_vision_tokens([(2048, 4096)])
        assert est.tokens == 1105

    def test_unknown_dims_uses_flat_constant(self):
        est = estimator.estimate_vision_tokens([None])
        assert est.tokens == estimator.FLAT_IMAGE_TOKENS == 765

    def test_mixed_known_and_unknown_dims(self):
        est = estimator.estimate_vision_tokens([(1024, 1024), None])
        assert est.tokens == 765 + estimator.FLAT_IMAGE_TOKENS

    def test_empty_image_list(self):
        est = estimator.estimate_vision_tokens([])
        assert est.tokens == 0


class TestRangeBoundaries:
    def test_range_uses_floor_0_6_and_ceil_1_4(self):
        est = estimator.estimate_text_tokens("a" * 40)  # tokens = 10
        assert est.tokens == 10
        assert est.low == math.floor(0.6 * 10) == 6
        assert est.high == math.ceil(1.4 * 10) == 14

    def test_range_at_tokens_zero(self):
        est = estimator.estimate_text_tokens("")
        assert (est.low, est.tokens, est.high) == (0, 0, 0)

    def test_range_at_small_token_count(self):
        est = estimator.estimate_text_tokens("a" * 4)  # tokens = 1
        assert est.tokens == 1
        assert est.low == 0
        assert est.high == 2


class TestTiktokenFallback:
    def test_absent_falls_back_to_heuristic(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "tiktoken", None)
        est = estimator.estimate_text_tokens("hello world", model="gpt-4o")
        assert est.usage_source == "estimated_heuristic"
        assert est.tokens == _expected_heuristic_tokens("hello world")

    def test_present_uses_tokenizer(self, monkeypatch):
        class _FakeEncoding:
            def encode(self, text):
                return list(text)

        class _FakeTiktokenModule:
            def encoding_for_model(self, model):
                raise KeyError(model)

            def get_encoding(self, name):
                return _FakeEncoding()

        monkeypatch.setitem(sys.modules, "tiktoken", _FakeTiktokenModule())
        est = estimator.estimate_text_tokens("hello", model="gpt-4o")
        assert est.usage_source == "estimated_tokenizer"
        assert est.tokens == 5

    def test_tokenizer_failure_falls_back_to_heuristic(self, monkeypatch):
        class _BrokenTiktokenModule:
            def encoding_for_model(self, model):
                raise RuntimeError("boom")

            def get_encoding(self, name):
                raise RuntimeError("boom")

        monkeypatch.setitem(sys.modules, "tiktoken", _BrokenTiktokenModule())
        est = estimator.estimate_text_tokens("hello", model="gpt-4o")
        assert est.usage_source == "estimated_heuristic"
