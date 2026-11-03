"""Tests for the shared ordering / stratified-sampling helpers (P0-1 の正本)."""
from dataclasses import dataclass

import pytest

from episteme_graph.agents.stratified_sampling import (
    NO_SECTION_KEY,
    SORT_KEY_ORDER,
    SORT_KEY_PAGE_ORDER,
    order_blocks,
    order_is_unique,
    resolve_limit,
    select_indices,
    unprocessed_section_entries,
)


@dataclass
class _Item:
    name: str
    order: int = 0
    page: int = 1
    section_id: str | None = None


def _section_key(item: _Item) -> str:
    return item.section_id or NO_SECTION_KEY


# ── resolve_limit ────────────────────────────────────────────────────────────

def test_resolve_limit_defaults_to_no_limit(monkeypatch):
    monkeypatch.delenv("X_MAX", raising=False)
    assert resolve_limit(None, "max_x", "X_MAX") == 0


def test_config_wins_over_env(monkeypatch):
    monkeypatch.setenv("X_MAX", "9")
    assert resolve_limit({"max_x": 3}, "max_x", "X_MAX") == 3


def test_env_is_used_when_config_is_absent(monkeypatch):
    monkeypatch.setenv("X_MAX", " 7 ")
    assert resolve_limit({}, "max_x", "X_MAX") == 7


@pytest.mark.parametrize("bad", ["", "abc", "-3", "0", None])
def test_invalid_or_non_positive_values_mean_no_limit(monkeypatch, bad):
    """設定ミスで静かに打ち切らない（不正値は上限なしに倒す）。"""
    monkeypatch.delenv("X_MAX", raising=False)
    assert resolve_limit({"max_x": bad}, "max_x", "X_MAX") == 0


@pytest.mark.parametrize("explicit_zero", [0, "0", "-1"])
def test_explicit_zero_turns_off_a_non_zero_default(monkeypatch, explicit_zero):
    """既定の上限がある設定（inline 32 等）を運用側から外せる。"""
    monkeypatch.delenv("X_MAX", raising=False)
    assert resolve_limit({"max_x": explicit_zero}, "max_x", "X_MAX", default=32) == 0
    monkeypatch.setenv("X_MAX", str(explicit_zero))
    assert resolve_limit({}, "max_x", "X_MAX", default=32) == 0


@pytest.mark.parametrize("unreadable", ["abc", ""])
def test_unreadable_values_keep_the_default(monkeypatch, unreadable):
    """タイポで既定の弁が黙って外れない（「未設定」として扱う）。"""
    monkeypatch.delenv("X_MAX", raising=False)
    assert resolve_limit({"max_x": unreadable}, "max_x", "X_MAX", default=32) == 32


# ── ordering ─────────────────────────────────────────────────────────────────

def test_unique_order_is_used_alone():
    items = [_Item("a", order=2, page=1), _Item("b", order=1, page=9)]
    ordered, key = order_blocks(items)
    assert key == SORT_KEY_ORDER
    assert [i.name for i in ordered] == ["b", "a"]


def test_duplicate_order_falls_back_to_page_order():
    items = [_Item("a", order=1, page=2), _Item("b", order=1, page=1)]
    assert not order_is_unique(items)
    ordered, key = order_blocks(items)
    assert key == SORT_KEY_PAGE_ORDER
    assert [i.name for i in ordered] == ["b", "a"]


def test_broken_grobid_pages_do_not_reshuffle_the_document():
    """page=1 のまま残った突合失敗ブロックを文書先頭に集めない（F-1 (c)）。"""
    items = [
        _Item("intro", order=0, page=1),
        _Item("late-but-page1", order=50, page=1),
        _Item("middle", order=10, page=3),
    ]
    ordered, key = order_blocks(items)
    assert key == SORT_KEY_ORDER
    assert [i.name for i in ordered] == ["intro", "middle", "late-but-page1"]


# ── stratified selection ─────────────────────────────────────────────────────

def _sectioned(counts: dict[str, int]) -> list[_Item]:
    items: list[_Item] = []
    order = 0
    for section, n in counts.items():
        for i in range(n):
            items.append(_Item(f"{section}_{i}", order=order, section_id=section))
            order += 1
    return items


def test_no_limit_selects_everything():
    items = _sectioned({"s1": 3, "s2": 2})
    targets = list(range(len(items)))
    assert select_indices(items, targets, 0, section_key_of=_section_key) == targets


def test_limit_above_population_selects_everything():
    items = _sectioned({"s1": 2})
    targets = list(range(len(items)))
    assert select_indices(items, targets, 99, section_key_of=_section_key) == targets


def test_every_section_gets_at_least_one_slot():
    """先頭切り捨てなら結論節が 0 件になるケース（F-1 (b)）。"""
    items = _sectioned({"intro": 20, "method": 20, "conclusion": 2})
    targets = list(range(len(items)))
    selected = select_indices(items, targets, 6, section_key_of=_section_key)
    sections = {items[i].section_id for i in selected}
    assert sections == {"intro", "method", "conclusion"}
    assert len(selected) == 6
    assert selected == sorted(selected)


def test_more_sections_than_slots_prefers_the_largest_sections():
    items = _sectioned({"a": 1, "b": 5, "c": 3})
    targets = list(range(len(items)))
    selected = select_indices(items, targets, 2, section_key_of=_section_key)
    assert {items[i].section_id for i in selected} == {"b", "c"}


def test_selection_is_deterministic():
    items = _sectioned({"s1": 7, "s2": 4, "s3": 9})
    targets = list(range(len(items)))
    first = select_indices(items, targets, 8, section_key_of=_section_key)
    second = select_indices(items, targets, 8, section_key_of=_section_key)
    assert first == second


def test_items_without_a_section_are_one_pseudo_section():
    items = [_Item("x", order=0), _Item("y", order=1), _Item("z", order=2, section_id="s1")]
    targets = [0, 1, 2]
    selected = select_indices(items, targets, 2, section_key_of=_section_key)
    assert {items[i].section_id for i in selected} == {None, "s1"}


# ── unprocessed sections ─────────────────────────────────────────────────────

def test_unprocessed_sections_lists_sections_with_dropped_items():
    items = _sectioned({"s1": 2, "s2": 2})
    targets = list(range(4))
    entries = unprocessed_section_entries(
        items, targets, [0, 2], section_key_of=_section_key,
        title_of=lambda key: f"Title {key}",
    )
    assert [e["section_id"] for e in entries] == ["s1", "s2"]
    assert entries[0]["title"] == "Title s1"


def test_unprocessed_sections_is_empty_when_nothing_is_dropped():
    items = _sectioned({"s1": 2})
    assert unprocessed_section_entries(
        items, [0, 1], [0, 1], section_key_of=_section_key
    ) == []
