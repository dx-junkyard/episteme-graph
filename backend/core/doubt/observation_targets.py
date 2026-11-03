"""観測系 claim の多段同定（SL-2, 非LLM・決定論的・読み時）。

正本: docs/features/stakes_ledger_design.md §4.1。単一の抽出手段に賭けず、
3段の縮退で観測系 claim を同定する。どの経路で同定したかを ``identified_via``
として保持する（P4: 情報を落とさない）。

  A. DSL 述語（第一）: ``graph_json.dsl.edges[]`` の ``predicate == "MEASURES"``
     → ``evidence_refs.claim_ids``。DSL node id は component 層と別体系のため
     claim 経由でのみ渡る。
  B. theory stage（第二）: main 層 node の stage を
     ``element_vocab.theory_stage_key(label or visual_label)`` で復元し、
     ``{diagnostic_application, observation_model, observable_construction}``
     に属する node の ``linked_claim_ids``。
  C. claim 型（第三の縮退）: ``theory_claims.claim_type ∈
     {observable_definition, diagnostic_claim}``。

``dsl`` ブロックが空の旧 run では A が空になるだけで B/C が生きる（fail-soft）。
同一 claim が複数経路で見つかった場合は最初（優先度の高い）経路の
``identified_via`` を保持する（A > B > C）。

FastAPI 非 import・LLM 非 import（core/doubt/dependency.py と同じ非LLM層）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.scope import document_scope_clause, normalize_document_ids
from core.element_vocab import theory_stage_key

logger = logging.getLogger(__name__)

# B段（theory stage）で観測系とみなす stage キー（正本: schema.py の THEORY_STAGES）
_OBSERVATION_STAGES = (
    "diagnostic_application",
    "observation_model",
    "observable_construction",
)

# C段（claim 型）で観測系とみなす claim_type
_OBSERVATION_CLAIM_TYPES = ("observable_definition", "diagnostic_claim")

_MEASURES_PREDICATE = "MEASURES"

# IK-1057: 観測記録として数えない主張。式から合成した主張（origin=equation_synthesis —
# 「この論文の式は X を定義する」は式の言い直しで、観測の記録ではない）はどの段でも数えない。
# 段（B）は集約ノードの分類による代理の同定なので、主張自身の種類が背景（prior_work）と
# 言っているものは段の代理より主張自身の分類を採る（A・C は主張そのものの記述なので外さない）。
_NON_OBSERVATION_ORIGINS = ("equation_synthesis",)
# 第 9 波（IK-1057 残り）: 手法の選択（method_choice —「^13CO is used to trace …」）も観測の記録ではなく
# 手法の記述。実データ（Cepheus TeX の台帳 1925 行）で段階語の変化は 0・主張 42c4e2e8 の根から 1 件消える。
_STAGE_PROXY_EXCLUDED_CLAIM_TYPES = ("prior_work", "method_choice")

_IDENTIFIED_VIA_DSL = "dsl_measures"
_IDENTIFIED_VIA_STAGE = "theory_stage"
_IDENTIFIED_VIA_CLAIM_TYPE = "claim_type"

_LABEL_MAX_CHARS = 160


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


def _fetch_graph_rows(session, document_ids) -> list[tuple[str, dict]]:
    """theory_component_graphs から (document_id, graph_json) を document 集合の範囲で読む。

    dependency.build_dependency_graph と同じ範囲の規約（``core/doubt/scope.py`` —
    空集合は SQL を出さずに空。IK-0852）。このモジュールは dsl ブロック・stage 判定に
    生の nodes/edges が必要なため、DependencyGraph（除外済み・加工済み）を経由せず独自に読む。
    """
    clause = document_scope_clause(document_ids)
    if clause is None:
        return []
    where, params = clause
    try:
        rows = session.execute(
            sa_text(f"""
                SELECT document_id::text AS document_id, graph_json
                FROM theory_component_graphs
                WHERE {where}
            """),
            params,
        ).fetchall()
    except Exception:
        logger.warning("observation target graph lookup failed", exc_info=True)
        return []
    return [(str(r[0] or ""), _graph_payload(r[1])) for r in rows]


def _claim_ids_from_dsl(payload: dict) -> list[str]:
    """A段: dsl.edges[] の predicate=='MEASURES' → evidence_refs.claim_ids。"""
    dsl = payload.get("dsl") if isinstance(payload.get("dsl"), dict) else {}
    edges = [e for e in _as_list(dsl.get("edges")) if isinstance(e, dict)]
    claim_ids: list[str] = []
    for edge in edges:
        predicate = str(edge.get("predicate") or edge.get("edge_type") or "").strip().upper()
        if predicate != _MEASURES_PREDICATE:
            continue
        refs = edge.get("evidence_refs") if isinstance(edge.get("evidence_refs"), dict) else {}
        for cid in _as_list(refs.get("claim_ids")):
            cid = str(cid or "").strip()
            if cid:
                claim_ids.append(cid)
    return claim_ids


def _claim_ids_from_stage(payload: dict) -> list[str]:
    """B段: main 層 node の stage が観測系 → linked_claim_ids。"""
    nodes = [n for n in _as_list(payload.get("nodes")) if isinstance(n, dict)]
    claim_ids: list[str] = []
    for node in nodes:
        layer = str(node.get("graph_layer") or "main")
        if layer != "main":
            continue
        key = theory_stage_key(node.get("label")) or theory_stage_key(node.get("visual_label"))
        if key not in _OBSERVATION_STAGES:
            continue
        for cid in _as_list(node.get("linked_claim_ids")):
            cid = str(cid or "").strip()
            if cid:
                claim_ids.append(cid)
    return claim_ids


def _resolve_document_ids(document_ids, graph_rows: list[tuple[str, dict]]) -> list[str]:
    """C段の theory_claims 検索範囲（document_id の集合）を決める。

    範囲（``document_ids``）は常に含める（グラフが無い document でも C段は生きる,
    fail-soft）。グラフ行の document は範囲の内側にしか無いので和集合は範囲そのもの。
    """
    doc_ids: list[str] = list(normalize_document_ids(document_ids))
    for doc_id, _payload in graph_rows:
        if doc_id and doc_id not in doc_ids:
            doc_ids.append(doc_id)
    return doc_ids


def _claim_labels(session, claim_ids: set[str]) -> dict[str, str]:
    if not claim_ids:
        return {}
    try:
        rows = session.execute(
            sa_text("""
                SELECT id::text, COALESCE(NULLIF(normalized_text, ''), text)
                FROM theory_claims_live
                WHERE id::text = ANY(:ids)
            """),
            {"ids": list(claim_ids)},
        ).fetchall()
    except Exception:
        logger.debug("observation target claim label lookup failed", exc_info=True)
        return {}
    return {str(r[0]): str(r[1] or "")[:_LABEL_MAX_CHARS] for r in rows}


def _claim_kinds(session, claim_ids: set[str]) -> dict[str, tuple[str, str]]:
    """主張の ``(origin, claim_type)``（IK-1057）。引けない主張は辞書に無い（外さない）。"""
    if not claim_ids:
        return {}
    try:
        rows = session.execute(
            sa_text("""
                SELECT id::text, COALESCE(origin, ''), COALESCE(claim_type, '')
                FROM theory_claims_live
                WHERE id::text = ANY(:ids)
            """),
            {"ids": list(claim_ids)},
        ).fetchall()
    except Exception:
        logger.debug("observation target claim kind lookup failed", exc_info=True)
        return {}
    out: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) < 3:
            continue
        out[str(row[0])] = (str(row[1] or ""), str(row[2] or ""))
    return out


def _counts_as_observation(kind, identified_via: str) -> bool:
    """IK-1057: 観測記録として数えるか。種類が引けない主張は従来どおり数える（fail-soft）。"""
    if not kind:
        return True
    origin, claim_type = kind
    if origin in _NON_OBSERVATION_ORIGINS:
        return False
    if identified_via == _IDENTIFIED_VIA_STAGE and claim_type in _STAGE_PROXY_EXCLUDED_CLAIM_TYPES:
        return False
    return True


def observation_claim_targets(
    session,
    *,
    document_ids,
) -> list[dict]:
    """観測系 claim の一覧を3段の縮退で同定する（非LLM・読み時・決定論的）。

    範囲は ``document_ids``（必須・IK-0852）。空集合なら SQL を出さずに空を返す。

    Returns:
        ``[{"claim_id", "label", "identified_via"}]``。claim_id 昇順・重複なし。
        identified_via の優先順位は dsl_measures > theory_stage > claim_type
        （同一 claim が複数経路で見つかったら最初の経路を保持する）。
    """
    document_ids = normalize_document_ids(document_ids)
    if not document_ids:
        return []

    graph_rows = _fetch_graph_rows(session, document_ids)

    identified_via: dict[str, str] = {}
    # IK-0903: どの論文の観測か（選択肢に論文を添え、開いているグラフの外の観測を見分ける）。
    claim_documents: dict[str, str] = {}

    # --- A: DSL 述語 MEASURES（第一） -------------------------------------
    for doc_id, payload in graph_rows:
        for cid in _claim_ids_from_dsl(payload):
            identified_via.setdefault(cid, _IDENTIFIED_VIA_DSL)
            claim_documents.setdefault(cid, doc_id)

    # --- B: theory stage（第二） -------------------------------------------
    for doc_id, payload in graph_rows:
        for cid in _claim_ids_from_stage(payload):
            identified_via.setdefault(cid, _IDENTIFIED_VIA_STAGE)
            claim_documents.setdefault(cid, doc_id)

    # --- C: claim 型（第三の縮退） -------------------------------------------
    doc_ids = _resolve_document_ids(document_ids, graph_rows)
    if doc_ids:
        try:
            rows = session.execute(
                sa_text("""
                    SELECT id::text, document_id::text
                    FROM theory_claims_live
                    -- docs は範囲の document id（text 表現）。text 比較で突き合わせる（080）。
                    WHERE document_id::text = ANY(CAST(:docs AS text[]))
                      AND claim_type = ANY(:ctypes)
                """),
                {"docs": doc_ids, "ctypes": list(_OBSERVATION_CLAIM_TYPES)},
            ).fetchall()
            for row in rows:
                cid = str(row[0])
                identified_via.setdefault(cid, _IDENTIFIED_VIA_CLAIM_TYPE)
                if len(row) > 1 and row[1]:
                    claim_documents.setdefault(cid, str(row[1]))
        except Exception:
            logger.warning("observation target claim_type lookup failed", exc_info=True)

    labels = _claim_labels(session, set(identified_via.keys()))
    from core.doubt.target_labels import UNNAMED_TARGET_LABEL
    from core.knowledge_objects.claim_refs import ClaimRefResolvers

    # IK-0930: A/B 段はグラフの主張参照（生成時の局所 ID のことがある）、C 段は DB の UUID。
    # 解決の正本で同じ主張を UUID に揃えて重ねる（先に見つかった経路を保持）。名前は本文
    # （DB → artifact）。UUID に届かない参照は元の綴りのまま残す（伝播はグラフの綴りで引ける）。
    resolvers = ClaimRefResolvers(session)
    merged: dict[str, dict] = {}
    for cid, via in identified_via.items():
        doc = claim_documents.get(cid, "")
        resolver = resolvers.for_document(doc)
        try:
            canonical = resolver.uuid_for(cid) or cid
            label = labels.get(cid) or resolver.label_for(cid)
        except Exception:  # noqa: BLE001
            canonical, label = cid, labels.get(cid) or ""
        if canonical in merged:
            continue
        # 本文の引けない主張（agent 側の id のまま）は id を名前に使わない（IK-0902 / PL7）。
        merged[canonical] = {
            "claim_id": canonical,
            "label": label or UNNAMED_TARGET_LABEL,
            "identified_via": via,
            "document_id": doc,
        }
    kinds = _claim_kinds(session, set(merged.keys()))
    items = [
        item for item in merged.values()
        if _counts_as_observation(kinds.get(item["claim_id"]), item["identified_via"])
    ]
    items.sort(key=lambda item: item["claim_id"])
    return items
