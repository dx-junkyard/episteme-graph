"""同一性リンク（`element_identity_links`）の DB プリミティブ（Phase W-β）。

設計書: `docs/features/knowledge_network_vision.md`（§3 修正② / §4 KN-2・KN-3）、
`docs/features/element_deliberation_workspace_design.md`（§5.5・§6）。migration 048。

- インスタンス（scope='document' の :class:`~core.deliberation.schema.ElementRef`）と
  共通部品（L層 `library_entries`、Phase W-β の設計では ``shared_part``）の同一性を
  **リンクの追加**として記録する（KN-2: インスタンス側は書き換えない）。
- 生成は常に ``status='candidate'``（KN-3）。確定（``confirmed``）/ 却下（``rejected``）は
  人間のみが :func:`decide` で行い、行削除はしない（P4）。
- confidence の生値は本モジュールの戻り値には残すが（DB 界面）、API 層は
  :func:`confidence_label` を通して段階ラベルのみを返すこと（W8）。

本モジュールは FastAPI にも ``routes``/``services`` にも依存しない（開発ルール2・W1 相当）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text as sa_text

from core.label_vocab import (
    CONFIDENCE_LABEL_REFERENCE,
    CONFIDENCE_LABEL_TENTATIVE,
    CONFIDENCE_LABEL_TENTATIVE_HIGH,
    CONFIDENCE_TENTATIVE_REFERENCE_HIGH,
)
from core.postgres import get_session
from core.schema import MAPPING_JUSTIFICATIONS
from core.deliberation.schema import (
    ElementRef,
    ElementResolutionError,
    IDENTITY_LINKABLE_ELEMENT_TYPES,
    IDENTITY_LINK_DECIDABLE_STATUSES,
    IDENTITY_LINK_STATUS_CANDIDATE,
    SCOPE_DOCUMENT,
)

# ── 段階ラベル（W8: confidence の生値を UI に出さない）───────────────────────────
# 段階の境界・語彙・「未測定は最も慎重な段階へ倒す」規則の正本は core/label_vocab.py。
# ここの語彙は「低」と言い切らない別語彙（暫定 / 参考 / 確度高）である。
CONFIDENCE_LABEL_HIGH = CONFIDENCE_LABEL_TENTATIVE_HIGH


def confidence_label(value: Any) -> str:
    """confidence の生値を段階ラベルへ変換する（W8）。

    未測定・変換不能は最も慎重な「暫定」に倒す（情報が無いことを高確度に見せない）。
    """
    return CONFIDENCE_TENTATIVE_REFERENCE_HIGH.label_for(value)


def _json(value: Any, default: Any) -> Any:
    # decomposition.py と同型の素朴な JSONB 正規化（psycopg2 が既に dict/list に
    # デコード済みのことが多いが、想定外の型が来た場合は既定値へフォールバックする）。
    return value if isinstance(value, type(default)) else default


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


_COLUMNS_SQL = """
    id::text, instance_element_type, instance_element_id,
    instance_document_id::text AS instance_document_id,
    shared_part_id::text, status, local_expression, evidence, reason, confidence,
    created_by::text, decided_by::text, decided_at, created_at, updated_at,
    mapping_justification
