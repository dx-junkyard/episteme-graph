"""``element_explanations`` の DB プリミティブ（Phase 2, migration 056）。

正本: ``docs/features/hierarchical_context_explanation_design.md`` §2（E1〜E8）・§5.2。

全要素型（figure / theory_component / theory_claim / equation）を受けるポリモーフィックな
説明台帳。C層 ``component_explanations`` とは別物（component 専用・course 文脈前提のため
流用しない）。W層 ``element_annotations`` / ``core.atlas_store`` と同じ流儀:
セッションは呼び出し側が管理する（``core/postgres.get_session()`` + try/finally）。
本モジュールはコミット/ロールバック/クローズを行わない — 呼び出し側（API 層）が
1トランザクションとして束ねる。

不変条項:
- E1 二層分離: generic/contextual を ``kind`` で区別する（テーブルは共通）。
- E2 candidate-only: 生成は常に ``status='candidate'``（:func:`insert_candidates`）。
  ``approved`` への遷移は :func:`approve` を通した人間の確定のみ。
- E6: confidence の生値は ``evidence`` JSONB に残すが（DB 界面）、API 層が段階ラベルへ
  変換すること（``core.deliberation.identity_links.confidence_label`` を再利用）。
- P4 情報を落とさない: 行削除 API は無い。再解析（:func:`insert_candidates`）は既存
  candidate を ``superseded`` に遷移させるのみで ``approved``/``dismissed`` には触れない
  （migration 053 と同じ原則）。編集（:func:`update_body`）も旧行を ``superseded`` にした
  うえで新行を INSERT する（履歴保持）。

本モジュールは FastAPI にも ``routes``/``services`` にも依存しない（開発ルール2）。
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import text as sa_text

ELEMENT_TYPE_FIGURE = "figure"
ELEMENT_TYPE_COMPONENT = "theory_component"
ELEMENT_TYPE_CLAIM = "theory_claim"
ELEMENT_TYPE_EQUATION = "equation"
ELEMENT_TYPES = (
    ELEMENT_TYPE_FIGURE,
    ELEMENT_TYPE_COMPONENT,
    ELEMENT_TYPE_CLAIM,
    ELEMENT_TYPE_EQUATION,
)

KIND_GENERIC = "generic"
KIND_CONTEXTUAL = "contextual"
KINDS = (KIND_GENERIC, KIND_CONTEXTUAL)

STATUS_CANDIDATE = "candidate"
STATUS_APPROVED = "approved"
STATUS_DISMISSED = "dismissed"
STATUS_SUPERSEDED = "superseded"
STATUSES = (STATUS_CANDIDATE, STATUS_APPROVED, STATUS_DISMISSED, STATUS_SUPERSEDED)

# 編集（update_body）で本文の書き換えを許す元 status（P4: 既に終端状態の行は編集対象にしない。
# dismissed/superseded は履歴として保持するのみで、書き換えるなら新規 candidate を起こす）。
_EDITABLE_STATUSES = (STATUS_CANDIDATE, STATUS_APPROVED)

PIPELINE_CREATED_BY = "pipeline"


class ElementExplanationError(Exception):
    """呼び出し側（API 層）が HTTP ステータスへマッピングするための軽量エラー。

    ``kind``: ``'invalid'``（入力不備）/ ``'conflict'``（candidate 以外からの承認・却下、
    または編集不可な status からの編集、並行更新の競合）。いずれも呼び出し側は 422 相当に
    マッピングしてよい（対象が存在しない場合は本例外ではなく ``None`` を返す — 404 は
    呼び出し側が別途判定する）。
    """

    def __init__(self, message: str, *, kind: str = "invalid") -> None:
        super().__init__(message)
        self.kind = kind


def _json(value: Any, default: Any) -> Any:
    return value if isinstance(value, type(default)) else default


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


_COLUMNS_SQL = """
    id::text, document_id::text, element_type, element_id, kind, body, evidence,
    status, created_by, reviewed_by, reviewed_at, created_at
