"""台帳対象の表示名の正本（IK-0901・D層・読み時・非LLM）。

台帳の対象（主張・前提・部品・式）の表示名は、かつて 2 つの関数に分かれていた:
``open_assumptions.target_label``（主張・部品 UUID・前提だけを引き、グラフのノード id・式の
agent ID は id のまま返す）と、ゼミ前ブリーフの ``_ReadableLabels``（式は印字番号、ノードは
操作の日本語名 + 根拠の主張）。前提の地図・未検証合意リスト・台帳詳細・学習者一覧は前者を
通っていたため、教員には ``eq_op_0529`` が、学生には（表示投影が内部 ID を遮断した結果）全行
「（本文を特定できない要素）」が出ていた。

規則（ここだけに置く）:
  - claim: 主張の本文（theory_claims_live）。
  - assumption: 前提の文面（assumption_nodes）。
  - component の UUID: 部品の名前（theory_components_live）。
  - component のノード id・equation の agent ID: **論文の中でしか一意でない**（IK-0899）ので
    ``document_id`` が必須。その論文のゼミ前ブリーフと同じ読める名前（ノード = 操作の日本語名 +
    根拠の主張 / 式 = ``graph_paper_layer.schema.equation_display_label``）。

``LedgerTargetLabels`` は論文ごとの読み時辞書を 1 回だけ作って使い回す（地図・一覧のように
同じ論文の対象を何件も名付けるときに使う）。FastAPI / LLM 非 import。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.ledger_targets import is_uuid_text, normalize_document_id

logger = logging.getLogger(__name__)

#: 名前を引けない対象の置き換え語（内部 ID を画面に出さない — PL7）。
UNNAMED_TARGET_LABEL = "名前を引けない対象"

_SQL_BY_TYPE: dict[str, tuple[str, int]] = {
    "claim": (
        "SELECT COALESCE(NULLIF(normalized_text, ''), text) FROM theory_claims_live WHERE id::text = :tid",
        160,
    ),
    "component": ("SELECT name FROM theory_components_live WHERE id::text = :tid", 160),
    "assumption": ("SELECT statement FROM assumption_nodes WHERE id::text = :tid", 200),
}


def _label_excerpt(value: Any, limit: int) -> str:
    """表示ラベルの丸め。語の途中で切らない（「→ clai」の切れ端を出さない — 第 17 周 te-05）。"""
    from core.text_excerpt import excerpt

    return excerpt(value, limit) or str(value or "")[:limit]


class LedgerTargetLabels:
    """台帳対象の表示名（論文ごとの読み時辞書を使い回す）。"""

    def __init__(self, session: Any) -> None:
        self.session = session
        self._by_document: dict[str, Any] = {}

    def _readable(self, document_id: str):
        doc = normalize_document_id(document_id)
        if not doc:
            return None
        if doc not in self._by_document:
            try:
                from core.doubt.seminar_brief import _ReadableLabels

                self._by_document[doc] = _ReadableLabels(self.session, doc)
            except Exception:  # noqa: BLE001 — 名前が引けないだけ（id は出さない）
                logger.debug("readable labels unavailable for %s", doc, exc_info=True)
                self._by_document[doc] = None
        return self._by_document[doc]

    def _claim_resolver(self, document_id: str):
        from core.knowledge_objects.claim_refs import ClaimRefResolver

        doc = normalize_document_id(document_id)
        key = "claims:" + doc
        if key not in self._by_document:
            self._by_document[key] = ClaimRefResolver(self.session, doc)
        return self._by_document[key]

    def raw(self, target_type: str, target_id: str, *, document_id: str | None) -> str:
        """表示名（引けなければ空文字）。"""
        tt = str(target_type or "").strip()
        tid = str(target_id or "").strip()
        if not tid:
            return ""
        sql = _SQL_BY_TYPE.get(tt)
        if sql and (tt != "component" or is_uuid_text(tid)):
            try:
                row = self.session.execute(sa_text(sql[0]), {"tid": tid}).fetchone()
            except Exception:  # noqa: BLE001
                logger.debug("target label lookup failed for %s/%s", tt, tid, exc_info=True)
                try:
                    self.session.rollback()
                except Exception:  # noqa: BLE001
                    pass
                row = None
            if row and row[0]:
                return _label_excerpt(row[0], sql[1])
            if tt not in ("component", "claim"):
                return ""
        if tt == "claim":
            # IK-0930: グラフ・DSL の主張参照（生成時の局所 ID）は、その論文の解決の正本で
            # UUID → 本文、無ければ artifact の本文へ（内部 ID は名前にしない）。
            return self._claim_resolver(document_id or "").label_for(tid)
        if tt not in ("component", "equation"):
            return ""
        labels = self._readable(document_id or "")
        if labels is None:
            return ""
        if tt == "equation":
            record = labels.equation_records.get(tid)
            if record:
                from core.graph_paper_layer.schema import equation_display_label

                name = equation_display_label(record)
                if name:
                    return name
        node_label = str(labels.node_labels.get(tid) or "").strip()
        if node_label:
            from core.display_projection import UNIDENTIFIED_ELEMENT_TEXT

            if node_label != UNIDENTIFIED_ELEMENT_TEXT and node_label != tid:
                return node_label
        return ""

    def label(self, target_type: str, target_id: str, *, document_id: str | None) -> str:
        """画面に出す表示名（引けなければ ``UNNAMED_TARGET_LABEL`` — 内部 ID を出さない）。"""
        name = self.raw(target_type, target_id, document_id=document_id)
        if not name:
            return UNNAMED_TARGET_LABEL
        from core.display_projection import contains_internal_id

        if contains_internal_id(name) and name.strip() == str(target_id or "").strip():
            return UNNAMED_TARGET_LABEL
        return name


def ledger_target_label(
    session: Any, *, document_id: str | None, target_type: str, target_id: str
) -> str:
    """台帳対象の表示名 1 件（正本。引けなければ ``UNNAMED_TARGET_LABEL``）。"""
    return LedgerTargetLabels(session).label(target_type, target_id, document_id=document_id)


__all__ = ["LedgerTargetLabels", "UNNAMED_TARGET_LABEL", "ledger_target_label"]
