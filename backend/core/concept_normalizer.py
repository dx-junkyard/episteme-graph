"""Concept and notation normalization for theory-component graph matching.

分野への係留（提案 C2）:

- 正規化は **分野が分かっているときだけ** 行う。``cartridge_id`` が空なら
  ``load_cartridge`` を呼ばず（既定カートリッジへ縮退させない）、``raw`` の
  ままの結果を返す（``core/descent/engine.py`` / ``routes/learning.py`` の
  「空なら呼ばない」ガードと同じ規律）。
- 正規化は **置換ではなく追加**（vision §2.5 / 原則7 / 原則3）。元の名前
  （``name``）は書き換えず、正規形は ``canonical`` / ``canonical_name`` として
  併記する。
- 別名の供給源は2つあり、出所を混ぜない（原則8）:
  ``normalization_source="ontology_alias"`` はカートリッジ同梱ファイル由来、
  ``"teacher_alias"`` は教員が UI で確定した骨格別名（VA層
  ``atlas_anchor_aliases``）由来。人間が確定した別名を優先する（原則1）。

本モジュールは FastAPI / SQLAlchemy を import しない。教員別名は DB 由来なので、
呼び出し側（route 層）が ``{別名: 正規形}`` の dict を組み立てて注入する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.cartridges import load_cartridge


_GREEK = {
    "Λ": "lambda",
    "\\Lambda": "lambda",
    "λ": "lambda",
    "\\lambda": "lambda",
    "Δ": "delta",
    "\\Delta": "delta",
    "δ": "delta",
    "\\delta": "delta",
    "μ": "mu",
    "\\mu": "mu",
    "π": "pi",
    "\\pi": "pi",
}


@dataclass(frozen=True)
class NormalizedConcept:
    raw: str
    normalized: str
    canonical: str
    concept_type: str = "Concept"
    normalization_source: str = "string_normalized"

    def as_dict(self) -> dict[str, str]:
        return {
            "raw": self.raw,
            "normalized": self.normalized,
            "canonical": self.canonical,
            "concept_type": self.concept_type,
            "normalization_source": self.normalization_source,
        }


def normalize_key(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\$+", "", text)
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\\mathrm|\\text|\\mathcal|\\mathbb", "", text)
    for src, dst in _GREEK.items():
        text = text.replace(src, dst)
    text = text.replace("→", " to ").replace("\\to", " to ")
    text = re.sub(r"[_^]+", " ", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text.lower()).strip("_")
    return re.sub(r"_+", "_", text)


#: 出所ラベル（原則8: どの弁が効いたのかを混ぜない）。
SOURCE_STRING_NORMALIZED = "string_normalized"
SOURCE_ONTOLOGY_ALIAS = "ontology_alias"
SOURCE_TEACHER_ALIAS = "teacher_alias"


def teacher_alias_index(teacher_aliases: dict | None) -> dict[str, tuple[str, str]]:
    """教員が確定した別名 ``{別名: 正規形}`` を正規化キー索引へ変換する。

    値は文字列（正規形ラベル）でも ``{"canonical": ..., "concept_type": ...}``
    でもよい（骨格ノードは concept_type を持たないので既定は ``"Concept"``）。
    """
    index: dict[str, tuple[str, str]] = {}
    for alias, value in (teacher_aliases or {}).items():
        if isinstance(value, dict):
            canonical = str(value.get("canonical") or "").strip()
            concept_type = str(value.get("concept_type") or "Concept").strip() or "Concept"
        else:
            canonical = str(value or "").strip()
            concept_type = "Concept"
        key = normalize_key(alias)
        if not key or not canonical:
            continue
        index.setdefault(key, (canonical, concept_type))
    return index


def _alias_index(cartridge_id: str | None = None) -> dict[str, tuple[str, str]]:
    cartridge = load_cartridge(cartridge_id)
    index: dict[str, tuple[str, str]] = {}
    for item in cartridge.ontology.aliases:
        canonical = str(item.get("canonical") or "").strip()
        if not canonical:
            continue
        concept_type = str(item.get("concept_type") or "Concept")
        keys = [canonical] + [str(alias) for alias in item.get("aliases", []) if str(alias).strip()]
        for key in keys:
            index[normalize_key(key)] = (canonical, concept_type)
    for pattern in cartridge.ontology.notation_patterns:
        concept_type = str(pattern.get("concept_type") or "Concept")
        raw_pattern = str(pattern.get("pattern") or "")
        if raw_pattern:
            index.setdefault(normalize_key(raw_pattern), (raw_pattern, concept_type))
    return index


def normalize_concept(
    value: Any,
    concept_type: str = "Concept",
    cartridge_id: str | None = None,
    *,
    teacher_aliases: dict | None = None,
) -> NormalizedConcept:
    """1概念名を正規化する（分野が空なら文字列正規化まで）。

    照合順は **教員が確定した別名 → カートリッジ同梱の別名 → 文字列正規化**
    （人間の確定を機械の表より先に見る、原則1）。``cartridge_id`` が空のときは
    カートリッジを読まない（既定カートリッジへ縮退させない）。
    """
    raw = str(value or "").strip()
    key = normalize_key(raw)

    teacher_index = teacher_alias_index(teacher_aliases)
    if key and key in teacher_index:
        canonical, alias_type = teacher_index[key]
        return NormalizedConcept(
            raw=raw,
            # 正規形が非ASCII（日本語ラベル等）だと normalize_key が空になるため、
            # そのときは入力側のキーを残す（空の normalized は行ごと落とされる）。
            normalized=normalize_key(canonical) or key,
            canonical=canonical,
            concept_type=alias_type,
            normalization_source=SOURCE_TEACHER_ALIAS,
        )

    # 分野が分からないときは分野語彙を読まない（fail-closed, 原則11）。
    if key and (cartridge_id or "").strip():
        aliases = _alias_index(cartridge_id)
        if key in aliases:
            canonical, alias_type = aliases[key]
            return NormalizedConcept(
                raw=raw,
                normalized=normalize_key(canonical) or key,
                canonical=canonical,
                concept_type=alias_type,
                normalization_source=SOURCE_ONTOLOGY_ALIAS,
            )
    return NormalizedConcept(
        raw=raw, normalized=key, canonical=raw, concept_type=concept_type or "Concept",
    )


def normalize_concepts(
    items: list[dict],
    cartridge_id: str | None = None,
    *,
    teacher_aliases: dict | None = None,
) -> list[dict]:
    """概念リストに正規形を**併記**して返す（元の ``name`` は書き換えない）。

    追加されるキー: ``raw`` / ``normalized`` / ``canonical`` / ``canonical_name``
    / ``concept_type`` / ``normalization_source``。``name`` は入力のまま残す
    （vision §2.5「正規化は追加であって置換ではない」・原則3「情報を落とさない」）。
    """
    normalized = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        concept = normalize_concept(
            item.get("name") or item.get("label") or "",
            item.get("concept_type") or "Concept",
            cartridge_id,
            teacher_aliases=teacher_aliases,
        )
        if not concept.normalized:
            continue
        merged = dict(item)
        original_name = str(item.get("name") or item.get("label") or "").strip()
        merged.update(concept.as_dict())
        # 元の名前を残し、正規形は併記に留める（置換しない）。
        merged["name"] = original_name or concept.canonical
        merged["canonical_name"] = concept.canonical
        normalized.append(merged)
    return normalized
