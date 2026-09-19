"""レジストリ ↔ 分野の地図（骨格 node）の**候補導出**（概念レジストリ P3-4・§6.1）。

設計正本: ``docs/features/concept_registry_design.md`` §6.1（不変条項 KR1〜KR10 は §2）。

3 つの導出だけを行う（すべて決定論・非LLM）:

1. **語彙一致** — 骨格 concept のラベル（+ 教員確定別名 ``atlas_anchor_aliases``）の
   ``normalize_label`` が、確定済みエントリのラベル（``name`` / ``alternate`` /
   ``hidden``）と一致する → ``exact_match`` 候補（``lexical_match``）。
2. **ベクトル近傍** — **保存済み**アンカープロトタイプ（``atlas_anchor_embeddings``）と
   **保存済み**エントリ凍結版 embedding（``library_entry_versions``）の cosine が
   :data:`core.label_vocab.ANCHOR_NEARNESS_THRESHOLD_NEAR` 以上 → ``close_match`` 候補
   （``vector_similarity``）。同じ (entry, node) に 1 と 2 が両方立てば **1 を採る**。
3. **ドメイン跨ぎの双子** — 別の live 凍結ドメインに語彙一致 or 最上位帯の近さを持つ
   node があり、**どちらの node も entry に結ばれていない** → candidate entry を 1 行 +
   node link candidate を 2 行（ハブ経由で「別ドメインの 2 node が同じ entry に繋がる」
   状態を作る。node—node の直接リンクは作らない = §4.5）。

不変条項の写像:

- **KR5 決定論・embedding 呼び出しゼロ** — 本モジュールは ``core.llm`` にも
  ``atlas_vectors.builder`` の埋め込み関数にも触れない。読むのは保存済みベクトルだけ。
- **KR2 確定は人間** — 書き込みは ``review_status='candidate'`` のエントリ行と
  ``status='candidate'`` の node リンク行の upsert のみ。``atlas_skeletons`` へは
  一切書かない（LS7 / AB4）。
- **KR6 数値を見せない** — cosine の生値は :func:`_similarity` の内部と DB の
  ``confidence`` 列までで、戻り値に現れるのは段階ラベル
  （:data:`core.label_vocab.ANCHOR_NEARNESS_SCALE`）と名前の列挙だけ。
- **KR7 情報を落とさない** — ``DELETE FROM`` は無い。``dismissed`` の候補
  （同じ ``candidate_key`` / ``link_key``）は**再提案しない**（LS3 と同じ規則）。
- **KR9 版非依存** — リンクキーに ``skeleton_version`` を含めない。骨格版は戻り値の
  ``skeleton_version`` として事実で添えるだけ（VA8）。

本モジュールは FastAPI を import しない（開発ルール2 / core/ 共通ルール）。セッションは
呼び出し側（route）が渡す（テストは fake session で行う）。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional

from sqlalchemy import text as sa_text

from core import atlas_store
from core.atlas_vectors.query import cosine_similarity
from core.atlas_vectors.store import (
    confirmed_aliases_by_node,
    load_anchor_vectors,
    parse_vector,
)
from core.label_vocab import ANCHOR_NEARNESS_SCALE, ANCHOR_NEARNESS_THRESHOLD_NEAR

from . import registry, schema

logger = logging.getLogger(__name__)


class AtlasLinksError(Exception):
    """候補導出の基底エラー。"""


class SkeletonUnavailableError(AtlasLinksError):
    """その分野に現行凍結版の骨格が無い（route は 422 の事実文）。"""


#: 候補導出の対象にする骨格ノード種別（v1 は concept のみ。region は「座標系の地形」で
#: あって概念ではない — RE7 / §6.1 と同じ判断）。
NODE_KIND_CONCEPT = schema.NODE_KIND_CONCEPT


# ---------------------------------------------------------------------------
# 骨格の投影（純関数）
# ---------------------------------------------------------------------------


def _clean(value: Any) -> str:
    return str(value or "").strip()


def concept_index(skeleton: Any) -> dict[str, dict]:
    """骨格の concept を ``node_id -> {label, region_id, region_label}`` に投影する。

    骨格が読めない場合は空 dict（候補ゼロへ fail-closed）。
    """
    out: dict[str, dict] = {}
    for region in getattr(skeleton, "regions", ()) or ():
        region_id = _clean(getattr(region, "id", ""))
        region_label = _clean(getattr(region, "label", ""))
        for concept in getattr(region, "concepts", ()) or ():
            concept_id = _clean(getattr(concept, "id", ""))
            if not concept_id:
                continue
            out[concept_id] = {
                "label": _clean(getattr(concept, "label", "")) or concept_id,
                "region_id": region_id,
                "region_label": region_label,
            }
    return out


def node_label_keys(
    node: dict, aliases_by_node: dict[str, list[str]], node_id: str
) -> set[str]:
    """1 node の照合キー集合（ラベル + 教員確定別名の ``normalize_label``）。

    ``node_id`` そのものは照合に使わない（内部 ID を表記の一致と偽らない = PL7）。
    """
    keys = {schema.normalize_label(node.get("label") or "")}
    for alias in aliases_by_node.get(node_id) or ():
        keys.add(schema.normalize_label(alias))
    return {key for key in keys if key}


def entry_label_keys(entry: dict, labels: Iterable[dict]) -> set[str]:
    """1 entry の照合キー集合（``name`` + alternate + hidden の正規化ラベル）。

    ``hidden`` は SKOS hiddenLabel（検索には使うが表示しない）なので、照合には
    **使う**（KR7: 捨てずに隠すための器）。
    """
    keys = {schema.normalize_label(entry.get("name") or "")}
    for label in labels or ():
        normalized = _clean(label.get("normalized_label")) or schema.normalize_label(
            label.get("label") or ""
        )
        if normalized:
            keys.add(normalized)
    return {key for key in keys if key}


def _similarity(left: Optional[list[float]], right: Optional[list[float]]) -> Optional[float]:
    """cosine（未測定は ``None``）。**この層の内部専用**（KR6）。"""
    return cosine_similarity(left, right)


# ---------------------------------------------------------------------------
# DB 読み出し（保存済みのものだけ）
# ---------------------------------------------------------------------------

_ENTRY_SQL = """
    SELECT id::text, domain_key, entry_type, name
      FROM library_entries
     WHERE status = :active
       AND review_status = :confirmed
     ORDER BY domain_key, name, id