"""


def _row_to_dict(row: Any) -> dict:
    return {
        "id": str(row[0]),
        "document_id": row[1],
        "element_type": row[2] or "",
        "element_id": row[3] or "",
        "kind": row[4] or "",
        "body": row[5] or "",
        "evidence": _json(row[6], {}),
        "status": row[7] or "",
        "created_by": row[8],
        "reviewed_by": row[9],
        "reviewed_at": row[10].isoformat() if row[10] else None,
        "created_at": row[11].isoformat() if row[11] else "",
    }


def _insert_row(
    session: Any,
    *,
    document_id: str,
    element_type: str,
    element_id: str,
    kind: str,
    body: str,
    evidence: dict,
    status: str,
    created_by: str,
    reviewed_by: str | None = None,
    reviewed_at: str | None = None,
) -> dict:
    row = session.execute(
        sa_text(
            f"""
            INSERT INTO element_explanations (
                document_id, element_type, element_id, kind, body, evidence,
                status, created_by, reviewed_by, reviewed_at
            ) VALUES (
                CAST(:document_id AS uuid), :element_type, :element_id, :kind, :body,
                CAST(:evidence AS jsonb), :status, :created_by,
                :reviewed_by, CAST(:reviewed_at AS timestamptz)
            )
            RETURNING {_COLUMNS_SQL}
            """
        ),
        {
            "document_id": document_id,
            "element_type": element_type,
            "element_id": element_id,
            "kind": kind,
            "body": body,
            "evidence": _dump(evidence or {}),
            "status": status,
            "created_by": created_by,
            "reviewed_by": reviewed_by,
            "reviewed_at": reviewed_at,
        },
    ).fetchone()
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# パイプライン用: 候補の一括投入（再解析セーフ）
# ---------------------------------------------------------------------------


def _validate_candidate_item(raw: dict) -> dict:
    element_type = str((raw or {}).get("element_type") or "").strip()
    if element_type not in ELEMENT_TYPES:
        raise ValueError(f"invalid element_type: {element_type!r}")
    element_id = str((raw or {}).get("element_id") or "").strip()
    if not element_id:
        raise ValueError("element_id is required")
    kind = str((raw or {}).get("kind") or "").strip()
    if kind not in KINDS:
        raise ValueError(f"invalid kind: {kind!r}")
    body = str((raw or {}).get("body") or "").strip()
    if not body:
        raise ValueError("body is required")
    evidence_raw = (raw or {}).get("evidence")
    evidence = evidence_raw if isinstance(evidence_raw, dict) else {}
    created_by = str((raw or {}).get("created_by") or "").strip() or PIPELINE_CREATED_BY
    return {
        "element_type": element_type,
        "element_id": element_id,
        "kind": kind,
        "body": body,
        "evidence": evidence,
        "created_by": created_by,
    }


def insert_candidates(session: Any, document_id: str, items: Iterable[dict]) -> list[dict]:
    """パイプライン（ContextualExplanationAgent 等）用の一括 candidate 投入。

    各 item は ``{element_type, element_id, kind, body, evidence?, created_by?}``。
    同一 ``(document_id, element_type, element_id, kind)`` の既存 ``candidate`` 行は
    ``superseded`` へ遷移させてから新しい candidate 行を INSERT する
    （``approved``/``dismissed``/既に ``superseded`` の行はそのまま — E2・P4。
    AI 再解析が教員確定を消さない。migration 053 と同じ原則）。

    ``items`` の要素が不正（語彙外の element_type/kind・空の element_id/body）な場合は
    ``ValueError`` を送出する（呼び出し元 agent の validator が事前に弾いている想定の
    最終ガード。ここで弾かれた要素があると以降の要素も含め全体が例外で止まるため、
    呼び出し側で事前検証しておくこと）。
    """
    created: list[dict] = []
    for raw in items or []:
        item = _validate_candidate_item(raw)
        session.execute(
            sa_text(
                """
                UPDATE element_explanations
                SET status = :superseded
                WHERE document_id = CAST(:document_id AS uuid)
                  AND element_type = :element_type
                  AND element_id = :element_id
                  AND kind = :kind
                  AND status = :candidate
                """
            ),
            {
                "document_id": document_id,
                "element_type": item["element_type"],
                "element_id": item["element_id"],
                "kind": item["kind"],
                "superseded": STATUS_SUPERSEDED,
                "candidate": STATUS_CANDIDATE,
            },
        )
        created.append(
            _insert_row(
                session,
                document_id=document_id,
                element_type=item["element_type"],
                element_id=item["element_id"],
                kind=item["kind"],
                body=item["body"],
                evidence=item["evidence"],
                status=STATUS_CANDIDATE,
                created_by=item["created_by"],
            )
        )
    return created


# ---------------------------------------------------------------------------
# 読み出し
# ---------------------------------------------------------------------------


def get_by_id(session: Any, explanation_id: str) -> dict | None:
    row = session.execute(
        sa_text(f"SELECT {_COLUMNS_SQL} FROM element_explanations WHERE id = CAST(:id AS uuid)"),
        {"id": explanation_id},
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_for_document(
    session: Any,
    document_id: str,
    *,
    element_type: str | None = None,
    status: str | None = None,
    kind: str | None = None,
) -> list[dict]:
    """document 内の説明一覧（承認 API・要素インベントリ用。P4: candidate/approved/
    dismissed/superseded すべて対象— フィルタは呼び出し側の指定次第）。"""
    clauses = ["document_id = CAST(:document_id AS uuid)"]
    params: dict[str, Any] = {"document_id": document_id}
    if element_type:
        clauses.append("element_type = :element_type")
        params["element_type"] = element_type
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if kind:
        clauses.append("kind = :kind")
        params["kind"] = kind
    where_sql = " AND ".join(clauses)
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_COLUMNS_SQL} FROM element_explanations
            WHERE {where_sql}
            ORDER BY created_at DESC
            """
        ),
        params,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def approved_for_elements(
    session: Any,
    document_id: str,
    element_refs: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], list[dict]]:
    """``(element_type, element_id)`` の集合について ``approved`` の説明をまとめて返す。

    学習者/lecture 露出（``learning.py::_generate_graph_element_explanation`` 等、
    後続実装）の正本ヘルパー。戻り値は ``{(element_type, element_id): [row, ...]}``
    — kind ごとに高々1件が典型だが、複数 approved 行があっても切り捨てず全件返す
    （P4）。``element_refs`` が空なら空 dict（DB を叩かない）。
    """
    pairs = [
        (str(t or "").strip(), str(i or "").strip())
        for t, i in (element_refs or [])
        if str(t or "").strip() and str(i or "").strip()
    ]
    if not pairs:
        return {}

    params: dict[str, Any] = {"document_id": document_id, "status": STATUS_APPROVED}
    clauses = []
    for idx, (element_type, element_id) in enumerate(pairs):
        clauses.append(f"(element_type = :etype_{idx} AND element_id = :eid_{idx})")
        params[f"etype_{idx}"] = element_type
        params[f"eid_{idx}"] = element_id
    where_pairs = " OR ".join(clauses)

    rows = session.execute(
        sa_text(
            f"""
            SELECT {_COLUMNS_SQL} FROM element_explanations
            WHERE document_id = CAST(:document_id AS uuid) AND status = :status
              AND ({where_pairs})
            ORDER BY element_type, element_id, kind, created_at DESC
            """
        ),
        params,
    ).fetchall()

    result: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        d = _row_to_dict(row)
        key = (d["element_type"], d["element_id"])
        result.setdefault(key, []).append(d)
    return result


