"""キーフレーズ候補の供給（PD3 — 検索語彙は分野語彙から供給し、出所を明示する）。

設計正本: ``docs/features/paper_discovery_design.md`` §4.2 / §1 の成長ループ。

供給元は4系統で、いずれも**システムが既に持っている分野語彙**である:

1. ``skeleton``  — atlas 骨格（凍結版）の概念ラベル
2. ``cartridge`` — カートリッジ ``ontology.json`` の aliases / concept_types
3. ``alias``     — 教員が確定した骨格ノードの別名（VA層の還流2。正本は
   ``docs/features/atlas_vector_anchoring_design.md`` §7 — 教員の裁定した語彙なので
   ``component`` 由来より信頼が高く、その手前に置く）
4. ``component``— 当該分野のコースが参照する document 群の、承認済み理論部品のラベル

**AI・サーバが購読条件を書き換えることはない**（PD3）。ここが返すのはあくまで
「購読編集 UI の初期チップ候補」であり、採否は教員の操作で決まる。

設計方針:

- FastAPI 非 import・``core.llm`` 非 import（発見層は LLM 0回）。
- **fail-soft**: どの供給元が落ちても他の供給元の結果を返す（骨格なし分野・
  カートリッジ無し分野・対応 document ゼロはいずれも正常な状態であって
  エラーではない）。握りつぶしは ``logger.debug`` に残して黙殺しない。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.paper_discovery import corpus
from core.paper_discovery.schema import KEYPHRASE_SOURCES

logger = logging.getLogger(__name__)

#: 承認済みとみなす ``theory_components.review_status``。
#: 語彙は C層実装に合わせる（``core/reconstruction/schema.py::APPROVED_REVIEW_STATUSES``
#: と同じ集合。R層への依存を作らないため値をここに置くが、片方だけ増やさないこと）。
APPROVED_REVIEW_STATUSES = ("teacher_approved", "teacher_reviewed", "endorsed")

#: 1供給元あたりの上限（1系統がチップ欄を占領しないようにする）。
_PER_SOURCE_LIMIT = 30

#: フレーズとして短すぎるものは検索の役に立たない（"SM" のような略語は
#: aliases 経由で入るが、1文字は落とす）。
_MIN_PHRASE_LENGTH = 2


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _skeleton_phrases(session, domain_key: str) -> list[str]:
    """atlas 骨格（凍結版）の概念ラベル。

    領域（region）ラベルは概念より粗く検索語として広すぎるため使わない
    （PD3 の「骨格概念」）。骨格が無い分野は空リスト（正常な状態）。
    """
    from core import atlas_store  # 遅延 import（テスト時の依存を最小にする）

    skeleton = atlas_store.load_learner_skeleton(domain_key, session)
    if skeleton is None:
        return []
    phrases: list[str] = []
    for region in getattr(skeleton, "regions", ()) or ():
        for concept in getattr(region, "concepts", ()) or ():
            label = _clean(getattr(concept, "label", ""))
            if label:
                phrases.append(label)
    return phrases


def _cartridge_phrases(domain_key: str) -> list[str]:
    """カートリッジ ``ontology.json`` の語彙。

    ``aliases``（canonical + 別名）を先に、``concept_types`` の ``examples`` /
    ``label_en`` を後に置く。具体名（"Heavy Quark Effective Theory" / "HQET"）の
    方が検索語として効くため。
    """
    from core import cartridges  # 遅延 import

    cartridge = cartridges.load_cartridge(domain_key)
    ontology = getattr(cartridge, "ontology", None)
    if ontology is None:
        return []

    phrases: list[str] = []
    for alias in getattr(ontology, "aliases", ()) or ():
        if not isinstance(alias, dict):
            continue
        canonical = _clean(alias.get("canonical"))
        if canonical:
            phrases.append(canonical)
        for name in alias.get("aliases") or ():
            value = _clean(name)
            if value:
                phrases.append(value)

    for concept_type in getattr(ontology, "concept_types", ()) or ():
        if not isinstance(concept_type, dict):
            continue
        for example in concept_type.get("examples") or ():
            value = _clean(example)
            if value:
                phrases.append(value)
        label = _clean(concept_type.get("label_en"))
        if label:
            phrases.append(label)

    return phrases


def _alias_phrases(session, domain_key: str) -> list[str]:
    """教員が確定した骨格ノードの別名（VA層 §7 の還流2）。

    ``atlas_anchor_aliases`` の confirmed 行だけを読む（dismissed は出さない）。
    並びは ``node_id`` → ``alias`` の決定論順（``confirmed_aliases_by_node`` が
    ``node_id`` ごとに normalized 昇順で返す dict を、キー順に平坦化する）。
    別名レジストリが無い分野・未登録の分野は空リスト（正常な状態）。
    """
    from core.atlas_vectors.store import confirmed_aliases_by_node  # 遅延 import

    by_node = confirmed_aliases_by_node(session, domain_key) or {}
    phrases: list[str] = []
    for node_id in sorted(by_node):
        for alias in by_node.get(node_id) or ():
            value = _clean(alias)
            if value:
                phrases.append(value)
    return phrases


def _domain_document_refs(session, domain_key: str) -> list[str]:
    """当該分野のコースが参照する document の参照値（id と source_path の両方）。

    実装の正本は :func:`core.paper_discovery.corpus.domain_document_refs`
    （Phase 3 の ``ranking`` / ``citation_search`` と共有する。同じ解決を3箇所へ
    コピペしない）。ここは import 面を保つための薄い委譲。
    """
    return corpus.domain_document_refs(session, domain_key)


def _component_phrases(session, domain_key: str) -> list[str]:
    """当該分野の document 群に属する、承認済み理論部品のラベル。"""
    refs = _domain_document_refs(session, domain_key)
    if not refs:
        return []

    rows = session.execute(
        sa_text(
            """
            SELECT DISTINCT name
              FROM theory_components
             WHERE document_id = ANY(CAST(:refs AS text[]))
               AND review_status = ANY(CAST(:statuses AS text[]))
               AND COALESCE(name, '') <> ''
             ORDER BY name
             LIMIT :limit
            """
        ),
        {
            "refs": refs,
            "statuses": list(APPROVED_REVIEW_STATUSES),
            "limit": _PER_SOURCE_LIMIT,
        },
    ).fetchall()
    return [_clean(row[0]) for row in rows if _clean(row[0])]


_SUPPLIERS = (
    ("skeleton", lambda session, domain_key: _skeleton_phrases(session, domain_key)),
    ("cartridge", lambda session, domain_key: _cartridge_phrases(domain_key)),
    # 教員の確定語彙は、解析由来の部品ラベルより先に置く（VA層 §7 の還流2）。
    ("alias", lambda session, domain_key: _alias_phrases(session, domain_key)),
    ("component", lambda session, domain_key: _component_phrases(session, domain_key)),
)


def keyphrase_candidates(session, domain_key: str, *, limit: int = 40) -> list[dict]:
    """分野語彙から供給されるキーフレーズ候補を ``[{"text", "source"}]`` で返す。

    同一テキストは**最初の供給元が優先**される（骨格 → カートリッジ → 別名 → 部品の順。
    出所は表示に使うため、後から来た同じ語で上書きしない）。
    どの供給元も fail-soft で、落ちた系統は結果から抜けるだけ。

    Args:
        session: SQLAlchemy セッション（読み取りのみ。commit しない）。
        domain_key: atlas ドメイン / cartridge_id。
        limit: 返す候補の総数上限。
    """
    key = str(domain_key or "").strip()
    if not key:
        return []
    try:
        total_limit = max(0, int(limit))
    except (TypeError, ValueError):
        total_limit = 40
    if total_limit == 0:
        return []

    seen: set[str] = set()
    out: list[dict] = []
    for source, supplier in _SUPPLIERS:
        assert source in KEYPHRASE_SOURCES  # 語彙の正本は schema.py
        try:
            phrases = supplier(session, key)
        except Exception:  # noqa: BLE001 — 1供給元の失敗で他を巻き添えにしない
            logger.debug(
                "keyphrase supplier %s failed for domain %s", source, key, exc_info=True
            )
            continue

        taken = 0
        for phrase in phrases:
            text = _clean(phrase)
            if len(text) < _MIN_PHRASE_LENGTH:
                continue
            dedupe_key = text.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append({"text": text, "source": source})
            taken += 1
            if taken >= _PER_SOURCE_LIMIT or len(out) >= total_limit:
                break
        if len(out) >= total_limit:
            break

    return out[:total_limit]
