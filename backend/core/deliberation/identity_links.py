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
    SYMBOL_LINK_CONFIRMED_FACT,
)
from core.candidate_flow import CandidateFlow, CandidateTransitionError, CandidateVocabulary
from core.postgres import get_session
from core.schema import MAPPING_JUSTIFICATIONS
from core.deliberation.schema import (
    ElementRef,
    ElementResolutionError,
    IDENTITY_LINKABLE_ELEMENT_TYPES,
    IDENTITY_LINK_DECIDABLE_STATUSES,
    IDENTITY_LINK_STATUS_CANDIDATE,
    IDENTITY_LINK_STATUS_CONFIRMED,
    IDENTITY_LINK_STATUS_REJECTED,
    IDENTITY_LINK_STATUS_REVOKED,
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
    mapping_justification, revoked_by::text, revoked_at, revoke_reason,
    restored_by::text, restored_at, restore_reason, restored_from
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
        # IK-0953（migration 093）: 確定の取り消しの記録。確定者 decided_by は上書きしない。
        "revoked_by": row[16] if len(row) > 16 else None,
        "revoked_at": (row[17].isoformat() if len(row) > 17 and row[17] else None),
        "revoke_reason": (row[18] or "") if len(row) > 18 else "",
        # IK-1062（093 の追補）: 否定の判断を候補に戻した記録。元の判断の列は上書きしない。
        "restored_by": row[19] if len(row) > 19 else None,
        "restored_at": (row[20].isoformat() if len(row) > 20 and row[20] else None),
        "restore_reason": (row[21] or "") if len(row) > 21 else "",
        "restored_from": (row[22] or "") if len(row) > 22 else "",
    }


#: 候補作成の結果の種類（IK-0918。IK-0897 のラベル追加と同じ作法）。新しく作った
#: ``created`` / 既にあった（候補のまま・確定済み・却下済み）を言い分ける。
LINK_OUTCOME_CREATED = "created"
LINK_OUTCOME_EXISTING_CANDIDATE = "existing_candidate"
LINK_OUTCOME_EXISTING_CONFIRMED = "existing_confirmed"
LINK_OUTCOME_EXISTING_REJECTED = "existing_rejected"
LINK_OUTCOME_EXISTING_REVOKED = "existing_revoked"
LINK_OUTCOMES = (
    LINK_OUTCOME_CREATED,
    LINK_OUTCOME_EXISTING_CANDIDATE,
    LINK_OUTCOME_EXISTING_CONFIRMED,
    LINK_OUTCOME_EXISTING_REJECTED,
    LINK_OUTCOME_EXISTING_REVOKED,
)
_EXISTING_OUTCOME_BY_STATUS = {
    "candidate": LINK_OUTCOME_EXISTING_CANDIDATE,
    "confirmed": LINK_OUTCOME_EXISTING_CONFIRMED,
    "rejected": LINK_OUTCOME_EXISTING_REJECTED,
    "revoked": LINK_OUTCOME_EXISTING_REVOKED,
}

