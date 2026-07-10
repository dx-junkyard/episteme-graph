"""経路B — コーパス横断の監査（D3-5, 非LLM・決定論的）。

「ほぼすべての導出が依存しているのに、どの論文もそれ自体を主張・引用・防御して
いないノード」= 引用グラフの空白を検出する。

同型判定について: core/isom.py は .isom 直列化のみで類似度評価は持たないため、
本監査の突合は **正規化した構造キー**（theory_object / label の正規化文字列）で行う。
表記ゆれの吸収は `_normalize_concept()` に一箇所隔離してあり、将来 DSL 埋め込み
ベースの同型判定に差し替える場合もここだけを変更すればよい。

原則:
  - 対象コーパス規模の下限 MIN_CORPUS_DOCUMENTS（既定 3 論文）。未満なら実行しない
    （小規模コーパスでの偽陽性防止）。単一論文コーパスでは候補が出ない。
  - 「依存されている」= 2 論文以上で下流依存（out-edge）を持つ。
  - 「被主張がない」= どの論文でも claim backing（linked_claim_ids /
    evidence_claim_ids）が付いていない。
  - 候補は assumption_nodes へ origin='mined_corpus' / status='candidate' で登録。
    確定フローは D2-4 を再利用（新 UI 不要）。cluster_key の一意性で
    confirmed / dismissed 済みの再候補化を防ぐ。
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.assumption_mining.schema import DETECTOR_VERSION, MIN_CORPUS_DOCUMENTS
from core.postgres import get_session

logger = logging.getLogger(__name__)


def _normalize_concept(label: str) -> str:
    """概念ラベルの正規化（表記ゆれ吸収の一箇所隔離）。"""
    text = unicodedata.normalize("NFKC", str(label or "")).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _graph_payload(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def run_corpus_audit(course_id: str = "") -> dict:
    """コーパス横断で「依存されているが被主張がない」ノードを候補化する。"""
    session = get_session()
    try:
        filters = ["TRUE"]
        params: dict[str, Any] = {}
        if course_id:
            filters.append("course_id = :course")
            params["course"] = course_id
        rows = session.execute(
            sa_text(f"""
                SELECT document_id, graph_json
                FROM theory_component_graphs
                WHERE {' AND '.join(filters)}
            """),
            params,
        ).fetchall()

        document_ids = sorted({str(r[0] or "") for r in rows if r[0]})
        if len(document_ids) < MIN_CORPUS_DOCUMENTS:
            logger.info(
                "corpus audit skipped: corpus too small (%d < %d documents)",
                len(document_ids), MIN_CORPUS_DOCUMENTS,
            )
            return {"skipped": True, "documents": len(document_ids), "registered": 0}

        # concept_key → {documents, depended_documents, claim_backed, node_ids, labels}
        concepts: dict[str, dict] = {}
        for row in rows:
            document_id = str(row[0] or "")
            payload = _graph_payload(row[1])
            nodes = [n for n in _as_list(payload.get("nodes")) if isinstance(n, dict)]
            edges = [e for e in _as_list(payload.get("edges")) if isinstance(e, dict)]

            out_degree: dict[str, int] = {}
            for edge in edges:
                source = str(edge.get("source_component_id") or edge.get("source") or "").strip()
                if source:
                    out_degree[source] = out_degree.get(source, 0) + 1

            for node in nodes:
                if str(node.get("graph_layer") or "main") == "debug":
                    continue
                nid = str(node.get("component_id") or node.get("id") or "").strip()
                raw_label = str(
                    node.get("theory_object") or node.get("label") or node.get("visual_label") or ""
                )
                key = _normalize_concept(raw_label)
                if not key or not nid:
                    continue
                entry = concepts.setdefault(key, {
                    "documents": set(),
                    "depended_documents": set(),
                    "claim_backed": False,
                    "node_ids": set(),
                    "label": raw_label.strip(),
                })
                entry["documents"].add(document_id)
                entry["node_ids"].add(nid)
                if out_degree.get(nid, 0) > 0:
                    entry["depended_documents"].add(document_id)
                if _as_list(node.get("linked_claim_ids")):
                    entry["claim_backed"] = True

            for edge in edges:
                evidence = edge.get("evidence") if isinstance(edge.get("evidence"), dict) else edge
                if _as_list(evidence.get("evidence_claim_ids")) or _as_list(evidence.get("evidence_claims")):
                    source = str(edge.get("source_component_id") or edge.get("source") or "").strip()
                    # 辺の evidence claim は source 側の主張とみなす
                    for entry in concepts.values():
                        if source in entry["node_ids"]:
                            entry["claim_backed"] = True

        registered = 0
        for key, entry in sorted(concepts.items()):
            if entry["claim_backed"]:
                continue
            if len(entry["depended_documents"]) < 2:
                continue  # 2 論文以上で依存されているノードのみ（偽陽性防止）
            cluster_key = f"corpus|{key}"
            created_from = {
                "detector_version": DETECTOR_VERSION,
                "audit": "corpus_gap",
                "concept_key": key,
                "node_ids": sorted(entry["node_ids"]),
                "document_ids": sorted(entry["documents"]),
                "depended_document_ids": sorted(entry["depended_documents"]),
                "gap_proof": "依存 out-edge が複数論文に存在する一方、linked_claim_ids / "
                             "evidence_claim_ids による主張 backing がコーパス内に存在しない",
            }
            statement = (
                f"概念『{entry['label']}』は複数の導出が依存しているが、"
                "コーパス内のどの論文もそれ自体を主張・防御していません（正規化待ち）"
            )
            result = session.execute(
                sa_text("""
                    INSERT INTO assumption_nodes
                        (statement, origin, status, cluster_key, created_from,
                         course_id, document_ids)
                    VALUES
                        (:statement, 'mined_corpus', 'candidate', :ckey, CAST(:cfrom AS jsonb),
                         :course, CAST(:docs AS jsonb))
                    ON CONFLICT (cluster_key) WHERE cluster_key <> '' DO NOTHING
                    RETURNING id
                """),
                {
                    "statement": statement,
                    "ckey": cluster_key,
                    "cfrom": json.dumps(created_from, ensure_ascii=False),
                    "course": course_id or "",
                    "docs": json.dumps(sorted(entry["documents"]), ensure_ascii=False),
                },
            ).fetchone()
            if result is not None:
                registered += 1
        session.commit()
        logger.info(
            "corpus audit done: concepts=%d registered=%d (documents=%d)",
            len(concepts), registered, len(document_ids),
        )
        return {"skipped": False, "documents": len(document_ids), "registered": registered}
    except Exception:
        session.rollback()
        logger.warning("corpus audit failed (course=%s)", course_id or "-", exc_info=True)
        return {"skipped": False, "registered": 0, "failed": True}
    finally:
        session.close()
