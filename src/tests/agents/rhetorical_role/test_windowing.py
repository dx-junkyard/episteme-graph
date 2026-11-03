"""Windowed judgement (IK-0699) and stage-internal checkpoint (IK-0710)."""
from __future__ import annotations

import json

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.paper_skeleton.schema import (
    LogicalBlock,
    PaperSkeletonResult,
    SKELETON_VERSION,
)
from episteme_graph.agents.rhetorical_role.agent import RhetoricalRoleAgent
from episteme_graph.agents.rhetorical_role.input_builder import RhetoricalRoleInputBuilder
from episteme_graph.agents.rhetorical_role.window import (
    DEFAULT_BLOCKS_PER_CALL,
    DEFAULT_WINDOW_MAX_CHARS,
    group_windows,
    resolve_window_config,
)

N_BLOCKS = 13


def _text(i: int) -> str:
    return f"We assume that quantity Q{i} is conserved in   regime {i}."


def _structure(n: int = N_BLOCKS) -> DocumentStructureResult:
    blocks = []
    for i in range(n):
        b = TypedBlock(f"b{i}", 1, i, _text(i), "body_paragraph")
        b.section_id = "sec_1"
        blocks.append(b)
    return DocumentStructureResult(
        document_id="doc_win",
        source_file="/tmp/x.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="T", pages=1),
        sections=[Section("sec_1", "Model", 1, 1, 1)],
        blocks=blocks,
    )


def _skeleton() -> PaperSkeletonResult:
    return PaperSkeletonResult(
        document_id="doc_win",
        skeleton_version=SKELETON_VERSION,
        cartridge_id=None,
        paper_goal={"text": "g", "evidence_block_ids": [], "reason": "", "confidence": 0.8},
        central_question={"text": "q", "evidence_block_ids": [], "reason": "", "confidence": 0.8},
        headline_claim={"text": "h", "evidence_block_ids": [], "reason": "", "confidence": 0.8},
        supporting_subclaims=[],
        logical_blocks=[LogicalBlock("l1", "model", "Model", ["sec_1"], [], "s", "r", 0.8)],
        excluded_regions=[],
        review_notes=[],
        confidence=0.8,
    )


def _entry(block_id: str, text: str, *, wrong_offsets: bool = True) -> dict:
    # The span text carries single spaces where the block has three, and the LLM
    # offsets are wrong: the server-side resolution must fix both per block.
    span_text = " ".join(text.split()) if wrong_offsets else text
    return {
        "block_id": block_id,
        "section_id": "sec_1",
        "backbone_block_type": "model",
        "span_annotations": [{
            "span_id": "span_001",
            "text": span_text,
            "char_start": 3 if wrong_offsets else 0,
            "char_end": 7 if wrong_offsets else len(text),
            "role_labels": ["assumption"],
            "is_claim_candidate": True,
            "is_reject_candidate": False,
            "confidence": 0.9,
            "reason": "fake",
        }],
    }


def _block_ids_in(messages: list[dict]) -> list[str]:
    content = messages[-1]["content"]
    if "## Blocks" in content:
        body = content.split("## Blocks", 1)[1].split("## Output Schema", 1)[0]
        return [line.split("### Block ", 1)[1].strip() for line in body.splitlines()
                if line.startswith("### Block ")]
    for line in content.splitlines():
        if line.startswith("block_id: "):
            return [line.split(": ", 1)[1].strip()]
    return []