#: 結果の事実文（正本はサーバ。JS は素通しで描く）。
LINK_OUTCOME_FACTS = {
    LINK_OUTCOME_CREATED: "結び付きの候補を作成しました。上の一覧で両側の意味を見比べて確定・却下できます。",
    LINK_OUTCOME_EXISTING_CANDIDATE: "この部品と共通部品の結び付きは既に候補としてあります。新しくは作っていません。",
    LINK_OUTCOME_EXISTING_CONFIRMED: "この部品と共通部品の結び付きは既に確定済みです。新しくは作っていません。",
    LINK_OUTCOME_EXISTING_REJECTED: "この部品と共通部品の結び付きは以前に却下されています。新しくは作っていません（却下は取り消していません。判断し直すときは、その行の［候補に戻す］から理由を書いて戻します）。",
    LINK_OUTCOME_EXISTING_REVOKED: "この部品と共通部品の結び付きは以前に確定され、その後に取り消されています。新しくは作っていません（取り消しの記録はそのまま残しています。判断し直すときは、その行の［候補に戻す］から理由を書いて戻します）。",
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
        outcome = LINK_OUTCOME_CREATED
        if row is None:
            outcome = LINK_OUTCOME_EXISTING_CANDIDATE  # 状態は読み直した行で確定する
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
    result = _row_to_dict(row)
    if outcome != LINK_OUTCOME_CREATED:
        # IK-0918: 既存行を返すときは、その行の状態で結果の種類を言い分ける。
        outcome = _EXISTING_OUTCOME_BY_STATUS.get(result.get("status") or "", LINK_OUTCOME_EXISTING_CANDIDATE)
    result["outcome"] = outcome
    return result


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


def decide(link_id: str, *, status: str, decided_by: str, reason: str = "") -> dict | None:
    """候補を確定（``confirmed``）/ 却下（``rejected``）する（KN-3: 人間のみ）。

    ``status`` は :data:`~core.deliberation.schema.IDENTITY_LINK_DECIDABLE_STATUSES`
    （``confirmed`` / ``rejected``）のみ受理する。``decided_by`` は必須（帰属必須）。
    対象行が存在しないか、既に ``candidate`` でない（＝二重遷移）場合は ``None`` を返す
    （行削除はしない・P4。呼び出し側が 404/409 にマッピングする）。

    IK-1050（第 25 周 第 7 波）: 見送り（``rejected``）は理由必須（KR7 — エントリの見送り・
    確定の取り消しと同じ作法）。理由は ``revoke_reason`` 列に入れる — この列は「否定の判断
    （見送り・取り消し）の理由」の列として読む（見送った行は取り消せないので衝突しない。
    migration を増やさない読み替え — ``concept_registry_design.md`` 第 7 波の追補）。
    """
    if status not in IDENTITY_LINK_DECIDABLE_STATUSES:
        raise ValueError(f"invalid status for decide(): {status!r}")
    if not str(decided_by or "").strip():
        raise ValueError("decided_by is required")
    reason_text = str(reason or "").strip()
    if status == IDENTITY_LINK_STATUS_REJECTED and not reason_text:
        raise ValueError("reason is required for rejected")

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                UPDATE element_identity_links
                SET status = :status,
                    decided_by = CAST(:decided_by AS uuid),
                    decided_at = now(),
                    revoke_reason = CASE WHEN :status = 'rejected' THEN :reason ELSE revoke_reason END,
                    updated_at = now()
                WHERE id = CAST(:id AS uuid) AND status = :current_status
                RETURNING {_COLUMNS_SQL}
                """
            ),
            {
                "id": link_id,
                "status": status,
                "decided_by": decided_by,
                "reason": reason_text[:500],
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


def revoke(link_id: str, *, revoked_by: str, reason: str) -> dict | None:
    """確定済みの同一性リンクを取り消す（IK-0953。confirmed → revoked のみ・人間のみ）。

    ``revoked_by`` と ``reason`` は必須（帰属必須・理由必須 → 空は ``ValueError``）。
    確定者（``decided_by`` / ``decided_at``）は上書きしない — 誰が確定し、誰が取り消したかを
    両方残す。対象行が無いか ``confirmed`` でない（候補・却下・取り消し済み）なら ``None``
    （呼び出し側が 404 / 409 にマッピングする）。行は削除しない（P4 / W4）。
    """
    if not str(revoked_by or "").strip():
        raise ValueError("revoked_by is required")
    reason_text = str(reason or "").strip()
    if not reason_text:
        raise ValueError("reason is required")
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                UPDATE element_identity_links
                SET status = :status,
                    revoked_by = CAST(:revoked_by AS uuid),
                    revoked_at = now(),
                    revoke_reason = :reason,
                    updated_at = now()
                WHERE id = CAST(:id AS uuid) AND status = :current_status
                RETURNING {_COLUMNS_SQL}
                """
            ),
            {
                "id": link_id,
                "status": IDENTITY_LINK_STATUS_REVOKED,
                "revoked_by": revoked_by,
                "reason": reason_text[:500],
                "current_status": IDENTITY_LINK_STATUS_CONFIRMED,
            },
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return _row_to_dict(row) if row else None


#: IK-1062: 否定の判断（見送り ``rejected`` / 確定の取り消し ``revoked``）を候補に戻す遷移の語彙。
#: 否定の状態が 2 つあるので、戻す元ごとに :class:`CandidateVocabulary` を 1 つずつ宣言し、
#: 遷移の検証（戻せるのは否定の状態からだけ）は :mod:`core.candidate_flow` に任せる。
RESTORE_VOCABULARIES = {
    IDENTITY_LINK_STATUS_REJECTED: CandidateVocabulary(
        candidate=IDENTITY_LINK_STATUS_CANDIDATE,
        accepted=IDENTITY_LINK_STATUS_CONFIRMED,
        dismissed=IDENTITY_LINK_STATUS_REJECTED,
    ),
    IDENTITY_LINK_STATUS_REVOKED: CandidateVocabulary(
        candidate=IDENTITY_LINK_STATUS_CANDIDATE,
        accepted=IDENTITY_LINK_STATUS_CONFIRMED,
        dismissed=IDENTITY_LINK_STATUS_REVOKED,
    ),
}
#: 監査の action（theory_review_events.metadata.action）。
RESTORE_AUDIT_ACTION = "identity_link.restored"


def restore(
    link_id: str,
    *,
    restored_by: str,
    reason: str,
    audit_entity_type: str,
    record_audit: Any,
) -> dict | None:
    """見送った・確定を取り消した同一性リンクを候補（``candidate``）に戻す（IK-1062・人間のみ）。

    ``restored_by`` と ``reason`` は必須（空は ``ValueError``）。戻す前の判断の列
    （``decided_by`` / ``decided_at`` / ``revoked_*`` / ``revoke_reason``）は上書きせず、
    戻した人・日時・理由・戻す前の状態を ``restored_*`` に残す。行は削除しない（P4）。
    一意制約で同じ組の行を作り直せないので、これが否定の判断からの唯一の回復経路になる。
    対象が無い・否定の状態でない（候補・確定）なら ``None``（呼び出し側が 404 / 409）。

    ``record_audit`` は ``(entity_type, entity_id, old_status, new_status, actor_id, metadata)``
    の位置引数で呼ぶ（routes の ``record_review_event`` と同じ形）。遷移の検証 → 書き込み →
    監査の順は :class:`~core.candidate_flow.CandidateFlow` が持つ。
    """
    actor = str(restored_by or "").strip()
    if not actor:
        raise ValueError("restored_by is required")
    reason_text = str(reason or "").strip()
    if not reason_text:
        raise ValueError("reason is required")
    existing = get_by_id(link_id)
    if not existing:
        return None
    current = str(existing.get("status") or "")
    vocab = RESTORE_VOCABULARIES.get(current)
    if vocab is None:
        return None

    def _apply(*, entity_id, old_status, new_status, actor_id, reason, metadata):  # noqa: ARG001
        session = get_session()
        try:
            row = session.execute(
                sa_text(
                    f"""
                    UPDATE element_identity_links
                    SET status = :new_status,
                        restored_by = CAST(:restored_by AS uuid),
                        restored_at = now(),
                        restore_reason = :reason,
                        restored_from = :old_status,
                        updated_at = now()
                    WHERE id = CAST(:id AS uuid) AND status = :old_status
                    RETURNING {_COLUMNS_SQL}
                    """
                ),
                {
                    "id": entity_id,
                    "new_status": new_status,
                    "old_status": old_status,
                    "restored_by": actor_id,
                    "reason": reason[:500],
                },
            ).fetchone()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        if row is None:
            raise CandidateTransitionError("identity link changed before restore")
        return _row_to_dict(row)

    def _audit(*, entity_type, entity_id, action, old_status, new_status, actor_id, reason, metadata):  # noqa: ARG001
        record_audit(entity_type, entity_id, old_status, new_status, actor_id, dict(metadata or {}))

    flow = CandidateFlow(
        vocab=vocab,
        audit_entity_type=audit_entity_type,
        apply_status=_apply,
        record_audit=_audit,
    )
    try:
        result = flow.restore(
            link_id,
            current_status=current,
            actor_id=actor,
            reason=reason_text,
            metadata={
                "action": RESTORE_AUDIT_ACTION,
                "reason": reason_text[:500],
                "restored_from": current,
                "decided_by": existing.get("decided_by"),
                "revoked_by": existing.get("revoked_by"),
                "shared_part_id": existing.get("shared_part_id"),
            },
        )
    except CandidateTransitionError:
        return None
    return result.get("applied")


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


