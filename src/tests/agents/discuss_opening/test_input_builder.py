"""DiscussOpeningInputBuilder / schema の境界（プロンプト膨張の防波堤・引用照合）。"""
from __future__ import annotations

from episteme_graph.agents.discuss_opening.input_builder import (
    DiscussOpeningInputBuilder,
    MAX_ASSUMPTIONS,
    MAX_AUTHOR_CHOICES,
    MAX_ITEM_CHARS,
    normalize_for_quote_match,
)
from episteme_graph.agents.discuss_opening.schema import (
    DEFAULT_LANGUAGE,
    DEFAULT_MAX_SEEDS,
    DiscussOpeningInput,
    DiscussOpeningResult,
)

BUILDER = DiscussOpeningInputBuilder()


def _payload(**kwargs) -> dict:
    payload = {
        "document_id": "doc-1",
        "thesis": {"central_question": "Q?", "central_thesis_text": "T."},
        "untested_assumptions": [{"statement": f"assumption {i}"} for i in range(20)],
        "author_choices": [
            {"operation": f"op_{i}", "reason": f"reason {i}"} for i in range(20)
        ],
    }
    payload.update(kwargs)
    return payload


class TestFromDict:
    def test_defaults_are_applied(self):
        item = DiscussOpeningInput.from_dict({"document_id": "doc-1"})
        assert item.language == DEFAULT_LANGUAGE
        assert item.max_seeds == DEFAULT_MAX_SEEDS
        assert item.has_material() is False

    def test_items_without_required_text_are_dropped(self):
        item = DiscussOpeningInput.from_dict(
            {
                "document_id": "doc-1",
                "untested_assumptions": [{"statement": ""}, {"statement": "kept"}],
                "author_choices": [{"operation": ""}, {"operation": "linearize"}],
            }
        )
        assert [a.statement for a in item.untested_assumptions] == ["kept"]
        assert [c.operation for c in item.author_choices] == ["linearize"]
        assert item.has_material() is True

    def test_invalid_max_seeds_falls_back_to_default(self):
        assert DiscussOpeningInput.from_dict({"max_seeds": "nope"}).max_seeds == DEFAULT_MAX_SEEDS
        assert DiscussOpeningInput.from_dict({"max_seeds": 0}).max_seeds == DEFAULT_MAX_SEEDS

    def test_result_roundtrips_through_dict(self):
        item = DiscussOpeningInput.from_dict(_payload())
        original = DiscussOpeningResult(document_id=item.document_id, language="ja")
        restored = DiscussOpeningResult.from_dict(original.to_dict())
        assert restored.to_dict() == original.to_dict()


class TestPromptBounds:
    def test_sections_are_capped(self):
        prepared = BUILDER.prepare_for_prompt(DiscussOpeningInput.from_dict(_payload()))
        assert len(prepared["untested_assumptions"]) == MAX_ASSUMPTIONS
        assert len(prepared["author_choices"]) == MAX_AUTHOR_CHOICES

    def test_long_text_is_truncated_without_mutating_the_input(self):
        long_statement = "x" * (MAX_ITEM_CHARS + 200)
        item = DiscussOpeningInput.from_dict(
            _payload(untested_assumptions=[{"statement": long_statement}])
        )
        prepared = BUILDER.prepare_for_prompt(item)

        assert len(prepared["untested_assumptions"][0]["statement"]) <= MAX_ITEM_CHARS + 1
        assert item.untested_assumptions[0].statement == long_statement


class TestQuoteHaystack:
    def test_haystack_uses_untruncated_source_text(self):
        long_statement = "y" * (MAX_ITEM_CHARS + 50)
        item = DiscussOpeningInput.from_dict(
            _payload(untested_assumptions=[{"statement": long_statement}])
        )
        assert long_statement in BUILDER.quote_haystack(item)

    def test_normalization_collapses_whitespace_only(self):
        assert normalize_for_quote_match(" a\n b\tc ") == "a b c"
        # 大文字小文字・記号は保存する（原文をそのまま引用させるため）
        assert normalize_for_quote_match("The Detector's response.") == "The Detector's response."
