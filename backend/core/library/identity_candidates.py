"""同一性候補の自動生成（概念レジストリ P3-6・§6.2）— パイプラインステージ本体。

設計正本: ``docs/features/concept_registry_design.md`` §6.2（不変条項 KR1〜KR10 は §2）。

対象は当該 document の **親 component**（``theory_components_live`` のうち
``parent_agent_component_id IS NULL``。子の機械名は対象外 — K-8）。3 つの導出を順に
かけ、先に当たった経路で確定する（同じ component に 2 種類の候補を作らない）:

1. **既存 entry への語彙一致** — ``normalize_label(component.name)`` が確定済み
   エントリのラベル（``name`` / ``alternate`` / ``hidden``）と一致 → 同一性リンク候補
   （``lexical_match``）。
2. **他 document の component との語彙一致** — 他 document の live 親 component と
   正規化名が一致し、**どちらも entry に結ばれていない** → candidate entry 1 行 +
   同一性リンク候補 2 本（``lexical_match``）。
3. **chunk-proxy ベクトル近傍** — 親 component の ``primary_chunk_id`` の
   ``chunks.embedding`` を代表ベクトルとし、``chunks`` の pgvector 近傍（他 document・
   上位 :data:`core.library.schema.IDENTITY_CHUNK_TOPK`）で
   :data:`core.library.schema.IDENTITY_CHUNK_PROXY_THRESHOLD` 以上のチャンクを
   ``primary_chunk_id`` / ``source_chunks`` に持つ他 document の live 親 component →
   candidate entry + 同一性リンク候補 2 本（``vector_similarity``）。

規則 ④（主張の概念接地・``claim_concept_grounding_design.md`` §7）: 当該 document の
``theory_claims_live`` のうち ``concepts`` 要素に ``entry_id`` を持つ行 → その主張
（``theory_claim``・DB UUID）から当該エントリへの同一性リンク候補。``entry`` は**作らない**
（既に確定済みのエントリを指しているため）。上限は規則 ①〜③ と**合算**で、
``mapping_justification`` は接地が記録した値（``lexical_match`` / ``llm_candidate``）を
そのまま使う。

不変条項の写像:

- **KR5 決定論・embedding 呼び出しゼロ** — 代表ベクトルは**保存済み** ``chunks.embedding``
  をそのまま使う（W層 cross_corpus と同じ下地だが、あちらの「クエリ文を埋め込む」段は
  通らない）。本モジュールは ``core.llm`` を import しない。
- **KR2 確定は人間** — 書き込みは ``review_status='candidate'`` のエントリ行と
  ``status='candidate'`` の同一性リンク行、および AI 提案層である
  ``theory_components.duplicate_candidates`` だけ。
- **KR6 数値を見せない** — cosine は ``element_identity_links.confidence``（DB 列）までで、
  ``duplicate_candidates`` にも戻り値の事実にも載せない。
- **KR7 情報を落とさない** — ``DELETE FROM`` は無い。``dismissed`` の候補エントリ
  （同じ ``candidate_key``）は再提案しない。既存の同一性リンクは
  ``create_candidate`` が上書きせずそのまま返す（P4）。
- **KO5 読み手は live ビュー** — 基表 ``theory_components`` は FROM / JOIN しない。
  唯一の基表 UPDATE は ``persistence.set_duplicate_candidates`` に置く。

本モジュールは FastAPI を import しない（開発ルール2 / core/ 共通ルール）。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from sqlalchemy import text as sa_text

from core.config import get_settings
from core.deliberation import identity_links as _identity_links
from core.deliberation.schema import (
    ELEMENT_THEORY_CLAIM,
    ELEMENT_THEORY_COMPONENT,
    SCOPE_DOCUMENT,
    ElementRef,
)
from core.paper_discovery import corpus as _corpus
from core.postgres import get_session

from . import registry, schema
from . import store as library_store

logger = logging.getLogger(__name__)


@contextmanager
def _session_scope(session: Any = None) -> Iterator[Any]:
    """``session`` が渡されればそのまま使い、渡されなければ自前で開閉する。

    パイプラインステージからは ``session=None`` で呼ばれる（自前トランザクション）。
    """
    if session is not None:
        yield session
        return
    own = get_session()
    try:
        yield own
        own.commit()
    except Exception:
        own.rollback()
        raise
    finally:
        own.close()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _element_ref(component_id: str, document_id: str) -> ElementRef:
    """component の ElementRef を **dataclass 直組み**で作る。

    ``core.deliberation.refs.resolve`` は要素の実在確認のために DB 往復を 1 件ずつ
    行う。ここでは直前に ``theory_components_live`` から読んだ行そのものを渡すので、
    実在は既に確かめられており、件数ぶんの往復は純粋な重複になる（1 document あたり
    親 component 数 × 2 クエリ）。``validate()`` は呼んで形の不変条項だけは守る。
    """
    ref = ElementRef(
        scope=SCOPE_DOCUMENT,
        element_type=ELEMENT_THEORY_COMPONENT,
        element_id=str(component_id),
        document_id=str(document_id),
    )
    ref.validate()
    return ref


# ---------------------------------------------------------------------------
# 読み出し（live ビュー・保存済みベクトルのみ）
# ---------------------------------------------------------------------------

_PARENT_COMPONENT_COLUMNS = """
    id::text, document_id::text, name, component_type,
    primary_chunk_id::text, source_chunks