# ── 結んだ相手（インスタンス側）の読める表示（IK-0916 / IK-0917）─────────────────
#: インスタンス側の要素型 → 教員向けの呼び名。内部の型名（``theory_component`` 等）を
#: 画面に出さないための写像（同一性リンクの行専用。学習者向けの呼び名とは用途が違う）。
INSTANCE_TYPE_DISPLAY: dict[str, str] = {
    "theory_component": "部品",
    "theory_claim": "主張",
    "equation": "式",
    "figure": "図",
    "symbol": "記号",
    "theory_module": "理論モジュール",
}

#: AI・決定論の照合が作った行（確信度の段階に意味がある行）。手で作った行・人が判断した
#: 行には確信度の段階を付けない（IK-0916: 「暫定」と「確定」が並んで矛盾して読める）。
MACHINE_JUSTIFICATIONS = frozenset(
    {"llm_candidate", "vector_similarity", "lexical_match", "structural_match", "corpus_cooccurrence"}
)

#: 行の出所（人の判断の状態）の事実語。確信度の段階とは別の位置に描く。
PROVENANCE_CONFIRMED = "教員が確定"
PROVENANCE_REJECTED = "教員が却下"
PROVENANCE_REVOKED = "教員が確定を取り消し"
PROVENANCE_TEACHER_CREATED = "教員が作成（未確定）"
PROVENANCE_MACHINE_CANDIDATE = "照合による候補（未確定）"

#: 確定の直前に添える事実文（IK-0917: 判断材料を弁の位置に置く）。
CONFIRM_NOTE = (
    "確定すると、この 2 つを同じものとして扱います。学生の画面では、この部品の文脈の"
    "「共通部品」に左の共通部品が出ます。部品が共通部品を使う・満たすだけの関係なら、"
    "同じものではないので確定しないでください。"
)

_SUMMARY_LIMIT = 160
_IO_LIMIT = 3


def shows_confidence(link: dict) -> bool:
    """確信度の段階を描いてよい行か（AI・照合由来の未確定の行だけ）。"""
    return (
        str(link.get("status") or "") == IDENTITY_LINK_STATUS_CANDIDATE
        and str(link.get("mapping_justification") or "") in MACHINE_JUSTIFICATIONS
    )


def provenance_label(link: dict) -> str:
    status = str(link.get("status") or "")
    if status == "confirmed":
        return PROVENANCE_CONFIRMED
    if status == "rejected":
        return PROVENANCE_REJECTED
    if status == "revoked":
        return PROVENANCE_REVOKED
    if str(link.get("mapping_justification") or "") in MACHINE_JUSTIFICATIONS:
        return PROVENANCE_MACHINE_CANDIDATE
    return PROVENANCE_TEACHER_CREATED


def _io_texts(value: Any) -> list[str]:
    items = value if isinstance(value, list) else []
    texts: list[str] = []
    for item in items:
        text = ""
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("name") or item.get("label") or "").strip()
        elif isinstance(item, str):
            text = item.strip()
        if text:
            texts.append(text[:60])
        if len(texts) >= _IO_LIMIT:
            break
    return texts


