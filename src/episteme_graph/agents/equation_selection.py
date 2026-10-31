"""LLM 入力に載せる「主要な式」の選び方（正本）。

thesis_reconstruction / dsl_linking は式を上限（16 / 24）までしか LLM に渡さない。
従来は ``equations.equations[:limit]`` の**先頭切り捨て**で、式の多い論文では
前半（定義・準備）の式だけが入り、主結果（結果節・結論節の式、主張が参照する式）が
中心命題・DSL の材料から落ちていた（再現性レビュー J-3 の規律違反）。

選び方（決定論・LLM 0 回）:

  1. 上限なし（``limit <= 0``）または母集合が上限以下 → 全件（文書順）。
  2. 第 1 優先: 入力に載る主張（と thesis）が参照する式（``linked_equation_ids``）。
  3. 第 2 優先: 結果系の節（``priority_section_ids`` — paper_skeleton の logical block
     種別から導く）にある式、または ``equation_type`` / ``role_in_argument`` が
     ``result`` の式。
  4. 残り枠: 残りの式を節単位の層化サンプリング（``stratified_sampling.select_indices``）
     で配る（節ごとに比例・節内は文書順）。
  5. 優先枠だけで上限を超えるときは、その枠の中を層化サンプリングで絞る。

戻り値の添字は文書順（入力順）の昇順。取りこぼしの報告は
``unprocessed_section_entries`` の形（件数は載せない）。
特定分野の語彙は使わない（語彙は skeleton / equation_semantics の統制語彙のみ）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from episteme_graph.agents.stratified_sampling import (
    select_indices,
    unprocessed_section_entries,
)

#: 結果を述べる節とみなす paper_skeleton の logical block 種別。
RESULT_BLOCK_TYPES: tuple[str, ...] = ("result", "core_relation", "diagnostic", "conclusion")

#: 結果の式とみなす equation_type / role_in_argument の値。
RESULT_EQUATION_ROLES: tuple[str, ...] = ("result",)

#: coverage の ``details.equation_selection`` に載せる選び方の名前。
SELECTION_RULE = "linked_and_result_first_then_stratified"


@dataclass
class EquationSelection:
    indices: list[int]
    population: int
    unprocessed_sections: list[dict] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return len(self.indices) < self.population


def _section_of(record: Any) -> str:
    src = getattr(record, "source_extraction", None)
    loc = getattr(src, "source_location", None) or {}
    return str(loc.get("section_id") or "")


def _is_result_equation(record: Any) -> bool:
    sem = getattr(record, "semantics", None)
    if sem is None:
        return False
    etype = str(getattr(sem, "equation_type", "") or "").strip().lower()
    role = str(getattr(sem, "role_in_argument", "") or "").strip().lower()
    return etype in RESULT_EQUATION_ROLES or role in RESULT_EQUATION_ROLES


def priority_section_ids_from_skeleton(skeleton: Any) -> set[str]:
    """結果系 logical block が覆う節 ID の集合（skeleton が無ければ空）。"""
    sections: set[str] = set()
    for block in getattr(skeleton, "logical_blocks", None) or []:
        if str(getattr(block, "block_type", "") or "") in RESULT_BLOCK_TYPES:
            for sid in getattr(block, "section_ids", None) or []:
                if sid:
                    sections.add(str(sid))
    return sections


def linked_equation_ids_from_claims(
    claim_ids: Iterable[str],
    claim_objects: Any = None,
) -> set[str]:
    """入力に載る主張が（確定リンクで）参照する式 ID の集合。

    式から合成した主張（``synth_claim_*``）は式そのものを指すだけなので数えない
    （数えると全式が優先枠に入り、優先の意味が無くなる）。``inferred_equation_ids``
    も確定リンクではないので数えない。
    """
    wanted = {str(c) for c in claim_ids if c}
    linked: set[str] = set()
    for claim in getattr(claim_objects, "claims", None) or []:
        cid = str(getattr(claim, "claim_id", "") or "")
        if not cid or cid not in wanted or cid.startswith("synth_claim"):
            continue
        for eid in getattr(claim, "equation_ids", None) or []:
            if eid:
                linked.add(str(eid))
    return linked


def select_equation_indices(
    records: Sequence[Any],
    limit: int,
    *,
    linked_equation_ids: Iterable[str] = (),
    priority_section_ids: Iterable[str] = (),
) -> EquationSelection:
    population = len(records)
    all_indices = list(range(population))
    if limit <= 0 or population <= limit:
        return EquationSelection(indices=all_indices, population=population)

    linked = {str(e) for e in linked_equation_ids if e}
    sections = {str(s) for s in priority_section_ids if s}

    tier_linked: list[int] = []
    tier_result: list[int] = []
    rest: list[int] = []
    for idx, record in enumerate(records):
        if str(getattr(record, "equation_id", "") or "") in linked:
            tier_linked.append(idx)
        elif _section_of(record) in sections or _is_result_equation(record):
            tier_result.append(idx)
        else:
            rest.append(idx)

    selected: list[int] = []
    remaining = limit
    for tier in (tier_linked, tier_result, rest):
        if remaining <= 0:
            break
        if len(tier) <= remaining:
            chosen = list(tier)
        else:
            chosen = select_indices(records, tier, remaining, section_key_of=_section_of)
        selected.extend(chosen)
        remaining -= len(chosen)

    selected.sort()
    return EquationSelection(
        indices=selected,
        population=population,
        unprocessed_sections=unprocessed_section_entries(
            records, all_indices, selected, section_key_of=_section_of
        ),
    )