"""


def _row_to_component(row: Any) -> dict:
    return {
        "id": str(row[0]),
        "document_id": str(row[1] or ""),
        "name": str(row[2] or ""),
        "component_type": str(row[3] or ""),
        "primary_chunk_id": str(row[4] or ""),
        "source_chunks": schema.as_list(row[5]),
    }


def load_parent_components(session: Any, document_id: str) -> list[dict]:
    """当該 document の live 親 component（``parent_agent_component_id IS NULL``）。"""
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_PARENT_COMPONENT_COLUMNS}
              FROM theory_components_live
             WHERE document_id = CAST(:document_id AS uuid)
               AND parent_agent_component_id IS NULL
             ORDER BY created_at, id
            """
        ),
        {"document_id": document_id},
    ).fetchall()
    return [_row_to_component(row) for row in rows]


def load_other_parent_components(session: Any, document_id: str) -> list[dict]:
    """他 document の live 親 component（語彙一致・chunk-proxy の照合対象）。"""
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_PARENT_COMPONENT_COLUMNS}
              FROM theory_components_live
             WHERE document_id <> CAST(:document_id AS uuid)
               AND parent_agent_component_id IS NULL
               AND btrim(name) <> ''
             ORDER BY document_id, created_at, id
            """
        ),
        {"document_id": document_id},
    ).fetchall()
    return [_row_to_component(row) for row in rows]


def _load_confirmed_entry_keys(session: Any) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """``({entry_id: entry}, {normalized_label: [entry_id, ...]})``（確定・公開中のみ）。"""
    rows = session.execute(
        sa_text(
            """
            SELECT id::text, domain_key, entry_type, name
              FROM library_entries
             WHERE status = :active AND review_status = :confirmed
             ORDER BY domain_key, name, id
            """
        ),
        {
            "active": schema.STATUS_ACTIVE,
            "confirmed": schema.REVIEW_STATUS_CONFIRMED,
        },
    ).fetchall()
    entries = {
        str(row[0]): {
            "id": str(row[0]),
            "domain_key": str(row[1] or ""),
            "entry_type": str(row[2] or ""),
            "name": str(row[3] or ""),
        }
        for row in rows
    }
    labels_by_entry = registry.labels_for_entries(
        list(entries), include_hidden=True, session=session
    )
    index: dict[str, list[str]] = {}
    for entry_id, entry in entries.items():
        keys = {schema.normalize_label(entry["name"])}
        for label in labels_by_entry.get(entry_id) or ():
            normalized = _clean(label.get("normalized_label")) or schema.normalize_label(
                label.get("label") or ""
            )
            if normalized:
                keys.add(normalized)
        for key in keys:
            if key:
                index.setdefault(key, []).append(entry_id)
    return entries, index


_GROUNDED_CLAIM_SQL = """
    SELECT id::text, text, concepts
      FROM theory_claims_live
     WHERE document_id = CAST(:document_id AS uuid)
       AND concepts IS NOT NULL
       AND jsonb_typeof(concepts) = 'array'
       AND jsonb_array_length(concepts) > 0
     ORDER BY created_at, id
