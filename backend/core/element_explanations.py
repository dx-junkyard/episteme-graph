"""``element_explanations`` の DB プリミティブ（Phase 2, migration 056）。

正本: ``docs/features/hierarchical_context_explanation_design.md`` §2（E1〜E8）・§5.2。

全要素型（figure / theory_component / theory_claim / equation）+ document スコープ
（migration 062・``discuss_opening_authoring_design.md`` §5）を受けるポリモーフィックな
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
# document スコープ（migration 062・discuss_opening_authoring_design.md §5）:
# 係留先が要素ではなく document 全体である素材（開幕画面の「議論のきっかけ」）。
# ``element_id`` は ``document_id`` と同値で使う。
ELEMENT_TYPE_DOCUMENT = "document"
ELEMENT_TYPES = (
    ELEMENT_TYPE_FIGURE,
    ELEMENT_TYPE_COMPONENT,
    ELEMENT_TYPE_CLAIM,
    ELEMENT_TYPE_EQUATION,
    ELEMENT_TYPE_DOCUMENT,
)

KIND_GENERIC = "generic"
KIND_CONTEXTUAL = "contextual"
KINDS = (KIND_GENERIC, KIND_CONTEXTUAL)

# role（migration 062）: 生成物の役割。NULL は二層説明の説明本文（既存行）。
# 語彙を増やすときは migration の CHECK と同時に更新する（DB とコードの二重管理を
# 避けるため、ここが唯一のコード側正本）。
ROLE_DISCUSSION_SEED = "discussion_seed"
ROLES = (ROLE_DISCUSSION_SEED,)

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
    status, created_by, reviewed_by, reviewed_at, created_at, role
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
        # role は migration 062 で後から足した列なので、旧 DB スキーマ相手でも
        # 落ちないよう長さで防御する（None = 二層説明の説明本文）。
        "role": (row[12] if len(row) > 12 else None) or None,
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
    role: str | None = None,
) -> dict:
    row = session.execute(
        sa_text(
            f"""
            INSERT INTO element_explanations (
                document_id, element_type, element_id, kind, body, evidence,
                status, created_by, reviewed_by, reviewed_at, role
            ) VALUES (
                CAST(:document_id AS uuid), :element_type, :element_id, :kind, :body,
                CAST(:evidence AS jsonb), :status, :created_by,
                :reviewed_by, CAST(:reviewed_at AS timestamptz), :role
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
            "role": role or None,
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
    role_raw = str((raw or {}).get("role") or "").strip()
    if role_raw and role_raw not in ROLES:
        raise ValueError(f"invalid role: {role_raw!r}")
    return {
        "element_type": element_type,
        "element_id": element_id,
        "kind": kind,
        "body": body,
        "evidence": evidence,
        "created_by": created_by,
        "role": role_raw or None,
    }


def insert_candidates(session: Any, document_id: str, items: Iterable[dict]) -> list[dict]:
    """パイプライン（ContextualExplanationAgent / DiscussOpeningAgent 等）用の
    一括 candidate 投入。

    各 item は ``{element_type, element_id, kind, body, evidence?, created_by?, role?}``。
    同一 ``(document_id, element_type, element_id, kind, role)`` の既存 ``candidate``
    行は ``superseded`` へ遷移させてから新しい candidate 行を INSERT する
    （``approved``/``dismissed``/既に ``superseded`` の行はそのまま — E2・P4。
    AI 再解析が教員確定を消さない。migration 053 と同じ原則）。

    supersede は **キー単位で1回だけ**（全 INSERT の前に）実行する。同じキーに複数行を
    積む使い方（migration 062 の ``role='discussion_seed'``: 1 document に 2〜3 件の
    議論のきっかけ）で、後続 INSERT が直前に入れた兄弟行を superseded にしてしまうのを
    防ぐため。1キー1行だった従来（二層説明）の挙動は変わらない。

    ``role`` は ``None``（既定）なら二層説明の説明本文として扱い、supersede も
    ``role IS NULL`` の行だけを対象にする（role 付きの開幕素材と二層説明が互いを
    巻き込まない）。

    ``items`` の要素が不正（語彙外の element_type/kind/role・空の element_id/body）な
    場合は ``ValueError`` を送出する（呼び出し元 agent の validator が事前に弾いている
    想定の最終ガード。ここで弾かれた要素があると以降の要素も含め全体が例外で止まるため、
    呼び出し側で事前検証しておくこと）。
    """
    validated = [_validate_candidate_item(raw) for raw in (items or [])]

    superseded_keys: set[tuple[str, str, str, str | None]] = set()
    for item in validated:
        key = (item["element_type"], item["element_id"], item["kind"], item["role"])
        if key in superseded_keys:
            continue
        superseded_keys.add(key)
        session.execute(
            sa_text(
                """
                UPDATE element_explanations
                SET status = :superseded
                WHERE document_id = CAST(:document_id AS uuid)
                  AND element_type = :element_type
                  AND element_id = :element_id
                  AND kind = :kind
                  AND role IS NOT DISTINCT FROM :role
                  AND status = :candidate
                """
            ),
            {
                "document_id": document_id,
                "element_type": item["element_type"],
                "element_id": item["element_id"],
                "kind": item["kind"],
                "role": item["role"],
                "superseded": STATUS_SUPERSEDED,
                "candidate": STATUS_CANDIDATE,
            },
        )

    created: list[dict] = []
    for item in validated:
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
                role=item["role"],
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
    role: str | None = None,
) -> list[dict]:
    """document 内の説明一覧（承認 API・要素インベントリ用。P4: candidate/approved/
    dismissed/superseded すべて対象— フィルタは呼び出し側の指定次第）。

    ``role`` を指定すると当該 role の行だけを返す（migration 062。開幕素材の
    レビューキュー・配信が ``role='discussion_seed'`` で引く）。未指定なら role の
    有無で絞らない（二層説明の説明本文＝``role IS NULL`` も含む）。
    """
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
    if role:
        clauses.append("role = :role")
        params["role"] = role
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


