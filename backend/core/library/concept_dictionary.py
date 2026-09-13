"""主張に供給する概念辞書（主張の概念接地 CG・§4）。

正本: ``docs/features/claim_concept_grounding_design.md`` §4（不変条項 CG1〜CG7 は §2）。
親は概念レジストリ（``concept_registry_design.md``・KR1〜KR10）で、辞書の材料は
そのレジストリ（確定済みエントリとラベル）とカートリッジ別名、そして DSL ノード。

問題（K-2）: ``claim_object_builder`` は概念を自分で決めず、外から渡される辞書
（``concept_resolver`` / ``cartridge_ontology``）で本文を照合する設計だが、orchestrator は
``cartridge_ontology=None`` で組み立てていたため、分野未指定の解析経路では辞書が**空**に
なり、claim の ``concepts`` が式の記号だけになっていた。本モジュールはその注入口へ渡す
**材料**（辞書と resolver）を組み立てる。A層のコードは触らない（CG1）。

不変条項の写像:

- **CG2 決定論・LLM 0 回・embedding 0 回** — 照合は
  :func:`episteme_graph.agents.alias_matching.text_mentions_alias`（語境界付き・P0-2）だけ。
  ``str in str`` の部分一致は書かない（``SM`` が ``cosmological`` に当たる F-7 の再発）。
- **CG5 情報を落とさない** — 正規化で ``name`` を書き換えず、代表表記は ``canonical`` に
  併記する（KN-2）。同じ正規化キーに複数の出所があれば ``also_from`` に残す。
- **CG6 記号は概念にしない** — 辞書に載せる名前は
  :func:`episteme_graph.agents.component_assembly.schema.is_symbol_like_concept_name`
  （P0-3）で記号を除く。
- **分野は入口で選ぶ規律** — ``cartridge_id`` が空なら cartridge を**読まない**
  （既定カートリッジへ縮退させない。descent / learning / orchestrator と同じ規律）。
- **fail-soft** — DB 不達・カートリッジ不在は空辞書（従来動作＝辞書なしに戻るだけ）。

FastAPI / ``core.llm`` を import しない（開発ルール2 / core/ 共通ルール）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from core.atlas_gaps.schema import normalize_label
from episteme_graph.agents.alias_matching import text_mentions_alias
from episteme_graph.agents.cartridge_loader import CartridgeLoader, load_cartridge_or_none
from episteme_graph.agents.claim_object_builder.schema import ClaimConcept
from episteme_graph.agents.component_assembly.schema import is_symbol_like_concept_name

from . import registry
from . import schema
from . import store as library_store

logger = logging.getLogger(__name__)

__all__ = [
    "ConceptDictionary",
    "SOURCE_CARTRIDGE_ALIAS",
    "SOURCE_DSL_NODE",
    "SOURCE_DSL_REFERENCE",
    "SOURCE_PRIORITY",
    "SOURCE_REGISTRY_LABEL",
    "build_concept_dictionary",
    "cartridge_ontology_for",
    "justification_for_source",
    "make_concept_resolver",
]

# ── 概念の出所（CG4: 出所を混ぜない）─────────────────────────────────────────
SOURCE_REGISTRY_LABEL = "registry_label"
SOURCE_CARTRIDGE_ALIAS = "cartridge_alias"
SOURCE_DSL_NODE = "dsl_node"
#: DSL ノードが ``source_refs.claim_ids`` で直接指した主張への付与（①'）。辞書には
#: 入らない（本文照合の材料ではない）が、出所語彙としては同じ表に並べる。
SOURCE_DSL_REFERENCE = "dsl_reference"

#: 同じ正規化キーに複数の出所があるときの代表の順（先勝ち）。
SOURCE_PRIORITY: tuple[str, ...] = (
    SOURCE_REGISTRY_LABEL,
    SOURCE_CARTRIDGE_ALIAS,
    SOURCE_DSL_NODE,
)

#: 出所 → ``mapping_justification``（§4 末尾）。``cartridge_declared`` は**別名の由来**で
#: あって照合方法ではないので使わない。``dsl_reference`` だけが ``llm_candidate``
#: （LLM が主張とノードを結んだ参照を写しただけ）。
_JUSTIFICATION_BY_SOURCE: dict[str, str] = {
    SOURCE_REGISTRY_LABEL: schema.JUSTIFICATION_LEXICAL,
    SOURCE_CARTRIDGE_ALIAS: schema.JUSTIFICATION_LEXICAL,
    SOURCE_DSL_NODE: schema.JUSTIFICATION_LEXICAL,
    SOURCE_DSL_REFERENCE: schema.JUSTIFICATION_LLM,
}

#: 1 主張に付ける概念の上限（プロンプト予算と同じ考え方の定数。env で緩めない）。
MAX_CONCEPTS_PER_CLAIM = 8


def justification_for_source(source: str) -> str:
    """出所 → ``mapping_justification``（未知の出所は ``lexical_match`` に倒す）。"""
    return _JUSTIFICATION_BY_SOURCE.get(str(source or ""), schema.JUSTIFICATION_LEXICAL)


# ---------------------------------------------------------------------------
# 辞書
# ---------------------------------------------------------------------------


@dataclass
class ConceptDictionary:
    """正規化キー → ``{names, concept_type, source, entry_id, also_from, claim_ids}``。

    ``names`` は照合に使う表記（代表名 + 別名）で、``normalize_label`` で畳んだキーとは
    別に**元の表記のまま**保つ（CG5: 正規化で ``name`` を書き換えない）。
    """

    entries: dict[str, dict] = field(default_factory=dict)

    # -- 構築 ---------------------------------------------------------------

    def add(
        self,
        name: str,
        *,
        source: str,
        concept_type: str = "unknown",
        entry_id: str | None = None,
        aliases: Iterable[str] = (),
        claim_ids: Iterable[str] = (),
    ) -> str | None:
        """1 概念を足す。記号名（CG6）と空名は入れない。戻り値は正規化キー。"""
        canonical = str(name or "").strip()
        if not canonical or is_symbol_like_concept_name(canonical):
            return None
        key = normalize_label(canonical)
        if not key:
            return None
        surfaces = [canonical]
        for alias in aliases or ():
            token = str(alias or "").strip()
            if token and not is_symbol_like_concept_name(token) and token not in surfaces:
                surfaces.append(token)
        existing = self.entries.get(key)
        if existing is None:
            self.entries[key] = {
                "canonical": canonical,
                "names": surfaces,
                "concept_type": str(concept_type or "unknown") or "unknown",
                "source": source,
                "entry_id": entry_id or None,
                "also_from": [],
                "claim_ids": [str(c) for c in (claim_ids or ()) if str(c or "").strip()],
            }
            return key
        # 既出のキー: 代表は優先順（registry > cartridge > dsl）で決め、負けた側も
        # 出所として残す（CG5: 情報を落とさない）。
        for surface in surfaces:
            if surface not in existing["names"]:
                existing["names"].append(surface)
        for claim_id in claim_ids or ():
            token = str(claim_id or "").strip()
            if token and token not in existing["claim_ids"]:
                existing["claim_ids"].append(token)
        if _priority(source) < _priority(existing["source"]):
            if existing["source"] not in existing["also_from"]:
                existing["also_from"].append(existing["source"])
            existing["canonical"] = canonical
            existing["source"] = source
            existing["entry_id"] = entry_id or None
            if str(concept_type or "unknown") not in ("", "unknown"):
                existing["concept_type"] = str(concept_type)
        elif source != existing["source"] and source not in existing["also_from"]:
            existing["also_from"].append(source)
            if existing["entry_id"] is None and entry_id:
                existing["entry_id"] = entry_id
        return key

    # -- 読み出し -----------------------------------------------------------

    def __bool__(self) -> bool:  # 「辞書が空なら resolver を渡さない」判定に使う
        return bool(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def provenance(self, normalized: str) -> Optional[dict]:
        """正規化キーの出所（``{source, entry_id, canonical, concept_type, also_from}``）。"""
        item = self.entries.get(str(normalized or ""))
        if item is None:
            return None
        return {
            "source": item["source"],
            "entry_id": item["entry_id"],
            "canonical": item["canonical"],
            "concept_type": item["concept_type"],
            "also_from": list(item["also_from"]),
            "mapping_justification": justification_for_source(item["source"]),
        }

    def match(self, text: str) -> list[tuple[str, str]]:
        """本文に**語として**現れる概念を ``[(正規化キー, 一致した表記)]`` で返す。

        辞書の登録順（registry → cartridge → dsl）で決定論的。部分文字列一致は
        使わない（CG2 / P0-2）。
        """
        body = str(text or "")
        if not body:
            return []
        found: list[tuple[str, str]] = []
        for key, item in self.entries.items():
            for surface in item["names"]:
                if text_mentions_alias(body, surface):
                    found.append((key, surface))
                    break
        return found


def _priority(source: str) -> int:
    try:
        return SOURCE_PRIORITY.index(str(source))
    except ValueError:
        return len(SOURCE_PRIORITY)


# ---------------------------------------------------------------------------
# 材料の読み出し
# ---------------------------------------------------------------------------


def _add_registry_entries(dictionary: ConceptDictionary, session: Any) -> int:
    """② 確定済みエントリの名前 + alternate / hidden ラベル（``registry_label``）。"""
    try:
        entries = library_store.list_entries(include_candidates=False)
    except Exception:  # noqa: BLE001 — DB 不達は空辞書（fail-soft・従来動作）
        logger.warning("concept dictionary: registry lookup failed (non-fatal)", exc_info=True)
        return 0
    if not entries:
        return 0
    try:
        labels = registry.labels_for_entries(
            [entry.get("id") for entry in entries], include_hidden=True, session=session
        )
    except Exception:  # noqa: BLE001 — ラベルが引けなくても name だけで辞書は作れる
        logger.warning("concept dictionary: label lookup failed (non-fatal)", exc_info=True)
        labels = {}
    added = 0
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        aliases = [
            str(label.get("label") or "")
            for label in labels.get(entry_id) or ()
        ]
        # 既存 L層エントリの ``aliases`` 列（ラベル表へミラー済み）も落とさない。
        aliases.extend(str(a or "") for a in (entry.get("aliases") or ()))
        if dictionary.add(
            entry.get("name") or "",
            source=SOURCE_REGISTRY_LABEL,
            concept_type=str(entry.get("entry_type") or "unknown"),
            entry_id=entry_id or None,
            aliases=aliases,
        ):
            added += 1
    return added


#: cartridge が解決できない run で ``ClaimObjectBuilder`` に渡す ontology（CG3）。
#: A層の ``_concepts_are_cartridge_backed`` は空 dict + resolver を「信頼」して
#: ``source_backed`` を返すため、**非空だが既知集合が空**の dict を渡して
#: ``inferred`` に留める。別名・型は入れない（レジストリ由来の概念を cartridge 由来と
#: 偽装しない = 原則8）。
REGISTRY_ONLY_ONTOLOGY: dict = {
    "aliases": {},
    "concept_types": {},
    "provenance": "concept_registry",
}


def cartridge_ontology_for(cartridge_id: str | None) -> Optional[dict]:
    """``{"aliases": {canonical: [...]}, "concept_types": {canonical: type}}`` か ``None``。

    ``cartridge_id`` が空なら **cartridge を読まない**（既定カートリッジへ縮退させない）。
    形は ``ClaimObjectBuilder`` が受け取る ``cartridge_ontology`` の契約に合わせる
    （A層は非改変なので、こちら側が合わせる）。
    """
    if not str(cartridge_id or "").strip():
        return None
    context = load_cartridge_or_none(
        CartridgeLoader(), cartridge_id, log_context=" (concept dictionary)"
    )
    if context is None:
        return None
    aliases = dict(context.aliases or {})
    ontology = context.ontology or {}
    concept_types: dict[str, str] = {}
    for item in ontology.get("concept_types") or ():
        if not isinstance(item, dict):
            continue
        type_id = str(item.get("id") or "").strip()
        if not type_id:
            continue
        for example in item.get("examples") or ():
            name = str(example or "").strip()
            if name:
                concept_types.setdefault(name, type_id)
    if not aliases and not concept_types:
        return None
    return {"aliases": aliases, "concept_types": concept_types}


def _add_cartridge_aliases(dictionary: ConceptDictionary, ontology: dict | None) -> int:
    """③ カートリッジの別名（``cartridge_alias``）。"""
    if not ontology:
        return 0
    aliases = ontology.get("aliases") or {}
    concept_types = ontology.get("concept_types") or {}
    added = 0
    for canonical, alias_list in aliases.items():
        name = str(canonical or "").strip()
        if not name:
            continue
        if dictionary.add(
            name,
            source=SOURCE_CARTRIDGE_ALIAS,
            concept_type=str(concept_types.get(name, "unknown") or "unknown"),
            aliases=[str(a or "") for a in (alias_list or ())],
        ):
            added += 1
    return added


def _add_dsl_nodes(dictionary: ConceptDictionary, dsl: Any) -> int:
    """① DSL ノード名（``dsl_node``）。``source_refs.claim_ids`` も持ち回る。"""
    nodes = getattr(dsl, "nodes", None) or []
    added = 0
    for node in nodes:
        value = str(getattr(node, "node_value", "") or "").strip()
        if not value:
            continue
        refs = getattr(node, "source_refs", None) or {}
        claim_ids = refs.get("claim_ids") if isinstance(refs, dict) else None
        if dictionary.add(
            value,
            source=SOURCE_DSL_NODE,
            concept_type=str(getattr(node, "node_type", "") or "unknown") or "unknown",
            claim_ids=claim_ids or (),
        ):
            added += 1
    return added


def build_concept_dictionary(
    session: Any = None,
    *,
    cartridge_id: str | None,
    dsl: Any = None,
) -> ConceptDictionary:
    """§4 の辞書を組み立てる（決定論・LLM 0 回・embedding 0 回）。

    Args:
        session: ラベル読み出しに使うセッション。``None`` なら
            :func:`core.library.registry.labels_for_entries` が自前で開閉する。
        cartridge_id: 解析 run の分野。**空なら cartridge を読まない**。
        dsl: ``DSLLinkingResult``（省略可）。渡されたときだけ ①（DSL ノード名）を足す。

    Returns:
        :class:`ConceptDictionary`。材料が 1 つも無ければ空（呼び出し側は
        「空なら resolver を渡さない」= 従来動作に縮退する）。
    """
    dictionary = ConceptDictionary()
    _add_registry_entries(dictionary, session)
    try:
        _add_cartridge_aliases(dictionary, cartridge_ontology_for(cartridge_id))
    except Exception:  # noqa: BLE001 — カートリッジが読めなくても辞書は作れる
        logger.warning("concept dictionary: cartridge lookup failed (non-fatal)", exc_info=True)
    if dsl is not None:
        try:
            _add_dsl_nodes(dictionary, dsl)
        except Exception:  # noqa: BLE001
            logger.warning("concept dictionary: DSL nodes skipped (non-fatal)", exc_info=True)
    return dictionary


# ---------------------------------------------------------------------------
# resolver（``ClaimObjectBuilder`` の既存注入口へ渡す）
# ---------------------------------------------------------------------------


def make_concept_resolver(dictionary: ConceptDictionary) -> Callable[..., list]:
    """``Callable[[text, role_labels, ontology], list[ClaimConcept]]`` を作る。

    戻り値の型は A層の契約（``coerce_claim_concepts`` が包む）。出所は resolver の
    外側で :meth:`ConceptDictionary.provenance` から引く（``ClaimConcept`` に列を
    足せないため = CG4）。
    """

    def resolve(text: str, role_labels: Any = None, ontology: Any = None) -> list[ClaimConcept]:
        concepts: list[ClaimConcept] = []
        for key, surface in dictionary.match(text):
            item = dictionary.entries.get(key) or {}
            concepts.append(
                ClaimConcept(
                    name=surface,
                    normalized=key,
                    concept_type=str(item.get("concept_type") or "unknown"),
                )
            )
            if len(concepts) >= MAX_CONCEPTS_PER_CLAIM:
                break
        return concepts

    return resolve
