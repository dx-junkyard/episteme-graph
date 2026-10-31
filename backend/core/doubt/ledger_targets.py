"""台帳行（``epistemic_ledger``）の作成に要る ``document_id`` の解決（D層・読み時・非LLM）。

migration 080 で ``epistemic_ledger.document_id`` は ``TEXT NOT NULL DEFAULT ''`` から
``UUID NOT NULL``（既定なし）になった。それ以前の「(target_id, target_type) だけで INSERT」は
NotNullViolation で落ちる（2026-10-03 の実機検証で判明・IK 記帳）。台帳行を新しく作る経路は
すべて ``ensure_ledger_row`` を通し、``document_id`` が渡されなければ対象から決定論で引く。

FastAPI / LLM 非 import。
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text as sa_text

# 対象種別 → 所属 document を引く SQL（live ビューのみ。KO5）
_LOOKUPS: dict[str, str] = {
    "claim": "SELECT document_id::text FROM theory_claims_live WHERE id::text = :tid LIMIT 1",
    "component": "SELECT document_id::text FROM theory_components_live WHERE id::text = :tid LIMIT 1",
    "equation": (
        "SELECT document_id::text FROM knowledge_equations_live "
        "WHERE id::text = :tid OR agent_equation_id = :tid ORDER BY created_at DESC LIMIT 1"
    ),
    # assumption_nodes は document_ids JSONB（複数論文にまたがる前提）。先頭を所属とみなす。
    "assumption": (
        "SELECT (document_ids ->> 0) FROM assumption_nodes WHERE id::text = :tid LIMIT 1"
    ),
}

LEDGER_TARGET_TYPES: tuple[str, ...] = tuple(_LOOKUPS)

# ---------------------------------------------------------------------------
# IK-0899: 台帳の行キーは (document_id, target_type, target_id)（migration 092）。
#
# 理論操作グラフのノード id（theory_op_* / eq_op_*）と式の agent ID（TeX ラベル由来）は
# **論文の中でしか一意でない**。これらの対象は document_id を必ず添えて引く・書く
# （分からなければ閉じる）。claim / assumption と UUID 形の id は全体で一意なので、
# document_id は権限ゲートと出所に使うだけで行の突き合わせには使わない（意味は不変）。
# 台帳行を引く WHERE 句の正本は ``ledger_row_filter``（ここ以外で組み立てない）。
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

#: 論文の中でしか一意でない id を持ちうる対象種別。
DOCUMENT_SCOPED_TARGET_TYPES: tuple[str, ...] = ("component", "equation")

#: document_id が無いと行を決められない対象を引こうとしたときの事実文（教員・API 向け）。
LEDGER_DOCUMENT_REQUIRED_NOTE = (
    "この対象の台帳は論文ごとに分かれています。どの論文の対象か（document_id）を指定してください。"
)

#: migration 092 より前に、論文をまたいで 1 行を共有していた記帳に添える事実文（教員向け）。
LEDGER_ATTRIBUTION_CHECK_NOTE = (
    "この記帳は、台帳が論文ごとに分かれる前に残されたものです。"
    "同じ番号の対象を持つ別の論文で記帳された可能性があるので、どの論文の記帳か確かめてください。"
)


class LedgerTargetRef(tuple):
    """worker が扱う台帳対象 ``(target_type, target_id)``（2 要素の tuple のまま）+ 行の論文。

    既存の ``for target_type, target_id in targets`` を崩さずに、行キーの論文（IK-0899）を
    ``.document_id`` で運ぶ。
    """

    document_id: str = ""

    def __new__(cls, target_type: str, target_id: str, document_id: str = ""):
        obj = super().__new__(cls, (str(target_type), str(target_id)))
        obj.document_id = str(document_id or "")
        return obj


def ledger_target_refs(rows) -> list["LedgerTargetRef"]:
    """``RETURNING target_type, target_id, document_id::text`` の行 → ``LedgerTargetRef`` の列。"""
    out: list[LedgerTargetRef] = []
    for r in rows or []:
        doc = ""
        try:
            doc = str(r[2] or "") if len(r) > 2 else ""
        except Exception:  # noqa: BLE001
            doc = ""
        out.append(LedgerTargetRef(r[0], r[1], doc))
    return out


def ledger_row_filter_or_none(
    *, target_type: str, target_id: str, document_id: str | None
) -> tuple[str, dict] | None:
    """``ledger_row_filter`` の閉じる版（document が要るのに無ければ None — worker 用）。"""
    try:
        return ledger_row_filter(target_type=target_type, target_id=target_id, document_id=document_id)
    except LedgerDocumentRequired:
        return None


class LedgerDocumentRequired(ValueError):
    """論文の中でしか一意でない対象を document_id なしで引こうとした（閉じる）。"""


def is_uuid_text(value: Any) -> bool:
    return bool(_UUID_RE.match(str(value or "").strip()))


def target_requires_document(target_type: str, target_id: str) -> bool:
    """行を決めるのに document_id が要る対象か（IK-0899）。"""
    return str(target_type or "") in DOCUMENT_SCOPED_TARGET_TYPES and not is_uuid_text(target_id)


def normalize_document_id(value: Any) -> str:
    """document_id の正規化（UUID 形のみ受ける。それ以外は空 = 指定なし）。"""
    text = str(value or "").strip()
    return text.lower() if is_uuid_text(text) else ""


def ledger_row_filter(
    *, target_type: str, target_id: str, document_id: str | None
) -> tuple[str, dict]:
    """台帳行を 1 行に決める WHERE 句とパラメタ（行キーの正本・IK-0899）。

    Raises:
        LedgerDocumentRequired: 論文の中でしか一意でない対象で document_id が無い。
    """
    tid = str(target_id or "").strip()
    tt = str(target_type or "").strip()
    params: dict = {"tid": tid, "ttype": tt}
    if target_requires_document(tt, tid):
        doc = normalize_document_id(document_id)
        if not doc:
            raise LedgerDocumentRequired(LEDGER_DOCUMENT_REQUIRED_NOTE)
        params["doc"] = doc
        return (
            "target_id = :tid AND target_type = :ttype AND document_id = CAST(:doc AS uuid)",
            params,
        )
    return ("target_id = :tid AND target_type = :ttype", params)


def resolve_ledger_document_id(
    session: Any, *, target_type: str, target_id: str, document_id: str | None
) -> str | None:
    """経路が扱う対象の document_id（権限ゲート・行キーに使う）。

    - 論文の中でしか一意でない対象: 指定された document_id だけ（無ければ
      ``LedgerDocumentRequired``）。推測で埋めない。
    - 全体で一意の対象: 既存の台帳行の document → 対象そのものの document → 指定値 の順。
      指定値が対象の document と食い違っても、行の突き合わせは対象の id だけで行う。
    """
    tid = str(target_id or "").strip()
    tt = str(target_type or "").strip()
    if target_requires_document(tt, tid):
        doc = normalize_document_id(document_id)
        if not doc:
            raise LedgerDocumentRequired(LEDGER_DOCUMENT_REQUIRED_NOTE)
        return doc
    try:
        row = session.execute(
            sa_text(
                "SELECT document_id::text FROM epistemic_ledger "
                "WHERE target_id = :tid AND target_type = :ttype LIMIT 1"
            ),
            {"tid": tid, "ttype": tt},
        ).fetchone()
    except Exception:  # noqa: BLE001
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        row = None
    if row and row[0]:
        return str(row[0])
    return resolve_target_document_id(session, tt, tid) or (normalize_document_id(document_id) or None)


def resolve_target_document_id(session: Any, target_type: str, target_id: str) -> str | None:
    """対象の所属 document_id（UUID 文字列）。引けなければ None（捏造しない）。"""
    sql = _LOOKUPS.get(str(target_type or ""))
    tid = str(target_id or "").strip()
    if not sql or not tid:
        return None
    try:
        row = session.execute(sa_text(sql), {"tid": tid}).fetchone()
    except Exception:  # noqa: BLE001 — 失敗は None（呼び出し側が事実文にする）。session は戻す
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
    value = row[0] if row else None
    return str(value) if value else None


def ensure_ledger_row(
    session: Any,
    *,
    target_type: str,
    target_id: str,
    document_id: str | None = None,
    course_id: str | None = None,
) -> str:
    """台帳行を（無ければ）作る。戻り値は使った document_id。

    ``document_id`` が無ければ対象から引く。それでも無ければ ``ValueError``（日本語の事実文）。
    既存行があれば INSERT は何もしない（``ON CONFLICT DO NOTHING``）。
    """
    tid = str(target_id or "").strip()
    tt = str(target_type or "").strip()
    if target_requires_document(tt, tid):
        # IK-0899: 論文の中でしか一意でない対象は、指定された論文の行だけを作る（推測しない）。
        doc = normalize_document_id(document_id)
        if not doc:
            raise LedgerDocumentRequired(LEDGER_DOCUMENT_REQUIRED_NOTE)
    else:
        doc = resolve_ledger_document_id(
            session, target_type=tt, target_id=tid, document_id=document_id
        ) or ""
    if not doc:
        raise ValueError("この対象がどの論文に属するか引けないため、台帳の行を作れません。")
    session.execute(
        sa_text(
            """
            INSERT INTO epistemic_ledger (target_id, target_type, document_id, course_id)
            VALUES (:tid, :ttype, CAST(:doc AS uuid), :course)
            ON CONFLICT ON CONSTRAINT epistemic_ledger_document_target_key DO NOTHING
            """
        ),
        {"tid": tid, "ttype": tt, "doc": doc, "course": str(course_id or "")},
    )
    return doc


# ---------------------------------------------------------------------------
# IK-0855: 「この部品の台帳対象」の解決（component の名前空間が 2 つあることの吸収）
#
# 台帳の target_type='component' 行は ``ledger_builder._collect_targets`` が理論操作グラフの
# **ノード id**（theory_op_* / eq_op_*）で作る。一方、学習画面の部品チップ・画面文脈は
# ``theory_components`` の **UUID** を持つ。UUID の行は誰も作らないので、UUID で引くと
# 台帳の一行が常に出なかった。ここを唯一の解決点にし、学習者向けの台帳投影
# （``routes/doubt.py::learner_ledger_line``）が通す。
#
# 規則（決定論・推測で結ばない）:
#   1. 台帳に (component, id) の行があればその id（ノード id でも UUID でも）。
#   2. id が theory_components_live の UUID なら、その部品の主張（linked_claim_ids ∪
#      evidence_claims）・式（linked_equation_ids）と、同じ document のグラフノードの
#      linked_claim_ids / linked_equation_ids の**交差**でノードを選ぶ。main 層を優先し、
#      交差の大きい順 → ノード id 昇順。交差ゼロなら解決しない（None）。
#   3. 選んだノードに台帳行が無ければ解決しない。
# ---------------------------------------------------------------------------


#: UUID の部品を理論操作グラフの段へ写して台帳を引いたときに添える事実文（学習者向け）。
LEDGER_COMPONENT_VIA_NODE_NOTE = "この一行は、この部品にあたる理論操作グラフの段「{label}」の台帳の記録です。"
LEDGER_COMPONENT_VIA_NODE_NOTE_UNNAMED = "この一行は、この部品にあたる理論操作グラフの段の台帳の記録です。"


def _jsonb_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:  # noqa: BLE001
            return []
    return []


def _jsonb_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _ref_text(value: Any) -> str:
    """主張参照の綴りを揃える（正規化の正本は core/knowledge_objects/references.py）。"""
    from core.knowledge_objects.references import normalize_claim_ref

    if isinstance(value, dict):
        value = value.get("claim_id") or value.get("id") or ""
    return normalize_claim_ref(value)


def _ledger_component_row_exists(session: Any, target_id: str, *, document_id: str | None) -> bool:
    try:
        where, params = ledger_row_filter(
            target_type="component", target_id=target_id, document_id=document_id
        )
    except LedgerDocumentRequired:
        return False
    try:
        row = session.execute(
            sa_text(f"SELECT 1 FROM epistemic_ledger WHERE {where} LIMIT 1"),
            params,
        ).fetchone()
    except Exception:  # noqa: BLE001
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False
    return row is not None


def pick_graph_node_for_component(
    nodes: list[dict], claim_ids: set[str], equation_ids: set[str],
) -> dict | None:
    """部品の主張・式とノードの交差で、台帳を引くノードを 1 つ選ぶ（純関数）。"""
    best: tuple | None = None
    best_node: dict | None = None
    for node in nodes:
        if not isinstance(node, dict):
            continue
        layer = str(node.get("graph_layer") or "main")
        if layer == "debug":
            continue
        nid = str(node.get("component_id") or node.get("id") or "").strip()
        if not nid:
            continue
        node_claims = {_ref_text(c) for c in _jsonb_list(node.get("linked_claim_ids"))}
        node_equations = {
            str(e or "").strip()
            for key in ("linked_equation_ids", "input_equation_ids", "output_equation_ids")
            for e in _jsonb_list(node.get(key))
        }
        overlap = len((node_claims - {""}) & claim_ids) + len((node_equations - {""}) & equation_ids)
        if overlap <= 0:
            continue
        key = (0 if layer == "main" else 1, -overlap, nid)
        if best is None or key < best:
            best = key
            best_node = node
    return best_node


def resolve_component_ledger_target(
    session: Any, component_id: str, *, document_id: str | None
) -> dict | None:
    """部品 id（グラフのノード id か theory_components の UUID）→ 台帳を引く対象。

    ``document_id`` は開いている論文（IK-0899: ノード id は論文の中でしか一意でない）。
    ノード id で来たときは必須で、無ければ解決しない（None）。UUID で来たときは部品の
    document を使う（指定値と食い違えば解決しない）。

    Returns:
        ``{"target_id", "document_id", "via_graph_node", "node_label"}`` か None（解決できない）。
        ``via_graph_node`` は UUID からグラフのノードへ写したときだけ True。
    """
    cid = str(component_id or "").strip()
    if not cid:
        return None
    wanted_doc = normalize_document_id(document_id)
    if not is_uuid_text(cid):
        if wanted_doc and _ledger_component_row_exists(session, cid, document_id=wanted_doc):
            return {"target_id": cid, "document_id": wanted_doc, "via_graph_node": False, "node_label": ""}
        return None
    if _ledger_component_row_exists(session, cid, document_id=None):
        row_doc = resolve_ledger_document_id(
            session, target_type="component", target_id=cid, document_id=wanted_doc
        ) or ""
        return {"target_id": cid, "document_id": row_doc, "via_graph_node": False, "node_label": ""}
    try:
        row = session.execute(
            sa_text(
                "SELECT document_id::text, linked_claim_ids, evidence_claims, linked_equation_ids "
                "FROM theory_components_live WHERE id::text = :cid LIMIT 1"
            ),
            {"cid": cid},
        ).fetchone()
    except Exception:  # noqa: BLE001
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
    if not row or not row[0]:
        return None
    # 部品の UUID は全体で一意なので、部品自身の論文が正本（開いている論文の指定とは突き合わせない）。
    document_id = str(row[0])
    claim_ids = {_ref_text(c) for c in _jsonb_list(row[1]) + _jsonb_list(row[2])} - {""}
    equation_ids = {str(e or "").strip() for e in _jsonb_list(row[3])} - {""}
    if not claim_ids and not equation_ids:
        return None
    try:
        graph_rows = session.execute(
            sa_text(
                "SELECT graph_json FROM theory_component_graphs "
                "WHERE document_id::text = :doc"
            ),
            {"doc": document_id},
        ).fetchall()
    except Exception:  # noqa: BLE001
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
    nodes: list[dict] = []
    for graph_row in graph_rows:
        payload = _jsonb_dict(graph_row[0])
        nodes.extend(n for n in _jsonb_list(payload.get("nodes")) if isinstance(n, dict))
    node = pick_graph_node_for_component(nodes, claim_ids, equation_ids)
    if node is None:
        return None
    nid = str(node.get("component_id") or node.get("id") or "").strip()
    if not _ledger_component_row_exists(session, nid, document_id=document_id):
        return None
    label = str(node.get("display_label") or node.get("visual_label") or node.get("label") or "").strip()
    return {"target_id": nid, "document_id": document_id, "via_graph_node": True, "node_label": label}


# ---------------------------------------------------------------------------
# IK-0904: 疑義の入口へ辿る道。疑義の受け皿は前提と主張だけで、前提の地図・ノード詳細が
# 載せる対象は式と部品なので、式・部品の台帳から「この対象を支える主張」を引いて主張の台帳へ
# 渡す（決定論・推測で結ばない — グラフのノードが持つ主張参照のうち live の主張だけ）。
# ---------------------------------------------------------------------------

_MAX_SUPPORTING_CLAIMS = 6


def supporting_claims_for_target(
    session: Any, *, document_id: str | None, target_type: str, target_id: str,
) -> list[dict]:
    """式・部品の対象を支える主張（``[{"claim_id", "label"}]``・live の主張だけ・最大 6）。

    部品（ノード id / UUID）はそのノードの ``linked_claim_ids``、式はその式を参照するノードの
    ``linked_claim_ids``。主張の id は DB の UUID だけを採る（主張の台帳はその id で開く）。
    取得失敗・該当なしは空（呼び出し側が事実文にする）。
    """
    tt = str(target_type or "").strip()
    tid = str(target_id or "").strip()
    doc = normalize_document_id(document_id)
    if tt not in ("component", "equation") or not tid or not doc:
        return []
    try:
        graph_rows = session.execute(
            sa_text("SELECT graph_json FROM theory_component_graphs WHERE document_id = CAST(:doc AS uuid)"),
            {"doc": doc},
        ).fetchall()
    except Exception:  # noqa: BLE001
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return []
    # IK-0930: グラフの主張参照は生成時の局所 ID（synth_claim_* / claim_span_*）か UUID。
    # 解決の正本（core/knowledge_objects/claim_refs.py）で UUID へ運ぶ（UUID 形だけを採らない）。
    from core.knowledge_objects.claim_refs import ClaimRefResolver

    resolver = ClaimRefResolver(session, doc)
    claim_ids: list[str] = []
    for graph_row in graph_rows:
        payload = _jsonb_dict(graph_row[0])
        for node in _jsonb_list(payload.get("nodes")):
            if not isinstance(node, dict):
                continue
            nid = str(node.get("component_id") or node.get("id") or "").strip()
            if tt == "component":
                hit = nid == tid
            else:
                hit = tid in {
                    str(e or "").strip()
                    for key in ("linked_equation_ids", "input_equation_ids", "output_equation_ids")
                    for e in _jsonb_list(node.get(key))
                }
            if not hit:
                continue
            for key in ("linked_claim_ids", "input_claim_ids", "output_claim_ids", "required_claim_ids"):
                for ref in _jsonb_list(node.get(key)):
                    cid = resolver.uuid_for(_ref_text(ref))
                    if cid and cid not in claim_ids:
                        claim_ids.append(cid)
    if not claim_ids:
        return []
    from core.doubt.target_labels import UNNAMED_TARGET_LABEL

    out: list[dict] = []
    for cid in claim_ids:
        out.append({
            "claim_id": cid,
            "label": resolver.label_for(cid) or UNNAMED_TARGET_LABEL,
        })
        if len(out) >= _MAX_SUPPORTING_CLAIMS:
            break
    return out


__all__ = [
    "supporting_claims_for_target",
    "LedgerTargetRef",
    "ledger_target_refs",
    "ledger_row_filter_or_none",
    "DOCUMENT_SCOPED_TARGET_TYPES",
    "LEDGER_ATTRIBUTION_CHECK_NOTE",
    "LEDGER_DOCUMENT_REQUIRED_NOTE",
    "LedgerDocumentRequired",
    "is_uuid_text",
    "ledger_row_filter",
    "normalize_document_id",
    "resolve_ledger_document_id",
    "target_requires_document",
    "LEDGER_COMPONENT_VIA_NODE_NOTE",
    "LEDGER_COMPONENT_VIA_NODE_NOTE_UNNAMED",
    "LEDGER_TARGET_TYPES",
    "ensure_ledger_row",
    "pick_graph_node_for_component",
    "resolve_component_ledger_target",
    "resolve_target_document_id",
]
