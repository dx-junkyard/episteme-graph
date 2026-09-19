"""主張の概念接地（CG・§5 の後段フック本体）— 決定論の純関数。

正本: ``docs/features/claim_concept_grounding_design.md`` §5（不変条項 CG1〜CG7 は §2）。

``dsl_linking`` の直後に走るフック（``orchestrator._hook_claim_concept_grounding``）が
この関数を呼び、次の 2 つを主張の ``concepts`` に**追加**する:

① **本文の語境界照合** — :class:`core.library.concept_dictionary.ConceptDictionary`
  （レジストリの確定ラベル + カートリッジ別名 + DSL ノード名）が主張本文に**語として**
  現れるか。部分文字列一致は書かない（P0-2 / F-7 の再発防止）。
①' **DSL の直接参照** — DSL ノードの ``source_refs.claim_ids`` がその主張を指していれば、
  本文に現れなくてもノード名を付ける（``source="dsl_reference"``。LLM が結んだ参照を
  写しただけなので ``mapping_justification="llm_candidate"``）。

不変条項の写像:

- **CG1 A層非改変** — ``src/episteme_graph/agents/`` のコードは触らない。ここで扱うのは
  A層 dataclass の**値**（``ClaimObjectRecord.concepts`` への追加）だけ。
- **CG3 概念は候補・確定は人間** — ``concept_assignment_status`` / ``is_atomic`` /
  ``support_status`` を**書き換えない**（builder が計算した値を後段が上書きしない）。
- **CG5 情報を落とさない** — 既存の概念（記号 mention を含む）は残し、追加だけ行う。
  正規化で ``name`` を書き換えず、代表表記は出所側の ``canonical`` に併記する。
- **CG6 記号は概念にしない** — 辞書側で ``is_symbol_like_concept_name`` を通っており、
  ①' の DSL ノード名もここで同じ判定を掛ける。
- **CG7 数値非表示** — 一致件数・被覆率は :meth:`GroundingResult.to_dict` の
  ``coverage`` 材料（population / processed）としてのみ artifact に残す。

FastAPI / ``core.llm`` を import しない（開発ルール2 / core/ 共通ルール）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from core.atlas_gaps.schema import normalize_label
from episteme_graph.agents.claim_object_builder.schema import ClaimConcept
from episteme_graph.agents.component_assembly.schema import is_symbol_like_concept_name

from .concept_dictionary import (
    MAX_CONCEPTS_PER_CLAIM,
    SOURCE_DSL_REFERENCE,
    ConceptDictionary,
    justification_for_source,
)

logger = logging.getLogger(__name__)

__all__ = [
    "GroundingResult",
    "REASON_NO_MATCH",
    "ground_claims",
]

#: 辞書のどれにも当たらなかった主張の理由コード（coverage の ``reasons``）。
REASON_NO_MATCH = "no_dictionary_match"


@dataclass
class GroundingResult:
    """接地の結果（artifact ``claim_concept_grounding`` の素）。

    ``claims`` は ``{claim_id: [{normalized, name, canonical, source, entry_id,
    mapping_justification}]}``。永続化（``persist_qualified_claims``）が
    ``theory_claims.concepts`` の各要素へ ``normalized`` キーで additive にマージする。
    """

    claims: dict[str, list[dict]] = field(default_factory=dict)
    population: int = 0
    processed: int = 0
    reasons: list[str] = field(default_factory=list)
    claims_changed: int = 0

    def to_dict(self) -> dict:
        return {
            "claims": {cid: [dict(item) for item in items] for cid, items in self.claims.items()},
            "population": self.population,
            "processed": self.processed,
            "reasons": list(self.reasons),
        }


def _concept_key(concept: Any) -> str:
    """既存概念の突合キー（``normalized`` 優先・無ければ ``name``）。"""
    raw = getattr(concept, "normalized", None) or getattr(concept, "name", None)
    if raw is None and isinstance(concept, dict):
        raw = concept.get("normalized") or concept.get("name")
    return normalize_label(str(raw or ""))


def _concept_name(concept: Any) -> str:
    raw = getattr(concept, "name", None)
    if raw is None and isinstance(concept, dict):
        raw = concept.get("name") or concept.get("normalized")
    return str(raw or "")


def _claim_text(claim: Any) -> str:
    """照合対象の本文（claim 本文 + 正規化文。どちらも原文の言い換えではない）。"""
    text = str(getattr(claim, "text", "") or "")
    normalized = str(getattr(claim, "normalized_text", "") or "")
    if normalized and normalized not in text:
        return f"{text} {normalized}"
    return text


def _dsl_reference_index(dsl: Any) -> dict[str, list[tuple[str, str]]]:
    """``claim_id -> [(ノード名, ノード型)]``（``source_refs.claim_ids`` の直接参照）。"""
    index: dict[str, list[tuple[str, str]]] = {}
    for node in getattr(dsl, "nodes", None) or []:
        value = str(getattr(node, "node_value", "") or "").strip()
        if not value or is_symbol_like_concept_name(value):
            continue
        refs = getattr(node, "source_refs", None) or {}
        if not isinstance(refs, dict):
            continue
        node_type = str(getattr(node, "node_type", "") or "unknown") or "unknown"
        for claim_id in refs.get("claim_ids") or ():
            key = str(claim_id or "").strip()
            if not key:
                continue
            bucket = index.setdefault(key, [])
            if (value, node_type) not in bucket:
                bucket.append((value, node_type))
    return index


def ground_claims(
    claim_objects: Any,
    dsl: Any,
    dictionary: ConceptDictionary,
) -> GroundingResult:
    """主張の ``concepts`` に概念層の mention を追加する（``claim_objects`` を**その場で**変更）。

    変更するのは呼び出し側が持つ **in-memory の値**だけで、``claim_object_builder``
    artifact（生成ログ）は書き換えない（KO6。2026-09-13 のレビュー Phase 3 A層⚠）。
    永続化への反映は、戻り値を専用 artifact ``claim_concept_grounding`` に残し、
    ``persist_qualified_claims`` がそれを join して ``theory_claims.concepts`` へ
    additive マージする経路で行う（CG §6）。戻り値は出所の記録と取りこぼし報告の素。

    Args:
        claim_objects: ``ClaimObjectBuildResult``（``claims`` を持つもの）。
        dsl: ``DSLLinkingResult``（省略可。``None`` なら ①' を行わない）。
        dictionary: 本文照合に使う辞書（DSL ノードを含めて組み立て済みのもの）。

    Returns:
        :class:`GroundingResult`。辞書が空なら概念は 1 つも足さず、
        ``processed`` は既存の概念層 mention の数だけを正直に数える。
    """
    result = GroundingResult()
    claims = list(getattr(claim_objects, "claims", None) or [])
    result.population = len(claims)
    if not claims:
        return result

    references = _dsl_reference_index(dsl) if dsl is not None else {}

    for claim in claims:
        claim_id = str(getattr(claim, "claim_id", "") or "")
        concepts = list(getattr(claim, "concepts", None) or [])
        existing_keys = {key for key in (_concept_key(c) for c in concepts) if key}
        added: list[dict] = []

        def _append(key: str, name: str, concept_type: str, provenance: dict) -> None:
            if not key or key in existing_keys:
                return
            if len(concepts) >= MAX_CONCEPTS_PER_CLAIM:
                return
            concepts.append(
                ClaimConcept(name=name, normalized=key, concept_type=concept_type or "unknown")
            )
            existing_keys.add(key)
            added.append(
                {
                    "normalized": key,
                    "name": name,
                    "canonical": provenance.get("canonical") or name,
                    "source": provenance.get("source") or "",
                    "entry_id": provenance.get("entry_id"),
                    "mapping_justification": provenance.get("mapping_justification")
                    or justification_for_source(provenance.get("source") or ""),
                }
            )

        # ① 本文の語境界照合（辞書順 = registry → cartridge → dsl の決定論）。
        for key, surface in dictionary.match(_claim_text(claim)):
            provenance = dictionary.provenance(key) or {}
            _append(key, surface, str(provenance.get("concept_type") or "unknown"), provenance)

        # ①' DSL ノードからの直接参照（本文に現れなくても付ける）。
        for value, node_type in references.get(claim_id, ()):
            key = normalize_label(value)
            reference_provenance = {
                "source": SOURCE_DSL_REFERENCE,
                "entry_id": None,
                "canonical": value,
                "concept_type": node_type,
                "mapping_justification": justification_for_source(SOURCE_DSL_REFERENCE),
            }
            registered = dictionary.provenance(key)
            if registered is not None and registered.get("entry_id"):
                # 同じ名前がレジストリにもあるなら entry_id は落とさない（CG5）。
                reference_provenance["entry_id"] = registered.get("entry_id")
            _append(key, value, node_type, reference_provenance)

        if added:
            _set_concepts(claim, concepts)
            result.claims[claim_id or _fallback_claim_key(claim)] = added
            result.claims_changed += 1

        if _has_concept_layer_mention(concepts):
            result.processed += 1

    if result.processed < result.population:
        result.reasons.append(REASON_NO_MATCH)
    return result


def _set_concepts(claim: Any, concepts: list) -> None:
    """``concepts`` を書き戻す（``concept_assignment_status`` には触らない = CG3）。"""
    try:
        setattr(claim, "concepts", concepts)
    except Exception:  # noqa: BLE001 — 1 件書けなくても他の主張の接地は残す
        logger.warning("failed to attach concepts to a claim (non-fatal)", exc_info=True)


def _has_concept_layer_mention(concepts: list) -> bool:
    """概念層（= 記号でない名前）の mention を 1 つ以上持つか（CG6 と同じ判定）。"""
    for concept in concepts or ():
        name = _concept_name(concept)
        if name and not is_symbol_like_concept_name(name):
            return True
    return False


def _fallback_claim_key(claim: Any) -> str:
    """``claim_id`` を持たない主張の記録キー（捏造せず、あるものを使う）。"""
    for attr in ("content_hash", "normalized_text", "text"):
        value = str(getattr(claim, attr, "") or "").strip()
        if value:
            return value[:64]
    return ""


def merge_grounding_into_concepts(
    concepts: Any, grounded: Optional[list]
) -> list:
    """``theory_claims.concepts`` の各要素へ出所を additive にマージする（§6）。

    永続化（``persist_qualified_claims``）から呼ぶ純関数。``normalized`` キーで突合し、
    当たらない要素はそのまま返す（既存キー・既存の意味は変えない）。
    """
    items = list(concepts or [])
    if not grounded:
        return items
    by_key: dict[str, dict] = {}
    for item in grounded:
        if not isinstance(item, dict):
            continue
        key = normalize_label(str(item.get("normalized") or item.get("name") or ""))
        if key:
            by_key[key] = item
    if not by_key:
        return items
    merged: list = []
    for concept in items:
        if not isinstance(concept, dict):
            merged.append(concept)
            continue
        key = normalize_label(
            str(concept.get("normalized") or concept.get("name") or "")
        )
        provenance = by_key.get(key)
        if provenance is None:
            merged.append(concept)
            continue
        enriched = dict(concept)
        for field_name in ("source", "entry_id", "mapping_justification", "canonical"):
            value = provenance.get(field_name)
            if value not in (None, "") and field_name not in enriched:
                enriched[field_name] = value
        merged.append(enriched)
    return merged
