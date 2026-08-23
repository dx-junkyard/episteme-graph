"""負荷順トリアージ（宣言された弁, Phase 4 教員支援 v1 §2）。

正本: ``docs/features/teacher_triage_instruments_design.md`` §2 / §5 精査①②③。

説明レビューキュー（``routes/element_explanations.py``）と R層 item 監査キュー
（``routes/reconstruction.py``）の ``sort=load`` の実体。candidate の対象要素を
D層 ``epistemic_ledger``（``load_calculator`` が書いた ``load_score``）で引き、
段階ラベル（低/中/高/最高位 — **既存 D層語彙** ``core/doubt/schema.py`` の
``LOAD_LEVEL_LABELS``。TT2: 独自辞書を作らない）で降順に並べる。

不変条項:

- **TT1 沈黙の並べ替えを作らない** — 既定は従来順。この関数群は ``sort=load`` が
  明示されたときだけ route 層から呼ばれる。
- **TT2 数値を見せない** — ``load_score`` 生値はこのモジュールの外へ出さない
  （付与するのは段階キーとラベルの2つだけ）。
- 台帳読みは **バッチ1クエリ**（``target_id = ANY(:ids)``）+ ``load_percentiles`` を
  **キューにつき1回**（§5 精査②。``routes/doubt.py`` の行ごと percentile 呼びは
  アンチパターン — 真似ない）。
- 対応が引けない candidate（figure / document スコープ、agent ID 未解決、台帳行なし、
  ``load_score IS NULL``、course_id の混在・不在）は**末尾**に置き、
  「影響度を導出できない候補」と正直にラベルする（縮退を隠さない）。

FastAPI / LLM は import しない（core の規律）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy import text as sa_text

from core.doubt.load_calculator import load_percentiles
from core.doubt.schema import LOAD_LEVEL_LABELS, LOAD_LEVELS, load_level_for_score

logger = logging.getLogger(__name__)

# sort パラメータの語彙（設計書 §2: default | load）。監査 metadata の sort_order も
# 同じ語彙で受ける（TT3: どの並び順の下で確定したかを偽らない）。
SORT_DEFAULT = "default"
SORT_LOAD = "load"
SORT_ORDERS = (SORT_DEFAULT, SORT_LOAD)

# 導出不能候補の正直なラベル（設計書 §2 の確定文言。段階ラベル本体は
# LOAD_LEVEL_LABELS = 低/中/高/最高位 を再利用し、新語彙はこの縮退ラベルのみ）。
LOAD_UNDERIVABLE_LABEL = "影響度を導出できない候補"

# 台帳 target_type 語彙（migration 029 の CHECK と一致）。
TARGET_CLAIM = "claim"
TARGET_COMPONENT = "component"
TARGET_EQUATION = "equation"

# 並べ替えランク: 最高位が先頭、導出不能（"" = level なし）は末尾。
_LEVEL_RANK = {level: rank for rank, level in enumerate(reversed(LOAD_LEVELS))}
_UNDERIVABLE_RANK = len(LOAD_LEVELS)


def explanation_target_for_row(
    row: dict,
    claim_lookup: dict[str, str],
    component_lookup: dict[str, str],
) -> tuple[str, str] | None:
    """説明レビューキューの1行 → 台帳 (target_type, target_id) の対応。

    説明キューの ``element_id`` は claim / component が **agent 側 ID**（§5 精査③）
    のため、``context_lens._claim_id_lookup / _component_id_lookup`` が組んだ索引
    （agent ID → DB UUID。DB UUID は恒等写像で素通り）で解決してから台帳を引く。
    equation は artifact の equation_id がそのまま台帳 target_id。figure / document
    スコープは台帳に対応が無いので ``None``（= 導出不能扱い）。
    """
    element_type = str(row.get("element_type") or "")
    element_id = str(row.get("element_id") or "").strip()
    if not element_id:
        return None
    if element_type == "theory_claim":
        resolved = claim_lookup.get(element_id)
        return (TARGET_CLAIM, resolved) if resolved else None
    if element_type == "theory_component":
        resolved = component_lookup.get(element_id)
        return (TARGET_COMPONENT, resolved) if resolved else None
    if element_type == "equation":
        return (TARGET_EQUATION, element_id)
    return None


def load_levels_for_targets(
    session, targets: list[tuple[str, str] | None]
) -> dict[tuple[str, str], str]:
    """台帳バッチ読み: (target_type, target_id) → 段階キー（'' = 導出不能）。

    - ``epistemic_ledger`` は ``target_id = ANY(:ids)`` の**1クエリ**で引く。
    - ``load_percentiles(session, course_id)`` は**キューにつき1回**。course_id は
      台帳行から取得し、混在（複数コース）・不在（全行が空）のときは percentile の
      基準が定まらないため**全件を導出不能扱い**にする（§5 精査②の正直な縮退。
      percentile クエリ自体を発行しない）。
    - ``load_score IS NULL`` の行・台帳行の無い target は '' を返す。
    - 生値（load_score）は返却値に含めない（TT2）。
    """
    wanted: set[tuple[str, str]] = set()
    for pair in targets:
        if not pair:
            continue
        target_type, target_id = str(pair[0] or ""), str(pair[1] or "")
        if target_type and target_id:
            wanted.add((target_type, target_id))
    if not wanted:
        return {}

    ids = sorted({target_id for _type, target_id in wanted})
    rows = session.execute(
        sa_text(
            "SELECT target_type, target_id, load_score, course_id "
            "FROM epistemic_ledger WHERE target_id = ANY(:ids)"
        ),
        {"ids": ids},
    ).fetchall()

    by_target: dict[tuple[str, str], tuple[float | None, str]] = {}
    course_ids: set[str] = set()
    for row in rows:
        key = (str(row[0] or ""), str(row[1] or ""))
        if key not in wanted:
            continue
        score = float(row[2]) if row[2] is not None else None
        course_id = str(row[3] or "")
        by_target[key] = (score, course_id)
        if course_id:
            course_ids.add(course_id)

    if len(course_ids) != 1:
        # course_id の混在・不在: 段階の基準（コーパス内パーセンタイル）が定まらない。
        # 黙って別コースの基準を流用せず、全件を導出不能として正直に返す。
        return {key: "" for key in wanted}

    course_id = next(iter(course_ids))
    p50, p90, p99 = load_percentiles(session, course_id)

    out: dict[tuple[str, str], str] = {}
    for key in wanted:
        score, row_course = by_target.get(key, (None, ""))
        if score is None or row_course != course_id:
            out[key] = ""
        else:
            out[key] = load_level_for_score(score, p50, p90, p99)
    return out


def annotate_and_sort_by_load(
    items: list[dict],
    levels: dict[tuple[str, str], str],
    *,
    target_for_item: Callable[[dict], tuple[str, str] | None],
) -> list[dict]:
    """各 item に ``load_level`` / ``load_level_label`` を付与し、負荷降順で返す。

    - 付与するのは段階キーと段階ラベルの2キーのみ（生値は載せない, TT2）。
    - 並びは 最高位 → 高 → 中 → 低 → 導出不能（末尾）。同段階内は**従来順を保持**
      （安定ソート。sort=load でも既存の並びの情報を落とさない）。
    - 導出不能は「影響度を導出できない候補」ラベル（縮退を隠さない）。
    """
    for item in items:
        target = target_for_item(item)
        level = levels.get(target, "") if target else ""
        item["load_level"] = level
        item["load_level_label"] = LOAD_LEVEL_LABELS.get(level) or LOAD_UNDERIVABLE_LABEL
    return sorted(
        items, key=lambda it: _LEVEL_RANK.get(it.get("load_level") or "", _UNDERIVABLE_RANK)
    )


def sort_metadata(metadata: dict[str, Any], sort_order: str | None) -> dict[str, Any]:
    """監査 metadata への ``sort_order`` 追記（TT3 / RR3 同型）。

    未指定（None / 空）のときは**載せない** — どの並び順で確定したか分からないものを
    ``default`` と偽装しない（設計書 §2）。値の検証（``SORT_ORDERS``）は API 層の責務。
    """
    if sort_order:
        metadata["sort_order"] = sort_order
    return metadata
