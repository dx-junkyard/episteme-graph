"""カテゴリギャップ候補 — 骨格 draft への決定論 JSON Patch 生成（設計書 §5.5）。

正本: ``docs/features/category_gap_candidates_design.md`` §5.5（合意事項 §3-1 / §3-8）。

このモジュールの立場:

- **LLM を使わない**。gap → patch は単一の ``add`` に落ちるので決定論生成できる
  （assist の日次コストゲートも不要）。id は ``core/atlas_generator.py`` の既存
  ヘルパー（``_slugify`` / ``_unique_id``）を再利用し、座標は空きスロットへ
  決定論配置する。
- **DB に触らない**。draft の読み書きは ``core/atlas_store.py``、適用は教員の既存
  ``PUT draft``（revision 楽観ロック）。ここは patch を**返すだけ**である
  （KN-3 / AB4: 確定は人間。AI/サーバが骨格 draft を書く経路を作らない）。
- **入力を変更しない**。``draft_skeleton`` は読み取りのみ（呼び出し側のプレビューが
  ``atlas_generator.apply_json_patch``（非破壊）で適用する）。
- **op は ``add`` のみ**（§5.5 / 合意事項8）。remove / replace(label) / 概念の領域間
  移動を生成する経路を作らない — ``id_migrations`` が学習者の足跡・landscape 配置に
  適用されない現実装では、再編は静かな参照切れを起こすため。再編は年次改版に隔離する。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from core import atlas as atlas_module
from core.atlas_generator import _slugify, _unique_id
from core.atlas_gaps import schema


class GapPatchError(ValueError):
    """patch を組めない（親領域が無い・ラベルが空 等）。route は事実文へ変換する。"""


class SkeletonCapacityError(GapPatchError):
    """骨格の上限に達しているため追加できない（§5.4 の非活性の理由）。

    ``core/atlas.py`` の ``MAX_REGIONS`` / ``MAX_CONCEPTS_PER_REGION`` は draft 保存時の
    hard error なので、patch を作る前にここで止める（作れない patch を提示しない）。
    """


# ---------------------------------------------------------------------------
# 決定論的な配置スロット
# ---------------------------------------------------------------------------

#: 領域の候補矩形（4列×3行 = MAX_REGIONS と同数。row-major で先頭から空きを探す）。
#: x + w <= 1.0 / y + h <= 1.0 を満たすこと（``atlas.validate_skeleton`` は超過を error）。
_REGION_SLOT_W = 0.23
_REGION_SLOT_H = 0.28
_REGION_SLOT_XS = (0.02, 0.27, 0.52, 0.77)
_REGION_SLOT_YS = (0.03, 0.36, 0.69)

#: 領域内の概念スロット（領域内相対 [0,1]。MAX_CONCEPTS_PER_REGION と同数）。
_CONCEPT_SLOTS = (
    (0.25, 0.2),
    (0.75, 0.2),
    (0.25, 0.5),
    (0.75, 0.5),
    (0.25, 0.8),
    (0.75, 0.8),
)

#: 既存概念とこの距離以内のスロットは「埋まっている」とみなす（重なりは
#: ``validate_skeleton`` でも warning 止まりなので、完璧な充填は要求しない）。
_CONCEPT_SLOT_MIN_DISTANCE = 0.12


def _region_slots() -> list[dict]:
    return [
        {"x": x, "y": y, "w": _REGION_SLOT_W, "h": _REGION_SLOT_H}
        for y in _REGION_SLOT_YS
        for x in _REGION_SLOT_XS
    ]


def _rects_overlap(a: Mapping[str, float], b: Mapping[str, float]) -> bool:
    """矩形の重なり判定（``core/atlas.py::_rects_overlap`` と同規則・private 非依存）。"""
    return not (
        a["x"] + a["w"] <= b["x"]
        or b["x"] + b["w"] <= a["x"]
        or a["y"] + a["h"] <= b["y"]
        or b["y"] + b["h"] <= a["y"]
    )


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _regions_root(draft_skeleton: Any) -> tuple[list, str]:
    """``regions`` のリストと JSON Pointer の接頭辞を返す。

    ``skeleton_to_dict`` は ``{"atlas_skeleton": {...}}`` で包むが、
    ``atlas_generator.apply_json_patch`` は骨格 dict 直下からの Pointer を扱う。
    どちらの形で渡されても、**渡された dict を根とする**正しい path を返す。
    """
    if not isinstance(draft_skeleton, Mapping):
        raise GapPatchError("draft_skeleton がオブジェクトではありません")
    inner = draft_skeleton.get("atlas_skeleton")
    if isinstance(inner, Mapping):
        return list(inner.get("regions") or []), "/atlas_skeleton"
    return list(draft_skeleton.get("regions") or []), ""


def _existing_ids(regions: list) -> set[str]:
    """領域 id と概念 id（同一名前空間。``validate_skeleton`` が両者の衝突を error）。"""
    ids: set[str] = set()
    for region in regions:
        region_id = _clean((region or {}).get("id"))
        if region_id:
            ids.add(region_id)
        for concept in (region or {}).get("concepts") or []:
            concept_id = _clean((concept or {}).get("id"))
            if concept_id:
                ids.add(concept_id)
    return ids


def _pick_region_slot(regions: list) -> dict:
    """既存領域と重ならない矩形スロットを決定論的に選ぶ。

    全スロットが埋まっている場合は最後のスロットへ縮退する（重なりは
    ``validate_skeleton`` の warning 止まりなので、提示自体は止めない）。
    """
    taken = [
        {
            "x": float((r.get("layout") or {}).get("x", 0.0)),
            "y": float((r.get("layout") or {}).get("y", 0.0)),
            "w": float((r.get("layout") or {}).get("w", 0.0)),
            "h": float((r.get("layout") or {}).get("h", 0.0)),
        }
        for r in regions
        if isinstance(r, Mapping) and isinstance(r.get("layout"), Mapping)
    ]
    taken = [t for t in taken if t["w"] > 0.0 and t["h"] > 0.0]
    slots = _region_slots()
    for slot in slots:
        if not any(_rects_overlap(slot, t) for t in taken):
            return dict(slot)
    return dict(slots[-1])


def _pick_concept_slot(concepts: list) -> dict:
    """領域内の空き概念スロットを決定論的に選ぶ（相対座標）。"""
    taken: list[tuple[float, float]] = []
    for concept in concepts:
        layout = (concept or {}).get("layout")
        if isinstance(layout, Mapping):
            try:
                taken.append((float(layout.get("x", 0.0)), float(layout.get("y", 0.0))))
            except (TypeError, ValueError):
                continue

    def _min_distance(slot: tuple[float, float]) -> float:
        if not taken:
            return 1.0
        return min(
            ((slot[0] - tx) ** 2 + (slot[1] - ty) ** 2) ** 0.5 for tx, ty in taken
        )

    for slot in _CONCEPT_SLOTS:
        if _min_distance(slot) > _CONCEPT_SLOT_MIN_DISTANCE:
            return {"x": slot[0], "y": slot[1]}
    # 全スロットが近接している場合は最も離れたスロット（同値は先頭優先で決定論的）。
    best = max(range(len(_CONCEPT_SLOTS)), key=lambda i: (_min_distance(_CONCEPT_SLOTS[i]), -i))
    return {"x": _CONCEPT_SLOTS[best][0], "y": _CONCEPT_SLOTS[best][1]}


# ---------------------------------------------------------------------------
# patch 生成
# ---------------------------------------------------------------------------


def _add_op(path: str, value: Any, *, after: str) -> dict:
    """``atlas_generator.apply_json_patch`` / assist UI が読める1操作（**add のみ**）。"""
    return {
        "op": "add",
        "path": path,
        "value_json": json.dumps(value, ensure_ascii=False),
        "before": None,
        "after": after,
    }


def build_gap_patch(
    draft_skeleton: Any,
    *,
    layer: str,
    parent_region_id: str = "",
    proposed_label: str,
) -> dict:
    """候補1件を次版下書きへ追加する JSON Patch を決定論生成する（§5.5）。

    戻り値: ``{"patch": [add 操作], "node_id": str, "layer": str,
    "parent_region_id": str, "node": 追加されるオブジェクト, "summary": 事実文}``。

    - ``layer='region'``: ``{path: "/regions/-"}`` に領域を追加する
    - ``layer='concept'``: ``{path: "/regions/{i}/concepts/-"}`` に概念を追加する
      （``parent_region_id`` が下書きに実在すること）

    id は ``_slugify`` → ``_unique_id``（領域 id と概念 id は同一名前空間）。日本語
    ラベルはスラッグ化で空になるため、``region{n}`` / ``{region_id}_c{n}`` の決定論
    フォールバックを使う（``atlas_generator.normalize_generated`` と同じ規則）。
    ``seed_status`` は付けない（maturity・review 情報を AI 由来で確定させない）。

    例外:

    - :class:`GapPatchError` — ラベルが空 / 層の語彙外 / 親領域が下書きに無い
    - :class:`SkeletonCapacityError` — 領域数が ``MAX_REGIONS`` に達している /
      親領域の概念数が ``MAX_CONCEPTS_PER_REGION`` に達している

    ``draft_skeleton`` は読み取りのみで変更しない。
    """
    label = _clean(proposed_label)
    if not label:
        raise GapPatchError("提案ラベルが空です")
    if not schema.is_valid_layer(layer):
        raise GapPatchError(f"層の指定が不正です: {layer!r}")

    regions, prefix = _regions_root(draft_skeleton)
    seen_ids = _existing_ids(regions)

    if layer == schema.GAP_LAYER_REGION:
        if len(regions) >= atlas_module.MAX_REGIONS:
            raise SkeletonCapacityError(
                f"この地図の領域は上限（{atlas_module.MAX_REGIONS}件）に達しています。"
                "追加するには次版で既存領域の整理が必要です。"
            )
        node_id = _unique_id(_slugify(label, f"region{len(regions) + 1}"), set(seen_ids))
        value = {
            "id": node_id,
            "label": label,
            "layout": _pick_region_slot(regions),
            "concepts": [],
        }
        return {
            "patch": [_add_op(f"{prefix}/regions/-", value, after=label)],
            "node_id": node_id,
            "layer": layer,
            "parent_region_id": "",
            "node": value,
            "summary": f"「{label}」を新しい領域として追加します（id: {node_id}）。",
        }

    parent_id = _clean(parent_region_id)
    if not parent_id:
        raise GapPatchError("概念の候補には親領域の指定が必要です")
    index = next(
        (
            i
            for i, region in enumerate(regions)
            if isinstance(region, Mapping) and _clean(region.get("id")) == parent_id
        ),
        -1,
    )
    if index < 0:
        raise GapPatchError(
            f"親領域 '{parent_id}' が次版の下書きにありません"
        )
    parent = regions[index]
    concepts = list(parent.get("concepts") or [])
    if len(concepts) >= atlas_module.MAX_CONCEPTS_PER_REGION:
        parent_label = _clean(parent.get("label")) or parent_id
        raise SkeletonCapacityError(
            f"領域「{parent_label}」の概念は上限"
            f"（{atlas_module.MAX_CONCEPTS_PER_REGION}件）に達しています。"
            "追加するには次版で既存概念の整理が必要です。"
        )

    node_id = _unique_id(
        _slugify(label, f"{parent_id}_c{len(concepts) + 1}"), set(seen_ids)
    )
    value = {
        "id": node_id,
        "label": label,
        "layout": _pick_concept_slot(concepts),
    }
    parent_label = _clean(parent.get("label")) or parent_id
    return {
        "patch": [
            _add_op(f"{prefix}/regions/{index}/concepts/-", value, after=label)
        ],
        "node_id": node_id,
        "layer": layer,
        "parent_region_id": parent_id,
        "node": value,
        "summary": (
            f"「{label}」を領域「{parent_label}」の概念として追加します"
            f"（id: {node_id}）。"
        ),
    }


__all__ = [
    "GapPatchError",
    "SkeletonCapacityError",
    "build_gap_patch",
]
