"""discuss モード「開幕画面」の投影（設計書 §3.3 / §6.3）。

白紙のチャット欄で始めないための3要素（中心命題・支持構造・最も脆い一手／理論の
バックボーン／最初の一手は最初の一手のチップのみフロント側で描く）を、A層成果物
（thesis_reconstruction artifact・TheoryOperationGraph の main 層）と D層台帳の投影
（``core.doubt.open_assumptions.compile_open_assumptions``）から**非LLM**で組み立てる。

設計原則:
- 純粋投影部（``project_thesis`` / ``project_backbone`` / ``project_fragile_points`` と
  その下請け）は fake の dict を渡すだけで単体テストできる（``core/personal_graph/derive.py``
  の「純粋関数と DB 読み出しの分離」を踏襲。参照: ``test_discuss_opening.py``）。
- DB 読み出し部（``_document_titles`` / ``_load_graph_nodes`` / `_claim_label_index`` /
  ``_compile_open_assumptions``）はこのモジュール内で完結させ、都度セッションを開いて
  ``finally`` で閉じる（``core/component_context.py`` と同じ流儀）。1文書の読み出し失敗が
  画面全体を壊さないよう、呼び出し側で fail-soft に握る。
- confidence / load_score 等の生数値は一切レスポンスに含めない（DM6/W8）。射影する
  フィールドをホワイトリストで組み立てたうえ、念のため再帰的にも除去する。
- 本モジュールは FastAPI を import しない。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from core.deliberation.refs import document_run_artifacts, equation_records

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 上限（設計書 §3.3 / §6.3 の契約どおり）
# ---------------------------------------------------------------------------

_MAX_CENTRAL_ITEMS = 5
_MAX_SUPPORT_ITEMS_PER_SECTION = 5
_MAX_BACKBONE_NODES = 12
_MAX_FRAGILE_POINTS = 8

# ---------------------------------------------------------------------------
# theory stage 語彙（domain-neutral・A層コードを import しない写し）。
# 順序の正本: core/atlas_state.py::_STAGE_ORDER（L3 導出チェーンが同じ並びを使う）。
# 表示名の正本: src/episteme_graph/agents/component_graph/schema.py::THEORY_STAGE_LABELS。
# ---------------------------------------------------------------------------

_STAGE_ORDER = (
    "theory_basis",
    "observation_model",
    "observable_construction",
    "equation_system",
    "elimination",
    "consistency_relation",
    "diagnostic_application",
)

_STAGE_LABELS = {
    "theory_basis": "Theory basis",
    "observation_model": "Observation model",
    "observable_construction": "Observable construction",
    "equation_system": "Equation system",
    "elimination": "Elimination",
    "consistency_relation": "Consistency relation",
    "diagnostic_application": "Diagnostic / application",
}


def _stage_label(stage: str) -> str:
    """stage コード → 表示ラベル。未知の stage は機械的な整形に縮退する（情報を落とさない）。

    正本 ``theory_stage_label()``（agents 側）と同じ縮退規則
    （``snake_case`` → 先頭大文字の空白区切り）。
    """
    key = str(stage or "").strip()
    if not key:
        return ""
    return _STAGE_LABELS.get(key, key.replace("_", " ").capitalize())


# ThesisReconstructionAgent.SUPPORT_SECTIONS の日本語ラベル。
# 正本: core/deliberation/positioning.py::_SUPPORT_SECTION_LABELS（private 定数のため
# import せず最小の再掲に留める。語彙自体は agents/thesis_reconstruction/schema.py の
# SUPPORT_SECTIONS が正本）。
_SUPPORT_SECTION_LABELS = {
    "direct_supports": "直接支持",
    "assumptions": "前提",
    "derivation_core": "導出の核",
    "correction_sources": "訂正の源",
    "uncertainty_sources": "不確実性の源",
    "diagnostic_consequences": "診断的帰結",
    "future_requirements": "将来要件",
}

# TheoryOperationGraph の review_reasons 語彙（CLAUDE.md「TheoryOperationGraph」節が正本）
# の事実文化。未知の reason コードはコードそのものを表示する（情報を落とさない）。
_REVIEW_REASON_FACT_PHRASES = {
    "missing_atomic_claim": "裏付けとなる atomic claim が見つかっていません",
    "missing_evidence_link": "根拠へのリンクが不足しています",
    "missing_equation_link": "式へのリンクが不足しています",
    "missing_derivation_link": "導出へのリンクが不足しています",
    "equation_needs_math_review": "数式の確認が必要です",
    "edge_not_source_backed": "関係の根拠が出典に裏付けられていません",
    "fallback_or_inferred_node": "推定によって補われた箇所です",
    "source_span_missing": "元テキストの範囲が特定されていません",
}

# 数値を一切見せない（W8/DM6）。射影後も念のため再帰的に除去するキー。
_FORBIDDEN_NUMERIC_KEYS = ("confidence", "load_score", "score")


# ---------------------------------------------------------------------------
# 純粋ヘルパ（DB非依存・fake データで単体テスト可能）
# ---------------------------------------------------------------------------


def _dedupe_ids(raw_ids: Any) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in raw_ids or []:
        key = str(raw or "").strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def _claim_ref_item(claim_id: str, claim_label_index: dict[str, str]) -> dict[str, str]:
    """claim_id → {"id","label"}。解決できない id は id 文字列そのものを label にする
    （設計書: 情報を落とさない）。
    """
    label = str(claim_label_index.get(claim_id) or "").strip() or claim_id
    return {"id": claim_id, "label": label}


def _equation_label(record: dict[str, Any] | None) -> str:
    """equation record → 表示ラベル（``core/component_context.py::_equation_label`` /
    ``core/deliberation/context_lens.py::_equation_label`` と同型。equation は独立
    テーブルを持たないため、各モジュールがこの小さな整形をそれぞれ持つのが既存の慣行）。
    """
    if not isinstance(record, dict):
        return ""
    src = record.get("source_extraction") if isinstance(record.get("source_extraction"), dict) else {}
    rec = record.get("reconstruction") if isinstance(record.get("reconstruction"), dict) else {}
    text = (
        rec.get("plain_text") or rec.get("latex")
        or src.get("plain_text") or src.get("latex")
        or record.get("label") or record.get("equation_id") or ""
    )
    return str(text)[:80]


def _equation_ref_item(equation_id: str, equations_by_id: dict[str, dict]) -> dict[str, str]:
    record = equations_by_id.get(equation_id)
    label = _equation_label(record) if record else ""
    return {"id": equation_id, "label": label or equation_id}


def project_thesis(
    thesis: dict[str, Any] | None,
    claim_label_index: dict[str, str],
    equations_by_id: dict[str, dict],
) -> dict[str, Any] | None:
    """thesis_reconstruction artifact → 開幕画面の thesis DTO（設計書 §3.3 の契約）。

    artifact 自体が無ければ ``None``。central_thesis / support_structure の
    claim_ids・equation_ids をラベル解決込みで射影する。各リストは上限で切るが、
    切ったこと自体を示す独立フラグは持たない（documents[].truncated は backbone 専用。
    設計契約に無いフィールドを増やさない）。
    """
    if not isinstance(thesis, dict):
        return None

    central = thesis.get("central_thesis")
    central = central if isinstance(central, dict) else {}
    central_claims = [
        _claim_ref_item(cid, claim_label_index)
        for cid in _dedupe_ids(central.get("claim_ids"))[:_MAX_CENTRAL_ITEMS]
    ]
    central_equations = [
        _equation_ref_item(eid, equations_by_id)
        for eid in _dedupe_ids(central.get("equation_ids"))[:_MAX_CENTRAL_ITEMS]
    ]

    support_structure = thesis.get("support_structure")
    support_structure = support_structure if isinstance(support_structure, dict) else {}
    support_sections: list[dict[str, Any]] = []
    for section_key, entries in support_structure.items():
        if not isinstance(entries, list):
            continue
        items: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for cid in _dedupe_ids(entry.get("claim_ids")):
                if len(items) >= _MAX_SUPPORT_ITEMS_PER_SECTION:
                    break
                items.append({"type": "claim", **_claim_ref_item(cid, claim_label_index)})
            if len(items) >= _MAX_SUPPORT_ITEMS_PER_SECTION:
                continue
            for eid in _dedupe_ids(entry.get("equation_ids")):
                if len(items) >= _MAX_SUPPORT_ITEMS_PER_SECTION:
                    break
                items.append({"type": "equation", **_equation_ref_item(eid, equations_by_id)})
        if not items:
            continue
        support_sections.append(
            {
                "key": str(section_key),
                "label": _SUPPORT_SECTION_LABELS.get(str(section_key), str(section_key)),
                "items": items,
            }
        )

    return {
        "central_claims": central_claims,
        "central_equations": central_equations,
        "support_sections": support_sections,
    }


def _thesis_is_empty(thesis: dict[str, Any] | None) -> bool:
    if not thesis:
        return True
    return not (
        thesis.get("central_claims") or thesis.get("central_equations") or thesis.get("support_sections")
    )


def project_backbone(
    graph_nodes: list[dict[str, Any]] | None, *, limit: int = _MAX_BACKBONE_NODES
) -> tuple[list[dict[str, Any]], bool]:
    """TheoryOperationGraph の main 層ノード → 開幕画面のバックボーン投影。

    ``graph_layer == "main"`` のみを対象にし（式単位の equation_detail / debug 層は
    集約ラベルを持たないため対象外。CLAUDE.md「TheoryOperationGraph」節）、
    theory stage 順（``_STAGE_ORDER``。無ければ末尾）→ node_id の安定ソートで並べ、
    上限で切って ``truncated`` を正直に返す（先例:
    ``core/atlas_state.py::build_derivation_chain`` / ``core/deliberation/decomposition.py::
    _graph_node_for_component`` の id キー探索を踏襲し、``component_id`` / ``id`` /
    ``node_id`` のいずれの表記も受理する）。
    """
    main_nodes: list[dict[str, Any]] = []
    for node in graph_nodes or []:
        if not isinstance(node, dict):
            continue
        layer = str(node.get("graph_layer") or "main")
        if layer != "main":
            continue
        node_id = str(node.get("component_id") or node.get("id") or node.get("node_id") or "").strip()
        if not node_id:
            continue
        main_nodes.append(node)

    def _node_id(node: dict[str, Any]) -> str:
        return str(node.get("component_id") or node.get("id") or node.get("node_id") or "")

    def _sort_key(node: dict[str, Any]) -> tuple[int, str]:
        stage = str(node.get("stage") or "")
        idx = _STAGE_ORDER.index(stage) if stage in _STAGE_ORDER else len(_STAGE_ORDER)
        return (idx, _node_id(node))

    main_nodes.sort(key=_sort_key)
    truncated = len(main_nodes) > limit

    projected: list[dict[str, Any]] = []
    for node in main_nodes[:limit]:
        stage = str(node.get("stage") or "")
        node_id = _node_id(node)
        review_reasons = [str(r) for r in (node.get("review_reasons") or []) if str(r or "").strip()]
        projected.append(
            {
                "node_id": node_id,
                "label": str(node.get("label") or "").strip() or _stage_label(stage) or node_id,
                "stage": stage,
                "stage_label": _stage_label(stage),
                "description": str(node.get("description") or ""),
                "review_status": str(node.get("review_status") or ""),
                "source_backing_status": str(node.get("source_backing_status") or ""),
                "review_reasons": review_reasons,
            }
        )
    return projected, truncated


def _is_fragile_backbone_node(node: dict[str, Any]) -> bool:
    """backbone ノードのうち「最も脆い一手」候補か（review_required 系）。

    CLAUDE.md: review_status は source_backing_status から導出されるが、念のため
    両方を見て判定する（一方が欠けていても他方で拾う。P4）。
    """
    if str(node.get("review_status") or "") == "review_required":
        return True
    return str(node.get("source_backing_status") or "") in ("inferred", "review_required")


def _backbone_fact_line(node: dict[str, Any]) -> str:
    """review_required なバックボーンノードの事実文（煽らない・断定しない）。"""
    reasons = node.get("review_reasons") or []
    phrases = [_REVIEW_REASON_FACT_PHRASES.get(str(r), str(r)) for r in reasons if str(r or "").strip()]
    if phrases:
        return "レビュー待ちの箇所です: " + "、".join(phrases)
    return "レビュー待ちの箇所です。"


def _assumption_fact_line(item: dict[str, Any]) -> str:
    """未検証合意リスト項目（``compile_open_assumptions`` の1件）の事実文。

    ``routes/doubt.py::_learner_fact_line`` と同じ「検証済みも未記帳も同じ精度で
    併記する」思想（§8-1/8-2）を、開幕画面向けの短い一文に凝縮したもの。
    """
    if bool(item.get("scope_count_is_zero")):
        return "検証スコープは記録されていません。"
    status = str(item.get("verification_status") or "unknown")
    if status in ("untested", "unknown"):
        return "未検証の前提です。"
    if status == "refuted":
        return "反証の記帳がある前提です。"
    return "検証状況の記帳がある前提です。"


def project_fragile_points(
    assumption_items: list[dict[str, Any]],
    backbone_by_document: dict[str, list[dict[str, Any]]] | Iterable[tuple[str, list[dict[str, Any]]]],
    *,
    limit: int = _MAX_FRAGILE_POINTS,
) -> tuple[list[dict[str, Any]], bool]:
    """「最も脆い一手」= 未検証合意（D層台帳の投影）+ backbone の review_required ノード。

    順序は「台帳の投影（開幕画面が最初に見せるべき、教材横断の一次情報）→ document 順の
    backbone ノード」の決定論的な並びに固定する（賞レース化しない=順位づけの演出はしない。
    D3-2 の「並び順は負荷段階→依存数」の精神を踏襲し、ここでは情報源の種類で1段階だけ分ける）。
    同一対象が両方の経路から出ても id 体系が異なる（台帳 target_id と backbone node_id）ため
    unify はせず両方保持する（情報を落とさない, P4）。
    """
    points: list[dict[str, Any]] = []
    for item in assumption_items or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("statement") or item.get("target_id") or "").strip()
        if not label:
            continue
        points.append(
            {
                "kind": "assumption",
                "label": label,
                "fact_line": _assumption_fact_line(item),
                "document_id": None,
            }
        )

    items_view = (
        backbone_by_document.items() if isinstance(backbone_by_document, dict) else backbone_by_document
    )
    for document_id, nodes in items_view or []:
        for node in nodes or []:
            if not isinstance(node, dict) or not _is_fragile_backbone_node(node):
                continue
            label = str(node.get("label") or node.get("node_id") or "").strip()
            if not label:
                continue
            points.append(
                {
                    "kind": "backbone_node",
                    "label": label,
                    "fact_line": _backbone_fact_line(node),
                    "document_id": str(document_id),
                }
            )

    truncated = len(points) > limit
    return points[:limit], truncated


def _is_available(documents: list[dict[str, Any]], fragile_points: list[dict[str, Any]]) -> bool:
    for doc in documents:
        if not _thesis_is_empty(doc.get("thesis")):
            return True
        if doc.get("backbone"):
            return True
    return bool(fragile_points)


def _strip_numeric_keys(value: Any) -> Any:
    """レスポンスを再帰走査して confidence/load_score/score 系キーを除去する
    （W8/DM6 相当。``core/component_context.py::_strip_confidence`` と同型の安全網。
    射影関数はそもそもホワイトリストで組み立てているため通常は何も落とさない）。
    """
    if isinstance(value, dict):
        return {
            k: _strip_numeric_keys(v) for k, v in value.items() if k not in _FORBIDDEN_NUMERIC_KEYS
        }
    if isinstance(value, list):
        return [_strip_numeric_keys(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# DB 読み出し部（都度セッションを開いて閉じる。core/component_context.py と同じ流儀）
# ---------------------------------------------------------------------------


def _document_titles(document_ids: list[str]) -> dict[str, str]:
    """documents.title（無ければ source_path）を id → title でまとめて解決する
    （N+1 回避。先例: core/personal_graph/queries.py::fetch_document_titles と同じ
    プレースホルダ方式 — ``id = ANY(:ids)`` は uuid 列と text 配列の比較で型不一致に
    なりうるため使わない）。
    """
    ids = sorted({str(d) for d in document_ids if str(d or "").strip()})
    if not ids:
        return {}
    from sqlalchemy import text as sa_text

    from core.postgres import get_session

    session = get_session()
    try:
        placeholders = ", ".join(f"CAST(:id_{i} AS uuid)" for i in range(len(ids)))
        rows = session.execute(
            sa_text(f"SELECT id::text AS id, title, source_path FROM documents WHERE id IN ({placeholders})"),
            {f"id_{i}": doc_id for i, doc_id in enumerate(ids)},
        ).mappings().fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("discuss opening: document title lookup failed", exc_info=True)
        return {}
    finally:
        session.close()
    return {str(r["id"]): str(r.get("title") or r.get("source_path") or "") for r in rows}


def _load_graph_nodes(document_id: str) -> list[dict[str, Any]]:
    """document の最新 theory_component_graphs 行から graph_json.nodes[] を返す
    （best-effort。先例: core/component_context.py::_load_graph_narrative /
    core/deliberation/decomposition.py::_graph_node_for_component）。
    """
    from sqlalchemy import text as sa_text

    from core.postgres import get_session

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT graph_json FROM theory_component_graphs
                WHERE document_id = :doc ORDER BY updated_at DESC LIMIT 1
                """
            ),
            {"doc": document_id},
        ).fetchone()
    except Exception:  # noqa: BLE001
        logger.warning("discuss opening: graph load failed for %s", document_id, exc_info=True)
        return []
    finally:
        session.close()
    graph = row[0] if row and isinstance(row[0], dict) else {}
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    return [n for n in nodes if isinstance(n, dict)] if isinstance(nodes, list) else []