# ---------------------------------------------------------------------------
# 承認・却下（状態遷移のみ・行削除なし）
# ---------------------------------------------------------------------------


def _transition(session: Any, explanation_id: str, *, new_status: str, user_id: str) -> dict | None:
    if not str(user_id or "").strip():
        raise ValueError("user_id is required")
    row = session.execute(
        sa_text(
            f"""
            UPDATE element_explanations
            SET status = :new_status, reviewed_by = :user_id, reviewed_at = now()
            WHERE id = CAST(:id AS uuid) AND status = :candidate
            RETURNING {_COLUMNS_SQL}
            """
        ),
        {
            "id": explanation_id,
            "new_status": new_status,
            "user_id": user_id,
            "candidate": STATUS_CANDIDATE,
        },
    ).fetchone()
    if row is not None:
        return _row_to_dict(row)
    # 遷移できなかった: 存在しないか、candidate 以外の状態にある。両者を区別して返す
    # (存在しない → None → 呼び出し側は404、candidate 以外 → 例外 → 呼び出し側は422)。
    existing = get_by_id(session, explanation_id)
    if existing is None:
        return None
    raise ElementExplanationError(
        f"cannot transition to {new_status!r} from status={existing['status']!r} "
        "(must be 'candidate')",
        kind="conflict",
    )