def describe_instance(link: dict) -> dict[str, Any]:
    """リンクのインスタンス側を読める形で返す（fail-soft・取れないキーは空文字）。

    戻り値: ``instance_type_label`` / ``instance_label`` / ``instance_summary`` /
    ``instance_inputs`` / ``instance_outputs`` / ``instance_document_title``。
    内部 ID・UUID は返さない（UI は title 属性に生 ID を残すだけ）。
    """
    element_type = str(link.get("instance_element_type") or "")
    element_id = str(link.get("instance_element_id") or "")
    document_id = str(link.get("instance_document_id") or "")
    out: dict[str, Any] = {
        "instance_type_label": INSTANCE_TYPE_DISPLAY.get(element_type, "要素"),
        "instance_label": "",
        "instance_summary": "",
        "instance_inputs": [],
        "instance_outputs": [],
        "instance_document_title": "",
    }
    session = None
    try:
        session = get_session()
        if document_id:
            row = session.execute(
                sa_text(
                    "SELECT d.title, d.filename, d.source_url, "
                    "(SELECT count(*) FROM documents o WHERE o.title = d.title) "
                    "FROM documents d WHERE d.id = CAST(:id AS uuid)"
                ),
                {"id": document_id},
            ).fetchone()
            if row and row[0]:
                out["instance_document_title"] = str(row[0])
                # IK-1026: 対の確認の問いに版（TeX / PDF）を言うため、形式だけは常に添える（ファイル名は出さない）。
                fmt = document_format_label(row[1], row[2])
                if fmt:
                    out["instance_document_format_label"] = fmt
                # IK-0956: 同じ題名の論文が 2 本以上あるときだけ、どの版かの手がかりを添える。
                if len(row) > 3 and int(row[3] or 0) > 1:
                    hint = document_version_hint(row[1], row[2])
                    if hint:
                        out["instance_document_version_label"] = hint
        if element_type == "theory_component":
            row = session.execute(
                sa_text(
                    "SELECT name, summary, inputs, outputs, component_type FROM theory_components_live "
                    "WHERE id = CAST(:id AS uuid) LIMIT 1"
                ),
                {"id": element_id},
            ).fetchone()
            if row:
                out["instance_label"] = str(row[0] or "")
                out["instance_summary"] = str(row[1] or "")[:_SUMMARY_LIMIT]
                out["instance_inputs"] = _io_texts(row[2])
                out["instance_outputs"] = _io_texts(row[3])
                # IK-0917: 粒度の照合用（内部キー。route が granularity_note に使い、応答からは落とす）。
                if len(row) > 4:
                    out["_component_type"] = str(row[4] or "")
            else:
                # IK-1012 / IK-1020: 再解析で置き換わり、新しい部品へ付け替えられなかった記録（消さずに言う）。
                from core.knowledge_objects.remap import STRANDED_RECORD_NOTE

                out["instance_stranded_note"] = STRANDED_RECORD_NOTE
        elif element_type == "theory_claim":
            row = session.execute(
                sa_text("SELECT text FROM theory_claims_live WHERE id = CAST(:id AS uuid) LIMIT 1"),
                {"id": element_id},
            ).fetchone()
            if row:
                out["instance_label"] = str(row[0] or "")[:_SUMMARY_LIMIT]
        elif element_type == "figure":
            row = session.execute(
                sa_text(
                    "SELECT figure_label, caption_text FROM document_figures "
                    "WHERE id = CAST(:id AS uuid) LIMIT 1"
                ),
                {"id": element_id},
            ).fetchone()
            if row:
                out["instance_label"] = str(row[0] or "")
                out["instance_summary"] = str(row[1] or "")[:_SUMMARY_LIMIT]
        elif element_type == "symbol":
            # IK-0983: 記号は名前（表記）と定義の文を並べて、教員が概念との対応を判断できるように。
            row = session.execute(
                sa_text(
                    "SELECT canonical_symbol, definition_evidence_texts FROM knowledge_symbols_live "
                    "WHERE document_id = CAST(:doc AS uuid) AND agent_symbol_id = :id LIMIT 1"
                ),
                {"doc": document_id, "id": element_id},
            ).fetchone()
            if row:
                out["instance_label"] = str(row[0] or "")
                texts = row[1] if isinstance(row[1], list) else []
                first = next((str(t).strip() for t in texts if isinstance(t, str) and str(t).strip()), "")
                out["instance_summary"] = first[:_SUMMARY_LIMIT]
        elif element_type == "theory_module":
            row = session.execute(
                sa_text(
                    "SELECT label FROM knowledge_theory_modules_live "
                    "WHERE id = CAST(:id AS uuid) LIMIT 1"
                ),
                {"id": element_id},
            ).fetchone()
            if row:
                out["instance_label"] = str(row[0] or "")
            else:
                # IK-1076: 理論モジュールも component と同じく、置き換わった行の記録は事実文で言う。
                from core.knowledge_objects.remap import STRANDED_RECORD_NOTE

                out["instance_stranded_note"] = STRANDED_RECORD_NOTE
    except Exception:  # noqa: BLE001 — 表示の補助。取れなければ空のまま（fail-soft）
        if session is not None:
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
    finally:
        if session is not None:
            session.close()
    if not out["instance_label"] and element_type == "equation" and document_id:
        try:
            from core.deliberation import labels as _labels
            from core.deliberation.refs import equation_records

            for record in equation_records(document_id):
                if str(record.get("equation_id") or "") == element_id:
                    out["instance_label"] = _labels.equation_label(record).text
                    break
        except Exception:  # noqa: BLE001
            pass
    if not out["instance_label"] and element_type == "symbol":
        canonical = ""
        local = link.get("local_expression") if isinstance(link.get("local_expression"), dict) else {}
        canonical = str(local.get("notation") or local.get("label") or "")
        out["instance_label"] = canonical
    return out


# ── 誰が作り・確定し・取り消したか（IK-0953）────────────────────────────────────────
#: 作成者と確定者が同じ人のときの事実文（自己確定は禁じない — 事実として並べる）。
SELF_CONFIRMED_NOTE = "作成者が確定しました。"
#: 取り消しの操作の前に添える事実文（JS にミラーしない）。
REVOKE_NOTE = (
    "取り消すと、この結び付きは確定ではなくなり、学生の画面の「共通部品」から消えます。"
    "記録（誰が確定し、誰がいつ・なぜ取り消したか）は残ります。理由は必須です。"
)


