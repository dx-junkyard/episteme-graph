"""分野マップの関係表示（RE層）— 骨格 draft への決定論 JSON Patch 生成（設計書 §4）。

正本: ``docs/features/atlas_relation_edges_design.md`` §4 / §5。

このモジュールの立場（``core/atlas_gaps/patching.py`` と同型）:

- **LLM を使わない**。辺の追加は単一の ``add`` に落ちるので決定論生成できる。
- **DB に触らない**。draft の読み書きは ``core/atlas_store.py``、適用は教員の既存
  ``PUT draft``（revision 楽観ロック）。ここは patch を**返すだけ**である
  （RE3 / KN-3 / AB4: サーバ・AI が骨格 draft を書く経路を作らない）。
- **入力を変更しない**。``draft_skeleton`` は読み取りのみで、呼び出し側のプレビューが
  ``core.atlas_generator.apply_json_patch``（非破壊）で適用する。
- **op は ``add`` のみ**（additive-only を継承。辺の削除・種別変更の patch を
  生成する経路を作らない）。

**apply_json_patch との関係（実装判断の記録）**: ``atlas.skeleton_to_dict`` は常に
``edges`` キー（空でもリスト）を出力し、``apply_json_patch`` の ``add`` は末尾トークン
``-`` のときリストへ append する。したがって本モジュールが返す
``{"op": "add", "path": ".../edges/-"}`` は**既存の apply_json_patch がそのまま適用できる**
（``atlas_generator`` には一切手を入れない）。専用の ``apply_edge_patch`` は設けない —
プレビューの ``patched_draft`` は route 層が ``apply_json_patch`` で組む。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from core import atlas as atlas_module
from core.atlas_edges import schema
from core.label_vocab import EDGE_KIND_LABELS


class EdgePatchError(ValueError):
    """patch を組めない（種別が語彙外・端点が draft に無い・既に同じ辺がある 等）。

    route は事実文（数値なし）へ変換する。
    """


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _skeleton_root(draft_skeleton: Any) -> tuple[Mapping[str, Any], str]:
    """骨格 dict と JSON Pointer の接頭辞を返す。

    ``skeleton_to_dict`` は ``{"atlas_skeleton": {...}}`` で包むが、
    ``atlas_generator.apply_json_patch`` は渡された dict を根とする Pointer を扱う。
    どちらの形で渡されても、**渡された dict を根とする**正しい path を返す。
    """
    if not isinstance(draft_skeleton, Mapping):
        raise EdgePatchError("draft_skeleton がオブジェクトではありません")
    inner = draft_skeleton.get("atlas_skeleton")
    if isinstance(inner, Mapping):
        return inner, "/atlas_skeleton"
    return draft_skeleton, ""


def _node_labels(root: Mapping[str, Any]) -> dict[str, str]:
    """draft に実在する node id → 表示ラベル（領域・概念は同一名前空間）。"""
    out: dict[str, str] = {}
    for region in root.get("regions") or []:
        if not isinstance(region, Mapping):
            continue
        region_id = _clean(region.get("id"))
        if region_id:
            out[region_id] = _clean(region.get("label")) or region_id
        for concept in region.get("concepts") or []:
            if not isinstance(concept, Mapping):
                continue
            concept_id = _clean(concept.get("id"))
            if concept_id:
                out[concept_id] = _clean(concept.get("label")) or concept_id
    return out


def _draft_edge_pairs(root: Mapping[str, Any]) -> set[tuple[str, str]]:
    """draft に既にある辺の**無向**ペア集合。"""
    pairs: set[tuple[str, str]] = set()
    for edge in root.get("edges") or []:
        if not isinstance(edge, Mapping):
            continue
        left, right = schema.undirected_pair(edge.get("from"), edge.get("to"))
        if left and right:
            pairs.add((left, right))
    return pairs


def _add_op(path: str, value: Any, *, after: str) -> dict:
    """``atlas_generator.apply_json_patch`` / assist UI が読める1操作（**add のみ**）。"""
    return {
        "op": "add",
        "path": path,
        "value_json": json.dumps(value, ensure_ascii=False),
        "before": None,
        "after": after,
    }


def build_edge_patch(
    draft_skeleton: Any, *, from_id: str, to_id: str, kind: str
) -> dict:
    """辺1本を次版下書きへ追加する JSON Patch を決定論生成する（§4 / §5）。

    戻り値: ``{"patch": [add 操作], "from_id", "to_id", "kind", "summary"}``。
    ``patch`` の path は ``{prefix}/edges/-``、value は
    ``{"from": ..., "to": ..., "kind": ...}``（``atlas.skeleton_from_dict`` が読む形）。

    例外（いずれも :class:`EdgePatchError`。route は 422 / 409 の事実文へ）:

    - 種別が ``core.atlas.EDGE_KINDS`` の語彙外
    - 端点が空 / 同一（自己ループ）
    - 端点が下書きに実在しない
    - 同じ無向ペアの辺が下書きに既にある（``validate_skeleton`` に重複検査が無いため、
      ここが防波堤になる）

    ``draft_skeleton`` は読み取りのみで変更しない。
    """
    edge_kind = _clean(kind)
    if edge_kind not in atlas_module.EDGE_KINDS:
        raise EdgePatchError(f"関係の種別が不正です: {kind!r}")

    left_raw = _clean(from_id)
    right_raw = _clean(to_id)
    if not left_raw or not right_raw:
        raise EdgePatchError("関係の両端の指定が必要です")
    if left_raw == right_raw:
        raise EdgePatchError("同じノード同士の関係は追加できません")

    root, prefix = _skeleton_root(draft_skeleton)
    labels = _node_labels(root)
    missing = [node_id for node_id in (left_raw, right_raw) if node_id not in labels]
    if missing:
        raise EdgePatchError(
            "次版の下書きにないノードです: " + "、".join(f"'{m}'" for m in missing)
        )

    left, right = schema.undirected_pair(left_raw, right_raw)
    if (left, right) in _draft_edge_pairs(root):
        raise EdgePatchError(
            f"「{labels[left]}」と「{labels[right]}」の関係は、"
            "すでに次版の下書きにあります。"
        )

    value = {"from": left, "to": right, "kind": edge_kind}
    kind_label = EDGE_KIND_LABELS.get(edge_kind, edge_kind)
    return {
        "patch": [
            _add_op(
                f"{prefix}/edges/-",
                value,
                after=f"{labels[left]} — {labels[right]}",
            )
        ],
        "from_id": left,
        "to_id": right,
        "kind": edge_kind,
        "summary": (
            f"「{labels[left]}」と「{labels[right]}」を"
            f"「{kind_label}」の関係として次版の下書きに追加します。"
        ),
    }


__all__ = [
    "EdgePatchError",
    "build_edge_patch",
]
