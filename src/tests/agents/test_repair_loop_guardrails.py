"""Guardrail: the LLM repair loop lives in one place.

Eleven agents used to carry a hand-written ``for attempt in range(...)`` repair
loop. Ten of them now delegate to ``agents/llm_step.run_repair_loop``; the
exceptions below are deliberate and documented. A new agent that copy-pastes a
loop instead of calling the shared runner fails this test.
"""
from __future__ import annotations

import pathlib

import pytest

AGENTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "episteme_graph" / "agents"

RUNNER_IMPORT = "from episteme_graph.agents.llm_step import"

# Agents built on ``backend/core/llm_worker/repair.run_with_repair`` instead.
# That runner has the opposite exception semantics (continue, not break) and a
# different client protocol (``complete_json(str)``), so it must not be merged
# with ``llm_step`` — see the ``llm_step`` module docstring.
WORKER_RUNNER_AGENTS = {"discuss_opening", "landscape_placement"}

# ``apparatus_semantics/iterative.py`` runs a multi-stage state machine
# (hypothesis → observation → alignment → rescan) with its own per-stage
# ceilings, not the single generate/parse/validate step this runner models.
NON_STEP_LOOP_FILES = {
    AGENTS_DIR / "apparatus_semantics" / "iterative.py",
    AGENTS_DIR / "llm_step.py",  # the one loop everyone else delegates to
}

# ``contextual_explanation`` batches several elements per call and repairs only
# the still-invalid element ids, accepting valid siblings as it goes. Deferred
# from the consolidation: its partial-accept loop is not a single-item step.
BATCHED_REPAIR_AGENTS = {"contextual_explanation"}


def _repair_modules() -> list[pathlib.Path]:
    paths = sorted(AGENTS_DIR.glob("*/repair.py"))
    assert paths, "no agent repair.py files found — did the layout change?"
    return paths


@pytest.mark.parametrize("path", _repair_modules(), ids=lambda p: p.parent.name)
def test_repairers_use_the_shared_runner(path: pathlib.Path):
    agent = path.parent.name
    source = path.read_text(encoding="utf-8")

    if agent in WORKER_RUNNER_AGENTS:
        assert "for attempt in range(" not in source
        assert RUNNER_IMPORT not in source
        return

    if agent in BATCHED_REPAIR_AGENTS:
        # Documented exception; still must not grow a second single-item loop.
        assert source.count("for attempt in range(") == 1
        return

    assert "for attempt in range(" not in source, (
        f"{agent}/repair.py still hand-rolls the repair loop; "
        "call episteme_graph.agents.llm_step.run_repair_loop instead"
    )
    assert RUNNER_IMPORT in source, (
        f"{agent}/repair.py must import the shared runner from llm_step"
    )
    assert "run_repair_loop(" in source


def test_no_other_agent_module_hand_rolls_the_loop():
    offenders = [
        path
        for path in sorted(AGENTS_DIR.rglob("*.py"))
        if path.name != "repair.py"
        and path not in NON_STEP_LOOP_FILES
        and "for attempt in range(" in path.read_text(encoding="utf-8")
    ]

    assert offenders == [], (
        "repair loops belong in repair.py via llm_step.run_repair_loop: "
        f"{[str(p.relative_to(AGENTS_DIR)) for p in offenders]}"
    )


def test_shared_attempt_ceiling_is_the_single_source_of_truth():
    """Migrated modules keep ``_MAX_REPAIR_ATTEMPTS`` as an alias, not a literal."""
    from episteme_graph.agents.llm_step import MAX_REPAIR_ATTEMPTS

    for path in _repair_modules():
        agent = path.parent.name
        if agent in WORKER_RUNNER_AGENTS or agent in BATCHED_REPAIR_AGENTS:
            continue
        source = path.read_text(encoding="utf-8")
        assert "_MAX_REPAIR_ATTEMPTS = MAX_REPAIR_ATTEMPTS" in source, agent

    assert MAX_REPAIR_ATTEMPTS == 2