def _claim_label_index(document_id: str, artifacts: dict[str, Any]) -> dict[str, str]:
    """claim_id（DB UUID / agent 側 legacy id・span id の両方）→ 本文ラベルの索引。

    ``core/deliberation/context_lens.py`` の2つの下請け（``_claim_id_lookup_from_rows``
    が theory_claims.source_scope.legacy_ids / span_id から id 変換表を作る規約、
    ``_artifact_claim_text_index`` が claim_object_builder artifact の text で
    agent 側 sub-claim id を補う規約）を、ここでは「id → 本文」まで一段で持つ索引に
    合成している（本モジュール専用の投影のため、ラベルまで持たない汎用の id 変換表
    より扱いやすい）。theory_claims に無い sub-claim id は artifact 側の text/
    normalized_text で補い、どちらにも無ければ呼び出し側が id 文字列をそのまま表示する。
    """
    index: dict[str, str] = {}

    from sqlalchemy import text as sa_text

    from core.postgres import get_session

    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                "SELECT id::text AS id, text, normalized_text, source_scope "
                "FROM theory_claims WHERE document_id = :doc"
            ),
            {"doc": document_id},
        ).mappings().fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("discuss opening: theory_claims label lookup failed for %s", document_id, exc_info=True)
        rows = []
    finally:
        session.close()

    for row in rows:
        db_id = str(row["id"])
        label = str(row.get("text") or "").strip() or str(row.get("normalized_text") or "").strip()
        if not label:
            continue
        index.setdefault(db_id, label)
        scope = row.get("source_scope") if isinstance(row.get("source_scope"), dict) else {}
        for legacy_id in scope.get("legacy_ids") or []:
            key = str(legacy_id or "").strip()
            if key:
                index.setdefault(key, label)
        span_id = scope.get("span_id")
        if span_id:
            index.setdefault(str(span_id), label)

    claim_builder = artifacts.get("claim_object_builder") if isinstance(artifacts, dict) else None
    claims = claim_builder.get("claims") if isinstance(claim_builder, dict) else None
    for c in claims or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("claim_id") or "").strip()
        if not cid or cid in index:
            continue
        label = str(c.get("text") or "").strip() or str(c.get("normalized_text") or "").strip()
        if label:
            index[cid] = label
    return index