"""


def load_confirmed_entries(session: Any) -> list[dict]:
    """確定済み・公開中のエントリ一覧（ドメイン横断）。

    ドメインで絞らないのは、``domain_key`` が**属性であって座標系ではない**ため
    （§4.4 / §4.5）。1 つのエントリが複数ドメインの node に繋がる状態（ハブ）は
    この層が作りたいものそのもの。
    """
    rows = session.execute(
        sa_text(_ENTRY_SQL),
        {
            "active": schema.STATUS_ACTIVE,
            "confirmed": schema.REVIEW_STATUS_CONFIRMED,
        },
    ).fetchall()
    return [
        {
            "id": str(row[0]),
            "domain_key": str(row[1] or ""),
            "entry_type": str(row[2] or ""),
            "name": str(row[3] or ""),
        }
        for row in rows
    ]


_ENTRY_VECTOR_SQL = """
    WITH latest AS (
        SELECT v.entry_id, v.embedding,
               row_number() OVER (
                   PARTITION BY v.entry_id ORDER BY v.version_no DESC
               ) AS rn
          FROM library_entry_versions v
         WHERE v.embedding IS NOT NULL
    )
    SELECT entry_id::text, embedding FROM latest WHERE rn = 1
"""


def load_entry_vectors(session: Any) -> dict[str, list[float]]:
    """``{entry_id: vector}``（**保存済み**凍結版 embedding の最新版のみ）。

    埋め込みを持たないエントリは単に現れない（「近さを測れなかった」を「遠い」に
    化けさせない — PR2 と同じ規律）。
    """
    try:
        rows = session.execute(sa_text(_ENTRY_VECTOR_SQL)).fetchall()
    except Exception:  # noqa: BLE001 — ベクトルが読めなくても語彙一致の候補は出す
        logger.warning("library entry embeddings unavailable (non-fatal)", exc_info=True)
        return {}
    out: dict[str, list[float]] = {}
    for row in rows:
        vector = parse_vector(row[1])
        if vector:
            out[str(row[0])] = vector
    return out


def _live_frozen_domains(session: Any, exclude: str) -> list[dict]:
    """retired でない・現行凍結版を持つ他ドメインの一覧（決定論順）。"""
    try:
        domains = atlas_store.list_domains(session) or []
    except Exception:  # noqa: BLE001 — 他ドメインが読めなければ双子は出さないだけ
        logger.warning("atlas domain list unavailable (non-fatal)", exc_info=True)
        return []
    out = [
        dict(domain)
        for domain in domains
        if _clean(domain.get("domain_key"))
        and _clean(domain.get("domain_key")) != _clean(exclude)
        and _clean(domain.get("frozen_version"))
        and _clean(domain.get("lifecycle") or "active") == "active"
    ]
    out.sort(key=lambda d: _clean(d.get("domain_key")))
    return out


# ---------------------------------------------------------------------------
# 候補導出本体
# ---------------------------------------------------------------------------


def _existing_link_index(session: Any) -> tuple[dict[str, str], set[tuple[str, str]]]:
    """``({link_key: status}, {(domain_key, node_id) で entry に結ばれている node})``。

    「結ばれている」は ``dismissed`` 以外のリンク行がある状態（見送られた対応は
    「結ばれていない」扱いに戻す — 教員が別の相手を選べるようにするため）。
    """
    links = registry.list_node_links(include_dismissed=True, session=session)
    by_key = {str(link.get("link_key") or ""): str(link.get("status") or "") for link in links}
    linked_nodes = {
        (str(link.get("domain_key") or ""), str(link.get("node_id") or ""))
        for link in links
        if str(link.get("status") or "") != schema.CANDIDATE_STATUS_DISMISSED
    }
    return by_key, linked_nodes


def _candidate_dto(link: dict, node: dict, similarity: Optional[float]) -> dict:
    """§9.1 の links 同形 + ``nearness_label``（cosine の生値は載せない = KR6）。"""
    item = dict(link)
    item["node_label"] = node.get("label") or ""
    item["region_label"] = node.get("region_label") or ""
    item["node_in_current_version"] = True
    item["nearness_label"] = ANCHOR_NEARNESS_SCALE.label_for(similarity)
    item.pop("confidence", None)
    return item


def derive_node_link_candidates(session: Any, *, domain_key: str) -> dict:
    """§6.1 の 1〜3 を実行し、候補（= 作成済みの candidate 行）を返す。

    Args:
        session: SQLAlchemy セッション（呼び出し側が開閉する）。
        domain_key: 対象の分野キー。

    Returns:
        ``{"candidates": [...], "skeleton_version": str, "facts": [str], "coverage": {...}}``。
        ``candidates`` は §9.1 の links と同形 + ``nearness_label``。``coverage`` は
        Phase 0 の共通報告形式（``population`` = 照合した concept node 数）で、**教員に
        見せる数値ではない**ので route は落とす（KR6）。

    Raises:
        SkeletonUnavailableError: その分野に現行凍結版の骨格が無い。
    """
    domain = _clean(domain_key)
    if not domain:
        raise SkeletonUnavailableError("分野が指定されていません。")

    skeleton = atlas_store.load_frozen_skeleton(session, domain)
    if skeleton is None:
        raise SkeletonUnavailableError(
            "この分野には、凍結された地図がまだありません。地図を凍結してから対応を導出してください。"
        )
    version = _clean(getattr(skeleton, "version", ""))
    nodes = concept_index(skeleton)

    facts: list[str] = []
    if not nodes:
        facts.append("この分野の地図には、照合できる概念がありません。")
        return {
            "candidates": [],
            "skeleton_version": version,
            "facts": facts,
            "coverage": _coverage(0, 0, []),
        }

    aliases_by_node = _safe(lambda: confirmed_aliases_by_node(session, domain), {})
    anchors = _safe(lambda: load_anchor_vectors(session, domain, version), [])
    anchor_vectors = {
        _clean(getattr(anchor, "node_id", "")): getattr(anchor, "vector", None)
        for anchor in anchors
        if _clean(getattr(anchor, "node_kind", "")) == NODE_KIND_CONCEPT
    }
    entries = load_confirmed_entries(session)
    entry_vectors = load_entry_vectors(session)
    labels_by_entry = _safe(
        lambda: registry.labels_for_entries(
            [e["id"] for e in entries], include_hidden=True, session=session
        ),
        {},
    )
    entry_keys = {
        entry["id"]: entry_label_keys(entry, labels_by_entry.get(entry["id"]) or [])
        for entry in entries
    }

    link_status_by_key, linked_nodes = _existing_link_index(session)

    candidates: list[dict] = []
    matched_node_labels: list[str] = []
    reasons: list[str] = []

    # -- 1 / 2. 既存エントリとの対応 -----------------------------------------
    for node_id in sorted(nodes):
        node = nodes[node_id]
        keys = node_label_keys(node, aliases_by_node, node_id)
        node_vector = anchor_vectors.get(node_id)
        for entry in entries:
            kind: str | None = None
            justification = ""
            similarity: Optional[float] = None
            if keys & entry_keys.get(entry["id"], set()):
                kind = schema.RELATION_KIND_EXACT_MATCH
                justification = schema.JUSTIFICATION_LEXICAL
            else:
                similarity = _similarity(node_vector, entry_vectors.get(entry["id"]))
                if similarity is not None and similarity >= ANCHOR_NEARNESS_THRESHOLD_NEAR:
                    kind = schema.RELATION_KIND_CLOSE_MATCH
                    justification = schema.JUSTIFICATION_VECTOR
            if kind is None:
                continue
            link = _record_link(
                session,
                entry_id=entry["id"],
                domain_key=domain,
                node_id=node_id,
                kind=kind,
                justification=justification,
                reason=_link_reason(kind, node, entry),
                confidence=similarity,
                link_status_by_key=link_status_by_key,
            )
            if link is None:
                continue
            candidates.append(_candidate_dto(link, node, similarity))
            matched_node_labels.append(node["label"])

    # -- 3. ドメイン跨ぎの双子 -----------------------------------------------
    twin_facts = _derive_cross_domain_twins(
        session,
        domain=domain,
        nodes=nodes,
        aliases_by_node=aliases_by_node,
        anchor_vectors=anchor_vectors,
        linked_nodes=linked_nodes,
        link_status_by_key=link_status_by_key,
        candidates=candidates,
    )
    facts.extend(twin_facts)

    # -- 事実文（数値を書かない = KR6）---------------------------------------
    facts.insert(0, f"この分野の地図（版 {version}）の概念と、確定済みの概念を照合しました。")
    if not anchor_vectors:
        facts.append(
            "この地図のベクトル索引がまだ作られていないため、意味の近さでは照合していません。"
        )
        reasons.append("no_anchor_vectors")
    if matched_node_labels:
        names = "、".join(dict.fromkeys(matched_node_labels))
        facts.append(f"対応の候補が見つかった地図の概念: {names}")
    if not candidates:
        facts.append("新しい対応の候補はありませんでした。")

    return {
        "candidates": candidates,
        "skeleton_version": version,
        "facts": facts,
        "coverage": _coverage(
            len(nodes),
            len(nodes) if anchor_vectors or entries else 0,
            reasons,
        ),
    }


@contextmanager
def _savepoint(session: Any) -> Iterator[None]:
    """``session`` が対応していれば SAVEPOINT で包む（P3-R5・非対応なら素通し）。

    PostgreSQL では 1 文の失敗でトランザクション全体が abort 状態になるため、
    「1 件の候補が書けなくても導出は続ける」という fail-soft は SAVEPOINT を伴って
    初めて成立する。fake session（テスト）や begin_nested を持たない実装では
    素通しにする — 包めないことを理由に導出そのものを止めない。
    """
    begin_nested = getattr(session, "begin_nested", None)
    if not callable(begin_nested):
        yield
        return
    nested = begin_nested()
    try:
        yield
    except Exception:
        try:
            nested.rollback()
        except Exception:  # noqa: BLE001 — 巻き戻しに失敗しても元の例外を優先する
            logger.warning("savepoint rollback failed (non-fatal)", exc_info=True)
        raise
    else:
        nested.commit()


def _link_reason(kind: str, node: dict, entry: dict) -> str:
    """候補に添える事実文（数値なし・推測を書かない）。"""
    if kind == schema.RELATION_KIND_EXACT_MATCH:
        return f"地図の概念「{node.get('label')}」と表記が一致しました。"
    return f"地図の概念「{node.get('label')}」と意味が近いと測定されました。"


def _record_link(
    session: Any,
    *,
    entry_id: str,
    domain_key: str,
    node_id: str,
    kind: str,
    justification: str,
    reason: str,
    confidence: Optional[float],
    link_status_by_key: dict[str, str],
) -> Optional[dict]:
    """候補リンクを 1 行 upsert する（``dismissed`` は再提案しない = LS3）。

    P3-R5: 書き込みは **SAVEPOINT の中**で行う。呼び出し側のセッションを共有しているため、
    1 件の INSERT が失敗すると（制約違反など）PostgreSQL ではトランザクション全体が
    abort 状態になり、``except`` で握りつぶしても以降の SELECT まで落ちて導出ごと 500 に
    なる。SAVEPOINT に閉じ込めれば、失敗した 1 件だけを巻き戻して残りの候補は出せる
    （fail-soft）。
    """
    link_key = schema.build_node_link_key(entry_id, domain_key, node_id)
    if link_status_by_key.get(link_key) == schema.CANDIDATE_STATUS_DISMISSED:
        return None
    try:
        with _savepoint(session):
            link = registry.create_node_link(
                entry_id=entry_id,
                domain_key=domain_key,
                node_id=node_id,
                kind=kind,
                mapping_justification=justification,
                node_kind=schema.NODE_KIND_CONCEPT,
                reason=reason,
                confidence=confidence,
                session=session,
            )
    except Exception:  # noqa: BLE001 — 1 件書けなくても他の候補は出す
        logger.warning(
            "failed to record atlas node link candidate (non-fatal): %s", link_key, exc_info=True
        )
        return None
    link_status_by_key[link_key] = str(link.get("status") or "")
    return link


def _derive_cross_domain_twins(
    session: Any,
    *,
    domain: str,
    nodes: dict[str, dict],
    aliases_by_node: dict[str, list[str]],
    anchor_vectors: dict[str, Optional[list[float]]],
    linked_nodes: set[tuple[str, str]],
    link_status_by_key: dict[str, str],
    candidates: list[dict],
) -> list[str]:
    """§6.1 の 3。別ドメインの双子 node を 1 つの候補エントリで結ぶ。

    **どちらの node も entry に結ばれていない**ときだけ候補を立てる（既に片方が
    結ばれているなら、その entry へのもう 1 本は 1 / 2 の経路で出る）。
    """
    from . import store as library_store  # 遅延 import（循環回避）

    facts: list[str] = []
    others = _live_frozen_domains(session, domain)
    if not others:
        return facts

    for other in others:
        other_key = _clean(other.get("domain_key"))
        other_version = _clean(other.get("frozen_version"))
        other_skeleton = atlas_store.load_frozen_skeleton(session, other_key)
        if other_skeleton is None:
            continue
        other_nodes = concept_index(other_skeleton)
        if not other_nodes:
            continue
        other_aliases = _safe(lambda: confirmed_aliases_by_node(session, other_key), {})
        other_anchors = _safe(
            lambda: load_anchor_vectors(session, other_key, other_version), []
        )
        other_vectors = {
            _clean(getattr(a, "node_id", "")): getattr(a, "vector", None)
            for a in other_anchors
            if _clean(getattr(a, "node_kind", "")) == NODE_KIND_CONCEPT
        }

        for node_id in sorted(nodes):
            if (domain, node_id) in linked_nodes:
                continue
            node = nodes[node_id]
            keys = node_label_keys(node, aliases_by_node, node_id)
            for other_id in sorted(other_nodes):
                if (other_key, other_id) in linked_nodes:
                    continue
                other_node = other_nodes[other_id]
                other_keys = node_label_keys(other_node, other_aliases, other_id)
                similarity: Optional[float] = None
                if keys & other_keys:
                    justification = schema.JUSTIFICATION_LEXICAL
                    kind = schema.RELATION_KIND_EXACT_MATCH
                    name = node["label"]
                else:
                    similarity = _similarity(
                        anchor_vectors.get(node_id), other_vectors.get(other_id)
                    )
                    if similarity is None or similarity < ANCHOR_NEARNESS_THRESHOLD_NEAR:
                        continue
                    justification = schema.JUSTIFICATION_VECTOR
                    kind = schema.RELATION_KIND_CLOSE_MATCH
                    # 先勝ち: 走査順（自ドメインの node）のラベルを採る。
                    name = node["label"]

                entry = _ensure_candidate_entry(
                    library_store,
                    domain_key=domain,
                    name=name,
                    justification=justification,
                )
                if entry is None:
                    continue
                for target_domain, target_id, target_node in (
                    (domain, node_id, node),
                    (other_key, other_id, other_node),
                ):
                    link = _record_link(
                        session,
                        entry_id=entry["id"],
                        domain_key=target_domain,
                        node_id=target_id,
                        kind=kind,
                        justification=justification,
                        reason=(
                            f"分野「{domain}」と分野「{other_key}」の地図に、"
                            "同じと言えそうな概念がありました。"
                        ),
                        confidence=similarity,
                        link_status_by_key=link_status_by_key,
                    )
                    if link is not None:
                        candidates.append(_candidate_dto(link, target_node, similarity))
                        linked_nodes.add((target_domain, target_id))
                facts.append(
                    f"別の分野の地図と共通しそうな概念: {node['label']}（分野 {other_key}）"
                )
                break  # 1 node につき 1 つの双子で十分（先勝ち・決定論）
    return facts


def _ensure_candidate_entry(
    library_store: Any, *, domain_key: str, name: str, justification: str
) -> Optional[dict]:
    """候補エントリを 1 行確保する（``dismissed`` は再提案しない = LS3）。

    P3-R5: ここは ``library_store`` 側が**自前のセッション**を開閉する（失敗時は
    そのセッションで rollback される）ため、呼び出し側の共有セッションは汚れない。
    したがって :func:`_savepoint` は不要で、``except`` による fail-soft がそのまま効く。
    """
    candidate_key = schema.build_candidate_key(domain_key, name)
    try:
        existing = library_store.get_entry_by_candidate_key(candidate_key)
    except Exception:  # noqa: BLE001
        logger.warning("candidate entry lookup failed (non-fatal)", exc_info=True)
        return None
    if existing is not None:
        if existing.get("review_status") == schema.REVIEW_STATUS_DISMISSED:
            return None
        return existing
    try:
        return library_store.create_entry(
            domain_key=domain_key,
            entry_type=schema.ENTRY_TYPE_CONCEPT,
            name=name,
            review_status=schema.REVIEW_STATUS_CANDIDATE,
            mapping_justification=justification,
            candidate_key=candidate_key,
        )
    except Exception:  # noqa: BLE001 — 1 件作れなくても他の候補は出す
        logger.warning("failed to create candidate entry (non-fatal)", exc_info=True)
        return None


def _coverage(population: int, processed: int, reasons: list[str]) -> dict:
    from episteme_graph.agents.coverage_report import build_coverage_report

    return build_coverage_report(
        population=population, processed=processed, reasons=reasons, unit="concepts"
    )


def _safe(fn, default):
    """読めないものは既定値へ倒す（1 系統の欠落で導出ごと落とさない = fail-soft）。"""
    try:
        return fn()
    except Exception:  # noqa: BLE001
        logger.warning("atlas link input unavailable (non-fatal)", exc_info=True)
        return default
