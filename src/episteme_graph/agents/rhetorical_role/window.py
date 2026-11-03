"""Windowed judgement + stage-internal checkpoint for RhetoricalRoleAgent.

IK-0699 (2026-10-22): the stage used to make one LLM call per body block, so the
number of calls grew with the population once the 64-block cap was removed
(P0-1 / J-3). Consecutive body blocks of the same section are now grouped into a
*window* bounded by a block count and a character budget, and the LLM judges the
whole window in one call. The per-block output schema is unchanged
(``BlockRoleAnnotation`` per block) — only the request unit changed.

* The window is never a reason to lose a block. A block the window answer does
  not cover (missing / invalid after one window repair) is re-asked through the
  existing single-block path, and that path keeps its own repair loop and its
  whole-block ``unknown`` fallback (P4).
* Span offsets stay server-side and per block: each per-block entry of the window
  answer goes through ``repair._parse_block_annotation`` with *that* block's
  ``RoleLLMInput``, i.e. ``resolve_span_offsets`` locates the span text inside
  that block only (IK-0331).
* ``blocks_per_call <= 1`` turns windowing off (every block takes the
  single-block path, exactly as before).

IK-0710 (2026-10-22): the agent can emit a checkpoint (``checkpoint_callback``)
after every window and accept one back (``checkpoint``). A block present in the
checkpoint with a matching fingerprint is never re-asked. The checkpoint format
is owned here; the orchestrator stores it opaquely.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import Any

from episteme_graph.agents.stratified_sampling import resolve_limit

from .schema import BlockRoleAnnotation, RoleLLMInput, SpanAnnotation

#: Blocks per LLM call (``config["blocks_per_call"]`` > env > default).
BLOCKS_PER_CALL_ENV = "RHETORICAL_ROLE_BLOCKS_PER_CALL"
DEFAULT_BLOCKS_PER_CALL = 6
#: Character budget of the block texts of one window
#: (``config["window_max_chars"]`` > env > default; 0 = no character budget).
WINDOW_MAX_CHARS_ENV = "RHETORICAL_ROLE_WINDOW_MAX_CHARS"
DEFAULT_WINDOW_MAX_CHARS = 6000
#: Window repair attempts before the unresolved blocks fall back to the
#: single-block path (initial call + this many repairs).
WINDOW_REPAIR_ATTEMPTS = 1

#: Checkpoint format version. Bump when the per-block entry or the prompt that
#: produced it changes meaning — older checkpoints are then ignored (re-asked).
CHECKPOINT_VERSION = 1

# Per-block outcomes (counted into ``summary_stats``).
OUTCOME_OK = "ok"
OUTCOME_REPAIRED = "repaired"
OUTCOME_REPAIR_FAILED = "repair_failed"
OUTCOME_LLM_ERROR = "llm_error"


def resolve_window_config(config: dict | None) -> tuple[int, int]:
    """``(blocks_per_call, window_max_chars)``; ``blocks_per_call`` is at least 1."""
    blocks_per_call = resolve_limit(
        config, "blocks_per_call", BLOCKS_PER_CALL_ENV, default=DEFAULT_BLOCKS_PER_CALL
    )
    max_chars = resolve_limit(
        config, "window_max_chars", WINDOW_MAX_CHARS_ENV, default=DEFAULT_WINDOW_MAX_CHARS
    )
    return max(1, blocks_per_call), max_chars


def group_windows(
    llm_inputs: list[RoleLLMInput],
    blocks_per_call: int,
    max_chars: int,
) -> list[list[RoleLLMInput]]:
    """Group consecutive inputs of the same section into windows.

    A window closes when the section changes, when it holds ``blocks_per_call``
    blocks, or when adding the next block would exceed ``max_chars`` (0 = no
    character budget). A single block longer than the budget forms its own
    window. Order is preserved and every input lands in exactly one window.
    """
    windows: list[list[RoleLLMInput]] = []
    current: list[RoleLLMInput] = []
    current_chars = 0
    for llm_input in llm_inputs:
        size = len(llm_input.block_text or "")
        if current:
            same_section = (current[-1].section_id or "") == (llm_input.section_id or "")
            fits_count = len(current) < blocks_per_call
            fits_chars = max_chars <= 0 or current_chars + size <= max_chars
            # The window answer is keyed by block_id, so one block_id per window.
            unique_id = all(str(i.block_id) != str(llm_input.block_id) for i in current)
            if not (same_section and fits_count and fits_chars and unique_id):
                windows.append(current)
                current, current_chars = [], 0
        current.append(llm_input)
        current_chars += size
    if current:
        windows.append(current)
    return windows


def block_fingerprint(llm_input: RoleLLMInput) -> str:
    """Content fingerprint of one block input (block id + text + format version)."""
    material = "\x00".join(
        [str(CHECKPOINT_VERSION), str(llm_input.block_id), llm_input.block_text or ""]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def annotation_from_dict(data: dict) -> BlockRoleAnnotation:
    spans = [SpanAnnotation(**s) for s in data.get("span_annotations", [])]
    return BlockRoleAnnotation(
        block_id=data["block_id"],
        section_id=data.get("section_id"),
        backbone_block_type=data.get("backbone_block_type"),
        span_annotations=spans,
    )


def block_keys(llm_inputs: list[RoleLLMInput]) -> list[str]:
    """Checkpoint key per input: the block_id, suffixed ``#n`` if it repeats."""
    seen: dict[str, int] = {}
    keys: list[str] = []
    for llm_input in llm_inputs:
        base = str(llm_input.block_id)
        seen[base] = seen.get(base, 0) + 1
        keys.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return keys


def restore_checkpoint(
    checkpoint: dict | None,
    llm_inputs: list[RoleLLMInput],
    keys: list[str],
    cartridge_id: str | None,
) -> dict[str, dict]:
    """Return ``{key: entry}`` for blocks the checkpoint already answered.

    An entry is reused only when the format version and the cartridge match and
    the block fingerprint matches the current input. ``llm_error`` entries are
    not reused (the error was most likely transient — e.g. the server restart
    that interrupted the stage), so those blocks are asked again.
    """
    if not isinstance(checkpoint, dict):
        return {}
    if checkpoint.get("version") != CHECKPOINT_VERSION:
        return {}
    if (checkpoint.get("cartridge_id") or None) != (cartridge_id or None):
        return {}
    stored = checkpoint.get("blocks")
    if not isinstance(stored, dict):
        return {}
    restored: dict[str, dict] = {}
    for llm_input, key in zip(llm_inputs, keys):
        entry = stored.get(key)
        if not isinstance(entry, dict):
            continue
        if entry.get("fp") != block_fingerprint(llm_input):
            continue
        if entry.get("outcome") == OUTCOME_LLM_ERROR:
            continue
        annotation = entry.get("annotation")
        if not isinstance(annotation, dict):
            continue
        try:
            annotation_from_dict(annotation)
        except (TypeError, KeyError):
            continue
        restored[key] = entry
    return restored


def checkpoint_entry(
    llm_input: RoleLLMInput,
    annotation: BlockRoleAnnotation,
    outcome: str,
) -> dict[str, Any]:
    return {
        "fp": block_fingerprint(llm_input),
        "outcome": outcome,
        "annotation": asdict(annotation),
    }


def build_checkpoint(cartridge_id: str | None, entries: dict[str, dict]) -> dict:
    return {
        "version": CHECKPOINT_VERSION,
        "cartridge_id": cartridge_id or None,
        "blocks": dict(entries),
    }