def describe_actors(link: dict, *, session: Any | None = None) -> dict[str, Any]:
    """リンクの作成者・確定者・取り消した人を表示名で返す（fail-soft・UUID は返さない）。

    戻り値のキー: ``created_by_label`` / ``decided_by_label`` / ``revoked_by_label``（引けない
    ときは「表示名を確認できない利用者」、記録が無いときは空文字）と、作成者 = 確定者の
    ときの ``self_confirmed_note``。
    """
    from core.atlas_review_display import DECISION_ACTOR_UNKNOWN, fetch_actor_labels

    keys = ("created_by", "decided_by", "revoked_by", "restored_by")
    ids = [str(link.get(k) or "").strip() for k in keys]
    own = session is None
    labels: dict[str, str] = {}
    try:
        if own:
            session = get_session()
        labels = fetch_actor_labels(session, [i for i in ids if i])
    except Exception:  # noqa: BLE001 — 表示の補助（fail-soft）
        labels = {}
    finally:
        if own and session is not None:
            session.close()
    out: dict[str, Any] = {}
    for key, user_id in zip(keys, ids):
        out[f"{key}_label"] = (labels.get(user_id) or DECISION_ACTOR_UNKNOWN) if user_id else ""
    status = str(link.get("status") or "")
    # IK-0953（一覧の印）: 判断の行の 1 文（印・判断した人・日付）。日付は日まで（時刻は出さない）。
    line = decision_line(status, out.get("decided_by_label") or "", link.get("decided_at"))
    if line:
        out["decision_line"] = line
        out["decided_on"] = _date_only(link.get("decided_at"))
        # IK-1123: 札と同じ行に並べる画面のための印の無い形（語の正本は decision_line のまま）。
        out["decision_actor_line"] = decision_line(
            status, out.get("decided_by_label") or "", link.get("decided_at"), with_mark=False,
        )
    if (
        status in (IDENTITY_LINK_STATUS_CONFIRMED, IDENTITY_LINK_STATUS_REVOKED)
        and ids[0]
        and ids[0] == ids[1]
    ):
        out["self_confirmed_note"] = SELF_CONFIRMED_NOTE
    # IK-1050: 見送った行の理由（判断の印の後ろに出す 1 行）。
    reason_line = decision_reason_line(status, link.get("revoke_reason"))
    if reason_line:
        out["decision_reason_line"] = reason_line
    # IK-0953 の残り（第 7 波）: 取り消しの行に日時と、元の確定（誰が・いつ）を残す。
    if status == IDENTITY_LINK_STATUS_REVOKED:
        out["revoke_line"] = revoke_line(
            out.get("revoked_by_label") or "",
            link.get("revoked_at"),
            out.get("decided_by_label") or "",
            link.get("decided_at"),
        )
        # IK-1123 の残り: 札（status_label）と同じ行に並べる画面のための印の無い形。
        out["decision_actor_line"] = revoke_line(
            out.get("revoked_by_label") or "",
            link.get("revoked_at"),
            out.get("decided_by_label") or "",
            link.get("decided_at"),
            with_mark=False,
        )
    # IK-1062: 否定の判断から候補に戻した行は、戻した人・日付・理由と、戻す前の判断を残す。
    if status == IDENTITY_LINK_STATUS_CANDIDATE and link.get("restored_at"):
        out["restore_line"] = restore_line(
            out.get("restored_by_label") or "", link.get("restored_at"), link.get("restored_from"),
        )
        reason_line = restore_reason_line(link.get("restore_reason"))
        if reason_line:
            out["restore_reason_line"] = reason_line
        # IK-1062 の残り（第 10 波）: 戻す前の判断の誰・いつ・なぜを戻した行にも残す。
        prior = prior_decision_line(
            link.get("restored_from"),
            decided_by_label=out.get("decided_by_label") or "",
            decided_at=link.get("decided_at"),
            revoked_by_label=out.get("revoked_by_label") or "",
            revoked_at=link.get("revoked_at"),
            reason=link.get("revoke_reason"),
        )
        if prior:
            out["prior_decision_line"] = prior
    if status in RESTORE_VOCABULARIES:
        out["restore_note"] = RESTORE_NOTE
    out["status_label"] = IDENTITY_LINK_STATUS_LABELS.get(status, "")
    return out



# ── 版の手がかり（IK-0956）と粒度の確認（IK-0917）──────────────────────────────────
_TEX_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".tex", ".zip", ".gz")


def document_version_hint(filename: Any, source_url: Any = None) -> str:
    """同じ題名の論文のどの版か（取り込み元の形式とファイル名）を短く言う（純関数）。

    例: ``TeX 版・arXiv-2605.31198v1.tar.gz`` / ``PDF 版・paper.pdf``。形式もファイル名も
    分からなければ空文字。
    """
    name = str(filename or "").strip()
    fmt = document_format_label(filename, source_url)
    parts = [p for p in (fmt, name) if p]
    return "・".join(parts)


def document_format_label(filename: Any, source_url: Any = None) -> str:
    """取り込み元の形式だけ（``TeX 版`` / ``PDF 版`` / 空文字）。学生向けはこちらを使う。"""
    lower = str(filename or "").strip().lower()
    url = str(source_url or "").strip().lower()
    if lower.endswith(".pdf") or "/pdf/" in url:
        return "PDF 版"
    if lower.endswith(_TEX_SUFFIXES) or "/src/" in url or "/e-print/" in url:
        return "TeX 版"
    return ""


