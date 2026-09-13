"""分野マップのノード版間対応 — 候補導出・版連鎖の解決・凍結 body の検証（NC1〜NC8）。

正本設計書: ``docs/features/atlas_node_correspondence_design.md``（§4）。親は
``docs/architecture/knowledge_structure_review_2026-09-12.md`` D4 / 付属調査 E **K-6**。

分野の地図は改訂のたびに版を凍結するが、改訂が「AI が下書きを生成し直す」流れなので
同じ概念でも node_id が振り直される。論文の配置（``landscape_placements``）は
``skeleton_version`` 付きの node_id を指すため、新版から見ると教員が確認済みにした
位置づけが消えたように見える。本モジュールは既存の ``AtlasSkeleton.id_migrations``
（§16-4 の改版時 id 移行規則）を**版間対応表の格納庫**として使い、

1. 凍結前の影響確認に並べる**候補**を決定論的に導出し（:func:`derive_correspondence_candidates`）、
2. 教員が確定した対応だけを凍結 body から検証して受け取り（:func:`validate_migrations`）、
3. 読み手が旧 node_id を現行 node_id へ**読み替える**ための解決器を組む（:func:`build_node_resolver`）。

本モジュールが構造として守るもの:

- **NC1 AI が ``atlas_skeletons`` に書かない** — ここには SQL が1文も無い（純データ + 純関数）。
  骨格へ書き込むのは教員の凍結操作（route 層）だけである。
- **NC2 候補は決定論・非LLM・embedding 0 回** — 候補の根拠は ①正規化ラベルの完全一致
  ②教員確定別名の一致 ③概念レジストリ（confirmed）経由 の3経路だけで、cosine を使わない。
  ``justification`` は :data:`ALLOWED_JUSTIFICATIONS`（``lexical_match`` / ``manual_curation``）
  の2語彙しか取らない（``core.schema.MAPPING_JUSTIFICATIONS`` の部分集合）。
- **NC5 自動付け替えをしない** — :class:`NodeResolver` は**読み替え**しか提供しない。
  配置行の ``node_id`` を書き換える関数はここにも他所にも無い。
- **NC6 数値非表示・閉世界** — 一致件数・対応率・cosine を返さない。事実文は
  :data:`FACT_*` の固定文だけで、他所で文言を組み立てない。
- **NC7 1 旧ノード → 1 新ノード** — ``from`` は一意（重複は :class:`ValueError`）。
  複数の旧ノードが1つの新ノードへ束なる（merge）のは可。
- **NC8 版の連鎖で解く** — 解決は全凍結版の ``id_migrations`` を版順に辿る。循環・欠落は
  ``unmapped``（例外を出さない）。

FastAPI / sqlalchemy / ``core.llm`` を import しない（開発ルール2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from core.atlas import IdMigration
from core.atlas_gaps.schema import normalize_label

__all__ = [
    "ALLOWED_JUSTIFICATIONS",
    "FACT_CANDIDATES_NOT_PRESELECTED",
    "FACT_LEXICAL_ONLY",
    "FACT_MIGRATED",
    "FACT_UNMAPPED_PLACEMENT",
    "FACT_UNMAPPED_PLACEMENT_NO_OLD_VERSION",
    "FACT_UNMATCHED_REMOVED",
    "JUSTIFICATION_LEXICAL",
    "JUSTIFICATION_MANUAL",
    "NODE_STATUS_CURRENT",
    "NODE_STATUS_MIGRATED",
    "NODE_STATUS_UNMAPPED",
    "NODE_STATUSES",
    "NodeResolver",
    "VIA_ALIAS",
    "VIA_LABEL",
    "VIA_REGISTRY",
    "build_node_resolver",
    "derive_correspondence_candidates",
    "migrated_fact",
    "unmapped_placement_fact",
    "validate_migrations",
]


# ---------------------------------------------------------------------------
# 語彙（NC2 / NC6）
# ---------------------------------------------------------------------------

#: 候補の根拠（``core.schema.MAPPING_JUSTIFICATIONS`` の部分集合。ここに
#: ``vector_similarity`` / ``llm_candidate`` を足さない = NC2 の構造的な担保）。
JUSTIFICATION_LEXICAL = "lexical_match"
JUSTIFICATION_MANUAL = "manual_curation"
ALLOWED_JUSTIFICATIONS: tuple[str, ...] = (JUSTIFICATION_LEXICAL, JUSTIFICATION_MANUAL)

#: どの経路で候補が立ったか（教員向けの内訳。学習者 DTO には出さない = §6）。
VIA_LABEL = "label"        # 経路①: 正規化ラベルの完全一致
VIA_ALIAS = "alias"        # 経路②: 教員確定別名（atlas_anchor_aliases）の一致
VIA_REGISTRY = "registry"  # 経路③: 概念レジストリ（confirmed）で同じ entry に結ばれている

#: 同じ ``from`` に複数の候補が立ったときの採用順（① > ③ > ②。設計書 §4）。
_VIA_PRIORITY: dict[str, int] = {VIA_LABEL: 0, VIA_REGISTRY: 1, VIA_ALIAS: 2}

_VIA_JUSTIFICATION: dict[str, str] = {
    VIA_LABEL: JUSTIFICATION_LEXICAL,
    VIA_ALIAS: JUSTIFICATION_LEXICAL,
    VIA_REGISTRY: JUSTIFICATION_MANUAL,
}

#: 旧 node_id を現行版から見たときの状態（読み手 DTO の ``node_status``）。
NODE_STATUS_CURRENT = "current"    # 現行版にそのまま在る
NODE_STATUS_MIGRATED = "migrated"  # 対応表で現行版のノードへ読み替えられる
NODE_STATUS_UNMAPPED = "unmapped"  # 現行版に対応する場所がない
NODE_STATUSES: tuple[str, ...] = (
    NODE_STATUS_CURRENT,
    NODE_STATUS_MIGRATED,
    NODE_STATUS_UNMAPPED,
)

NODE_KIND_REGION = "region"
NODE_KIND_CONCEPT = "concept"


# ---------------------------------------------------------------------------
# 事実文（NC6: 数値を書かない。文言はここだけで持つ）
# ---------------------------------------------------------------------------

#: 候補の作り方を明かす一行（「近い」で並べていないことを教員に見せる）。
FACT_LEXICAL_ONLY = (
    "候補は、正規化した名前の一致と、教員が確定した別名・登録概念だけから作っています。"
    "近さや意味の推定はしていません。"
)

#: 候補を既定で選択済みにしない（NC3）ことを明示する一行。
FACT_CANDIDATES_NOT_PRESELECTED = (
    "候補は選択済みにしていません。凍結に含める対応は、ひとつずつ確認して選んでください。"
)

#: 対応先の候補が立たなかった旧ノードがあるときの一行（見送れることを書く）。
FACT_UNMATCHED_REMOVED = (
    "対応先の候補が見つからなかった項目があります。"
    "対応を付けないまま凍結できます（前の版で確認された記録は残ります）。"
)

#: 読み替えができなかった配置に添える事実文（学習者にもこの一行だけを出す。NC6）。
FACT_UNMAPPED_PLACEMENT = (
    "前の版（版 {old}）の地図で確認された位置づけで、"
    "現行版（版 {new}）に対応する場所がありません。"
)

#: 旧版の版数が分からないときの言い換え（版数を推測して書かない）。
FACT_UNMAPPED_PLACEMENT_NO_OLD_VERSION = (
    "前の版の地図で確認された位置づけで、現行版（版 {new}）に対応する場所がありません。"
)

#: 対応づけの結果（教員向け詳細のみ。学習者には出さない）。
FACT_MIGRATED = (
    "版 {old} のノード「{from_label}」は版 {new} の「{to_label}」に対応づけられています。"
)


def unmapped_placement_fact(old_version: str, new_version: str) -> str:
    """:data:`FACT_UNMAPPED_PLACEMENT` の事実文（旧版が不明なら版数を書かない形）。"""
    old = str(old_version or "").strip()
    new = str(new_version or "").strip()
    if not old:
        return FACT_UNMAPPED_PLACEMENT_NO_OLD_VERSION.format(new=new)
    return FACT_UNMAPPED_PLACEMENT.format(old=old, new=new)


def migrated_fact(
    *, old_version: str, new_version: str, from_label: str, to_label: str
) -> str:
    """:data:`FACT_MIGRATED` の事実文（教員向け詳細）。"""
    return FACT_MIGRATED.format(
        old=str(old_version or "").strip(),
        new=str(new_version or "").strip(),
        from_label=str(from_label or "").strip(),
        to_label=str(to_label or "").strip(),
    )


# ---------------------------------------------------------------------------
# 骨格 → ノード索引（純関数。AtlasSkeleton / dict の両方を受ける）
# ---------------------------------------------------------------------------


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _nodes_of(skeleton: Any) -> dict[str, dict]:
    """``{node_id: {"id", "label", "kind"}}``（region → concept の順で決定論）。"""
    out: dict[str, dict] = {}
    if skeleton is None:
        return out
    for region in _attr(skeleton, "regions") or ():
        region_id = str(_attr(region, "id") or "")
        if not region_id:
            continue
        out.setdefault(
            region_id,
            {
                "id": region_id,
                "label": str(_attr(region, "label") or ""),
                "kind": NODE_KIND_REGION,
            },
        )
        for concept in _attr(region, "concepts") or ():
            concept_id = str(_attr(concept, "id") or "")
            if not concept_id:
                continue
            out.setdefault(
                concept_id,
                {
                    "id": concept_id,
                    "label": str(_attr(concept, "label") or ""),
                    "kind": NODE_KIND_CONCEPT,
                },
            )
    return out


def _node_ids_of(skeleton: Any) -> set[str]:
    return set(_nodes_of(skeleton))


def _migrations_of(skeleton: Any) -> list[tuple[str, str, str]]:
    """``[(from_id, to_id, version)]``（空要素は落とす）。"""
    out: list[tuple[str, str, str]] = []
    for migration in _attr(skeleton, "id_migrations") or ():
        from_id = str(_attr(migration, "from_id") or _attr(migration, "from") or "")
        to_id = str(_attr(migration, "to_id") or _attr(migration, "to") or "")
        version = str(_attr(migration, "version") or "")
        if from_id and to_id:
            out.append((from_id, to_id, version))
    return out


# ---------------------------------------------------------------------------
# 候補の導出（§4・NC2）
# ---------------------------------------------------------------------------


def _candidate(from_node: Mapping[str, Any], to_node: Mapping[str, Any], via: str) -> dict:
    return {
        "from_id": str(from_node.get("id") or ""),
        "from_label": str(from_node.get("label") or ""),
        "from_kind": str(from_node.get("kind") or ""),
        "to_id": str(to_node.get("id") or ""),
        "to_label": str(to_node.get("label") or ""),
        "to_kind": str(to_node.get("kind") or ""),
        "via": via,
        "justification": _VIA_JUSTIFICATION[via],
    }


def _registry_node_groups(registry_links: Iterable[Mapping[str, Any]] | None) -> list[set[str]]:
    """confirmed のレジストリリンクから ``entry_id`` ごとの node_id 集合を作る（経路③）。

    ``status`` が ``confirmed`` の行だけを使う（候補のリンクを根拠にしない = KR2 / NC2）。
    """
    grouped: dict[str, set[str]] = {}
    for link in registry_links or []:
        if not isinstance(link, Mapping):
            continue
        if str(link.get("status") or "") != "confirmed":
            continue
        entry_id = str(link.get("entry_id") or "")
        node_id = str(link.get("node_id") or "")
        if not entry_id or not node_id:
            continue
        grouped.setdefault(entry_id, set()).add(node_id)
    return [nodes for _entry, nodes in sorted(grouped.items()) if len(nodes) > 1]


def derive_correspondence_candidates(
    *,
    frozen: Any,
    draft: Any,
    confirmed_aliases_by_node: Mapping[str, Sequence[str]] | None = None,
    registry_links: Iterable[Mapping[str, Any]] | None = None,
) -> dict:
    """現行凍結版から draft へ消えるノードの**対応候補**を決定論的に導出する（§4）。

    引数:
        frozen: 現行の凍結骨格（``AtlasSkeleton`` / dict / ``None``）。``None``（初回凍結）は
            removed が空なので候補も空。
        draft: 凍結しようとしている draft。``None`` なら全て空。
        confirmed_aliases_by_node: ``{node_id: [alias, ...]}``
            （``core.atlas_vectors.store.confirmed_aliases_by_node`` の結果。教員が確定した
            別名だけが入っている前提 = VA6）。
        registry_links: 概念レジストリ ↔ 骨格 node のリンク行
            （``core.library.registry.list_node_links`` の結果）。``status='confirmed'`` の
            行だけを使う。

    戻り値（数値なし・NC6）::

        {"candidates": [...], "alternatives": [...], "unmatched_removed": [...],
         "already_declared": [...], "added_nodes": [...], "facts": [...]}

    - ``candidates``: 同じ ``from`` につき1件（① > ③ > ② の順で採る = NC7）。
    - ``alternatives``: 採らなかった候補（情報を落とさない = NC4）。
    - ``unmatched_removed``: 候補の立たなかった旧ノード（``{node_id, label, kind}``）。
    - ``already_declared``: draft の ``id_migrations`` に既に載っている対応
      （``{from, to, from_label, to_label, version}``。キーは骨格と同じ綴り）。
    - ``added_nodes``: draft で新しく増えたノード（手動対応の選択肢の材料）。
    """
    frozen_nodes = _nodes_of(frozen)
    draft_nodes = _nodes_of(draft)

    empty: dict[str, list] = {
        "candidates": [],
        "alternatives": [],
        "unmatched_removed": [],
        "already_declared": [],
        "added_nodes": [],
        "facts": [],
    }
    if not draft_nodes or not frozen_nodes:
        # draft が無い / 初回凍結（比較対象が無い）— 候補は立たない。
        return empty

    removed_ids = sorted(set(frozen_nodes) - set(draft_nodes))
    added_ids = sorted(set(draft_nodes) - set(frozen_nodes))

    # draft 自身が既に宣言している対応（教員が前回付けたもの）は再提示しない。
    # キーは骨格の ``id_migrations`` と同じ綴り（``from`` / ``to``）。ラベルは
    # 引ければ添えるが、表示側は draft から解決できる（内部 ID を表示に使わない）。
    already_declared = [
        {
            "from": from_id,
            "to": to_id,
            "from_label": str((frozen_nodes.get(from_id) or {}).get("label") or ""),
            "to_label": str((draft_nodes.get(to_id) or {}).get("label") or ""),
            "version": version,
        }
        for from_id, to_id, version in sorted(_migrations_of(draft))
    ]
    declared_from = {entry["from"] for entry in already_declared}

    # 経路①: 正規化ラベルの完全一致（kind が一致するもの同士）。
    label_index: dict[tuple[str, str], list[str]] = {}
    for node_id, node in draft_nodes.items():
        key = (str(node["kind"]), normalize_label(str(node["label"])))
        if key[1]:
            label_index.setdefault(key, []).append(node_id)

    # 経路②: 教員確定別名の正規形 → draft ノード。
    alias_by_node = {
        str(k): [str(a) for a in (v or [])]
        for k, v in (confirmed_aliases_by_node or {}).items()
    }

    # 経路③: レジストリ（confirmed）で同じ entry に結ばれている node の集合。
    registry_groups = _registry_node_groups(registry_links)

    by_from: dict[str, list[dict]] = {}

    def _add(from_id: str, to_id: str, via: str) -> None:
        if not to_id or to_id not in draft_nodes or to_id == from_id:
            return
        bucket = by_from.setdefault(from_id, [])
        if any(c["to_id"] == to_id and c["via"] == via for c in bucket):
            return
        bucket.append(_candidate(frozen_nodes[from_id], draft_nodes[to_id], via))

    for from_id in removed_ids:
        if from_id in declared_from:
            continue
        from_node = frozen_nodes[from_id]
        kind = str(from_node["kind"])

        for to_id in sorted(label_index.get((kind, normalize_label(str(from_node["label"]))), [])):
            _add(from_id, to_id, VIA_LABEL)

        for alias in alias_by_node.get(from_id) or []:
            for to_id in sorted(label_index.get((kind, normalize_label(alias)), [])):
                _add(from_id, to_id, VIA_ALIAS)

        for group in registry_groups:
            if from_id not in group:
                continue
            for to_id in sorted(group):
                _add(from_id, to_id, VIA_REGISTRY)

    candidates: list[dict] = []
    alternatives: list[dict] = []
    for from_id in removed_ids:
        bucket = by_from.get(from_id) or []
        if not bucket:
            continue
        bucket.sort(key=lambda c: (_VIA_PRIORITY.get(c["via"], 99), c["to_id"]))
        candidates.append(bucket[0])
        alternatives.extend(bucket[1:])

    unmatched_removed = [
        {
            "node_id": node_id,
            "label": str(frozen_nodes[node_id]["label"]),
            "kind": str(frozen_nodes[node_id]["kind"]),
        }
        for node_id in removed_ids
        if node_id not in declared_from and not (by_from.get(node_id) or [])
    ]

    facts: list[str] = []
    if candidates or unmatched_removed:
        facts.append(FACT_LEXICAL_ONLY)
    if candidates:
        facts.append(FACT_CANDIDATES_NOT_PRESELECTED)
    if unmatched_removed:
        facts.append(FACT_UNMATCHED_REMOVED)

    return {
        "candidates": candidates,
        "alternatives": alternatives,
        "unmatched_removed": unmatched_removed,
        "already_declared": already_declared,
        "added_nodes": [
            {
                "node_id": node_id,
                "label": str(draft_nodes[node_id]["label"]),
                "kind": str(draft_nodes[node_id]["kind"]),
            }
            for node_id in added_ids
        ],
        "facts": facts,
    }


# ---------------------------------------------------------------------------
# 版の連鎖で解く（NC8）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeResolver:
    """旧 node_id → 現行 node_id の読み替え器（純データ。読み替えるだけ = NC5）。

    :func:`build_node_resolver` で作る。``resolve`` は例外を出さない
    （循環・欠落・履歴不在はすべて ``unmapped``）。
    """

    current_version: str
    current_node_ids: frozenset[str]
    #: ``{from_id: to_id}``（最も古い版の宣言が勝つ。id が消えた時点の対応が正）。
    migrations: Mapping[str, str]
    #: ``{node_id: その node が実在した最後の凍結版}``。
    last_seen_version: Mapping[str, str]

    @property
    def available(self) -> bool:
        """凍結版の履歴が読めているか（読めていなければ読み替えを主張しない）。"""
        return bool(self.current_node_ids) or bool(self.current_version)

    def resolve(self, node_id: str) -> dict:
        """``{"status", "current_node_id", "via", "last_version"}`` を返す（例外なし）。

        ``via`` は辿った node_id の列（開始ノードを含まず、到達ノードを含む）。
        ``last_version`` は ``node_id`` が実在した最後の凍結版（分からなければ空文字）。
        """
        key = str(node_id or "").strip()
        last_version = str(self.last_seen_version.get(key, "") or "")
        if not key:
            return {
                "status": NODE_STATUS_UNMAPPED,
                "current_node_id": None,
                "via": [],
                "last_version": "",
            }
        if key in self.current_node_ids:
            return {
                "status": NODE_STATUS_CURRENT,
                "current_node_id": key,
                "via": [],
                "last_version": self.current_version or last_version,
            }

        via: list[str] = []
        seen = {key}
        cursor = key
        while True:
            nxt = str(self.migrations.get(cursor, "") or "")
            if not nxt or nxt in seen:
                break
            via.append(nxt)
            seen.add(nxt)
            cursor = nxt
            if cursor in self.current_node_ids:
                return {
                    "status": NODE_STATUS_MIGRATED,
                    "current_node_id": cursor,
                    "via": via,
                    "last_version": last_version,
                }
        return {
            "status": NODE_STATUS_UNMAPPED,
            "current_node_id": None,
            "via": via,
            "last_version": last_version,
        }


def build_node_resolver(frozen_history: Sequence[Any] | None) -> NodeResolver:
    """凍結版の履歴（**古い順**）から解決器を作る（NC8）。

    ``frozen_history`` は ``atlas_store.load_frozen_history``（``created_at ASC``）の
    結果をそのまま渡す。空・``None`` でも例外を出さず、「何も分からない」解決器
    （全て ``unmapped``）を返す — 呼び出し側はその場合、生の node_id を使う従来動作へ
    縮退させること（読み替えを主張しない = fail-soft）。
    """
    history = [s for s in (frozen_history or []) if s is not None]
    if not history:
        return NodeResolver(
            current_version="",
            current_node_ids=frozenset(),
            migrations={},
            last_seen_version={},
        )

    migrations: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    for skeleton in history:
        version = str(_attr(skeleton, "version") or "")
        for node_id in _node_ids_of(skeleton):
            last_seen[node_id] = version
        for from_id, to_id, _version in _migrations_of(skeleton):
            # 古い版の宣言が勝つ（id が消えた時点の対応が正。以降の再宣言で鎖を切らない）。
            migrations.setdefault(from_id, to_id)

    current = history[-1]
    return NodeResolver(
        current_version=str(_attr(current, "version") or ""),
        current_node_ids=frozenset(_node_ids_of(current)),
        migrations=migrations,
        last_seen_version=last_seen,
    )


# ---------------------------------------------------------------------------
# 凍結 body の検証（§4・NC7）
# ---------------------------------------------------------------------------

#: 検証エラーの事実文（数値を書かない。route が 422 の detail にそのまま使う）。
_ERR_EMPTY_PAIR = "対応の指定に、前の版の項目か新しい版の項目が入っていません。"
_ERR_NO_FROZEN = "現在の凍結版がないため、前の版との対応は指定できません。"
_ERR_FROM_NOT_IN_FROZEN = "「{node_id}」は現在の版の地図にありません。"
_ERR_TO_NOT_IN_DRAFT = "「{node_id}」は新しい版の地図にありません。"
_ERR_DUPLICATE_FROM = "「{node_id}」に複数の対応先が指定されています。ひとつだけ選んでください。"


def validate_migrations(
    pairs: Iterable[Any] | None,
    *,
    frozen: Any,
    draft: Any,
    version: str = "",
) -> list[IdMigration]:
    """凍結 body の ``id_migrations`` を検証して :class:`IdMigration` の列にする。

    受け付ける要素は ``{"from": ..., "to": ...}`` / ``{"from_id": ..., "to_id": ...}`` /
    ``(from_id, to_id)`` / ``from_id`` ``to_id`` 属性を持つオブジェクト。

    :class:`ValueError`（route が 422 の事実文にする）:

    - ``from`` / ``to`` のどちらかが空
    - 現行の凍結版が無いのに対応を指定した
    - ``from`` が現行凍結版のノードに無い / ``to`` が draft のノードに無い
    - ``from`` が重複している（NC7: 1 旧ノード → 1 新ノード）
    """
    items = list(pairs or [])
    if not items:
        return []

    frozen_ids = _node_ids_of(frozen)
    draft_ids = _node_ids_of(draft)
    if not frozen_ids:
        raise ValueError(_ERR_NO_FROZEN)

    out: list[IdMigration] = []
    seen_from: set[str] = set()
    for item in items:
        if isinstance(item, Mapping):
            from_id = str(item.get("from") or item.get("from_id") or "").strip()
            to_id = str(item.get("to") or item.get("to_id") or "").strip()
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            from_id = str(item[0] or "").strip()
            to_id = str(item[1] or "").strip()
        else:
            from_id = str(_attr(item, "from_id") or _attr(item, "from") or "").strip()
            to_id = str(_attr(item, "to_id") or _attr(item, "to") or "").strip()

        if not from_id or not to_id:
            raise ValueError(_ERR_EMPTY_PAIR)
        if from_id not in frozen_ids:
            raise ValueError(_ERR_FROM_NOT_IN_FROZEN.format(node_id=from_id))
        if to_id not in draft_ids:
            raise ValueError(_ERR_TO_NOT_IN_DRAFT.format(node_id=to_id))
        if from_id in seen_from:
            raise ValueError(_ERR_DUPLICATE_FROM.format(node_id=from_id))
        seen_from.add(from_id)
        out.append(
            IdMigration(from_id=from_id, to_id=to_id, version=str(version or ""))
        )
    return out