"""


def load_grounded_claims(session: Any, document_id: str) -> list[dict]:
    """当該 document の live claim のうち ``concepts`` を持つ行（規則 ④ の母集合）。

    読むのは live ビューだけ（KO5: 基表 ``theory_claims`` は FROM しない）。
    """
    rows = session.execute(
        sa_text(_GROUNDED_CLAIM_SQL), {"document_id": document_id}
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        out.append(
            {
                "id": str(row[0]),
                "text": str(row[1] or ""),
                "concepts": schema.as_list(row[2]),
            }
        )
    return out


def _linked_claim_pairs(session: Any) -> set[tuple[str, str]]:
    """既にリンク（状態を問わず）がある ``(claim_id, entry_id)`` の組。

    状態を問わないのは、却下された組を毎回作り直さないため（KR7 / 既存規律）。
    """
    rows = session.execute(
        sa_text(
            """
            SELECT instance_element_id, shared_part_id::text
              FROM element_identity_links
             WHERE instance_element_type = :element_type
            """
        ),
        {"element_type": ELEMENT_THEORY_CLAIM},
    ).fetchall()
    return {(str(row[0]), str(row[1])) for row in rows}


def _linked_component_ids(session: Any, component_ids: list[str]) -> set[str]:
    """既に同一性リンク（状態を問わず）を持つ component の id 集合。

    状態を問わないのは、却下（``rejected``）された組を毎回作り直さないため（KR7）。
    """
    keys = [cid for cid in component_ids if cid]
    if not keys:
        return set()
    rows = session.execute(
        sa_text(
            """
            SELECT DISTINCT instance_element_id
              FROM element_identity_links
             WHERE instance_element_type = :element_type
               AND instance_element_id = ANY(:ids)
            """
        ),
        {"element_type": ELEMENT_THEORY_COMPONENT, "ids": keys},
    ).fetchall()
    return {str(row[0]) for row in rows}


_CHUNK_NEIGHBOR_SQL = """
    WITH q AS (
        SELECT embedding FROM chunks WHERE id = CAST(:chunk_id AS uuid)
    )
    SELECT c.id::text,
           1 - (c.embedding <=> (SELECT embedding FROM q)) AS similarity
      FROM chunks c
     WHERE c.embedding IS NOT NULL
       AND c.document_id <> CAST(:document_id AS uuid)
       AND (SELECT embedding FROM q) IS NOT NULL
     ORDER BY c.embedding <=> (SELECT embedding FROM q)
     LIMIT :top_k
