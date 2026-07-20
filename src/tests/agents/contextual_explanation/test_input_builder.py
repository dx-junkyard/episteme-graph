"""Tests for ContextualExplanationInputBuilder (batching + prompt bounding)."""
from __future__ import annotations

from episteme_graph.agents.contextual_explanation.input_builder import (
    MAX_CONTEXT_ITEM_CHARS,
    MAX_CONTEXT_ITEMS_PER_ELEMENT,
    MAX_LOCAL_TEXT_CHARS,
    ContextualExplanationInputBuilder,
)
from episteme_graph.agents.contextual_explanation.schema import (
    DEFAULT_MAX_ELEMENTS_PER_CALL,
    ElementExplanationInput,
)


def _element(element_id: str, **kwargs) -> ElementExplanationInput:
    defaults = dict(
        element_type="theory_claim",
        element_id=element_id,
        local_text="A claim about the system.",
        upper_context=[],
        lower_context=[],
        library_excerpt=None,
    )
    defaults.update(kwargs)
    return ElementExplanationInput(**defaults)


def test_build_batches_uses_default_size():
    elements = [_element(f"e{i}") for i in range(DEFAULT_MAX_ELEMENTS_PER_CALL + 3)]
    batches = ContextualExplanationInputBuilder().build_batches(elements)
    assert len(batches) == 2
    assert len(batches[0]) == DEFAULT_MAX_ELEMENTS_PER_CALL
    assert len(batches[1]) == 3


def test_build_batches_respects_custom_size():
    elements = [_element(f"e{i}") for i in range(10)]
    batches = ContextualExplanationInputBuilder().build_batches(elements, max_elements_per_call=4)
    assert [len(b) for b in batches] == [4, 4, 2]
    # order preserved
    assert [e.element_id for e in batches[0]] == ["e0", "e1", "e2", "e3"]


def test_build_batches_drops_elements_without_id():
    elements = [_element("e0"), _element("")]
    batches = ContextualExplanationInputBuilder().build_batches(elements)
    assert sum(len(b) for b in batches) == 1


def test_build_batches_falls_back_to_default_for_non_positive_size():
    elements = [_element(f"e{i}") for i in range(3)]
    batches = ContextualExplanationInputBuilder().build_batches(elements, max_elements_per_call=0)
    assert len(batches) == 1
    assert len(batches[0]) == 3


def test_prepare_for_prompt_truncates_local_text():
    long_text = "x" * (MAX_LOCAL_TEXT_CHARS + 500)
    item = _element("e0", local_text=long_text)
    prepared = ContextualExplanationInputBuilder().prepare_for_prompt(item)
    assert len(prepared["local_text"]) <= MAX_LOCAL_TEXT_CHARS + 1  # +1 for the ellipsis mark
    assert prepared["local_text"] != long_text
    # original object is untouched
    assert item.local_text == long_text


def test_prepare_for_prompt_bounds_context_item_count_and_length():
    upper = [
        {"relation": "supports", "kind": "thesis", "text": "y" * (MAX_CONTEXT_ITEM_CHARS + 100)}
        for _ in range(MAX_CONTEXT_ITEMS_PER_ELEMENT + 5)
    ]
    item = _element("e0", upper_context=upper)
    prepared = ContextualExplanationInputBuilder().prepare_for_prompt(item)
    assert len(prepared["upper_context"]) == MAX_CONTEXT_ITEMS_PER_ELEMENT
    for entry in prepared["upper_context"]:
        assert len(entry["text"]) <= MAX_CONTEXT_ITEM_CHARS + 1


def test_prepare_for_prompt_keeps_none_library_excerpt():
    item = _element("e0", library_excerpt=None)
    prepared = ContextualExplanationInputBuilder().prepare_for_prompt(item)
    assert prepared["library_excerpt"] is None


def test_prepare_for_prompt_bounds_library_excerpt_text():
    excerpt = {
        "entry_id": "lib_1",
        "version_no": 2,
        "name": "Effective Hamiltonian",
        "summary": "s" * 2000,
        "body_excerpt": "b" * 2000,
    }
    item = _element("e0", library_excerpt=excerpt)
    prepared = ContextualExplanationInputBuilder().prepare_for_prompt(item)
    assert prepared["library_excerpt"]["entry_id"] == "lib_1"
    assert len(prepared["library_excerpt"]["summary"]) < 2000
    assert len(prepared["library_excerpt"]["body_excerpt"]) < 2000
