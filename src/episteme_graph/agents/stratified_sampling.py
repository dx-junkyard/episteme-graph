"""入力の並べ替え・節単位の層化サンプリング・未処理節の列挙（正本）。

`docs/architecture/knowledge_structure_review_2026-09-12.md` §4 Phase 0 の P0-1 で
rhetorical_role の input_builder に実装した3つの規則を、他ステージ
（claim_qualification / equation_semantics / paper_skeleton）からも同じ規則で使える
ように切り出したもの。**「先頭 N 件で切る」を新規に書かない**ための正本で、
`coverage_report.py`（報告の形式）と対になる（こちらは選び方の規則）。

stdlib のみに依存し、src / backend の両方から import できる。

--------------------------------------------------------------------------
ソートキーの判定規則（P0-1 / F-1 (c)）
--------------------------------------------------------------------------
``order`` は PyMuPDF パーサ・GROBID TEI パーサのどちらでも**文書全体で単調増加する
通し番号**として振られる（ページ内で振り直されない）。一方 ``page`` は GROBID 経路では
既定 1 で、PDF 本文との突合に成功したブロックだけ実ページに書き換わるため、**突合に
失敗したブロックが page=1 のまま残る**（実測: 803 ブロック中 558 / 936 ブロック中 212）。
``(page, order)`` で並べるとこの取りこぼし群が文書先頭に集まり、順序が内容と無関係になる。

  - ``order`` が全要素で**一意**なら ``order`` のみで並べる（``sort_key = "order"``）。
  - 一意でないなら通し番号として信用できないので ``(page, order)`` で並べる
    （``sort_key = "page_order"``）。

どちらの規則で並べたかは coverage 報告の ``details.sort_key`` に必ず残す。

--------------------------------------------------------------------------
層化サンプリングの規則（P0-1 / F-1 (b)）
--------------------------------------------------------------------------
上限が対象数より小さいとき、各節へ**対象数に比例**して配分する。

  1. 各節に最低 1 件（節数が上限を超える場合は、対象数の多い節から順に 1 件ずつ
     配って上限で止める）。
  2. 残り枠を ``floor(上限 × 節の対象数 / 全対象数)`` で配分（既に配った 1 件を含む）。
  3. 端数は「対象数の多い節」→「節の初出順」の順に 1 件ずつ配る。
  4. 節の中では**順序どおり先頭から**採る。

節の初出順・対象数による比較はすべて決定論で、同数の節は初出順で安定する。
これにより、上限を敷いた運用でも結論・限界の節が丸ごと落ちることがなくなる
（従来の先頭切り捨てでは Conclusions が 1 件も入力に入らなかった）。
"""

from __future__ import annotations

import os
from typing import Any, Callable, Iterable, Sequence

#: 節 ID を持たない要素をまとめる擬似節のキー。
NO_SECTION_KEY = ""

#: ソートキー名（``details.sort_key`` に載る値）。
SORT_KEY_ORDER = "order"
SORT_KEY_PAGE_ORDER = "page_order"


def resolve_limit(
    config: dict | None,
    config_key: str,
    env_name: str,
    *,
    default: int = 0,
) -> int:
    """入力上限を「``config`` > env > 既定」の順で解決する（0 = 上限なし）。

    A層 agent は backend の ``core.config`` を import できない（src は backend に
    依存しない）ため env を直接読む。``core/config.py`` 側の同名 Field は同じ env の
    **宣言**で、値の正本は env そのもの。

    値の解釈:
      - 指定なし（config にキーが無く env も未設定）→ ``default``
      - 正の整数 → その値
      - **0 以下を明示** → 0（上限なし）。既定の上限がある設定
        （例: inline 数式の 32）を運用側から外せるようにするため、明示の 0 は
        ``default`` に巻き戻さない。
      - 数値として読めない値 → ``default``（「設定されていない」とみなす。
        タイポで既定の弁が黙って外れるのを避ける）
    """
    cfg = config or {}
    if config_key in cfg:
        raw: Any = cfg.get(config_key)
    else:
        raw = os.environ.get(env_name)
        if raw is None:
            return default
    if raw is None:
        return default
    try:
        value = int(str(raw).strip()) if isinstance(raw, str) else int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else 0


