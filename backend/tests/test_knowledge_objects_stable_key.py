"""stable_key の性質（knowledge_objects_design.md §5.1 / KO2）。

不変条項 KO2 が要求するのは次の4つ:
  ①内容由来（材料は document_id + 正規化テキスト + 出典 block_id 集合 + 少数の構造項）
  ②版非依存（run_id・出現順・agent ID・confidence を材料にしない）
  ③決定論（同じ入力 → 同じ出力・block_ids の順序に依らない）
  ④同一 run 内の衝突は決定論順（agent ID 昇順）で ``#2`` ``#3`` を付ける

純関数のみのテスト。DB / LLM / FastAPI に触れない。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.knowledge_objects.schema import STABLE_KEY_VERSION_PREFIX  # noqa: E402
from core.knowledge_objects.stable_key import (  # noqa: E402
    claim_stable_key,
    component_stable_key,
    dedupe_stable_keys,
    derivation_step_stable_key,
    equation_stable_key,
    evidence_stable_key,
    symbol_stable_key,
)

DOC = "11111111-1111-1111-1111-111111111111"
OTHER_DOC = "22222222-2222-2222-2222-222222222222"

_ALL_KEY_FUNCS = [
    claim_stable_key,
    component_stable_key,
    equation_stable_key,
    evidence_stable_key,
    derivation_step_stable_key,
    symbol_stable_key,
]


def _sample(func):
    """各キー関数を既定の引数で1回呼ぶ（種別ごとの差を吸収するヘルパ）。"""
    return {
        claim_stable_key: lambda: claim_stable_key(DOC, "The mass is 1.", ["b1"]),
        component_stable_key: lambda: component_stable_key(DOC, "Mass model", "define_mass", ["b1"]),
        equation_stable_key: lambda: equation_stable_key(DOC, "E = mc^2", None, "b1"),
        evidence_stable_key: lambda: evidence_stable_key(DOC, "b1", "verbatim quote"),
        derivation_step_stable_key: lambda: derivation_step_stable_key(DOC, "solve_x", ["k1"], ["k2"]),
        symbol_stable_key: lambda: symbol_stable_key(DOC, "m", "paper", ["k1"]),
    }[func]()


# ---------------------------------------------------------------------------
# 形式と決定論
# ---------------------------------------------------------------------------


class TestShapeAndDeterminism:
    @pytest.mark.parametrize("func", _ALL_KEY_FUNCS)
    def test_prefix_and_length(self, func):
        key = _sample(func)
        assert key.startswith(STABLE_KEY_VERSION_PREFIX)
        assert len(key) == len(STABLE_KEY_VERSION_PREFIX) + 32

    @pytest.mark.parametrize("func", _ALL_KEY_FUNCS)
    def test_same_input_same_output(self, func):
        assert _sample(func) == _sample(func)

    @pytest.mark.parametrize("func", _ALL_KEY_FUNCS)
    def test_kinds_do_not_collide(self, func):
        keys = [_sample(f) for f in _ALL_KEY_FUNCS]
        assert len(set(keys)) == len(keys)


# ---------------------------------------------------------------------------
# 材料（KO2）
# ---------------------------------------------------------------------------


class TestMaterials:
    def test_block_ids_are_order_independent(self):
        assert claim_stable_key(DOC, "t", ["b1", "b2"]) == claim_stable_key(DOC, "t", ["b2", "b1"])
        assert component_stable_key(DOC, "n", "op", ["b3", "b1"]) == component_stable_key(
            DOC, "n", "op", ["b1", "b3"]
        )
        assert derivation_step_stable_key(DOC, "op", ["a", "b"], ["c"]) == (
            derivation_step_stable_key(DOC, "op", ["b", "a"], ["c"])
        )

    def test_duplicate_and_blank_block_ids_are_ignored(self):
        assert claim_stable_key(DOC, "t", ["b1", "b1", "", None]) == claim_stable_key(DOC, "t", ["b1"])

    def test_text_normalization_absorbs_whitespace_and_trailing_period(self):
        base = claim_stable_key(DOC, "The mass is one.", ["b1"])
        assert claim_stable_key(DOC, "  The   mass is one.  ", ["b1"]) == base
        assert claim_stable_key(DOC, "The mass is one", ["b1"]) == base

    def test_document_id_separates_keys(self):
        assert claim_stable_key(DOC, "t", ["b1"]) != claim_stable_key(OTHER_DOC, "t", ["b1"])
        assert component_stable_key(DOC, "n", "op", ["b1"]) != component_stable_key(
            OTHER_DOC, "n", "op", ["b1"]
        )
        assert evidence_stable_key(DOC, "b1", "q") != evidence_stable_key(OTHER_DOC, "b1", "q")

    def test_structural_terms_change_the_key(self):
        assert component_stable_key(DOC, "n", "define_x", ["b1"]) != component_stable_key(
            DOC, "n", "solve_x", ["b1"]
        )
        assert symbol_stable_key(DOC, "m", "paper", []) != symbol_stable_key(DOC, "m", "section", [])
        assert derivation_step_stable_key(DOC, "solve_x", ["a"], ["b"]) != (
            derivation_step_stable_key(DOC, "solve_y", ["a"], ["b"])
        )

    def test_equation_falls_back_to_label_when_block_is_absent(self):
        with_label = equation_stable_key(DOC, "E=mc^2", None, None, "eq:1")
        assert with_label != equation_stable_key(DOC, "E=mc^2", None, None, "eq:2")
        # block_id があるときは label を見ない（block が出典の正本）。
        assert equation_stable_key(DOC, "E=mc^2", None, "b1", "eq:1") == equation_stable_key(
            DOC, "E=mc^2", None, "b1", "eq:2"
        )


class TestVersionIndependence:
    """run_id・出現順・agent ID・confidence を**引数として受け取らない**ことを
    シグネチャで固定する（値として混ぜようがない形にしておく）。"""

    _FORBIDDEN = ("run", "agent", "confidence", "order", "index", "created", "version")

    @pytest.mark.parametrize("func", _ALL_KEY_FUNCS)
    def test_signature_has_no_version_dependent_material(self, func):
        names = list(inspect.signature(func).parameters)
        bad = [n for n in names if any(token in n for token in self._FORBIDDEN)]
        assert bad == [], f"{func.__name__} が版依存の材料を受け取っています: {bad}"


# ---------------------------------------------------------------------------
# 同一 run 内の衝突解消
# ---------------------------------------------------------------------------


class _Item:
    def __init__(self, agent_id: str, key: str):
        self.agent_id = agent_id
        self.key = key


def _dedupe(items):
    return dedupe_stable_keys(items, key_of=lambda i: i.key, agent_id_of=lambda i: i.agent_id)


class TestDedupe:
    def test_no_collision_keeps_plain_keys(self):
        out = _dedupe([_Item("a", "k1:aaa"), _Item("b", "k1:bbb")])
        assert out == {"a": "k1:aaa", "b": "k1:bbb"}

    def test_collision_numbered_by_agent_id_ascending(self):
        out = _dedupe([_Item("c_3", "k1:x"), _Item("c_1", "k1:x"), _Item("c_2", "k1:x")])
        assert out == {"c_1": "k1:x", "c_2": "k1:x#2", "c_3": "k1:x#3"}

    def test_input_order_does_not_matter(self):
        forward = _dedupe([_Item("a", "k1:x"), _Item("b", "k1:x")])
        backward = _dedupe([_Item("b", "k1:x"), _Item("a", "k1:x")])
        assert forward == backward

    def test_same_agent_id_twice_is_one_entry(self):
        out = _dedupe([_Item("a", "k1:x"), _Item("a", "k1:x")])
        assert out == {"a": "k1:x"}
