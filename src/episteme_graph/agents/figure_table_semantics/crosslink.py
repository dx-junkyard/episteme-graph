"""crosslink: mention ベースの claim ⇄ 図・表クロスリンク（決定論・非LLM）。

設計: docs/features/figure_concept_linking_design.md の F1。

背景: ``FigureTableSemanticsAgent._build_figure_record`` / ``_build_table_record`` は
caption block の block_id で ``claim_link_index``（claim の source span block_id →
claim_id 群）を引くが、claim スパンの供給源である rhetorical_role は
``_TARGET_BLOCK_TYPES = {"body_paragraph"}`` に限定しているため caption block が
claim スパンになることはなく、この参照は常に空振りする。

本モジュールは caption ベースのリンクとは別に、本文（body_paragraph）中の
"Fig. N" / "Figure N" / "図N"（表は "Table N" / "図表" 等）という参照メンションを
文書順に走査し、メンションを含む block の block_id で ``claim_link_index`` を
引いてヒットした claim_id を該当 figure/table の ``linked_claim_ids`` に追加する。

- 図・表番号の導出は ``backend/core/document_pipeline/figure_context.py`` の
  ``_derive_figure_number`` と同じ規約（id prefix を剥がし ``_`` → ``.`` 変換）。
  agents は backend を import できないため同等ロジックをここに複製する。
- GROBID 由来テキストでは図・表番号の小数点前後にスペースが混入する実例
  （例: "Fig. 3 .3"）が多数観測されるため、mention パターンは番号内部の
  空白揺れを吸収する（``_number_pattern_fragment`` 参照）。
- LLM 不使用・domain 固有語彙のハードコードなし。
"""
from __future__ import annotations

import re
from typing import Callable, Iterable

from episteme_graph.agents.document_structure.schema import DocumentStructureResult, TypedBlock

from .schema import FigureRecord, TableRecord

_FIGURE_ID_PREFIX = "fig_"
_TABLE_ID_PREFIX = "table_"


def _derive_reference_number(entity_id: str, prefix: str) -> str | None:
    """figure_id / table_id から参照メンション検索用の番号文字列を導出する。

    ``figure_context.py::_derive_figure_number`` の fallback 分岐と同じ規約
    （prefix を剥がし ``_`` → ``.`` に変換）。caption からラベルを抽出できず
    block_id にフォールバックした figure_id/table_id（prefix を持たない）は
    番号を導出できないため None を返す（クロスリンク対象外）。
    """
    if not entity_id or not entity_id.startswith(prefix):
        return None
    rest = entity_id[len(prefix):].strip()
    if not rest:
        return None
    return rest.replace("_", ".")


def _number_pattern_fragment(num: str) -> str:
    """図表番号 num を検索用の正規表現断片に変換する（figure/table 共通ヘルパー）。

    GROBID 由来の本文では、図・表番号の小数点前後にスペースが混入する表記
    （実データ例: "Fig. 3 .3"）が生じるため、num を1文字ずつ走査し ``.`` は
    ``\\s*\\.\\s*``（前後の空白を許容）に、それ以外の英数字は ``re.escape(ch)``
    としてそのまま連結する（例: "3.3" -> ``3\\s*\\.\\s*3``）。
    """
    parts: list[str] = []
    for ch in num:
        if ch == ".":
            parts.append(r"\s*\.\s*")
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


def _figure_mention_pattern(num: str) -> re.Pattern[str]:
    """"Fig. N" / "Figure N" / "図N" 形式の参照メンションにマッチするパターンを作る。

    num 内部の小数点まわりの空白混入（GROBID 由来、実データ例 "Fig. 3 .3"）は
    ``_number_pattern_fragment`` で吸収する。末尾の否定先読みは、番号がさらに
    続く場合（例: "5.21" が "5.2" に前方一致してしまうケースや、
    "3.3.5" のような下位番号への前方一致）を除外しつつ、文末の句点がそのまま
    続くケース（"...Fig. 3 .3." のように GROBID 実データでは番号の直後に文末の
    句点が来ることがある）は誤って弾かないよう ``(?!\\.?[0-9])``
    （「省略可能な "." の後に数字が続く」場合のみ拒否）とする。
    """
    return re.compile(
        rf"(?:Figs?\.?|Figures?|図)\s*{_number_pattern_fragment(num)}(?!\.?[0-9])",
        re.IGNORECASE,
    )