"""


def _row_to_dict(row: Any) -> dict:
    return {
        "id": str(row[0]),
        "instance_element_type": row[1] or "",
        "instance_element_id": row[2] or "",
        "instance_document_id": row[3] or "",
        "shared_part_id": str(row[4]) if row[4] else "",
        "status": row[5] or "",
        "local_expression": _json(row[6], {}),
        "evidence": _json(row[7], []),
        "reason": row[8] or "",
        "confidence": row[9],  # 生値のまま（core は DB 界面。API 層で confidence_label へ）
        "created_by": row[10],
        "decided_by": row[11],
        "decided_at": row[12].isoformat() if row[12] else None,
        "created_at": row[13].isoformat() if row[13] else "",
        "updated_at": row[14].isoformat() if row[14] else "",
        # 「なぜ同じと言えたか」（概念レジストリ KR4 / migration 082）。既存行は
        # 導出できないので None = 「記録なし」のまま（推測で埋めない）。
        "mapping_justification": row[15] if len(row) > 15 else None,
    }


def create_candidate(
    instance_ref: ElementRef,
    shared_part_id: str,
    *,
    local_expression: dict | None = None,
    evidence: list | None = None,
    reason: str = "",
    confidence: float | None = None,
    created_by: str | None = None,
    mapping_justification: str | None = None,
) -> dict:
    """同一性リンクの候補を1件作成する（常に ``status='candidate'``、KN-3）。

    ``instance_ref`` は ``core.deliberation.refs.resolve`` 済みで ``scope='document'`` の
    ElementRef であること（インスタンス側のみが対象。domain スコープはここでは受理しない
    — 共通部品同士の直接リンクはビジョン §8 未決2 により当面作らない）。

    ``UNIQUE(instance_element_type, instance_element_id, instance_document_id, shared_part_id)``
    の衝突はエラーにせず、既存行をそのまま返す（P4: 既存の候補/確定/却下を上書きしない。
    同じ組の同一性は一度で十分であり、再提案は重複行を増やさず既存の状態を尊重する）。
    ``instance_document_id`` を制約に含めるのは、equation のように独立テーブルを持たない
    要素型では ``element_id``（例: ``eq_1``）が論文間で衝突しうるため（レビュー指摘
    2026-07-15）。document_id を含めないと、別論文からの候補作成が別論文の既存行を
    返してしまう（衝突・情報漏えい）。

    ``mapping_justification``（概念レジストリ KR4・migration 082）は「なぜ同じと
    言えたか」の記録で、**新規作成では必須**（``core.schema.MAPPING_JUSTIFICATIONS``
    の語彙内。未指定・語彙外は ``ValueError`` → route が 422）。W層 UI からの手動作成は
    ``manual_curation``、決定論の候補導出は ``lexical_match`` / ``vector_similarity``。
    """
    if instance_ref.scope != SCOPE_DOCUMENT:
        raise ElementResolutionError(
            "identity link source must be a document-scoped instance "
            f"(got scope={instance_ref.scope!r})",
            kind="invalid",
        )
    if instance_ref.element_type not in IDENTITY_LINKABLE_ELEMENT_TYPES:
        # 設計書 §16: evidence（原文の引用）/ derivation（この論文の導出手順）は
        # 共通部品化の単位ではないため同一性リンクの source にしない。migration 048 の
        # CHECK 制約と同じ集合をコード側でも fail-closed に強制する（DB エラーで 500 に
        # なる前に ElementResolutionError へ倒す）。
        raise ElementResolutionError(
            f"element_type {instance_ref.element_type!r} cannot be linked to a shared part",
            kind="invalid",
        )
    if not str(shared_part_id or "").strip():
        raise ValueError("shared_part_id is required")
    # KR4: 「なぜ同じと言えたか」の無い同一性候補は作らない。要素型・スコープの検査より
    # **後**に置く（不正な source は従来どおり ElementResolutionError で返す）。
    justification = str(mapping_justification or "").strip()
    if not justification:
        raise ValueError("mapping_justification is required")
    if justification not in MAPPING_JUSTIFICATIONS:
        raise ValueError(f"invalid mapping_justification: {mapping_justification!r}")

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                INSERT INTO element_identity_links (
                    instance_element_type, instance_element_id, instance_document_id,
                    shared_part_id, status, local_expression, evidence, reason,
                    confidence, created_by, mapping_justification
                ) VALUES (
                    :element_type, :element_id, CAST(:document_id AS uuid),
                    CAST(:shared_part_id AS uuid), :status,
                    CAST(:local_expression AS jsonb), CAST(:evidence AS jsonb),
                    :reason, :confidence, CAST(:created_by AS uuid), :mapping_justification
                )
                ON CONFLICT (instance_element_type, instance_element_id, instance_document_id, shared_part_id)
                DO NOTHING
                RETURNING {_COLUMNS_SQL}
                """
            ),
            {
                "element_type": instance_ref.element_type,
                "element_id": instance_ref.element_id,
                # migration 080 以降 instance_document_id は uuid NOT NULL。空文字を
                # そのまま入れると例外になる（identity link は必ず document 由来の
                # instance に張るので、空なら NOT NULL 違反として失敗するのが正しい）。
                "document_id": instance_ref.document_id or None,
                "shared_part_id": shared_part_id,
                # status は引数として受け取らず、常にこの定数を束縛する（KN-3固定）。
                "status": IDENTITY_LINK_STATUS_CANDIDATE,
                "local_expression": _dump_json(local_expression or {}),
                "evidence": _dump_json(evidence or []),
                "reason": reason or "",
                "confidence": confidence,
                "created_by": created_by,
                "mapping_justification": justification,
            },
        ).fetchone()
        if row is None:
            # 衝突 → 既存行を読み直す（上書きしない・P4）。instance_document_id も
            # 一致条件に含める（別論文の同一 element_id を誤って返さないため）。
            row = session.execute(
                sa_text(
                    f"""
                    SELECT {_COLUMNS_SQL} FROM element_identity_links
                    WHERE instance_element_type = :element_type
                      AND instance_element_id = :element_id
                      AND instance_document_id = CAST(NULLIF(:document_id, '') AS uuid)
                      AND shared_part_id = CAST(:shared_part_id AS uuid)
                    """
                ),
                {
                    "element_type": instance_ref.element_type,
                    "element_id": instance_ref.element_id,
                    "document_id": instance_ref.document_id or "",
                    "shared_part_id": shared_part_id,
                },
            ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return _row_to_dict(row)


def get_by_id(link_id: str) -> dict | None:
    """id で1件取得する（無ければ None）。権限ゲート前の存在確認に使う。"""
    session = get_session()
    try:
        row = session.execute(
            sa_text(f"SELECT {_COLUMNS_SQL} FROM element_identity_links WHERE id = CAST(:id AS uuid)"),
            {"id": link_id},
        ).fetchone()
    finally:
        session.close()
    return _row_to_dict(row) if row else None


def decide(link_id: str, *, status: str, decided_by: str) -> dict | None:
    """候補を確定（``confirmed``）/ 却下（``rejected``）する（KN-3: 人間のみ）。

    ``status`` は :data:`~core.deliberation.schema.IDENTITY_LINK_DECIDABLE_STATUSES`
    （``confirmed`` / ``rejected``）のみ受理する。``decided_by`` は必須（帰属必須）。
    対象行が存在しないか、既に ``candidate`` でない（＝二重遷移）場合は ``None`` を返す
    （行削除はしない・P4。呼び出し側が 404/409 にマッピングする）。
    """
    if status not in IDENTITY_LINK_DECIDABLE_STATUSES:
        raise ValueError(f"invalid status for decide(): {status!r}")
    if not str(decided_by or "").strip():
        raise ValueError("decided_by is required")

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                UPDATE element_identity_links
                SET status = :status,
                    decided_by = CAST(:decided_by AS uuid),
                    decided_at = now(),
                    updated_at = now()
                WHERE id = CAST(:id AS uuid) AND status = :current_status
                RETURNING {_COLUMNS_SQL}
                """
            ),
            {
                "id": link_id,
                "status": status,
                "decided_by": decided_by,
                "current_status": IDENTITY_LINK_STATUS_CANDIDATE,
            },
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return _row_to_dict(row) if row else None


def list_for_instance(element_type: str, element_id: str, document_id: str) -> list[dict]:
    """あるインスタンス要素に付いた同一性リンク一覧（候補・確定・却下すべて。P4）。

    ``document_id`` は必須（キーワード省略不可）。equation のように独立テーブルを
    持たない要素型では ``element_id``（例: ``eq_1``）が論文間で衝突しうるため、
    ``instance_document_id`` で絞り込まないと別論文のリンクを返してしまう
    （呼び出し側の document 閲覧ゲートをすり抜ける情報漏えい。レビュー指摘 2026-07-15）。
    呼び出し側は解決済み ``ElementRef.document_id`` を渡すこと。
    """
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                f"""
                SELECT {_COLUMNS_SQL} FROM element_identity_links
                WHERE instance_element_type = :element_type
                  AND instance_element_id = :element_id
                  AND instance_document_id = CAST(NULLIF(:document_id, '') AS uuid)
                ORDER BY created_at ASC
                """
            ),
            {"element_type": element_type, "element_id": element_id, "document_id": document_id},
        ).fetchall()
    finally:
        session.close()
    return [_row_to_dict(r) for r in rows]


def list_for_shared_part(shared_part_id: str) -> list[dict]:
    """ある共通部品（library_entry）に紐づく同一性リンク一覧（候補・確定・却下すべて）。"""
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                f"""
                SELECT {_COLUMNS_SQL} FROM element_identity_links
                WHERE shared_part_id = CAST(:shared_part_id AS uuid)
                ORDER BY created_at ASC
                """
            ),
            {"shared_part_id": shared_part_id},
        ).fetchall()
    finally:
        session.close()
    return [_row_to_dict(r) for r in rows]


def list_for_shared_parts(shared_part_ids: list[str]) -> dict[str, list[dict]]:
    """複数の共通部品の同一性リンクをまとめて 1 クエリで引く（P3-R11: N+1 の解消）。

    戻り値は ``{shared_part_id: [link, ...]}``。リンクの無い id はキー自体を持たない
    （呼び出し側は ``.get(id, [])``）。``list_for_shared_part`` と同じ内容・同じ順序。
    """
    keys = [str(i or "") for i in shared_part_ids if str(i or "")]
    if not keys:
        return {}
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                f"""
                SELECT {_COLUMNS_SQL} FROM element_identity_links
                WHERE shared_part_id = ANY(CAST(:shared_part_ids AS uuid[]))
                ORDER BY created_at ASC
                """
            ),
            {"shared_part_ids": keys},
        ).fetchall()
    finally:
        session.close()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        link = _row_to_dict(row)
        grouped.setdefault(str(link.get("shared_part_id") or ""), []).append(link)
    return grouped


def confirmed_links_for_document(document_id: str) -> list[dict]:
    """document 内で確定済みの同一性リンクのみを返す。

    Phase P の旅の traversal（``論文ローカルグラフ → 同一性リンク → L層ハブ →
    atlas骨格 → 本人のノード``）が読む正本。candidate/rejected は traversal の
    根拠にしない（KN-3: 未確定の同一視を事実であるかのように辿らせない）。
    """
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                f"""
                SELECT {_COLUMNS_SQL} FROM element_identity_links
                WHERE instance_document_id = CAST(NULLIF(:document_id, '') AS uuid) AND status = 'confirmed'
                ORDER BY created_at ASC
                """
            ),
            {"document_id": document_id},
        ).fetchall()
    finally:
        session.close()
    return [_row_to_dict(r) for r in rows]