def approve(session: Any, explanation_id: str, user_id: str) -> dict | None:
    """``candidate → approved``（人間確定・E2）。存在しなければ ``None``。

    candidate 以外からの承認は :class:`ElementExplanationError`\\ (``kind='conflict'``)
    を送出する（呼び出し側は 422 相当にマッピングする）。
    """
    return _transition(session, explanation_id, new_status=STATUS_APPROVED, user_id=user_id)


def dismiss(session: Any, explanation_id: str, user_id: str) -> dict | None:
    """``candidate → dismissed``（却下・行削除しない・P4）。存在しなければ ``None``。

    candidate 以外からの却下は :class:`ElementExplanationError`\\ (``kind='conflict'``)
    を送出する。
    """
    return _transition(session, explanation_id, new_status=STATUS_DISMISSED, user_id=user_id)


# ---------------------------------------------------------------------------
# 編集（新 revision 行 + 旧行 superseded・履歴保持）
# ---------------------------------------------------------------------------


def update_body(session: Any, explanation_id: str, user_id: str, new_body: str) -> dict | None:
    """本文を編集する。旧行は ``superseded`` に遷移し、旧 evidence を引き継いだ
    新行を ``created_by=user_id`` で INSERT する（履歴保持・P4。行の書き換えはしない）。

    新行の ``status`` は旧行の ``status`` を維持する（``candidate`` を編集すれば
    ``candidate`` のまま、``approved`` を編集すれば ``approved`` のまま — 教員が承認済みの
    説明の言い回しだけ直すケースを再承認なしに反映できる）。``reviewed_by``/``reviewed_at``
    も旧行の値をそのまま引き継ぐ（承認の事実そのものは編集で失われない）。

    編集できるのは ``candidate``/``approved`` の行のみ（:data:`_EDITABLE_STATUSES`）。
    既に ``dismissed``/``superseded`` になっている行は履歴であり編集対象にしない —
    :class:`ElementExplanationError`\\ (``kind='conflict'``) を送出する。対象が存在しない
    場合は ``None`` を返す。
    """
    if not str(user_id or "").strip():
        raise ValueError("user_id is required")
    new_body_norm = str(new_body or "").strip()
    if not new_body_norm:
        raise ValueError("new_body is required")

    existing = get_by_id(session, explanation_id)
    if existing is None:
        return None
    if existing["status"] not in _EDITABLE_STATUSES:
        raise ElementExplanationError(
            f"cannot edit explanation with status={existing['status']!r} "
            f"(must be one of {_EDITABLE_STATUSES!r})",
            kind="conflict",
        )

    superseded = session.execute(
        sa_text(
            """
            UPDATE element_explanations
            SET status = :superseded
            WHERE id = CAST(:id AS uuid) AND status = :current_status
            RETURNING id
            """
        ),
        {
            "id": explanation_id,
            "superseded": STATUS_SUPERSEDED,
            "current_status": existing["status"],
        },
    ).fetchone()
    if superseded is None:
        # 直前の SELECT と UPDATE の間に別リクエストが状態を変えた(競合)。
        raise ElementExplanationError(
            "concurrent update detected on element_explanation; reload and retry",
            kind="conflict",
        )

    return _insert_row(
        session,
        document_id=existing["document_id"],
        element_type=existing["element_type"],
        element_id=existing["element_id"],
        kind=existing["kind"],
        body=new_body_norm,
        evidence=existing["evidence"],
        status=existing["status"],
        created_by=user_id,
        reviewed_by=existing["reviewed_by"],
        reviewed_at=existing["reviewed_at"],
    )