"""


def _chunk_neighbors(
    session: Any, *, chunk_id: str, document_id: str, top_k: int
) -> list[tuple[str, float]]:
    """代表チャンクの pgvector 近傍（他 document・**追加 embedding ゼロ**）。"""
    if not chunk_id:
        return []
    try:
        rows = session.execute(
            sa_text(_CHUNK_NEIGHBOR_SQL),
            {"chunk_id": chunk_id, "document_id": document_id, "top_k": int(top_k)},
        ).fetchall()
    except Exception:  # noqa: BLE001 — ベクトル近傍が引けなくても語彙一致の候補は残す
        logger.warning("chunk-proxy neighbour search failed (non-fatal)", exc_info=True)
        return []
    out: list[tuple[str, float]] = []
    for row in rows:
        try:
            similarity = float(row[1])
        except (TypeError, ValueError):
            continue
        out.append((str(row[0]), similarity))
    return out


# ---------------------------------------------------------------------------
# 候補の書き込み（すべて candidate。確定は人間 = KR2）
# ---------------------------------------------------------------------------


def _ensure_candidate_entry(
    *, domain_key: str, name: str, component: dict, other: dict, justification: str
) -> Optional[dict]:
    """候補エントリを 1 行確保する（``dismissed`` は再提案しない = LS3）。"""
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
            entry_type=schema.entry_type_for_component_type(component.get("component_type")),
            name=name,
            source_component_ids=[component["id"], other["id"]],
            source_document_ids=[component["document_id"], other["document_id"]],
            review_status=schema.REVIEW_STATUS_CANDIDATE,
            mapping_justification=justification,
            candidate_key=candidate_key,
        )
    except Exception:  # noqa: BLE001 — 1 件作れなくても他の候補は残す
        logger.warning("failed to create candidate entry (non-fatal)", exc_info=True)
        return None


def _claim_element_ref(claim_id: str, document_id: str) -> ElementRef:
    """主張の ElementRef（規則 ④。live ビューから読んだ行なので実在は確認済み）。"""
    ref = ElementRef(
        scope=SCOPE_DOCUMENT,
        element_type=ELEMENT_THEORY_CLAIM,
        element_id=str(claim_id),
        document_id=str(document_id),
    )
    ref.validate()
    return ref


#: 同一性リンクの evidence に載せる主張本文の長さ（§7）。全文は載せない。
CLAIM_EVIDENCE_MAX_CHARS = 200


def _claim_concept_links(
    claims: list[dict],
    *,
    document_id: str,
    entries_by_id: dict[str, dict],
    linked_pairs: set[tuple[str, str]],
    budget: int,
) -> tuple[int, bool]:
    """規則 ④: 接地済み主張 → 確定済みエントリの同一性リンク候補（``(本数, 打ち切り)``）。

    ``entry_id`` を持つ ``concepts`` 要素だけが対象で、指す先が確定・公開中の
    エントリでなければ**作らない**（retired / dismissed を復活させない）。既にリンクの
    ある ``(claim, entry)`` の組は再提案しない（KR7）。
    """
    created = 0
    for claim in claims:
        claim_id = _clean(claim.get("id"))
        if not claim_id:
            continue
        seen_entries: set[str] = set()
        for concept in claim.get("concepts") or ():
            if not isinstance(concept, dict):
                continue
            entry_id = _clean(concept.get("entry_id"))
            if not entry_id or entry_id in seen_entries:
                continue
            entry = entries_by_id.get(entry_id)
            if entry is None:
                continue
            if (claim_id, entry_id) in linked_pairs:
                seen_entries.add(entry_id)
                continue
            if created >= budget:
                return created, True
            justification = _clean(concept.get("mapping_justification"))
            if not schema.is_valid_justification(justification):
                justification = schema.JUSTIFICATION_LEXICAL
            name = _clean(concept.get("name")) or entry["name"]
            link = _link_claim(
                claim,
                entry,
                document_id=document_id,
                local_name=name,
                justification=justification,
            )
            seen_entries.add(entry_id)
            if link is not None:
                created += 1
                linked_pairs.add((claim_id, entry_id))
    return created, False


def _claim_link_reason(entry: dict, justification: str) -> str:
    if justification == schema.JUSTIFICATION_LEXICAL:
        return f"この主張の本文に概念「{entry['name']}」が現れました。"
    return f"この主張は解析結果で概念「{entry['name']}」に結ばれています。"


def _link_claim(
    claim: dict, entry: dict, *, document_id: str, local_name: str, justification: str,
) -> Optional[dict]:
    """主張 → エントリの同一性リンク候補を 1 本作る（既存行があればそのまま返る）。"""
    excerpt = _clean(claim.get("text"))[:CLAIM_EVIDENCE_MAX_CHARS]
    try:
        return _identity_links.create_candidate(
            _claim_element_ref(claim["id"], document_id),
            entry["id"],
            local_expression={"name": local_name},
            evidence=[{"claim_text": excerpt}] if excerpt else None,
            reason=_claim_link_reason(entry, justification),
            mapping_justification=justification,
        )
    except Exception:  # noqa: BLE001 — 1 本作れなくても他の候補は残す
        logger.warning(
            "failed to create claim identity link candidate (non-fatal)", exc_info=True
        )
        return None


def _link(
    component: dict, entry_id: str, *, justification: str, reason: str,
    confidence: Optional[float] = None,
) -> Optional[dict]:
    """同一性リンク候補を 1 本作る（既存行があればそのまま返る = P4）。"""
    try:
        return _identity_links.create_candidate(
            _element_ref(component["id"], component["document_id"]),
            entry_id,
            local_expression={"name": component.get("name") or ""},
            reason=reason,
            confidence=confidence,
            mapping_justification=justification,
        )
    except Exception:  # noqa: BLE001 — 1 本作れなくても他の候補は残す
        logger.warning("failed to create identity link candidate (non-fatal)", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


def resolve_domain_key(session: Any, document_id: str, run_id: str | None) -> str:
    """候補エントリの ``domain_key`` を 3 段で解決する（§6.2 の 2）。

    ``corpus.document_domain_keys`` の先頭 → run の ``cartridge_id`` →
    :data:`core.library.schema.DOMAIN_KEY_UNASSIGNED`。**推測で分野名を作らない**。
    """
    try:
        keys = _corpus.document_domain_keys(session, document_id) or []
    except Exception:  # noqa: BLE001
        logger.warning("document domain lookup failed (non-fatal)", exc_info=True)
        keys = []
    for key in keys:
        if _clean(key):
            return _clean(key)
    if run_id:
        try:
            row = session.execute(
                sa_text(
                    "SELECT COALESCE(cartridge_id, '') FROM document_analysis_runs "
                    "WHERE id = CAST(:run_id AS uuid) LIMIT 1"
                ),
                {"run_id": run_id},
            ).fetchone()
        except Exception:  # noqa: BLE001
            logger.warning("analysis run cartridge lookup failed (non-fatal)", exc_info=True)
            row = None
        if row and _clean(row[0]):
            return _clean(row[0])
    return schema.DOMAIN_KEY_UNASSIGNED


def run_identity_candidates(
    *, document_id: str, run_id: str | None = None, session: Any = None
) -> dict:
    """§6.2 の 1〜6 を実行する（決定論・非LLM・追加 embedding ゼロ）。

    Args:
        document_id: 対象 document（``documents.id`` の UUID 文字列）。
        run_id: この候補を出した解析 run（監査の帰属）。
        session: SQLAlchemy セッション。``None`` なら自前で開閉する
            （パイプラインステージからはこちら）。

    Returns:
        ``{"document_id", "domain_key", "entries_created", "links_created",
        "components_with_candidates", "coverage"}``。``coverage`` は Phase 0 の共通
        報告形式（``population`` = 親 component 数）。**教員 UI へ出す数値ではない**
        （stage_outputs の中の取りこぼし報告 = KR6 の対象外の内部報告）。

    Note:
        候補エントリ（``library_store.create_entry``）と同一性リンク
        （``identity_links.create_candidate``）はそれぞれ自前のトランザクションで
        書く（既存の公開面がそうなっているため）。全体は 1 トランザクションでは
        ないが、``candidate_key`` と 4 列 UNIQUE により**再実行が安全**（冪等）で、
        途中で落ちても作れたところまでが残る（P4: 情報を落とさない）。
    """
    doc_id = _clean(document_id)
    settings = get_settings()
    limit = int(getattr(settings, "identity_candidates_max_per_document", 0) or 0)

    with _session_scope(session) as sess:
        parents = load_parent_components(sess, doc_id) if doc_id else []
        population = len(parents)
        if not doc_id:
            return _result(doc_id, "", 0, 0, 0, population, 0, [])
        if limit <= 0:
            return _result(
                doc_id, "", 0, 0, 0, population, 0, ["candidate_limit_is_zero"]
            )

        domain_key = resolve_domain_key(sess, doc_id, run_id)
        entries_by_id, entry_index = _load_confirmed_entry_keys(sess)
        # 規則 ④ は component が 1 つも無い document でも成立する（主張は別に在る）。
        others = load_other_parent_components(sess, doc_id) if parents else []
        others_by_normalized: dict[str, list[dict]] = {}
        others_by_chunk: dict[str, list[dict]] = {}
        for other in others:
            others_by_normalized.setdefault(
                schema.normalize_label(other["name"]), []
            ).append(other)
            for chunk_id in _component_chunk_ids(other):
                others_by_chunk.setdefault(chunk_id, []).append(other)

        linked = (
            _linked_component_ids(
                sess, [c["id"] for c in parents] + [o["id"] for o in others]
            )
            if parents
            else set()
        )

        entries_created = 0
        links_created = 0
        duplicate_by_component: dict[str, list[dict]] = {}
        reasons: list[str] = []
        processed = 0

        for component in parents:
            if entries_created + links_created >= limit:
                reasons.append("candidate_limit")
                break
            processed += 1
            normalized = schema.normalize_label(component["name"])
            if not normalized:
                continue

            # -- 1. 既存 entry への語彙一致 ---------------------------------
            matched = [
                entries_by_id[eid]
                for eid in entry_index.get(normalized, [])
                if eid in entries_by_id
            ]
            if matched:
                entry = matched[0]
                link = _link(
                    component,
                    entry["id"],
                    justification=schema.JUSTIFICATION_LEXICAL,
                    reason=f"概念「{entry['name']}」と表記が一致しました。",
                )
                if link is not None:
                    links_created += 1
                    duplicate_by_component.setdefault(component["id"], []).append(
                        {
                            "component_id": component["id"],
                            "document_id": component["document_id"],
                            "entry_id": entry["id"],
                            "mapping_justification": schema.JUSTIFICATION_LEXICAL,
                        }
                    )
                continue

            if component["id"] in linked:
                continue

            # -- 2. 他 document の component との語彙一致 --------------------
            twin = _first_unlinked(others_by_normalized.get(normalized), linked)
            justification = schema.JUSTIFICATION_LEXICAL
            similarity: Optional[float] = None
            if twin is None:
                # -- 3. chunk-proxy ベクトル近傍 ----------------------------
                twin, similarity = _chunk_proxy_twin(
                    sess,
                    component=component,
                    others_by_chunk=others_by_chunk,
                    linked=linked,
                )
                justification = schema.JUSTIFICATION_VECTOR
            if twin is None:
                continue

            entry = _ensure_candidate_entry(
                domain_key=domain_key,
                name=component["name"],
                component=component,
                other=twin,
                justification=justification,
            )
            if entry is None:
                continue
            if entry.get("review_status") == schema.REVIEW_STATUS_CANDIDATE:
                entries_created += 1
            for target, target_similarity in ((component, similarity), (twin, similarity)):
                link = _link(
                    target,
                    entry["id"],
                    justification=justification,
                    reason=_twin_reason(justification, component, twin),
                    confidence=target_similarity,
                )
                if link is None:
                    continue
                links_created += 1
                linked.add(target["id"])
                duplicate_by_component.setdefault(target["id"], []).append(
                    {
                        "component_id": target["id"],
                        "document_id": target["document_id"],
                        "entry_id": entry["id"],
                        "mapping_justification": justification,
                    }
                )

        # -- 規則 ④: 接地済み主張 → 確定済みエントリ（CG §7）------------------
        # component の候補（規則 ①〜③）と**上限を合算**する。entry は作らない。
        try:
            grounded_claims = load_grounded_claims(sess, doc_id)
        except Exception:  # noqa: BLE001 — 主張が読めなくても component 側の候補は残す
            logger.warning("grounded claim lookup failed (non-fatal)", exc_info=True)
            grounded_claims = []
        if grounded_claims:
            claim_links, claim_truncated = _claim_concept_links(
                grounded_claims,
                document_id=doc_id,
                entries_by_id=entries_by_id,
                linked_pairs=_linked_claim_pairs(sess),
                budget=max(limit - (entries_created + links_created), 0),
            )
            links_created += claim_links
            if claim_truncated and "candidate_limit" not in reasons:
                reasons.append("candidate_limit")

        # -- 4. duplicate_candidates への書き戻し + 監査 ---------------------
        written = 0
        if duplicate_by_component or links_created:
            from core.document_pipeline import persistence as _persistence

            for component_id, items in duplicate_by_component.items():
                try:
                    _persistence.set_duplicate_candidates(sess, component_id, items)
                    written += 1
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "failed to store duplicate_candidates (non-fatal)", exc_info=True
                    )
            try:
                _persistence.record_knowledge_audit(
                    sess,
                    document_id=doc_id,
                    run_id=run_id,
                    stats={
                        "identity_candidates": {
                            "entries": entries_created,
                            "links": links_created,
                            "components": written,
                        }
                    },
                )
            except Exception:  # noqa: BLE001 — 監査で候補生成を落とさない（非致命ステージ）
                logger.warning("identity candidate audit failed (non-fatal)", exc_info=True)

        return _result(
            doc_id,
            domain_key,
            entries_created,
            links_created,
            written,
            population,
            processed,
            reasons,
        )


def _component_chunk_ids(component: dict) -> list[str]:
    """component の代表 / 出典チャンク id（重複除去・順序保持）。"""
    ids = [_clean(component.get("primary_chunk_id"))]
    for value in component.get("source_chunks") or ():
        if isinstance(value, str):
            ids.append(_clean(value))
        elif isinstance(value, dict):
            ids.append(_clean(value.get("chunk_id") or value.get("id")))
    return [i for i in dict.fromkeys(ids) if i]


def _first_unlinked(candidates: Any, linked: set[str]) -> Optional[dict]:
    for candidate in candidates or ():
        if candidate["id"] not in linked:
            return candidate
    return None


def _chunk_proxy_twin(
    session: Any,
    *,
    component: dict,
    others_by_chunk: dict[str, list[dict]],
    linked: set[str],
) -> tuple[Optional[dict], Optional[float]]:
    """chunk-proxy 近傍から、まだ entry に結ばれていない他 document の component を 1 件。"""
    chunk_id = _clean(component.get("primary_chunk_id"))
    if not chunk_id:
        return None, None
    neighbours = _chunk_neighbors(
        session,
        chunk_id=chunk_id,
        document_id=component["document_id"],
        top_k=schema.IDENTITY_CHUNK_TOPK,
    )
    for neighbour_id, similarity in neighbours:
        if similarity < schema.IDENTITY_CHUNK_PROXY_THRESHOLD:
            continue
        twin = _first_unlinked(others_by_chunk.get(neighbour_id), linked)
        if twin is not None:
            return twin, similarity
    return None, None


def _twin_reason(justification: str, component: dict, twin: dict) -> str:
    if justification == schema.JUSTIFICATION_LEXICAL:
        return f"別の論文の「{twin['name']}」と表記が一致しました。"
    return f"別の論文の「{twin['name']}」と本文の意味が近いと測定されました。"


def _result(
    document_id: str,
    domain_key: str,
    entries_created: int,
    links_created: int,
    components_with_candidates: int,
    population: int,
    processed: int,
    reasons: list[str],
) -> dict:
    from episteme_graph.agents.coverage_report import build_coverage_report

    return {
        "document_id": document_id,
        "domain_key": domain_key,
        "entries_created": entries_created,
        "links_created": links_created,
        "components_with_candidates": components_with_candidates,
        "coverage": build_coverage_report(
            population=population,
            processed=processed,
            reasons=reasons,
            unit="components",
        ),
    }