def _table_mention_pattern(num: str) -> re.Pattern[str]:
    """"Table N" / "Tab. N" / "表N" 形式の参照メンションにマッチするパターンを作る。

    空白混入の吸収・否定先読みの理由は ``_figure_mention_pattern`` と同じ。
    """
    return re.compile(
        rf"(?:Tabs?\.?|Tables?|表)\s*{_number_pattern_fragment(num)}(?!\.?[0-9])",
        re.IGNORECASE,
    )


def _ordered_body_paragraph_blocks(structure: DocumentStructureResult) -> list[TypedBlock]:
    """claim スパンの供給源と同じ対象（body_paragraph のみ）を文書順に返す。

    caption block（figure_caption / table_caption）はここで自然に除外される
    （防御的な追加除外は不要）。
    """
    ordered = sorted(structure.blocks, key=lambda b: (b.page, b.order))
    return [b for b in ordered if b.block_type == "body_paragraph"]


def _mention_linked_claim_ids(
    num: str,
    pattern_factory: Callable[[str], re.Pattern[str]],
    body_blocks: Iterable[TypedBlock],
    claim_link_index: dict[str, list[str]],
) -> list[str]:
    """num への参照メンションを含む body_paragraph block の block_id で claim_link_index を引く。

    文書順で走査し、ヒットした claim_id を重複排除しつつ順序を保って返す。
    """
    pattern = pattern_factory(num)
    ids: list[str] = []
    seen: set[str] = set()
    for block in body_blocks:
        if not block.text or not pattern.search(block.text):
            continue
        for claim_id in claim_link_index.get(block.block_id, []):
            if claim_id in seen:
                continue
            seen.add(claim_id)
            ids.append(claim_id)
    return ids


def _merge_claim_ids(existing: list[str], additional: list[str]) -> list[str]:
    """既存（caption 由来）分を先頭に維持しつつ、mention 由来分を重複排除して追記する。"""
    merged = list(existing)
    seen = set(existing)
    for claim_id in additional:
        if claim_id in seen:
            continue
        seen.add(claim_id)
        merged.append(claim_id)
    return merged


def apply_mention_crosslinks(
    structure: DocumentStructureResult,
    figures: list[FigureRecord],
    tables: list[TableRecord],
    claim_link_index: dict[str, list[str]] | None,
) -> None:
    """本文中の参照メンションから claim ⇄ 図・表 のクロスリンクを付与する（in-place 更新）。

    figures / tables の ``linked_claim_ids`` をその場で書き換える。既存の
    caption block_id ルックアップ由来の分は先頭に維持し、mention 由来分を
    文書順で追記、重複は排除する。メンションが1件も無い figure/table は
    ``linked_claim_ids`` を変更しない（validator の
    ``figure_missing_linked_claim_ids`` / ``table_missing_linked_claim_ids``
    警告がそのまま立つ = 正直）。
    """
    claim_link_index = claim_link_index or {}
    if not claim_link_index:
        return
    body_blocks = _ordered_body_paragraph_blocks(structure)
    if not body_blocks:
        return

    for fig in figures:
        num = _derive_reference_number(fig.figure_id, _FIGURE_ID_PREFIX)
        if not num:
            continue
        additional = _mention_linked_claim_ids(
            num, _figure_mention_pattern, body_blocks, claim_link_index
        )
        if additional:
            fig.linked_claim_ids = _merge_claim_ids(fig.linked_claim_ids, additional)

    for tbl in tables:
        num = _derive_reference_number(tbl.table_id, _TABLE_ID_PREFIX)
        if not num:
            continue
        additional = _mention_linked_claim_ids(
            num, _table_mention_pattern, body_blocks, claim_link_index
        )
        if additional:
            tbl.linked_claim_ids = _merge_claim_ids(tbl.linked_claim_ids, additional)