def _equations_index_for(
    document_id: str, artifacts: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    """document の equation_id → record 索引。

    ``artifacts`` を渡すと ``equation_records`` に素通しし、呼び出し側が既に取得済みの
    ``document_run_artifacts`` を再利用する（同一 document で ``document_analysis_runs``
    を2回 SELECT しない。省略時は従来どおり自前で取得する）。
    """
    return {
        str(r.get("equation_id")): r
        for r in equation_records(document_id, artifacts=artifacts)
        if isinstance(r, dict) and r.get("equation_id")
    }


def _compile_open_assumptions(course_id: str) -> list[dict[str, Any]]:
    """D層台帳の未検証合意リスト（``core.doubt.open_assumptions.compile_open_assumptions``）
    を学習者向け設定（``include_challenger_names=False``。既存の学習者向け
    ``GET /api/learning/courses/{course_id}/open-assumptions`` と同じ呼び出し方）で読む。
    台帳未記帳コースでは空リスト（fail-closed。エラーにしない）。
    """
    from core.postgres import get_session

    from core.doubt.open_assumptions import compile_open_assumptions

    session = get_session()
    try:
        return compile_open_assumptions(session, course_id, include_challenger_names=False)
    except Exception:  # noqa: BLE001
        logger.warning("discuss opening: open assumptions read failed for course %s", course_id, exc_info=True)
        return []
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 公開インターフェース
# ---------------------------------------------------------------------------


def build_opening(course_id: str, document_ids: Iterable[str]) -> dict[str, Any]:
    """discuss モード開幕画面の DTO を組み立てる（設計書 §3.3 / §6.3）。

    呼び出し側（``routes/learning.py``）が受講ゲート（``get_course_data``）を通した後、
    ``services.list_course_source_document_ids(course_data)`` の結果を ``document_ids``
    として渡す。document 単位の読み出し失敗は fail-soft に握り、その文書だけ
    thesis=None / backbone=[] に縮退させて画面全体は返す。
    """
    ids = sorted({str(d) for d in document_ids if str(d or "").strip()})
    titles = _document_titles(ids)

    documents: list[dict[str, Any]] = []
    backbone_by_document: dict[str, list[dict[str, Any]]] = {}

    for document_id in ids:
        try:
            artifacts = document_run_artifacts(document_id)
            artifacts = artifacts if isinstance(artifacts, dict) else {}
        except Exception:  # noqa: BLE001
            logger.warning("discuss opening: artifact read failed for %s", document_id, exc_info=True)
            artifacts = {}

        thesis_artifact = artifacts.get("thesis_reconstruction")

        try:
            equations_by_id = _equations_index_for(document_id, artifacts)
        except Exception:  # noqa: BLE001
            logger.warning("discuss opening: equation index failed for %s", document_id, exc_info=True)
            equations_by_id = {}

        claim_label_index = _claim_label_index(document_id, artifacts)
        thesis_dto = (
            project_thesis(thesis_artifact, claim_label_index, equations_by_id)
            if isinstance(thesis_artifact, dict)
            else None
        )

        graph_nodes = _load_graph_nodes(document_id)
        backbone_nodes, backbone_truncated = project_backbone(graph_nodes)
        backbone_by_document[document_id] = backbone_nodes

        documents.append(
            {
                "document_id": document_id,
                "title": titles.get(document_id, ""),
                "thesis": thesis_dto,
                "backbone": backbone_nodes,
                "truncated": backbone_truncated,
            }
        )

    assumption_items = _compile_open_assumptions(course_id)
    fragile_points, fragile_truncated = project_fragile_points(assumption_items, backbone_by_document)

    result = {
        "course_id": course_id,
        "available": _is_available(documents, fragile_points),
        "documents": documents,
        "fragile_points": fragile_points,
        "truncated": fragile_truncated,
    }
    return _strip_numeric_keys(result)
