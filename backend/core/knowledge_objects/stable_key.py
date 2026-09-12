"""stable_key — 内容由来・版非依存の同一性キー（knowledge_objects_design.md §5.1 / KO2）。

材料は ``document_id`` + 正規化テキスト + 出典 block_id 集合（+ 種別固有の少数の構造項）。
run_id・出現順・agent ID・confidence は **材料にしない**。正規化は agent 側 ``content_hash`` と
同じ ``episteme_graph.agents.content_normalization``（stdlib のみ）を使い、新しい正規化を書かない。

すべて純関数。DB・FastAPI・LLM を import しない。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence
from typing import Any, TypeVar

from episteme_graph.agents.content_normalization import (
    normalize_equation_for_hash,
    normalize_text_for_hash,
)

from .schema import STABLE_KEY_VERSION_PREFIX

_SEP = "\x1f"

T = TypeVar("T")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _join_sorted(values: Iterable[Any]) -> str:
    return ",".join(sorted({_clean(v) for v in values if _clean(v)}))


def digest(parts: Sequence[str]) -> str:
    """``k1:`` + sha256(parts を \\x1f 連結)[:32]。"""
    raw = _SEP.join(str(p) for p in parts).encode("utf-8")
    return STABLE_KEY_VERSION_PREFIX + hashlib.sha256(raw).hexdigest()[:32]


# ---------------------------------------------------------------------------
# 種別ごとのキー
# ---------------------------------------------------------------------------


def claim_stable_key(document_id: str, text: str, block_ids: Iterable[str]) -> str:
    """claim。``text`` は normalized_text（無ければ text）。block_ids は出典 block の集合。"""
    return digest(["claim", _clean(document_id), normalize_text_for_hash(text), _join_sorted(block_ids)])


def component_stable_key(
    document_id: str, label: str, operation: str, block_ids: Iterable[str]
) -> str:
    """component。label + operation（primary_operation でも可）+ 出典 block 集合。"""
    return digest([
        "component", _clean(document_id), normalize_text_for_hash(label),
        _clean(operation), _join_sorted(block_ids),
    ])


def equation_stable_key(
    document_id: str,
    latex: str | None,
    plain_text: str | None,
    block_id: str | None,
    label: str | None = None,
) -> str:
    """equation。正規化 LaTeX（無ければ plain / raw）+ block_id（無ければ label）。"""
    return digest([
        "equation", _clean(document_id), normalize_equation_for_hash(latex, plain_text),
        _clean(block_id) or _clean(label),
    ])


def evidence_stable_key(document_id: str, block_id: str, evidence_text: str) -> str:
    """evidence（逐語根拠）。block_id + 正規化本文。"""
    return digest(["evidence", _clean(document_id), _clean(block_id), normalize_text_for_hash(evidence_text)])


def derivation_step_stable_key(
    document_id: str,
    operation: str,
    input_equation_keys: Iterable[str],
    output_equation_keys: Iterable[str],
) -> str:
    """derivation step。operation + 入力式キー集合 + 出力式キー集合（式は stable_key、解決不能なら agent ID）。"""
    return digest([
        "derivation_step", _clean(document_id), _clean(operation),
        _join_sorted(input_equation_keys), _join_sorted(output_equation_keys),
    ])


def symbol_stable_key(
    document_id: str,
    canonical_symbol: str,
    scope: str,
    defining_equation_keys: Iterable[str],
) -> str:
    """symbol。canonical_symbol + scope + 定義式キー集合。"""
    return digest([
        "symbol", _clean(document_id), _clean(canonical_symbol), _clean(scope),
        _join_sorted(defining_equation_keys),
    ])


def learning_unit_stable_key(
    document_id: str,
    unit_kind: str,
    text: str,
    refs: Iterable[str],
) -> str:
    """学ぶ単位（learning_units_design.md §5・migration 081）。

    材料は種別 + 正規化テキスト + 参照集合だけで、``order_index`` / run_id /
    confidence は入れない（章の並びが変わっても同じ単位を指し続ける）。``refs`` の
    中身は種別ごとに決まる（section_block = 出典 block 集合 / thesis_support =
    出所 ID + claim・equation の agent ID 集合 / parent_component = 子の出典 block 集合 /
    dsl_node = 空 / figure = figure_id）。集合なので順序には依存しない。
    """
    return digest([
        "learning_unit", _clean(document_id), _clean(unit_kind),
        normalize_text_for_hash(text), _join_sorted(refs),
    ])


# ---------------------------------------------------------------------------
# 同一 run 内の衝突解消
# ---------------------------------------------------------------------------


def dedupe_stable_keys(
    items: Sequence[T],
    *,
    key_of: Callable[[T], str],
    agent_id_of: Callable[[T], str],
) -> dict[str, str]:
    """同一 run 内で同じ stable_key を持つ項目に決定論的に ``#2`` ``#3`` … を付ける。

    Returns:
        ``{agent_id: final_stable_key}``。衝突が無い項目は元のキーのまま。衝突した組は
        agent ID 昇順で 1 番目が素のキー、2 番目以降が ``#n``。agent ID が空の項目は
        位置で代用しない（その項目は ``agent_id_of`` の戻り値 ``""`` をキーに1件だけ載る）。
    """
    groups: dict[str, list[str]] = {}
    for item in items:
        groups.setdefault(_clean(key_of(item)), []).append(_clean(agent_id_of(item)))
    out: dict[str, str] = {}
    for key, agent_ids in groups.items():
        ordered = sorted(dict.fromkeys(agent_ids))
        for index, agent_id in enumerate(ordered):
            out[agent_id] = key if index == 0 else f"{key}#{index + 1}"
    return out
