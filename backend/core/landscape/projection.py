"""知識ランドスケープ — API DTO への投影（設計書 §8 / §9）。

読み取り専用・非LLM・DB 非依存の純関数群（引数で受けた行と骨格だけを使う）。
``core/landscape/store.py`` が返す DB 行 dict と ``core/atlas.py`` の
``AtlasSkeleton`` を組み合わせて、教員 DTO / 学習者 DTO を組み立てる。

**LS5 数値を見せない**: どちらの DTO にも ``weight`` / ``confidence`` の生値を
**キーごと出さない**（段階ラベル ``weight_label`` のみ）。ガードレールテスト
``backend/tests/test_landscape_guardrails.py`` が実データで再帰的に検査する。

**LS6 fail-closed**: 現行の凍結骨格に存在しない ``node_id`` の配置は学習者 DTO から
落とす（地図上に描けない・ラベルが生 id になるため）。行そのものは DB に残り、
教員向け一覧には（骨格版とともに）出続ける — P4。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from core.landscape import schema


# ---------------------------------------------------------------------------
# 骨格ノード索引
# ---------------------------------------------------------------------------


def _iter_domain_skeletons(domains: Any) -> Iterable[tuple[str, Any]]:
    """``(domain_key, skeleton)`` の列へ正規化する。

    受け付ける形（呼び出し側の都合に合わせて3形）:

    - ``{domain_key: AtlasSkeleton}``
    - ``[(domain_key, AtlasSkeleton), ...]``
    - ``[{"domain_key": ..., "skeleton": AtlasSkeleton, ...}, ...]``
      （``atlas_store.list_domains()`` の結果に骨格を足した dict をそのまま渡せる）
    """
    if isinstance(domains, Mapping):
        for key, skeleton in domains.items():
            yield str(key), skeleton
        return
    for entry in domains or []:
        if isinstance(entry, Mapping):
            key = str(entry.get("domain_key") or "")
            skeleton = entry.get("skeleton")
            if key and skeleton is not None:
                yield key, skeleton
            continue
        if isinstance(entry, (tuple, list)) and len(entry) == 2:
            key, skeleton = entry
            if skeleton is not None:
                yield str(key), skeleton


def skeleton_node_index(domains: Any) -> dict[tuple[str, str], dict]:
    """``{(domain_key, node_id): {"label", "kind", "region_id"}}`` を組む。

    region は ``kind='region'`` / ``region_id`` は自分自身の id、concept は
    ``kind='concept'`` / ``region_id`` は所属 region の id。配置行の ``node_id`` を
    ラベルと所属領域へ解決する唯一の索引で、教員 DTO・学習者 DTO の両方が使う。
    """
    index: dict[tuple[str, str], dict] = {}
    for domain_key, skeleton in _iter_domain_skeletons(domains):
        for region in getattr(skeleton, "regions", ()) or ():
            region_id = str(getattr(region, "id", "") or "")
            if not region_id:
                continue
            index[(domain_key, region_id)] = {
                "label": str(getattr(region, "label", "") or region_id),
                "kind": schema.NODE_KIND_REGION,
                "region_id": region_id,
            }
            for concept in getattr(region, "concepts", ()) or ():
                concept_id = str(getattr(concept, "id", "") or "")
                if not concept_id:
                    continue
                index[(domain_key, concept_id)] = {
                    "label": str(getattr(concept, "label", "") or concept_id),
                    "kind": schema.NODE_KIND_CONCEPT,
                    "region_id": region_id,
                }
    return index


def _node_info(
    node_index: Mapping[tuple[str, str], dict], domain_key: str, node_id: str
) -> dict | None:
    entry = node_index.get((domain_key, node_id)) if node_index else None
    return entry if isinstance(entry, Mapping) else None


# ---------------------------------------------------------------------------
# 教員 DTO（§9.1 PlacementAdminDTO）
# ---------------------------------------------------------------------------


def admin_placement_dto(
    row: Mapping[str, Any],
    node_index: Mapping[tuple[str, str], dict],
    *,
    domain_names: Mapping[str, str] | None = None,
) -> dict:
    """1配置行 → 教員 DTO（§9.1）。

    ``domain_names`` は ``{domain_key: 表示名}``（``atlas_store.list_domains()`` の
    ``domain_name`` 等）。未指定・空名のときは ``domain_key`` をそのまま表示名にする。

    骨格に存在しない ``node_id``（骨格改版で消えた等）は ``node_label`` に生 id を
    出し ``region_id`` を空にする — 行を隠さず、地図に描けないことが分かる形で残す
    （教員はここから却下・再提案を判断できる。学習者側では :func:`learner_landscape_dto`
    が落とす）。
    """
    domain_key = str(row.get("domain_key") or "")
    node_id = str(row.get("node_id") or "")
    info = _node_info(node_index, domain_key, node_id)
    status = str(row.get("status") or "")
    names = domain_names or {}
    return {
        "id": str(row.get("id") or ""),
        "domain_key": domain_key,
        "domain_name": str(names.get(domain_key) or "") or domain_key,
        "node_id": node_id,
        "node_label": str((info or {}).get("label") or "") or node_id,
        "node_kind": str(
            (info or {}).get("kind") or row.get("node_kind") or schema.NODE_KIND_REGION
        ),
        "region_id": str((info or {}).get("region_id") or ""),
        "skeleton_version": str(row.get("skeleton_version") or ""),
        "perspective": str(row.get("perspective") or ""),
        "perspective_label": schema.perspective_label(str(row.get("perspective") or "")),
        # LS5: 生 weight は出さない（段階ラベルのみ）。
        "weight_label": schema.weight_label(row.get("weight")),
        "status": status,
        "status_label": schema.status_label(status),
        "provenance": str(row.get("provenance") or ""),
        "provenance_label": schema.provenance_label(status),
        "reason": str(row.get("reason") or ""),
        "evidence": [
            {"quote": str(e.get("quote") or ""), "claim_id": e.get("claim_id") or None}
            for e in (row.get("evidence") or [])
            if isinstance(e, Mapping)
        ],
        "review_note": str(row.get("review_note") or ""),
        "created_at": str(row.get("created_at") or ""),
        "reviewed_at": row.get("reviewed_at") or None,
    }


# ---------------------------------------------------------------------------
# 学習者 DTO（§9.2）
# ---------------------------------------------------------------------------


def _learner_placement_dto(
    row: Mapping[str, Any], info: Mapping[str, Any]
) -> dict:
    status = str(row.get("status") or "")
    return {
        "domain_key": str(row.get("domain_key") or ""),
        "node_id": str(row.get("node_id") or ""),
        "node_label": str(info.get("label") or ""),
        "region_id": str(info.get("region_id") or ""),
        "node_kind": str(info.get("kind") or schema.NODE_KIND_REGION),
        # 観点・関連の強さはラベルのみ（LS5）。
        "perspective_label": schema.perspective_label(str(row.get("perspective") or "")),
        "weight_label": schema.weight_label(row.get("weight")),
        "status": status,
        "provenance_label": schema.provenance_label(status),
        "reason": str(row.get("reason") or ""),
        # 学習者 DTO は quote のみ（claim_id を落とす。chunk 遡及は Phase 2）。
        "evidence": [
            {"quote": str(e.get("quote") or "")}
            for e in (row.get("evidence") or [])
            if isinstance(e, Mapping) and str(e.get("quote") or "").strip()
        ],
    }


def learner_landscape_dto(
    placements: Iterable[Mapping[str, Any]],
    node_index: Mapping[tuple[str, str], dict],
    domains: Iterable[Mapping[str, Any]],
    course_domain_key: str | None,
    *,
    document_titles: Mapping[str, str] | None = None,
    source_document_count: int | None = None,
) -> dict:
    """学習者向け「論文の位置づけ」DTO（§9.2）。

    引数:
      ``placements``: :func:`core.landscape.store.list_for_documents` の行。
        ここで改めて :data:`schema.LEARNER_VISIBLE_STATUSES` で絞る（呼び出し側の
        フィルタ漏れに二重の歯止め = fail-closed）。
      ``node_index``: :func:`skeleton_node_index` の結果。索引に無いノードの配置は落とす。
      ``domains``: ``[{"domain_key", "domain_name"?, "frozen_version"?}]``。
        表示に出すのは「可視配置が1件以上あるドメイン」+「コースがバインドされた
        ドメイン（配置ゼロでも地図の出所として出す）」。
      ``course_domain_key``: ``core.course_data.course_cartridge_id(course_data)``。
      ``document_titles``: ``{document_id: 論文タイトル}``。与えた順序が documents の
        並び順になる（コースの sources 順を保てる）。
      ``source_document_count``: LS8 のコーパス事実行に出す「登録済み論文 N 件」。
        未指定なら ``document_titles`` の件数、それも無ければ配置のあった document 数。

    戻り値は ``{course_domain_key, domains, documents, corpus}``。``course_id`` は
    ルート層が付与する（本関数はコースを知らない）。数値は corpus の件数のみで、
    weight / confidence は一切含まない（LS5）。
    """
    titles = dict(document_titles or {})
    grouped: dict[str, list[dict]] = {}
    for row in placements or []:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("status") or "") not in schema.LEARNER_VISIBLE_STATUSES:
            continue
        domain_key = str(row.get("domain_key") or "")
        node_id = str(row.get("node_id") or "")
        info = _node_info(node_index, domain_key, node_id)
        if info is None:
            # 現行の凍結骨格に無いノード（改版で消えた等）は地図に描けないので出さない。
            continue
        document_id = str(row.get("document_id") or "")
        if not document_id:
            continue
        grouped.setdefault(document_id, []).append(_learner_placement_dto(row, info))

    ordered_ids = [d for d in titles if d in grouped]
    ordered_ids += [d for d in grouped if d not in titles]

    documents = [
        {
            "document_id": document_id,
            "title": str(titles.get(document_id) or ""),
            "placements": grouped[document_id],
        }
        for document_id in ordered_ids
    ]

    placed_domain_keys = {
        p["domain_key"] for items in grouped.values() for p in items
    }
    domain_dtos = []
    for entry in domains or []:
        if not isinstance(entry, Mapping):
            continue
        domain_key = str(entry.get("domain_key") or "")
        if not domain_key:
            continue
        is_course_map = bool(course_domain_key) and domain_key == course_domain_key
        if domain_key not in placed_domain_keys and not is_course_map:
            continue
        domain_dtos.append(
            {
                "domain_key": domain_key,
                "domain_name": str(entry.get("domain_name") or "") or domain_key,
                "frozen_version": str(entry.get("frozen_version") or ""),
                "is_course_map": is_course_map,
            }
        )
    # コースの地図を先頭に（§4.2 手順4）。以降は domain_key 昇順で決定論的に並べる。
    domain_dtos.sort(key=lambda d: (0 if d["is_course_map"] else 1, d["domain_key"]))

    if source_document_count is None:
        source_document_count = len(titles) if titles else len(grouped)

    return {
        "course_domain_key": course_domain_key or None,
        "domains": domain_dtos,
        "documents": documents,
        "corpus": {
            "source_document_count": int(source_document_count),
            "placed_document_count": len(grouped),
        },
    }


__all__ = [
    "admin_placement_dto",
    "learner_landscape_dto",
    "skeleton_node_index",
]
