"""グラフ対話レビュー — グラフ全体対話の grounding 構築 + 1ターン実行。

正本: docs/features/graph_dialogue_review_design.md §5。

- **要素単位の対話（dialogue.py）とは別の疑似要素型** ``document_graph`` を使う
  （migration 075 で ``deliberation_sessions.element_type`` CHECK にのみ追加。
  ``ElementRef`` / ``ELEMENT_TYPES`` には**加えない** — overview / context /
  annotations / identity の解決対象にしない）。
- grounding は最新の ``theory_component_graphs`` 行からの**非LLM・決定論**投影
  （main 層バックボーン + 関係 + 式の詳細層の規模 + レビュー状況 + validation +
  narrative）。グラフ未構築（ノード0）は :class:`GraphNotAvailableError`。
- 1ターン = 1 LLM コール（W6 継承）。失敗時は degraded 固定文 + 注釈なしで返す
  （``run_with_repair`` 不使用）。**候補注釈は生成しない**（GR: 全体対話は見取り図の
  検討に限定し、確定につながる操作は要素単位に降りてから行う）。
- コスト上限は W層の CostGate（``dialogue.check_and_count_llm_call``）に**相乗り**する
  （GR5。専用の env 上限を作らない）。U層 feature は ``deliberation:graph_chat`` で
  分離し、M層 scene は ``deliberation`` を共用する（``llm_policy.scene_for_feature``
  の ``deliberation:`` prefix マッチ）。
- confidence 等の生数値を LLM に述べさせない・レスポンスにも含めない（GR3/W8）。

本モジュールは FastAPI にも ``routes``/``services`` にも依存しない（開発ルール2）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text as sa_text

from core.llm import generate_conversation_turn
from core.llm_usage import usage_context
from core.postgres import get_session
from core.deliberation import dialogue

logger = logging.getLogger(__name__)

# migration 075 の CHECK 語彙（ELEMENT_TYPES には加えない — モジュール docstring 参照）。
ELEMENT_DOCUMENT_GRAPH = "document_graph"

_FEATURE_GRAPH_CHAT = "deliberation:graph_chat"

_DEGRADED_REPLY = (
    "AI 応答を生成できませんでした。グラフの各ノードの詳細と根拠は画面右のペインを参照してください。"
)

# grounding のプロンプト肥大化を避ける上限（コスト上限ではないため env にしない）。
_MAX_MAIN_NODE_LINES = 40
_MAX_EDGE_LINES = 60
_MAX_UNREVIEWED_LINES = 30
_MAX_VALIDATION_LINES = 10

# 対話の契約（ガードレールが原文 grep する固定文言を含む）:
# - 仮説文体・グラフに現れる関係のみ・承認判断の非代行（GR1）・数値 confidence 禁止。
_INSTRUCTION_HEADER = (
    "あなたは教員による理論構造レビューを補助する検討パートナーです。"
    "以下は1本の論文からパイプラインが構築した理論操作グラフの事実の一覧です。"
    "この一覧に現れているノード・関係・裏付け状態だけを根拠に答え、"
    "グラフに現れていない関係・根拠を作らないでください（無い場合は「グラフには現れていません」と述べる）。"
    "内容の正しさについては断定せず、「〜の可能性があります」のような仮説の文体で述べてください。"
    "承認・却下の判断は教員が行います。「承認すべき」「却下すべき」のような指示・推奨はせず、"
    "裏付けの状態と考えられる論点を事実として示すに留めてください。"
    "数値の確信度・スコアを述べないでください。"
)

_BACKING_LABELS: dict[str, str] = {
    "source_backed": "原文裏付けあり",
    "partially_source_backed": "部分的な裏付け",
    "inferred": "推定（裏付けなし）",
    "review_required": "要確認",
}

_REVIEW_LABELS: dict[str, str] = {
    "source_backed": "原文裏付けあり",
    "teacher_approved": "承認済み",
    "teacher_reviewed": "承認済み",
    "endorsed": "承認済み",
    "teacher_review_required": "未レビュー",
    "review_required": "要確認",
    "needs_revision": "要修正",
    "rejected": "却下",
}

# 「承認済み」とみなす review_status（未レビュー抽出の否定側）。
APPROVED_REVIEW_STATUSES = ("teacher_approved", "teacher_reviewed", "endorsed")


class GraphNotAvailableError(Exception):
    """対象 document にグラフ（ノード1件以上）が存在しない。呼び出し側が 422 にマッピングする。"""


# ---------------------------------------------------------------------------
# DB 読み出し（最新グラフ）
# ---------------------------------------------------------------------------


def load_latest_graph(document_id: str) -> dict[str, Any]:
    """最新の theory_component_graphs 行を返す（無ければ空 dict）。"""
    if not str(document_id or "").strip():
        return {}
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT graph_json, validation_results
                FROM theory_component_graphs
                WHERE document_id = :document_id
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ),
            {"document_id": document_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return {}
    graph = row[0] if isinstance(row[0], dict) else {}
    if isinstance(graph, dict) and "validation_results" not in graph:
        graph = dict(graph)
        graph["validation_results"] = row[1] if isinstance(row[1], list) else []
    return graph if isinstance(graph, dict) else {}


# ---------------------------------------------------------------------------
# grounding 構築（純粋関数 — fake graph dict でテスト可能）
# ---------------------------------------------------------------------------


def _node_id(node: dict[str, Any]) -> str:
    return str(node.get("component_id") or node.get("id") or "").strip()


def _node_label(node: dict[str, Any]) -> str:
    return str(
        node.get("display_label") or node.get("label") or node.get("name") or _node_id(node)
    ).strip()


def _node_layer(node: dict[str, Any]) -> str:
    return str(node.get("graph_layer") or "main").strip().lower()


def _backing_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    return _BACKING_LABELS.get(key, key or "不明")


def _review_label(value: Any) -> str:
    key = str(value or "").strip()
    return _REVIEW_LABELS.get(key, key or "未レビュー")


def build_graph_grounding(graph: dict[str, Any]) -> dict[str, Any]:
    """グラフ dict から grounding 素材を組み立てる（非LLM・決定論）。

    ノードが1件も無ければ :class:`GraphNotAvailableError`。
    """
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict) and _node_id(n)]
    if not nodes:
        raise GraphNotAvailableError("この教材にはまだ理論操作グラフが構築されていません。")
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]

    main_nodes = sorted(
        (n for n in nodes if _node_layer(n) == "main"),
        key=lambda n: (int(n.get("display_order") or 0), _node_label(n)),
    )
    detail_nodes = [n for n in nodes if _node_layer(n) == "equation_detail"]
    debug_nodes = [n for n in nodes if _node_layer(n) == "debug"]

    label_by_id = {_node_id(n): _node_label(n) for n in nodes}
    main_ids = {_node_id(n) for n in main_nodes}

    unreviewed = [
        n for n in nodes
        if _node_layer(n) != "debug"
        and str(n.get("review_status") or "") not in APPROVED_REVIEW_STATUSES
        and str(n.get("review_status") or "") not in ("rejected",)
    ]

    return {
        "main_nodes": main_nodes,
        "detail_node_count": len(detail_nodes),
        "debug_node_count": len(debug_nodes),
        "edges": edges,
        "main_ids": main_ids,
        "label_by_id": label_by_id,
        "unreviewed_nodes": unreviewed,
        "validation_results": [
            v for v in (graph.get("validation_results") or []) if isinstance(v, dict)
        ],
        "narrative": graph.get("narrative") if isinstance(graph.get("narrative"), dict) else {},
    }


def graph_grounding_to_text(grounding: dict[str, Any]) -> str:
    """grounding dict を LLM prompt 用のテキストへ整形する（純粋関数・テスト容易）。"""
    lines: list[str] = []
    label_by_id: dict[str, str] = grounding.get("label_by_id") or {}
    main_ids: set = grounding.get("main_ids") or set()

    main_nodes = grounding.get("main_nodes") or []
    lines.append("[主グラフ（理論構成のバックボーン）]")
    for node in main_nodes[:_MAX_MAIN_NODE_LINES]:
        line = f"- {_node_label(node)}"
        description = str(node.get("description") or "").strip()
        if description:
            line += f"：{description}"
        line += (
            f"（裏付け: {_backing_label(node.get('source_backing_status'))}"
            f" / レビュー: {_review_label(node.get('review_status'))}）"
        )
        lines.append(line)
    if len(main_nodes) > _MAX_MAIN_NODE_LINES:
        lines.append(f"(注記) 主グラフの残り {len(main_nodes) - _MAX_MAIN_NODE_LINES} ノードは省略。")

    edges = grounding.get("edges") or []
    main_edges = [
        e for e in edges
        if str(e.get("source_component_id") or e.get("from") or "") in main_ids
        and str(e.get("target_component_id") or e.get("to") or "") in main_ids
    ]
    if main_edges:
        lines.append("[主グラフの関係]")
        for edge in main_edges[:_MAX_EDGE_LINES]:
            src = str(edge.get("source_component_id") or edge.get("from") or "")
            dst = str(edge.get("target_component_id") or edge.get("to") or "")
            relation = str(edge.get("edge_type") or edge.get("relation") or "").strip()
            lines.append(
                f"- {label_by_id.get(src, src)} →({relation}) {label_by_id.get(dst, dst)}"
                f"（裏付け: {_backing_label(edge.get('source_backing_status'))}）"
            )
        if len(main_edges) > _MAX_EDGE_LINES:
            lines.append(f"(注記) 関係の残り {len(main_edges) - _MAX_EDGE_LINES} 本は省略。")

    detail_count = int(grounding.get("detail_node_count") or 0)
    debug_count = int(grounding.get("debug_node_count") or 0)
    if detail_count or debug_count:
        lines.append(
            f"[式の詳細層] 式単位のステップが {detail_count} ノード"
            + (f"、debug 層（推定のみ）が {debug_count} ノード" if debug_count else "")
            + "あります（この一覧には展開していません）。"
        )

    unreviewed = grounding.get("unreviewed_nodes") or []
    if unreviewed:
        lines.append("[未レビューのノード]")
        for node in unreviewed[:_MAX_UNREVIEWED_LINES]:
            reasons = [str(r) for r in (node.get("review_reasons") or []) if str(r or "").strip()]
            reason_text = f"（理由: {'、'.join(reasons)}）" if reasons else ""
            lines.append(f"- {_node_label(node)}: {_review_label(node.get('review_status'))}{reason_text}")
        if len(unreviewed) > _MAX_UNREVIEWED_LINES:
            lines.append(f"(注記) 未レビューの残り {len(unreviewed) - _MAX_UNREVIEWED_LINES} ノードは省略。")

    validations = grounding.get("validation_results") or []
    if validations:
        lines.append("[構築時の検証記録]")
        for item in validations[:_MAX_VALIDATION_LINES]:
            severity = str(item.get("severity") or item.get("level") or "").strip()
            message = str(item.get("message") or item.get("detail") or "").strip()
            if not message:
                continue
            lines.append(f"- ({severity or 'info'}) {message}")

    narrative = grounding.get("narrative") or {}
    summary = str(narrative.get("summary") or "").strip()
    if summary:
        lines.append(f"[グラフの読み方（AI提案・未確認）] {summary}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# セッション（deliberation_sessions への相乗り。ElementRef は使わない）
# ---------------------------------------------------------------------------

_SESSION_COLUMNS_SQL = """
    id::text, scope, element_type, element_id, document_id, domain_key,
    title, messages, created_by::text, created_at, updated_at