#: IK-0953: 判断の行の印（一覧・詳細で同じ語）。
DECISION_MARKS = {
    IDENTITY_LINK_STATUS_CONFIRMED: "確定",
    IDENTITY_LINK_STATUS_REJECTED: "見送り",
    IDENTITY_LINK_STATUS_REVOKED: "取り消し済み",
}
#: IK-0953: リンクの判断の後の知らせ（JS にミラーしない）。
LINK_CONFIRMED_FACT = (
    "結び付きを確定しました（{line}）。学生の「共通部品として」に出るのは、この共通部品に凍結した版が"
    "あるときです。取り消すときは、エントリの詳細の［確定を取り消す］から理由を書いて取り消します。"
)
LINK_REJECTED_FACT = (
    "結び付きを見送りました（{line}）。行は理由とともに残り、同じ組は繰り返し提案されません。"
    "判断を誤ったときは、行の［候補に戻す］から理由を書いて候補に戻せます。"
)
#: IK-1062: 候補に戻した直後の知らせ。
LINK_RESTORED_FACT = (
    "結び付きを候補に戻しました（{line}）。前の判断の記録（誰が・いつ・なぜ）は残っています。"
    "両側の意味を見比べて、改めて確定するか見送ってください。"
)
#: IK-1062: 見送った・取り消した行の［候補に戻す］の前に添える事実文（JS にミラーしない）。
RESTORE_NOTE = (
    "候補に戻すと、この組をもう一度確定・見送りできるようになります。前の判断の記録は残り、"
    "戻した人・日時・理由も記録されます。理由は必須です。学生の画面は、改めて確定するまで変わりません。"
)
#: 戻す前の判断の呼び名（restore_line）。
_RESTORED_FROM_LABELS = {
    IDENTITY_LINK_STATUS_REJECTED: "見送り",
    IDENTITY_LINK_STATUS_REVOKED: "確定の取り消し",
}


def restore_reason_line(reason: Any) -> str:
    """候補に戻した理由の 1 行（理由が無ければ空文字）。"""
    text = str(reason or "").strip()
    return f"候補に戻した理由: {text}" if text else ""


def restore_line(restored_by_label: str, restored_at: Any, restored_from: Any) -> str:
    """候補に戻した行の 1 文（``〔候補に戻した〕 戻した: 名前・日付（前の判断: 見送り）``）。"""
    who = restored_by_label or "記録なし"
    date = _date_only(restored_at)
    head = f"〔候補に戻した〕 戻した: {who}" + (f"・{date}" if date else "")
    origin = _RESTORED_FROM_LABELS.get(str(restored_from or ""), "")
    return head + (f"（前の判断: {origin}）" if origin else "")


def _date_only(value: Any) -> str:
    text = value.isoformat() if hasattr(value, "isoformat") else str(value or "")
    return text[:10]


def decision_line(status: Any, actor_label: str, decided_at: Any, *, with_mark: bool = True) -> str:
    """判断の行の 1 文（``〔確定〕 確定: 名前・2026-10-27``）。判断していない行は空文字。

    ``with_mark=False``（IK-1123）は印「〔…〕」を付けない形（``見送り: 名前・日付``）。状態の札が
    同じ行に既にある画面（詳細の結びついた部品・記号）で、同じ語が札と印に重ならないようにする。
    """
    mark = DECISION_MARKS.get(str(status or ""))
    if not mark or str(status or "") == IDENTITY_LINK_STATUS_REVOKED:
        # 取り消しは取り消した人の行（revoked_by_label）が別にある。ここでは確定・見送りだけ。
        return ""
    verb = "確定" if str(status) == IDENTITY_LINK_STATUS_CONFIRMED else "見送り"
    who = actor_label or "記録なし"
    date = _date_only(decided_at)
    head = f"〔{mark}〕 " if with_mark else ""
    return f"{head}{verb}: {who}" + (f"・{date}" if date else "")


def prior_decision_line(
    restored_from: Any,
    *,
    decided_by_label: str,
    decided_at: Any,
    revoked_by_label: str = "",
    revoked_at: Any = None,
    reason: Any = None,
) -> str:
    """候補に戻した行に残る、戻す前の判断の記録の 1 行（IK-1062 の残り・第 10 波）。

    戻しても前の判断の列（``decided_*`` / ``revoked_*`` / ``revoke_reason``）は上書きしない
    ので、誰が・いつ・なぜ判断したかをそのまま言う。理由の無い旧い行は「記録なし」と言う。
    例: ``前の判断の記録: 見送り: 西川 智也・2026-10-27（見送りの理由: 記録なし（…））``。
    """
    origin = str(restored_from or "")
    text = str(reason or "").strip()
    if origin == IDENTITY_LINK_STATUS_REJECTED:
        head = decision_line(IDENTITY_LINK_STATUS_REJECTED, decided_by_label, decided_at, with_mark=False)
        why = decision_reason_line(IDENTITY_LINK_STATUS_REJECTED, text)
        return f"前の判断の記録: {head}（{why}）"
    if origin == IDENTITY_LINK_STATUS_REVOKED:
        head = revoke_line(revoked_by_label, revoked_at, decided_by_label, decided_at)
        head = head.replace("〔取り消し済み〕 ", "", 1)
        why = f"取り消しの理由: {text}" if text else "取り消しの理由: 記録なし"
        return f"前の判断の記録: {head}（{why}）"
    return ""


