"""面①「内訳・同定」の組み立て（設計書 §3.1）。

resolve 済みの :class:`ElementRef` を受け、要素型ごとの内訳を **既存データの読み出しのみ**
（非LLM）で組み立てて JSON-serializable な dict を返す。W2/W4: 何も生成・確定しない。

各 build 関数は共通の外形を返す::

    {
        "element_type": str,
        "label": str,              # UI 見出し用の短いラベル
        "fields": dict,            # 要素型固有の内訳
        "notes": list[str],        # レビュー留意（例: apparatus 候補は review_required）
    }
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text as sa_text

from core import element_explanations as element_explanations_store
from core.postgres import get_session
from core.figure_presentation import presentation_payload
from core.document_pipeline.persistence import get_latest_analysis_run
from core.deliberation import identity_links as identity_links_mod
from core.deliberation import refs as refs_mod
from core.deliberation.schema import (
    ELEMENT_EQUATION,
    ELEMENT_FIGURE,
    ELEMENT_SHARED_PART,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_THEORY_COMPONENT,
    IDENTITY_LINK_STATUS_CONFIRMED,
    SCOPE_DOCUMENT,
    ElementRef,
    ElementResolutionError,
)

logger = logging.getLogger(__name__)

# migration 041 で theory_components.component_type CHECK に追加された装置系語彙。
_APPARATUS_TYPES = ("apparatus", "instrument", "part")


def _json(value: Any, default: Any) -> Any:
    return value if isinstance(value, type(default)) else default


# ---------------------------------------------------------------------------
# 要素説明（element_explanations, Phase 2 / migration 056）の生行取得
# ---------------------------------------------------------------------------
#
# overview（routes/deliberation.py）と dialogue grounding（dialogue.py）の双方が
# 同じ選定ロジック（candidate + approved のみ・dismissed/superseded は除外）を必要と
# するため、DB 読み出しをここに一本化する。呼び出し側が公開用の整形（confidence の
# 生値を段階ラベルへ変換する等）を行う（本関数は core.element_explanations の生行を
# そのまま返す）。


def _agent_id_candidates_for_focus(session: Any, ref: ElementRef) -> set[str]:
    """focus 要素の element_explanations 突合キー候補（``{ref.element_id}`` ∪ legacy_ids）。

    ギャップ（2026-07-19 確認済み）: ``_stage_contextual_explanation``
    （``document_pipeline/orchestrator.py`` の ``_PIPELINE_STEPS``）は
    ``persist_claims_components_graph`` より**前**に実行されるため、
    ``element_explanations.element_id`` は theory_component/theory_claim について
    **agent 側 ID**（``ComponentRecord.component_id`` /
    ``ClaimObjectRecord.claim_id``）で保存される
    （``contextual_explanation_inputs.py`` 冒頭 docstring に明記の既知の逸脱）。
    一方 ``ElementRef.element_id`` は常に DB UUID（``refs.py`` の
    ``_resolve_theory_component``/``_resolve_theory_claim`` が uuid 検証済みの id しか
    受け付けない）。figure（``document_figures.id``）と equation（artifact の
    ``equation_id``）はこの逸脱の対象外で、element_explanations にも常に正準 ID の
    ままなのでこの関数は素通りする。

    突合は focus 行自身（``ref.element_id`` の1行）の ``source_scope.legacy_ids`` を
    読んで行う。theory_components は ``legacy_ids = [component.component_id]``
    （persistence.py の ``persist_components``。F2 の source_scope マージ後も維持される
    契約）、theory_claims は ``legacy_ids = sorted(_claim_legacy_keys({"span_id": span_id}))``
    = ``{span_id, f"claim_{safe(span_id)}"}``（persistence.py の
    ``persist_qualified_claims``）で、``claim_{safe(span_id)}`` は
    ``ClaimObjectBuilder._make_claim_id`` が採番する agent 側 ``claim_id`` と同じ書式
    （``persistence.py`` の ``_rebuild_theory_claims_in_session`` 経由の行はさらに
    ``agent_id`` 自体も legacy_ids に積むため、そちらの経路でも解決できる）。この
    突合キーの流儀は ``context_lens.py`` の ``_component_id_lookup_from_rows`` /
    ``_claim_id_lookup_from_rows``（document 全体を索く版）と同一で、ここでは
    focus 行1件のみを直接引くだけで十分（呼び出し元は既に DB UUID を知っている）。

    DB 行が引けない・``legacy_ids`` が空・クエリ失敗（fail-soft: 例えば旧仕様の
    フェイクセッションのように ``execute`` を持たない場合も含む）は
    ``{ref.element_id}`` のみを返す（= 従来どおりの完全一致にフォールバックする）。
    """
    match_ids = {str(ref.element_id)}
    if ref.element_type == ELEMENT_THEORY_COMPONENT:
        sql = "SELECT source_scope FROM theory_components WHERE id = CAST(:id AS uuid) LIMIT 1"
    elif ref.element_type == ELEMENT_THEORY_CLAIM:
        sql = "SELECT source_scope FROM theory_claims WHERE id = CAST(:id AS uuid) LIMIT 1"
    else:
        # figure / equation / shared_part: element_explanations は既に正準 ID なので
        # legacy_ids 展開は不要（呼び出し元でも shared_part は既に scope ガードで弾かれる）。
        return match_ids
    try:
        row = session.execute(sa_text(sql), {"id": ref.element_id}).fetchone()
    except Exception:  # noqa: BLE001
        logger.warning(
            "decomposition: legacy id lookup failed for %s:%s",
            ref.element_type, ref.element_id, exc_info=True,
        )
        return match_ids
    if not row:
        return match_ids
    scope = row[0] if isinstance(row[0], dict) else {}
    for legacy_id in scope.get("legacy_ids") or []:
        key = str(legacy_id or "").strip()
        if key:
            match_ids.add(key)
    return match_ids


def explanations_for_element(ref: ElementRef) -> list[dict[str, Any]]:
    """focus 要素の element_explanations 生行を返す（candidate + approved のみ）。

    document-scoped 要素のみ対象（``element_explanations.element_type`` の語彙に
    shared_part は無いため、domain-scoped 要素は常に空リスト）。dismissed/superseded は
    ``status`` を明示指定した2回の問い合わせで最初から取得しない（P4: 除外であって
    削除ではない — 行自体は DB に残る。ここで返さないだけ）。

    突合は ``ref.element_id`` の完全一致に加え、theory_component/theory_claim は
    agent 側 ID（保存時の実際の element_id 形式）も候補に含める
    （:func:`_agent_id_candidates_for_focus` 参照。write 側の保存形式は変更しない —
    agent 側 ID は再解析を跨いで決定論的なため、保存形式としてはむしろ安定している）。
    """
    if ref.scope != SCOPE_DOCUMENT or not str(ref.document_id or "").strip():
        return []
    session = get_session()
    try:
        candidate_rows = element_explanations_store.list_for_document(
            session, ref.document_id, element_type=ref.element_type,
            status=element_explanations_store.STATUS_CANDIDATE,
        )
        approved_rows = element_explanations_store.list_for_document(
            session, ref.document_id, element_type=ref.element_type,
            status=element_explanations_store.STATUS_APPROVED,
        )
        match_ids = _agent_id_candidates_for_focus(session, ref)
    finally:
        session.close()
    rows = [
        r for r in candidate_rows + approved_rows
        if str(r.get("element_id") or "") in match_ids
    ]
    rows.sort(
        key=lambda r: (
            0 if r.get("status") == element_explanations_store.STATUS_APPROVED else 1,
            str(r.get("kind") or ""),
            str(r.get("created_at") or ""),
        )
    )
    return rows


def _decompose_theory_claim(ref: ElementRef) -> dict[str, Any]:
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT claim_type, text, normalized_text, concepts, equation,
                       support_status, evidence_text, review_status
                FROM theory_claims WHERE id = CAST(:id AS uuid) LIMIT 1
                """
            ),
            {"id": ref.element_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        raise ElementResolutionError(f"theory_claim not found: {ref.element_id}", kind="not_found")
    equation = _json(row[4], {})
    notes: list[str] = []
    if str(row[5] or "") != "source_backed":
        notes.append(f"support_status={row[5]}（source_backed でない）")
    return {
        "element_type": ELEMENT_THEORY_CLAIM,
        "label": (str(row[1] or "")[:80] or "claim"),
        "fields": {
            "claim_type": str(row[0] or ""),
            "text": str(row[1] or ""),
            "normalized_text": str(row[2] or ""),
            "concepts": _json(row[3], []),
            "equation": equation,
            "support_status": str(row[5] or ""),
            "evidence_text": str(row[6] or ""),
            "review_status": str(row[7] or ""),
            "has_equation": bool(equation),
        },
        "notes": notes,
    }


def _graph_node_for_component(document_id: str, component_id: str) -> dict[str, Any]:
    """theory_component_graphs.graph_json から当該 component の node を1つ探す（best-effort）。"""
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT graph_json FROM theory_component_graphs
                WHERE document_id = :document_id
                ORDER BY updated_at DESC LIMIT 1
                """
            ),
            {"document_id": document_id},
        ).fetchone()
    finally:
        session.close()
    graph = _json(row[0], {}) if row else {}
    nodes = _json(graph.get("nodes"), []) if isinstance(graph, dict) else []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if component_id in (
            str(node.get("component_id") or ""),
            str(node.get("id") or ""),
            str(node.get("node_id") or ""),
        ):
            return {
                "graph_layer": node.get("graph_layer"),
                "component_type": node.get("component_type"),
                "stage": node.get("stage"),
                "label": node.get("label"),
                "description": node.get("description"),
                "source_backing_status": node.get("source_backing_status"),
                "review_status": node.get("review_status"),
                "review_reasons": _json(node.get("review_reasons"), []),
                "member_component_ids": _json(node.get("member_component_ids"), []),
                "parent_component_id": node.get("parent_component_id"),
            }
    return {}


def _decompose_theory_component(ref: ElementRef) -> dict[str, Any]:
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT name, component_type, summary, status,
                       inputs, outputs, preconditions, constraints, dependencies
                FROM theory_components WHERE id = CAST(:id AS uuid) LIMIT 1
                """
            ),
            {"id": ref.element_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        raise ElementResolutionError(
            f"theory_component not found: {ref.element_id}", kind="not_found"
        )
    graph_node = _graph_node_for_component(ref.document_id or "", ref.element_id)
    notes: list[str] = []
    if str(row[3] or "") != "teacher_reviewed":
        notes.append(f"status={row[3]}（未承認）")
    if graph_node.get("review_reasons"):
        notes.append("グラフ上に review_reasons あり")
    return {
        "element_type": ELEMENT_THEORY_COMPONENT,
        "label": str(row[0] or "component"),
        "fields": {
            "name": str(row[0] or ""),
            "component_type": str(row[1] or ""),
            "summary": str(row[2] or ""),
            "status": str(row[3] or ""),
            "inputs": _json(row[4], []),
            "outputs": _json(row[5], []),
            "preconditions": _json(row[6], []),
            "constraints": _json(row[7], []),
            "dependencies": _json(row[8], []),
            "graph_node": graph_node,
        },
        "notes": notes,
    }


def _decompose_figure(ref: ElementRef) -> dict[str, Any]:
    session = get_session()
    try:
        fig = session.execute(
            sa_text(
                """
                SELECT figure_label, caption_text, page, status,
                       extraction_method, caption_block_id, document_id, figure_key,
                       suggested_mode, reviewed_mode, mode_reason, mode_review_status,
                       analysis_profile, bbox, inner_labels,
                       reviewed_analysis_mode, reviewed_analysis_profile,
                       analysis_review_status, analysis_reviewed_by::text,
                       analysis_reviewed_at, analysis_review_source_annotation_id::text
                FROM document_figures WHERE id = CAST(:id AS uuid) LIMIT 1
                """
            ),
            {"id": ref.element_id},
        ).mappings().first()
        # 装置・部品候補（apparatus_semantics → ComponentAssembly 経由の候補コンポーネント）。
        apparatus_rows = session.execute(
            sa_text(
                """
                SELECT id, name, component_type, status, summary
                FROM theory_components
                WHERE source_scope->>'document_id' = :document_id
                  AND component_type IN ('apparatus','instrument','part')
                ORDER BY created_at ASC
                """
            ),
            {"document_id": ref.document_id},
        ).fetchall()
    finally:
        session.close()
    if not fig:
        raise ElementResolutionError(f"figure not found: {ref.element_id}", kind="not_found")
    apparatus = [
        {
            "id": str(r[0]),
            "name": str(r[1] or ""),
            "component_type": str(r[2] or ""),
            "status": str(r[3] or ""),
            "summary": str(r[4] or ""),
        }
        for r in apparatus_rows
    ]
    notes: list[str] = []
    if apparatus:
        notes.append("装置・部品候補は review_required（確定は人間・L層）")
    if not fig.get("caption_text"):
        notes.append("caption 対応なし（caption_block_id=NULL でも保持・P4）")
    artifact_record: dict[str, Any] = {}
    try:
        latest_run = get_latest_analysis_run(document_id=ref.document_id or "")
        artifacts = (((latest_run or {}).get("stage_outputs") or {}).get("_artifacts") or {})
        for record in (artifacts.get("apparatus_semantics") or {}).get("apparatus_records") or []:
            if isinstance(record, dict) and str(record.get("figure_id") or "") == ref.element_id:
                artifact_record = record
                break
    except Exception:
        # Old/no run is expected; persisted columns and an unknown profile are
        # sufficient for a useful, fail-soft overview.
        artifact_record = {}
    presentation = presentation_payload(
        dict(fig), artifact_record, caption_text=str(fig.get("caption_text") or "")
    )
    document_id = str(fig.get("document_id") or ref.document_id or "")
    return {
        "element_type": ELEMENT_FIGURE,
        "label": str(
            fig.get("figure_label") or ref.provenance.get("figure_key") or "figure"
        ),
        "fields": {
            "document_id": document_id,
            "image_url": f"/api/admin/documents/{document_id}/figures/{ref.element_id}/image",
            "figure_label": str(fig.get("figure_label") or ""),
            "figure_key": str(
                fig.get("figure_key") or ref.provenance.get("figure_key") or ""
            ),
            "caption_text": str(fig.get("caption_text") or ""),
            "page": fig.get("page"),
            "status": str(fig.get("status") or ""),
            "extraction_method": str(fig.get("extraction_method") or ""),
            "bbox": _json(fig.get("bbox"), []),
            "inner_labels": _json(fig.get("inner_labels"), []),
            "apparatus_candidates": apparatus,
            **presentation,
        },
        "notes": notes,
    }


def _decompose_equation(ref: ElementRef) -> dict[str, Any]:
    # 独立テーブル無し（設計書 §2）。equation_semantics artifact から best-effort に読む。
    records = refs_mod.equation_records(ref.document_id or "")
    record: dict[str, Any] = {}
    for r in records:
        if str(r.get("equation_id") or "") == str(ref.element_id):
            record = r
            break
    src = _json(record.get("source_extraction"), {})
    rec = _json(record.get("reconstruction"), {})
    sem = _json(record.get("semantics"), {})
    latex = rec.get("latex") or src.get("latex")
    plain_text = rec.get("plain_text") or src.get("plain_text")
    return {
        "element_type": ELEMENT_EQUATION,
        "label": (str(plain_text or latex or ref.element_id)[:80]),
        "fields": {
            "equation_id": str(ref.element_id),
            "latex": latex,
            "plain_text": plain_text,
            "role_in_argument": sem.get("role_in_argument") or record.get("role_in_argument"),
            "input_equation_ids": _json(sem.get("input_equation_ids"), [])
            or _json(record.get("input_equation_ids"), []),
            "output_equation_ids": _json(sem.get("output_equation_ids"), [])
            or _json(record.get("output_equation_ids"), []),
            "needs_math_review": bool(src.get("needs_math_review")),
        },
        "notes": (
            ["equation は独立テーブル無し・artifact 由来の best-effort（設計書 §2）"]
            + (["数式は needs_math_review（表示用数式ではなく監査テキスト）"] if src.get("needs_math_review") else [])
        ),
    }


def _approved_contextual_explanation_for_instance(
    element_type: str, element_id: str, document_id: str
) -> str | None:
    """あるインスタンス要素の承認済み contextual 説明の本文（あれば1件）を返す
    （設計書 §6 shared_part 逆方向: 各インスタンスの approved contextual 説明）。
    複数 approved 行があっても最初の1件のみを使う（一覧表示の要約用途のため）。
    """
    if not str(document_id or "").strip():
        return None
    session = get_session()
    try:
        rows = element_explanations_store.list_for_document(
            session, document_id, element_type=element_type,
            status=element_explanations_store.STATUS_APPROVED,
            kind=element_explanations_store.KIND_CONTEXTUAL,
        )
    finally:
        session.close()
    for row in rows:
        if str(row.get("element_id") or "") == str(element_id):
            body = str(row.get("body") or "").strip()
            if body:
                return body
    return None


def _confirmed_linked_instances(shared_part_id: str) -> list[dict[str, Any]]:
    """shared_part へ confirmed な同一性リンクを持つインスタンス一覧（設計書 §6）。

    KN-2 継承: インスタンス側の表記は書き換えない（読み出すだけ）。candidate/rejected は
    対象にしない（KN-3: 確定済みのみを事実として扱う）。各インスタンスの由来 document の
    閲覧権限は本関数では判定できない（core は per-user 権限を持たない）ため、呼び出し側
    （routes/deliberation.py）が document_id で権限フィルタし hidden_count を算出する。
    """
    result: list[dict[str, Any]] = []
    for link in identity_links_mod.list_for_shared_part(shared_part_id):
        if str(link.get("status") or "") != IDENTITY_LINK_STATUS_CONFIRMED:
            continue
        instance_type = str(link.get("instance_element_type") or "")
        instance_id = str(link.get("instance_element_id") or "")
        instance_doc = str(link.get("instance_document_id") or "")
        result.append(
            {
                "element_type": instance_type,
                "element_id": instance_id,
                "document_id": instance_doc,
                "local_expression": link.get("local_expression") or {},
                "approved_contextual_explanation": _approved_contextual_explanation_for_instance(
                    instance_type, instance_id, instance_doc
                ),
            }
        )
    return result


def _decompose_shared_part(ref: ElementRef) -> dict[str, Any]:
    session = get_session()
    try:
        entry = session.execute(
            sa_text(
                """
                SELECT domain_key, entry_type, status, name, summary, standardization_status
                FROM library_entries WHERE id = CAST(:id AS uuid) LIMIT 1
                """
            ),
            {"id": ref.element_id},
        ).fetchone()
        # 凍結版本文（最新の frozen version）。retrieval が読むのは凍結版のみ（L層方針）なので、
        # 凍結版があればそのスナップショット（content JSONB = エントリ全体）を優先表示する。
        version = session.execute(
            sa_text(
                """
                SELECT content, version_no FROM library_entry_versions
                WHERE entry_id = CAST(:id AS uuid)
                ORDER BY version_no DESC LIMIT 1
                """
            ),
            {"id": ref.element_id},
        ).fetchone()
    finally:
        session.close()
    if not entry:
        raise ElementResolutionError(f"shared_part not found: {ref.element_id}", kind="not_found")
    notes: list[str] = []
    if str(entry[2] or "") == "retired":
        notes.append("retired（retrieval には出ない・P4 で保持）")
    frozen_content = _json(version[0], {}) if version else {}
    if not version:
        notes.append("凍結版なし（draft のみ。パイプライン retrieval には未反映）")
    name = str(entry[3] or "")

    # 設計書 §6 shared_part 逆方向: confirmed な同一性リンクを持つインスタンス一覧
    # + 各インスタンスの approved contextual 説明。付帯情報の取得失敗は shared_part
    # 本体の内訳表示を巻き込まない（fail-soft・既存の apparatus_semantics artifact
    # 読み出しと同じ try/except の作法）。
    linked_instances: list[dict[str, Any]] = []
    try:
        linked_instances = _confirmed_linked_instances(ref.element_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "decomposition: confirmed linked instances lookup failed for shared_part %s",
            ref.element_id, exc_info=True,
        )
        linked_instances = []

    return {
        "element_type": ELEMENT_SHARED_PART,
        "label": name or f"shared_part:{ref.element_id[:8]}",
        "fields": {
            "domain_key": str(entry[0] or ""),
            "entry_type": str(entry[1] or ""),
            "status": str(entry[2] or ""),
            "name": name,
            "summary": str(entry[4] or ""),
            # 標準化判定（Phase S）の確定状態。候補（element_annotations, kind='standardization',
            # status='candidate'）はここには出ない — 確定（commit）されて初めてこの列に反映される
            # （教員向け一覧は `GET .../annotations` を参照。§5.5・migration 050）。
            "standardization_status": str(entry[5] or "unknown"),
            "frozen_content": frozen_content,
            # 権限フィルタ前の生リスト。routes/deliberation.py が document 閲覧権限で
            # フィルタし、隠した件数を linked_instances_hidden_count として正直に返す
            # （W5・P4。ここでは全件を返す）。
            "linked_instances": linked_instances,
        },
        "notes": notes,
    }


_DISPATCH = {
    ELEMENT_THEORY_CLAIM: _decompose_theory_claim,
    ELEMENT_THEORY_COMPONENT: _decompose_theory_component,
    ELEMENT_FIGURE: _decompose_figure,
    ELEMENT_EQUATION: _decompose_equation,
    ELEMENT_SHARED_PART: _decompose_shared_part,
}


def build(ref: ElementRef) -> dict[str, Any]:
    """ElementRef の面①内訳を組み立てて返す。"""
    builder = _DISPATCH.get(ref.element_type)
    if builder is None:
        raise ElementResolutionError(
            f"unknown element_type: {ref.element_type!r}", kind="invalid"
        )
    return builder(ref)