"""


def _session_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "scope": row[1] or "",
        "element_type": row[2] or "",
        "element_id": row[3] or "",
        "document_id": row[4],
        "domain_key": row[5],
        "title": row[6] or "",
        "messages": row[7] if isinstance(row[7], list) else [],
        "created_by": row[8],
        "created_at": row[9].isoformat() if row[9] else "",
        "updated_at": row[10].isoformat() if row[10] else "",
    }


def find_latest_graph_session(document_id: str, created_by: str | None) -> dict[str, Any] | None:
    """本人 × document の最新グラフ対話セッションを返す（get-or-create の get 側）。"""
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                SELECT {_SESSION_COLUMNS_SQL}
                FROM deliberation_sessions
                WHERE element_type = :element_type
                  AND element_id = :element_id
                  AND created_by = CAST(:created_by AS uuid)
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ),
            {
                "element_type": ELEMENT_DOCUMENT_GRAPH,
                "element_id": document_id,
                "created_by": created_by,
            },
        ).fetchone()
    finally:
        session.close()
    return _session_row_to_dict(row) if row else None


def create_graph_session(document_id: str, *, title: str = "", created_by: str | None = None) -> dict[str, Any]:
    """グラフ全体対話のセッションを1件作成する（scope は document 固定）。"""
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                INSERT INTO deliberation_sessions
                    (scope, element_type, element_id, document_id, domain_key, title, created_by)
                VALUES
                    ('document', :element_type, :element_id, :document_id, NULL,
                     :title, CAST(:created_by AS uuid))
                RETURNING {_SESSION_COLUMNS_SQL}
                """
            ),
            {
                "element_type": ELEMENT_DOCUMENT_GRAPH,
                "element_id": document_id,
                "document_id": document_id,
                "title": title or "",
                "created_by": created_by,
            },
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return _session_row_to_dict(row)


