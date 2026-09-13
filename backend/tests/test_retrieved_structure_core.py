"""知識の転用層 P4-2 — 検索由来の構造 1 hop の解決器（純関数）のテスト。

正本: ``docs/features/knowledge_transfer_design.md`` §5（SA層 kind
``retrieved_structure``）。SA層の契約は
``docs/features/assistant_screen_adapter_design.md`` §11.16。

固定する事実:

- 事実文の文言（出典番号 → 主張本文 + 主張の種類ラベル / 理論の骨格の段階）
- 上限（出典ごと2主張・全体8行・主張本文120字）
- 内部 ID を含む文は捨てる（KT7 / SA4）
- 数値（一致度・件数・confidence）を出さない（KT7）
- stage を引けないノードは**何も出さない**（英語の内部表示名を学習者へ渡さない）
- 入力を mutate しない / 例外を外へ出さない / 空入力は空リスト
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.assistant_context import (  # noqa: E402
    BLOCK_HEADER_RETRIEVED,
    MAX_BLOCK_CHARS_LEARNING,
    SCREEN_LEARNING,
    normalize_screen_context,
    render_block,
    resolve,
)
from core.assistant_context.schema import (  # noqa: E402
    MAX_LEARNING_RETRIEVED_CLAIM_CHARS,
    MAX_LEARNING_RETRIEVED_CLAIMS_PER_SOURCE,
    MAX_LEARNING_RETRIEVED_FACTS,
)
from core.assistant_context.resolvers.learning import (  # noqa: E402
    resolve_retrieved_structure,
)

KINDS = ("retrieved_structure",)


def _ctx():
    return normalize_screen_context({"screen": SCREEN_LEARNING})


def _node(label: str = "Theory basis", display: str = "Theory basis: 重力ポテンシャルの定義"):
    return {"label": label, "display_label": display}


def _sources(*entries) -> dict:
    if not entries:
        entries = (
            {
                "index": "1",
                "claims": [
                    {
                        "text": "時間遅延はレンズポテンシャルの差で決まる。",
                        "claim_type": "definition",
                        "node": _node(),
                    }
                ],
            },
        )
    return {"retrieved_structure": {"sources": list(entries)}}


def _facts(sources: dict) -> list[str]:
    return resolve_retrieved_structure(_ctx(), sources)


# ---------------------------------------------------------------------------
# 1. 事実文の文言（§5）
# ---------------------------------------------------------------------------


class TestFactWording:
    def test_claim_fact_binds_the_citation_number_to_the_claim_text(self):
        facts = _facts(_sources())
        assert facts[0] == (
            "[出典1] の箇所には次の主張が構造化されています: "
            "「時間遅延はレンズポテンシャルの差で決まる。」（主張の種類: 定義）"
        )

    def test_node_fact_names_the_theory_stage_in_japanese(self):
        facts = _facts(_sources())
        assert facts[1] == (
            "この主張は、理論の骨格では『理論の土台』の段階に置かれています"
            "（重力ポテンシャルの定義）"
        )

    def test_unknown_claim_type_drops_only_the_parenthetical(self):
        facts = _facts(
            _sources(
                {
                    "index": "2",
                    "claims": [{"text": "ある主張。", "claim_type": "no_such_type"}],
                }
            )
        )
        assert facts == ["[出典2] の箇所には次の主張が構造化されています: 「ある主張。」"]

    def test_node_without_a_theory_object_states_only_the_stage(self):
        facts = _facts(
            _sources(
                {
                    "index": "1",
                    "claims": [
                        {
                            "text": "ある主張。",
                            "claim_type": "definition",
                            "node": _node(display="Theory basis"),
                        }
                    ],
                }
            )
        )
        assert facts[1] == "この主張は、理論の骨格では『理論の土台』の段階に置かれています"

    def test_facts_render_into_their_own_block_with_the_retrieved_header(self):
        block = render_block(
            _facts(_sources()),
            header=BLOCK_HEADER_RETRIEVED,
            max_chars=MAX_BLOCK_CHARS_LEARNING,
        )
        assert block.startswith(BLOCK_HEADER_RETRIEVED)
        assert "検索で当たった箇所の構造" in BLOCK_HEADER_RETRIEVED
        assert "根拠ではなく" in BLOCK_HEADER_RETRIEVED


# ---------------------------------------------------------------------------
# 2. 上限（§5: 出典ごと2主張・全体8行・本文120字）
# ---------------------------------------------------------------------------


class TestLimits:
    def test_at_most_two_claims_per_citation(self):
        entry = {
            "index": "1",
            "claims": [
                {"text": f"主張{i}。", "claim_type": "definition"} for i in range(6)
            ],
        }
        facts = _facts(_sources(entry))
        assert len(facts) == MAX_LEARNING_RETRIEVED_CLAIMS_PER_SOURCE

    def test_total_facts_are_capped(self):
        entries = [
            {
                "index": str(i + 1),
                "claims": [
                    {"text": f"主張{i}-{j}。", "claim_type": "definition", "node": _node()}
                    for j in range(2)
                ],
            }
            for i in range(8)
        ]
        facts = _facts(_sources(*entries))
        assert len(facts) == MAX_LEARNING_RETRIEVED_FACTS

    def test_claim_text_is_truncated_deterministically(self):
        long_text = "あ" * 400
        facts = _facts(
            _sources({"index": "1", "claims": [{"text": long_text, "claim_type": ""}]})
        )
        assert "あ" * MAX_LEARNING_RETRIEVED_CLAIM_CHARS in facts[0]
        assert "あ" * (MAX_LEARNING_RETRIEVED_CLAIM_CHARS + 1) not in facts[0]


# ---------------------------------------------------------------------------
# 3. 遮断（KT7: 内部 ID・数値を学習者へ渡さない）
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_claim_text_with_an_internal_id_is_dropped(self):
        facts = _facts(
            _sources(
                {
                    "index": "1",
                    "claims": [
                        {"text": "synth_claim_0001 を定量化する。", "claim_type": "definition"}
                    ],
                }
            )
        )
        assert facts == []

    def test_theory_object_with_an_internal_id_is_dropped_but_the_stage_survives(self):
        facts = _facts(
            _sources(
                {
                    "index": "1",
                    "claims": [
                        {
                            "text": "ある主張。",
                            "claim_type": "definition",
                            "node": _node(display="Theory basis: eq_op_0007"),
                        }
                    ],
                }
            )
        )
        assert facts[1] == "この主張は、理論の骨格では『理論の土台』の段階に置かれています"

    def test_unknown_stage_label_produces_no_node_fact(self):
        """英語の内部表示名をそのまま学習者へ渡さない（SA4 / PL7）。"""
        facts = _facts(
            _sources(
                {
                    "index": "1",
                    "claims": [
                        {
                            "text": "ある主張。",
                            "claim_type": "definition",
                            "node": _node(label="theory_op_0003", display="theory_op_0003"),
                        }
                    ],
                }
            )
        )
        assert len(facts) == 1

    def test_no_numeric_keys_leak_into_the_facts(self):
        sources = _sources(
            {
                "index": "1",
                "claims": [
                    {
                        "text": "ある主張。",
                        "claim_type": "definition",
                        "confidence": 0.93,
                        "score": 0.71,
                        "weight": 3,
                        "node": _node(),
                    }
                ],
            }
        )
        blob = "\n".join(_facts(sources))
        for leaked in ("0.93", "0.71", "confidence", "score", "weight"):
            assert leaked not in blob


# ---------------------------------------------------------------------------
# 4. 純関数としての契約
# ---------------------------------------------------------------------------


class TestPurity:
    def test_empty_sources_yield_nothing(self):
        assert _facts({}) == []
        assert _facts({"retrieved_structure": {}}) == []
        assert _facts({"retrieved_structure": {"sources": []}}) == []

    def test_entry_without_an_index_is_skipped(self):
        facts = _facts(_sources({"claims": [{"text": "ある主張。", "claim_type": ""}]}))
        assert facts == []

    def test_input_is_not_mutated(self):
        sources = _sources()
        snapshot = copy.deepcopy(sources)
        _facts(sources)
        assert sources == snapshot

    def test_malformed_payloads_do_not_raise(self):
        for bad in ({"retrieved_structure": "x"}, {"retrieved_structure": {"sources": "x"}},
                    {"retrieved_structure": {"sources": [None, 5]}}):
            assert _facts(bad) == []

    def test_registry_path_produces_the_same_facts(self):
        sources = _sources()
        assert resolve(_ctx(), sources, kinds=KINDS) == _facts(sources)
