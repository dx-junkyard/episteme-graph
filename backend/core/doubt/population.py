"""D層の一覧の母集合から claim チェーンの導出ステップを外す（IK-0933・読み時・非LLM）。

前提の地図・未検証合意リスト（教員・学習者）・ゼミ前ブリーフの台帳対象は、理論操作グラフの
ノード（target_type='component'）を粒度の区別なく拾っていた。式の詳細層には、論文の主張を
出現順に並べた claim チェーンの手順（「中間主張を導出: …」）が大量に並び、前提・主張・式の
点が一覧の先頭から消えていた。

claim チェーンが「理論の構造ではない」という判断は理論モジュール層が既に持つ
（``core/theory_modules/builder.py::claim_sequence_node_ids`` — グラフレビューのキャンバスから
外す目印）。D層はその判定を**そのまま**通す（判定を二重に持たない — canonical_source）。

外したことは一覧に事実文 1 行で残す（情報を落とさない — 行は台帳に残り、詳細は開ける）。
依存の伝播のうち負荷（load）と反実仮想は、2026-10-27 第 6 波（IK-1030・オーナー判断）から
同じ判定で claim チェーンの辺（主張の出現順の並び）を依存に数えない
（``claim_sequence_ids_for_payload`` → ``dependency.build_dependency_graph(exclude_claim_sequence_edges=True)``）。
支持線・ゼミ前ブリーフ・反証条件の入力は従来どおり全ノード・全辺で計算する（別に決める）。
FastAPI / LLM 非 import。失敗は fail-soft（外さない = 従来どおり）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

#: 一覧から導出の手順を外したときに添える事実文（教員・学習者共通。内部語・数値なし）。
CLAIM_CHAIN_EXCLUDED_NOTE = (
    "論文の主張を出現順にたどる導出の手順は、前提ではないためこの一覧に含めていません。"
)


def _graph_payload(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def claim_sequence_ids_for_payload(session: Any, document_id: str, payload: Any) -> set[str]:
    """1 論文のグラフ payload について claim チェーン由来ノードの集合を返す（IK-1030）。

    判定は ``core/theory_modules/builder.py::claim_sequence_node_ids`` をそのまま通す
    （一覧の母集合 = IK-0933 と、依存の広がり・反実仮想の辺の採用 = IK-1030 が同じ正本）。
    成果物の読みは呼び出し側のトランザクションを壊さないよう、可能ならセーブポイントの中で行う。
    判定できないときは空集合（= 外さない・従来どおり）。
    """
    doc = str(document_id or "").strip().lower()
    nodes = [n for n in (_graph_payload(payload).get("nodes") or []) if isinstance(n, dict)]
    if not doc or not nodes:
        return set()
    try:
        from core.document_pipeline.persistence import document_run_artifacts
        from core.theory_modules.builder import claim_sequence_node_ids

        nested = None
        begin_nested = getattr(session, "begin_nested", None)
        if callable(begin_nested):
            try:
                nested = begin_nested()
            except Exception:  # noqa: BLE001 — セーブポイントが張れない = そのまま読む
                nested = None
        try:
            artifacts = document_run_artifacts(doc, session=session) or {}
        except Exception:  # noqa: BLE001
            if nested is not None:
                try:
                    nested.rollback()
                except Exception:  # noqa: BLE001
                    pass
            raise
        else:
            if nested is not None:
                try:
                    nested.commit()
                except Exception:  # noqa: BLE001
                    pass
        return set(claim_sequence_node_ids(graph_json={"nodes": nodes}, artifacts=artifacts))
    except Exception:  # noqa: BLE001 — 判定できない = 外さない（従来どおり）
        logger.debug("claim sequence ids unavailable for %s", doc, exc_info=True)
        return set()


class ClaimChainExclusion:
    """論文ごとの claim チェーン由来ノードの集合を遅延で作り、台帳対象を判定する。"""

    def __init__(self, session: Any) -> None:
        self.session = session
        self._by_document: dict[str, set[str]] = {}
        #: 1 件でも外したか（一覧に事実文を添えるかの判定に使う）。
        self.excluded = False

    def node_ids(self, document_id: str) -> set[str]:
        doc = str(document_id or "").strip().lower()
        if not doc:
            return set()
        if doc in self._by_document:
            return self._by_document[doc]
        result: set[str] = set()
        try:
            rows = self.session.execute(
                sa_text(
                    "SELECT graph_json FROM theory_component_graphs "
                    "WHERE document_id = CAST(:doc AS uuid)"
                ),
                {"doc": doc},
            ).fetchall()
            nodes: list = []
            for row in rows or []:
                payload = _graph_payload(row[0] if row else None)
                nodes.extend(n for n in (payload.get("nodes") or []) if isinstance(n, dict))
            if nodes:
                from core.document_pipeline.persistence import document_run_artifacts
                from core.theory_modules.builder import claim_sequence_node_ids

                artifacts = document_run_artifacts(doc, session=self.session) or {}
                result = set(claim_sequence_node_ids(graph_json={"nodes": nodes}, artifacts=artifacts))
        except Exception:  # noqa: BLE001 — 判定できない = 外さない（従来どおり）
            logger.debug("claim chain exclusion unavailable for %s", doc, exc_info=True)
            try:
                self.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            result = set()
        self._by_document[doc] = result
        return result

    def excludes(self, target_type: str, target_id: str, document_id: str | None) -> bool:
        """この台帳対象を一覧から外すか（component のノード id だけが対象）。"""
        if str(target_type or "") != "component":
            return False
        tid = str(target_id or "").strip()
        if not tid or not document_id:
            return False
        hit = tid in self.node_ids(str(document_id))
        if hit:
            self.excluded = True
        return hit


__all__ = ["CLAIM_CHAIN_EXCLUDED_NOTE", "ClaimChainExclusion", "claim_sequence_ids_for_payload"]