#: IK-1050: リンクの状態の訳語（一覧の札・詳細・W層で同じ語。JS の表はこの値の素通しの代わり）。
IDENTITY_LINK_STATUS_LABELS = {
    IDENTITY_LINK_STATUS_CANDIDATE: "候補",
    IDENTITY_LINK_STATUS_CONFIRMED: "確定",
    IDENTITY_LINK_STATUS_REJECTED: "見送り",
    IDENTITY_LINK_STATUS_REVOKED: "確定を取り消し",
}


def decision_reason_line(status: Any, reason: Any) -> str:
    """見送った行の理由の 1 行（``見送りの理由: …``）。理由が無い旧い行は「記録なし」と言う。"""
    if str(status or "") != IDENTITY_LINK_STATUS_REJECTED:
        return ""
    text = str(reason or "").strip()
    return f"見送りの理由: {text}" if text else "見送りの理由: 記録なし（理由を取る前に見送られた行です）"


def revoke_line(
    revoked_by_label: str, revoked_at: Any, confirmed_by_label: str, confirmed_at: Any,
    *, with_mark: bool = True,
) -> str:
    """取り消した行の 1 文（取り消した人・日付と、元の確定の人・日付）。

    例: ``〔取り消し済み〕 取り消し: 西川 智也・2026-10-27（元の確定: 岡村 千尋・2026-10-26）``。
    ``with_mark=False``（IK-1123 の残り）は印「〔取り消し済み〕」を付けない形（札と同じ行の画面）。
    """
    who = revoked_by_label or "記録なし"
    date = _date_only(revoked_at)
    mark = f"〔{DECISION_MARKS[IDENTITY_LINK_STATUS_REVOKED]}〕 " if with_mark else ""
    head = f"{mark}取り消し: {who}" + (f"・{date}" if date else "")
    origin_who = confirmed_by_label or "記録なし"
    origin_date = _date_only(confirmed_at)
    return head + f"（元の確定: {origin_who}" + (f"・{origin_date}" if origin_date else "") + "）"


def decision_outcome_fact(status: Any, line: str, *, instance_element_type: Any = None) -> str:
    """確定・見送りの直後の知らせ（判断した人と日付を含む）。

    IK-1152: 記号のリンクの確定は、学生の記号の吹き出しの概念名に出る（部品の「共通部品として」ではない）。
    """
    if str(status or "") == IDENTITY_LINK_STATUS_CONFIRMED:
        if str(instance_element_type or "") == "symbol":
            return SYMBOL_LINK_CONFIRMED_FACT.format(line=line or "確定")
        return LINK_CONFIRMED_FACT.format(line=line or "確定")
    if str(status or "") == IDENTITY_LINK_STATUS_REJECTED:
        return LINK_REJECTED_FACT.format(line=line or "見送り")
    if str(status or "") == IDENTITY_LINK_STATUS_CANDIDATE and line:
        return LINK_RESTORED_FACT.format(line=line)
    return ""


# ── 判断の対象にできるか（IK-1076・第 9 波）──────────────────────────────────────────
#: 再解析で行が置き換わり得る要素型（live ビューに無ければ「置き換わった部品」）。
STRANDED_ELEMENT_TYPES = ("theory_component", "theory_module")


def instance_is_live(
    element_type: Any,
    element_id: Any,
    live_component_ids: set[str] | None,
    live_module_ids: set[str] | None = None,
) -> bool:
    """リンクのインスタンス側が live に実在するか（一覧と詳細が共有する判断可否の述語）。

    component / 理論モジュール以外の要素型はそのまま True（置き換わりの概念を持たない）。
    """
    kind = str(element_type or "")
    key = str(element_id or "")
    if kind == "theory_component":
        return key in (live_component_ids or set())
    if kind == "theory_module":
        return key in (live_module_ids or set())
    return True


#: 置き換わった部品に付いた記録の行に添える 1 文（判断のボタンの代わり。JS は素通し）。
LINK_UNDECIDABLE_STRANDED_NOTE = (
    "この行は判断の対象にしていません（付いている部品が解析のやり直しで置き換わったため、"
    "確認・見送り・候補に戻すはできません）。記録は消さずに残しています。"
)
#: 概念（共通部品）が見送られているときの 1 文（IK-0982 の見送り側）。
LINK_UNDECIDABLE_ENTRY_DISMISSED_NOTE = (
    "この概念は見送られているため、結び付きは判断の対象にしていません"
    "（概念を候補に戻すと判断できます）。記録は残しています。"
)
#: 判断の対象でない行への確定・候補に戻すの 409（日本語の事実文）。
LINK_UNDECIDABLE_CONFLICT = "この結び付きは判断の対象ではありません。"


def link_undecidable_note(link_response: dict, *, entry_review_status: Any = None) -> str:
    """判断の対象でない理由の 1 文（判断できるなら空文字）。

    置き換わった部品の記録（``instance_stranded_note`` — :func:`describe_instance` が live に
    無い component / 理論モジュールに付ける）と、見送った概念に付いた行が対象。
    """
    if str(link_response.get("instance_stranded_note") or "").strip():
        return LINK_UNDECIDABLE_STRANDED_NOTE
    if str(entry_review_status or "") == "dismissed":
        return LINK_UNDECIDABLE_ENTRY_DISMISSED_NOTE
    return ""