# ---------------------------------------------------------------------------
# 1ターン実行（W6: 1応答=1 LLM コール・注釈なし）
# ---------------------------------------------------------------------------


class _GraphTurnOutput(BaseModel):
    reply: str = ""


@dataclass
class GraphTurnResult:
    reply: str
    degraded: bool = False


def build_llm_messages(
    prior_messages: list[dict[str, str]],
    user_content: str,
    grounding_text: str,
) -> list[dict[str, str]]:
    """会話履歴 + 新規発話から LLM 送信用メッセージ列を組み立てる。

    grounding_text は**最初の user メッセージにのみ**注入する（dialogue.py と同じ規約）。
    """
    turns = list(prior_messages) + [{"role": "user", "content": user_content}]
    messages: list[dict[str, str]] = []
    first_user_injected = False
    for turn in turns:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if not first_user_injected and role == "user":
            first_user_injected = True
            if grounding_text:
                content = _INSTRUCTION_HEADER + "\n\n" + grounding_text + "\n\n---\n\n" + content
        messages.append({"role": role, "content": content})
    return messages


def run_graph_turn(
    document_id: str,
    *,
    prior_messages: list[dict[str, str]],
    user_content: str,
    grounding_text: str,
    model: str | None = None,
    user_id: str | None = None,
) -> GraphTurnResult:
    """グラフ全体対話の1ターンを実行する。

    候補注釈は生成しない（structured output は reply のみ）。LLM 失敗は degraded
    固定文で返す（同期パスを重くしない・W6）。
    """
    llm_messages = build_llm_messages(prior_messages, user_content, grounding_text)
    with usage_context(_FEATURE_GRAPH_CHAT, user_id=user_id, document_id=document_id):
        resolved_model = model or dialogue.resolve_turn_model(_FEATURE_GRAPH_CHAT)
        try:
            parsed = generate_conversation_turn(llm_messages, _GraphTurnOutput, model=resolved_model)
        except Exception:  # noqa: BLE001
            logger.warning(
                "graph dialogue: LLM turn failed for document %s", document_id, exc_info=True,
            )
            return GraphTurnResult(reply=_DEGRADED_REPLY, degraded=True)
    reply = (parsed.reply or "").strip() or _DEGRADED_REPLY
    return GraphTurnResult(reply=reply, degraded=False)


__all__ = [
    "APPROVED_REVIEW_STATUSES",
    "ELEMENT_DOCUMENT_GRAPH",
    "GraphNotAvailableError",
    "GraphTurnResult",
    "build_graph_grounding",
    "build_llm_messages",
    "create_graph_session",
    "find_latest_graph_session",
    "graph_grounding_to_text",
    "load_latest_graph",
    "run_graph_turn",
]
