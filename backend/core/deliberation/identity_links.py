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


#: 候補作成の結果の種類（IK-0918。IK-0897 のラベル追加と同じ作法）。新しく作った
#: ``created`` / 既にあった（候補のまま・確定済み・却下済み）を言い分ける。
LINK_OUTCOME_CREATED = "created"
LINK_OUTCOME_EXISTING_CANDIDATE = "existing_candidate"
LINK_OUTCOME_EXISTING_CONFIRMED = "existing_confirmed"
LINK_OUTCOME_EXISTING_REJECTED = "existing_rejected"
LINK_OUTCOMES = (
    LINK_OUTCOME_CREATED,
    LINK_OUTCOME_EXISTING_CANDIDATE,
    LINK_OUTCOME_EXISTING_CONFIRMED,
    LINK_OUTCOME_EXISTING_REJECTED,
)
_EXISTING_OUTCOME_BY_STATUS = {
    "candidate": LINK_OUTCOME_EXISTING_CANDIDATE,
    "confirmed": LINK_OUTCOME_EXISTING_CONFIRMED,
    "rejected": LINK_OUTCOME_EXISTING_REJECTED,
}

#: 結果の事実文（正本はサーバ。JS は素通しで描く）。
LINK_OUTCOME_FACTS = {
    LINK_OUTCOME_CREATED: "結び付きの候補を作成しました。上の一覧で両側の意味を見比べて確定・却下できます。",
    LINK_OUTCOME_EXISTING_CANDIDATE: "この部品と共通部品の結び付きは既に候補としてあります。新しくは作っていません。",
    LINK_OUTCOME_EXISTING_CONFIRMED: "この部品と共通部品の結び付きは既に確定済みです。新しくは作っていません。",
    LINK_OUTCOME_EXISTING_REJECTED: "この部品と共通部品の結び付きは以前に却下されています。新しくは作っていません（却下は取り消していません）。",
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
                sa_text("SELECT title FROM documents WHERE id = CAST(:id AS uuid)"),
                {"id": document_id},
            ).fetchone()
            if row and row[0]:
                out["instance_document_title"] = str(row[0])
        if element_type == "theory_component":
            row = session.execute(
                sa_text(
                    "SELECT name, summary, inputs, outputs FROM theory_components_live "
                    "WHERE id = CAST(:id AS uuid) LIMIT 1"
                ),
                {"id": element_id},
            ).fetchone()
            if row:
                out["instance_label"] = str(row[0] or "")
                out["instance_summary"] = str(row[1] or "")[:_SUMMARY_LIMIT]
                out["instance_inputs"] = _io_texts(row[2])
                out["instance_outputs"] = _io_texts(row[3])
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