# ── 対の確認の問い（IK-1026）と、片側が置き換わった候補（IK-1020）──────────────────────────
#: 片側の部品が再解析で置き換わり、組の相手が残っていない候補の行の 1 文（根拠の文の代わりに出す）。
PARTNER_REPLACED_REASON = (
    "組になっていた相手の部品は、解析のやり直しで置き換わりました。"
    "この候補が立ったときの根拠は、相手が残っていないため確かめられません。"
)
#: IK-0980: 文の近さで立った候補の根拠の保存値（「別の論文の記述と…」）は、組の相手が同じ論文の
#: 別の版のとき事実と食い違う。読み時にこの文へ言い換える（保存値は recorded_reason に残す）。
OTHER_PAPER_REASON_PREFIX = "別の論文の記述と"
SAME_PAPER_PROXY_REASON = (
    "同じ論文の別の版の記述と、本文の意味が近いと測定されました"
    "（別の版どうしを文の近さで結んだ、旧い候補づくりの規則で立った候補です）。"
)


def reworded_same_paper_reason(reason: Any, *, partner_is_same_paper: bool) -> str:
    """組の相手が同じ論文の別の版なら、「別の論文の…」の根拠の文を言い換える（それ以外は空文字）。"""
    if partner_is_same_paper and str(reason or "").startswith(OTHER_PAPER_REASON_PREFIX):
        return SAME_PAPER_PROXY_REASON
    return ""


#: 同じ候補のカードの 1 文（エントリ単位）。
PARTNER_REPLACED_NOTE = (
    "この候補は、解析のやり直しで片側の部品が置き換わったため、組の片方だけが残っています。"
    "相手の部品は再解析で置き換わりました。残った部品だけで同じものと言えなければ見送ってください。"
)


def instance_side_label(format_label: Any, document_title: Any) -> str:
    """部品の側（``TeX 版『題名』`` / ``『題名』``）。版も題名も無ければ空文字。"""
    fmt = str(format_label or "").strip()
    title = str(document_title or "").strip()
    titled = f"『{title}』" if title else ""
    return f"{fmt}{titled}" if (fmt or titled) else ""


def pair_confirm_question(
    *,
    instance_type_label: str,
    instance_label: str,
    instance_side: str,
    partners: list[tuple[str, str, str]],
    shared_part_name: str,
) -> str:
    """対の確認の問い（IK-1026）。比べる相手（版・論文題名・部品名）を名指しする。

    ``partners`` は ``[(種別, 名前, 側)]``（同じ共通部品に結び付いた、自分以外の行）。空なら
    :func:`confirm_question`（部品 ⇄ 共通部品）に戻す — 部品名の写しの共通部品と比べる同語反復を、
    相手が見えているときは作らない。
    """
    if not partners:
        return confirm_question(instance_type_label, instance_label, shared_part_name)

    def _named(type_label: str, label: str, side: str) -> str:
        head = f"{type_label or '要素'}「{label or '（名前を読めない要素）'}」"
        return head + (f"（{side}）" if side else "")

    left = _named(instance_type_label, instance_label, instance_side)
    rights = "・".join(_named(t, l, sd) for t, l, sd in partners[:2])
    return (
        f"{left}と{rights}は、同じものですか（一方が他方を使う・満たすだけではありませんか）。"
        f"確定すると、この行の部品を共通部品「{shared_part_name or '（名前の無いエントリ）'}」と同じものとして記録します。"
    )


def partner_replaced_question(instance_type_label: str, instance_label: str, instance_side: str, shared_part_name: str) -> str:
    """片側が置き換わった候補の確認の問い（IK-1020）。相手が無いことを先に言う。"""
    head = f"{instance_type_label or '要素'}「{instance_label or '（名前を読めない要素）'}」"
    head += f"（{instance_side}）" if instance_side else ""
    return (
        f"{head}と組になっていた相手の部品は、解析のやり直しで置き換わりました。"
        f"この部品だけを共通部品「{shared_part_name or '（名前の無いエントリ）'}」と同じものとして確定しますか"
        "（一方が他方を使う・満たすだけではありませんか）。"
    )


def confirm_question(instance_type_label: str, instance_label: str, shared_part_name: str) -> str:
    """確定の直前に問う 1 文（IK-0917: 両側を名指しして「同じものか」を問う）。"""
    left = f"{instance_type_label or '要素'}「{instance_label or '（名前を読めない要素）'}」"
    right = f"共通部品「{shared_part_name or '（名前の無いエントリ）'}」"
    return f"{left}と{right}は、同じものですか（一方が他方を使う・満たすだけではありませんか）。"


#: 両側の種別が違うときの一段強い確認の事実文（IK-0917）。
GRANULARITY_MISMATCH_NOTE = (
    "種別が違います（この部品は「{instance_kind}」、共通部品は「{shared_kind}」）。"
    "部品がその共通部品を使う・満たす関係を、同じものとして確定していないか確かめてください。"
)


def granularity_note(component_type: Any, shared_entry_type: Any) -> str:
    """部品の種別（component_type を entry_type に写したもの）と共通部品の種別が違えば事実文。

    部品以外（主張・式・記号など）や種別が分からないときは空文字（判定しない）。
    """
    from core.library import schema as library_schema

    ctype = str(component_type or "").strip()
    shared = str(shared_entry_type or "").strip()
    if not ctype or not shared:
        return ""
    instance_kind = library_schema.entry_type_for_component_type(ctype)
    if instance_kind == shared:
        return ""
    return GRANULARITY_MISMATCH_NOTE.format(
        instance_kind=library_schema.entry_type_label(instance_kind),
        shared_kind=library_schema.entry_type_label(shared),
    )