def order_is_unique(items: Sequence[Any]) -> bool:
    """``order`` 属性が全要素で一意か（通し番号として信用できるか）。"""
    orders = [int(getattr(item, "order", 0) or 0) for item in items]
    return len(set(orders)) == len(orders)


def order_blocks(items: Sequence[Any]) -> tuple[list[Any], str]:
    """並べ替えた要素列と、採用したソートキー名を返す（判定規則は docstring 参照）。"""
    listed = list(items)
    if order_is_unique(listed):
        return (
            sorted(listed, key=lambda b: int(getattr(b, "order", 0) or 0)),
            SORT_KEY_ORDER,
        )
    return (
        sorted(
            listed,
            key=lambda b: (
                int(getattr(b, "page", 0) or 0),
                int(getattr(b, "order", 0) or 0),
            ),
        ),
        SORT_KEY_PAGE_ORDER,
    )


def select_indices(
    items: Sequence[Any],
    target_indices: Iterable[int],
    max_items: int,
    *,
    section_key_of: Callable[[Any], str],
) -> list[int]:
    """対象要素の添字から、処理する添字を決定論的に選ぶ（層化サンプリング）。

    上限なし（``max_items <= 0``）または上限が母集合以上なら全件をそのまま返す。
    戻り値は ``items`` 内の添字の昇順。
    """
    targets = list(target_indices)
    if max_items <= 0 or len(targets) <= max_items:
        return targets

    # 節ごとの対象添字（節の初出順を保つ）
    by_section: dict[str, list[int]] = {}
    for idx in targets:
        key = section_key_of(items[idx]) or NO_SECTION_KEY
        by_section.setdefault(key, []).append(idx)

    section_keys = list(by_section.keys())
    first_seen = {key: pos for pos, key in enumerate(section_keys)}
    total = len(targets)

    quota: dict[str, int] = {key: 0 for key in section_keys}
    remaining = max_items

    # 1. 各節に最低1件（枠が節数より少なければ、対象数の多い節から順に）
    priority = sorted(
        section_keys,
        key=lambda key: (-len(by_section[key]), first_seen[key]),
    )
    for key in priority:
        if remaining <= 0:
            break
        quota[key] = 1
        remaining -= 1

    # 2. 比例配分（既に配った1件を含む上限まで）
    if remaining > 0:
        for key in section_keys:
            if remaining <= 0:
                break
            size = len(by_section[key])
            share = (max_items * size) // total
            extra = min(share - quota[key], size - quota[key], remaining)
            if extra > 0:
                quota[key] += extra
                remaining -= extra

    # 3. 端数は「対象数の多い節」→「初出順」で1件ずつ
    while remaining > 0:
        progressed = False
        for key in priority:
            if remaining <= 0:
                break
            if quota[key] < len(by_section[key]):
                quota[key] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break

    # 4. 節内は順序どおり先頭から
    selected: list[int] = []
    for key in section_keys:
        selected.extend(by_section[key][: quota[key]])
    selected.sort()
    return selected


def unprocessed_section_entries(
    items: Sequence[Any],
    target_indices: Iterable[int],
    selected: Iterable[int],
    *,
    section_key_of: Callable[[Any], str],
    title_of: Callable[[str], str | None] | None = None,
) -> list[dict]:
    """取りこぼしのある節を初出順で列挙する（件数は載せない）。

    「1件も処理されなかった節」ではなく「**処理されなかった要素を含む節**」を挙げる
    （部分的にしか見ていない節も取りこぼしとして正直に出す）。
    ``coverage.details.unprocessed_sections`` にそのまま入る形
    （``{"section_id": ..., "title": ...}``）。
    """
    selected_set = set(selected)
    entries: list[dict] = []
    seen: set[str] = set()
    for idx in target_indices:
        if idx in selected_set:
            continue
        key = section_key_of(items[idx]) or NO_SECTION_KEY
        if key in seen:
            continue
        seen.add(key)
        entries.append({
            "section_id": key or None,
            "title": title_of(key) if title_of else None,
        })
    return entries