def bulk_transition(
    session: Any,
    document_id: str,
    explanation_ids: Iterable[str],
    *,
    new_status: str,
    user_id: str,
) -> dict:
    """一括承認・一括却下（``candidate → approved/dismissed``）。教員のレビュー負荷軽減用。

    candidate-only 原則（E2）は一括操作でも維持する — 遷移できるのは ``candidate`` の行
    のみで、対象は必ず ``document_id`` にスコープする（他 document の行を巻き込まない）。
    確定は依然として人間（教員）の明示操作であり、本関数はその操作を1回にまとめる
    だけで承認基準そのものは変えない。

    部分成功セマンティクス: 1件の競合（既に approved/dismissed 等）や不正な id が
    混ざっていても全体を例外で失敗させない。遷移できた行は ``updated``
    （入力 ``explanation_ids`` の順序で整列）、できなかった行は ``skipped`` に
    ``{"id", "status", "reason"}`` で正直に報告する（P4: 失敗を隠さない）。
    ``reason`` は次の2値:

    - ``"conflict"``: 同一 document 内に存在するが ``status`` が ``candidate`` ではない
      （``status`` に実際の値を入れる）。
    - ``"not_found"``: id が存在しない、または**別 document** に属する行だった
      （権限境界のため、別 document 行の実際の ``status`` は返さず ``None`` にする —
      他 document の状態を漏らさない）。

    ``user_id`` が空、``new_status`` が :data:`STATUS_APPROVED`/:data:`STATUS_DISMISSED`
    以外、または正規化後（str化・strip・重複除去・順序保持）の ``explanation_ids`` が
    空の場合は ``ValueError`` を送出する。
    """
    if not str(user_id or "").strip():
        raise ValueError("user_id is required")
    if new_status not in (STATUS_APPROVED, STATUS_DISMISSED):
        raise ValueError(f"invalid new_status: {new_status!r} (must be approved/dismissed)")

    ids: list[str] = []
    seen: set[str] = set()
    for raw in explanation_ids or []:
        normalized = str(raw or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ids.append(normalized)
    if not ids:
        raise ValueError("explanation_ids is required")

    rows = session.execute(
        sa_text(
            f"""
            UPDATE element_explanations
            SET status = :new_status, reviewed_by = :user_id, reviewed_at = now()
            WHERE id::text = ANY(:ids)
              AND document_id = CAST(:document_id AS uuid)
              AND status = :candidate
            RETURNING {_COLUMNS_SQL}
            """
        ),
        {
            "ids": ids,
            "document_id": document_id,
            "new_status": new_status,
            "user_id": user_id,
            "candidate": STATUS_CANDIDATE,
        },
    ).fetchall()
    updated_by_id: dict[str, dict] = {}
    for row in rows:
        d = _row_to_dict(row)
        updated_by_id[d["id"]] = d

    skipped: list[dict] = []
    remaining_ids = [i for i in ids if i not in updated_by_id]
    if remaining_ids:
        check_rows = session.execute(
            sa_text(
                "SELECT id::text, document_id::text, status FROM element_explanations "
                "WHERE id::text = ANY(:ids)"
            ),
            {"ids": remaining_ids},
        ).fetchall()
        found = {r[0]: {"document_id": r[1], "status": r[2]} for r in check_rows}
        for eid in remaining_ids:
            info = found.get(eid)
            if info is not None and info["document_id"] == document_id:
                skipped.append({"id": eid, "status": info["status"], "reason": "conflict"})
            else:
                # 存在しない、または別 document の行（権限境界のため status を漏らさない）。
                skipped.append({"id": eid, "status": None, "reason": "not_found"})

    updated = [updated_by_id[i] for i in ids if i in updated_by_id]
    return {"updated": updated, "skipped": skipped}


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
        # role は素材の役割そのものなので編集で失わない（migration 062）。
        role=existing.get("role"),
    )