class FakeLLM:
    """Answers window prompts with one entry per listed block, single prompts with one."""

    def __init__(self, *, broken_window_ids: set[str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.broken_window_ids = broken_window_ids or set()

    def generate(self, messages):
        ids = _block_ids_in(messages)
        self.calls.append(ids)
        is_window = "## Blocks" in messages[-1]["content"]
        texts = {f"b{i}": _text(i) for i in range(N_BLOCKS)}
        if is_window:
            if set(ids) & self.broken_window_ids:
                # Invalid: one block missing, another with an invalid role label.
                broken = [_entry(b, texts[b]) for b in ids[1:]]
                if broken:
                    broken[0]["span_annotations"][0]["role_labels"] = ["not_a_role"]
                return {"block_annotations": broken}
            return {"block_annotations": [_entry(b, texts[b]) for b in ids]}
        return _entry(ids[0], texts[ids[0]])


def _agent(fake: FakeLLM) -> RhetoricalRoleAgent:
    agent = RhetoricalRoleAgent()
    agent._llm_client = fake  # noqa: SLF001 — test seam
    return agent


def _assert_all_answered(result, n: int = N_BLOCKS) -> None:
    ids = [a.block_id for a in result.role_annotations]
    assert ids == [f"b{i}" for i in range(n)]
    for i, annotation in enumerate(result.role_annotations):
        span = annotation.span_annotations[0]
        assert span.role_labels == ["assumption"]
        # Offsets resolved server-side against *this* block (IK-0331).
        assert _text(i)[span.char_start:span.char_end] == span.text
        assert span.offset_correction is not None


def test_defaults_and_config_precedence(monkeypatch):
    monkeypatch.delenv("RHETORICAL_ROLE_BLOCKS_PER_CALL", raising=False)
    assert resolve_window_config(None) == (DEFAULT_BLOCKS_PER_CALL, DEFAULT_WINDOW_MAX_CHARS)
    monkeypatch.setenv("RHETORICAL_ROLE_BLOCKS_PER_CALL", "4")
    monkeypatch.setenv("RHETORICAL_ROLE_WINDOW_MAX_CHARS", "900")
    assert resolve_window_config(None) == (4, 900)
    assert resolve_window_config({"blocks_per_call": 2, "window_max_chars": 0}) == (2, 0)
    assert resolve_window_config({"blocks_per_call": 0})[0] == 1


def test_group_windows_respects_section_count_and_chars():
    inputs = RhetoricalRoleInputBuilder().build(_structure(), _skeleton())
    windows = group_windows(inputs, 6, 0)
    assert [len(w) for w in windows] == [6, 6, 1]
    size = len(_text(0))
    windows = group_windows(inputs, 6, size * 2)
    assert all(len(w) <= 2 for w in windows)
    assert sum(len(w) for w in windows) == N_BLOCKS
    inputs[3].section_id = "sec_2"
    windows = group_windows(inputs, 6, 0)
    assert [w[0].block_id for w in windows][:2] == ["b0", "b3"]


def test_13_blocks_window_6_makes_3_calls_and_answers_all():
    fake = FakeLLM()
    result = _agent(fake).run(_structure(), _skeleton(), config={"blocks_per_call": 6, "window_max_chars": 0})
    assert len(fake.calls) == 3
    assert [len(c) for c in fake.calls] == [6, 6, 1]
    _assert_all_answered(result)
    stats = result.summary_stats
    assert stats["llm_calls"] == 3
    assert stats["windows"] == 2  # the trailing 1-block window takes the single path
    assert stats["window_fallback_windows"] == 0
    assert stats["blocks_processed"] == N_BLOCKS
    assert stats["coverage"]["population"] == N_BLOCKS
    assert stats["coverage"]["processed"] == N_BLOCKS


def test_window_invalid_twice_falls_back_to_single_without_losing_blocks():
    fake = FakeLLM(broken_window_ids={"b0"})
    result = _agent(fake).run(_structure(), _skeleton(), config={"blocks_per_call": 6, "window_max_chars": 0})
    _assert_all_answered(result)
    window_calls = [c for c in fake.calls if len(c) > 1]
    # first window: initial + one repair, both invalid for b0/b1.
    assert window_calls[0] == ["b0", "b1", "b2", "b3", "b4", "b5"]
    # the repair re-asks only the unresolved blocks
    assert window_calls[1] == ["b0", "b1"]
    singles = [c[0] for c in fake.calls if len(c) == 1]
    assert "b0" in singles and "b1" in singles
    stats = result.summary_stats
    assert stats["window_fallback_windows"] == 1
    assert stats["window_fallback_blocks"] == 2
    assert stats["repair_failed_blocks"] == 0
    assert stats["llm_calls"] == len(fake.calls)


def test_checkpoint_resume_asks_only_unprocessed_blocks():
    config = {"blocks_per_call": 6, "window_max_chars": 0}
    first = FakeLLM()
    states: list[dict] = []
    _agent(first).run(_structure(), _skeleton(), config=config, checkpoint_callback=states.append)
    # Emulate an interruption after 7 of 13 blocks: keep the state of b0..b6.
    full = json.loads(json.dumps(states[-1]))
    partial = dict(full, blocks={k: v for k, v in full["blocks"].items() if int(k[1:]) < 7})
    assert len(partial["blocks"]) == 7

    second = FakeLLM()
    result = _agent(second).run(
        _structure(), _skeleton(), config=config, checkpoint=partial,
    )
    asked = [b for call in second.calls for b in call]
    assert sorted(asked, key=lambda b: int(b[1:])) == [f"b{i}" for i in range(7, 13)]
    assert len(asked) == 6
    _assert_all_answered(result)
    assert result.summary_stats["resumed_blocks"] == 7


def test_checkpoint_is_ignored_when_block_text_changed():
    config = {"blocks_per_call": 6, "window_max_chars": 0}
    states: list[dict] = []
    _agent(FakeLLM()).run(_structure(), _skeleton(), config=config, checkpoint_callback=states.append)
    tampered = json.loads(json.dumps(states[-1]))
    tampered["blocks"]["b0"]["fp"] = "stale"
    tampered["cartridge_id"] = None
    second = FakeLLM()
    _agent(second).run(_structure(), _skeleton(), config=config, checkpoint=tampered)
    assert [b for call in second.calls for b in call] == ["b0"]

    other_cartridge = dict(states[-1], cartridge_id="particle_physics")
    third = FakeLLM()
    _agent(third).run(_structure(), _skeleton(), config=config, checkpoint=other_cartridge)
    assert sum(len(c) for c in third.calls) == N_BLOCKS


def test_llm_error_blocks_are_not_reused_from_checkpoint():
    config = {"blocks_per_call": 1}

    class Failing(FakeLLM):
        def generate(self, messages):
            ids = _block_ids_in(messages)
            if ids == ["b2"]:
                self.calls.append(ids)
                raise RuntimeError("provider down")
            return super().generate(messages)

    states: list[dict] = []
    result = _agent(Failing()).run(_structure(), _skeleton(), config=config, checkpoint_callback=states.append)
    assert result.summary_stats["llm_error_blocks"] == 1
    second = FakeLLM()
    _agent(second).run(_structure(), _skeleton(), config=config, checkpoint=states[-1])
    assert second.calls == [["b2"]]


def test_window_prompt_names_every_block_and_demands_one_result_each():
    inputs = RhetoricalRoleInputBuilder().build(_structure(), _skeleton())
    from episteme_graph.agents.rhetorical_role.prompt import RhetoricalRolePromptFactory

    content = RhetoricalRolePromptFactory().build_window_messages(inputs[:3])[-1]["content"]
    for b in ("b0", "b1", "b2"):
        assert f"### Block {b}" in content
    assert "exactly one entry in block_annotations for each block_id: b0, b1, b2" in content
    assert "block_annotations" in content
