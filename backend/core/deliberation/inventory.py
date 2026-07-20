"""要素インベントリ（Element Inventory）の組み立て（設計書 §4〜§8）。

`docs/features/element_inventory_design.md` が正本。教材（document）単位で
``theory_component`` / ``theory_claim`` / ``equation`` / ``figure`` の4型を統一カード
（``ElementCard``）に整形し、W層の検討状況（注釈・対話セッション・同一性リンク）を合流した
一覧を返す。読み取り専用・非LLM・DB 書き込みゼロ（I1）。

``build(document_id)`` が DB 読み出し（``core.postgres.get_session`` 直・try/finally close、
既存 ``refs.py`` / ``decomposition.py`` と同じ流儀）を行い、実際のカード整形・並び順・
型別上限・検討状況の合流はすべて DB 非依存の純粋関数（``build_inventory`` とその内部の
``_*_card`` 群）に分離してある。fake rows（dict/tuple のリスト）だけで
``build_inventory`` を直接検証できる（``core/personal_graph/derive.py`` と同じテスト戦略、
設計書 §8）。

型別上限 ``_INVENTORY_MAX_PER_TYPE`` を超えた型は切り詰めたうえで ``truncated_types`` に
型名を入れて正直に報告する（I4）。equation の ``confidence`` はカードに含めない（I3。
他の型も confidence 系の生数値をカードに持ち出さない）。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from sqlalchemy import text as sa_text

from core.postgres import get_session
from core.deliberation import refs as refs_mod
from core.deliberation.schema import (
    ELEMENT_EQUATION,
    ELEMENT_FIGURE,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_THEORY_COMPONENT,
)

# 型別の安全弁（設計書 §5・§8）。1論文の現実的規模では到達しない想定で、到達した型は
# 切り詰めたうえで truncated_types に正直に報告する（I4）。
_INVENTORY_MAX_PER_TYPE = 500

# snippet の最大文字数（設計書 §4）。
_SNIPPET_MAX_CHARS = 300

# 型グループ順（設計書 §4「並び順」）。elements 配列はこの順でグループ化される。
_TYPE_ORDER = (
    ELEMENT_THEORY_COMPONENT,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_EQUATION,
    ELEMENT_FIGURE,
)


def _truncate(text: Any, limit: int = _SNIPPET_MAX_CHARS) -> str:
    return str(text or "").strip()[:limit]


def _zero_deliberation() -> dict[str, Any]:
    """検討状況の合流が無い要素の既定値（全部ゼロ。設計書 §4/§7）。"""
    return {
        "annotations": {"candidate": 0, "committed": 0, "dismissed": 0},
        "sessions": 0,
        "identity_links": {"candidate": 0, "confirmed": 0, "rejected": 0},
    }


# ---------------------------------------------------------------------------
# ElementCard 整形（型ごと。設計書 §4 の表）
# ---------------------------------------------------------------------------


def _theory_component_card(row: Mapping[str, Any]) -> dict[str, Any]:
    source_scope = row.get("source_scope")
    location = dict(source_scope) if isinstance(source_scope, dict) else {}
    return {
        "element_type": ELEMENT_THEORY_COMPONENT,
        "element_id": str(row.get("id") or ""),
        "label": str(row.get("name") or "").strip() or "component",
        "snippet": _truncate(row.get("summary")),
        "badges": {
            "component_type": str(row.get("component_type") or ""),
            "status": str(row.get("status") or ""),
            "review_status": str(row.get("review_status") or ""),
        },
        "location": location,
    }


def _theory_claim_card(row: Mapping[str, Any]) -> dict[str, Any]:
    text = str(row.get("text") or "")
    evidence_text = str(row.get("evidence_text") or "").strip()
    snippet_source = f"{text} {evidence_text}".strip() if evidence_text else text
    chunk_id = row.get("chunk_id")
    return {
        "element_type": ELEMENT_THEORY_CLAIM,
        "element_id": str(row.get("id") or ""),
        "label": text[:80].strip() or "claim",
        "snippet": _truncate(snippet_source),
        "badges": {
            "claim_type": str(row.get("claim_type") or ""),
            "support_status": str(row.get("support_status") or ""),
            "review_status": str(row.get("review_status") or ""),
        },
        "location": {"chunk_id": str(chunk_id) if chunk_id else None},
    }


def _equation_card(record: Mapping[str, Any]) -> dict[str, Any]:
    # equation は独立テーブルを持たない（equations.json 由来。設計書 §2）。
    # to_equations_export() のフラット形（label/latex/plain_text/source_location/
    # equation_type/semantic_status/extraction_status がトップレベル）を主として読み、
    # 念のため入れ子形（source_extraction/semantics 配下）にもフォールバックする。
    location = record.get("source_location")
    if not isinstance(location, dict):
        nested_src = record.get("source_extraction")
        location = (
            nested_src.get("source_location")
            if isinstance(nested_src, dict) and isinstance(nested_src.get("source_location"), dict)
            else {}
        )

    plain_text = record.get("plain_text")
    latex = record.get("latex")
    if plain_text is None and latex is None:
        nested_rec = record.get("reconstruction")
        nested_src = record.get("source_extraction")
        if isinstance(nested_rec, dict):
            plain_text = plain_text or nested_rec.get("plain_text")
            latex = latex or nested_rec.get("latex")
        if isinstance(nested_src, dict):
            plain_text = plain_text or nested_src.get("plain_text")
            latex = latex or nested_src.get("latex")

    sem = record.get("semantics") if isinstance(record.get("semantics"), dict) else {}
    equation_type = record.get("equation_type") or sem.get("equation_type")
    semantic_status = record.get("semantic_status") or sem.get("semantic_status")
    extraction_status = record.get("extraction_status")
    if not extraction_status:
        nested_src = record.get("source_extraction")
        if isinstance(nested_src, dict):
            extraction_status = nested_src.get("extraction_status")

    label = record.get("label") or record.get("equation_id") or "equation"
    return {
        "element_type": ELEMENT_EQUATION,
        "element_id": str(record.get("equation_id") or ""),
        "label": str(label)[:80],
        # I3: confidence はここに含めない（等値な生数値は他型にもそもそも選ばない）。
        "snippet": _truncate(plain_text if plain_text is not None else latex),
        "badges": {
            "equation_type": str(equation_type or ""),
            "semantic_status": str(semantic_status or ""),
            "extraction_status": str(extraction_status or ""),
        },
        "location": dict(location) if isinstance(location, dict) else {},
    }


def _figure_card(row: Mapping[str, Any]) -> dict[str, Any]:
    label = row.get("figure_label") or row.get("figure_key") or "figure"
    return {
        "element_type": ELEMENT_FIGURE,
        "element_id": str(row.get("id") or ""),
        "label": str(label),
        "snippet": _truncate(row.get("caption_text")),
        "badges": {
            "extraction_method": str(row.get("extraction_method") or ""),
            # 設計書 §4 の表は「match_status」を挙げるが、document_figures テーブル自体は
            # そのカラムを持たない（match_status は apparatus 候補側 = theory_components の
            # source_scope に付く値で、図単位の一覧にそのまま存在しない）。図自体の段階表示は
            # 実在カラムの status（'extracted'/'failed'）を使う（I5: 状態はそのまま出す）。
            "status": str(row.get("status") or ""),
        },
        "location": {"page": row.get("page")},
    }


# ---------------------------------------------------------------------------
# 検討状況の集約合流（設計書 §7）
# ---------------------------------------------------------------------------


def _build_deliberation_index(
    annotation_rows: Sequence[Sequence[Any]],
    session_rows: Sequence[Sequence[Any]],
    identity_rows: Sequence[Sequence[Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """3つの GROUP BY 結果（type, id[, status], count のタプル列）を
    ``(element_type, element_id) -> deliberation dict`` へ合流する。"""
    index: dict[tuple[str, str], dict[str, Any]] = {}

    def _entry(element_type: Any, element_id: Any) -> dict[str, Any]:
        key = (str(element_type), str(element_id))
        if key not in index:
            index[key] = _zero_deliberation()
        return index[key]

    for element_type, element_id, status, count in annotation_rows:
        entry = _entry(element_type, element_id)
        if status in entry["annotations"]:
            entry["annotations"][status] += int(count)

    for element_type, element_id, count in session_rows:
        entry = _entry(element_type, element_id)
        entry["sessions"] += int(count)

    for element_type, element_id, status, count in identity_rows:
        entry = _entry(element_type, element_id)
        if status in entry["identity_links"]:
            entry["identity_links"][status] += int(count)

    return index


# ---------------------------------------------------------------------------
# 純粋な組み立て本体（DB 非依存。fake rows で直接テストできる）
# ---------------------------------------------------------------------------


def build_inventory(
    document_id: str,
    *,
    component_rows: Sequence[Mapping[str, Any]],
    claim_rows: Sequence[Mapping[str, Any]],
    equation_records: Sequence[Mapping[str, Any]],
    figure_rows: Sequence[Mapping[str, Any]],
    annotation_rows: Sequence[Sequence[Any]] = (),
    session_rows: Sequence[Sequence[Any]] = (),
    identity_rows: Sequence[Sequence[Any]] = (),
) -> dict[str, Any]:
    """§5 のレスポンス形をそのまま返す純粋な組み立て。DB には触れない。

    ``*_rows`` / ``equation_records`` は既に取得済みの行（dict-like）または
    GROUP BY のタプル列を渡す。並び順は各引数の並び順をそのまま尊重する
    （component: created_at ASC / claim: chunk 出現順 / equation: artifact 配列順 /
    figure: page 順 — いずれも呼び出し側の SQL ORDER BY 由来。設計書 §4）。
    """
    cards_by_type: dict[str, list[dict[str, Any]]] = {
        ELEMENT_THEORY_COMPONENT: [_theory_component_card(r) for r in component_rows],
        ELEMENT_THEORY_CLAIM: [_theory_claim_card(r) for r in claim_rows],
        ELEMENT_EQUATION: [_equation_card(r) for r in equation_records],
        ELEMENT_FIGURE: [_figure_card(r) for r in figure_rows],
    }

    counts = {type_name: len(cards_by_type[type_name]) for type_name in _TYPE_ORDER}

    deliberation_index = _build_deliberation_index(annotation_rows, session_rows, identity_rows)

    truncated_types: list[str] = []
    elements: list[dict[str, Any]] = []
    for type_name in _TYPE_ORDER:
        cards = cards_by_type[type_name]
        if len(cards) > _INVENTORY_MAX_PER_TYPE:
            truncated_types.append(type_name)
            cards = cards[:_INVENTORY_MAX_PER_TYPE]
        for card in cards:
            key = (card["element_type"], card["element_id"])
            card["deliberation"] = deliberation_index.get(key, _zero_deliberation())
            elements.append(card)

    return {
        "document_id": str(document_id),
        "elements": elements,
        "counts": counts,
        "truncated_types": truncated_types,
    }


# ---------------------------------------------------------------------------
# DB 読み出し（core.postgres.get_session 直。既存 refs.py / decomposition.py と同じ流儀）
# ---------------------------------------------------------------------------


def build(document_id: str) -> dict[str, Any]:
    """document 1件分の要素インベントリを組み立てて返す（設計書 §5・§8）。

    権限ゲート・整形は呼び出し側（route 層）の責務。ここは既存データの読み出しと
    整形のみ（I1: LLM 呼び出しゼロ・DB 書き込みゼロ）。
    """
    session = get_session()
    try:
        component_rows = (
            session.execute(
                sa_text(
                    """
                    SELECT id, name, component_type, status, review_status, summary,
                           source_scope, created_at
                    FROM theory_components
                    WHERE source_scope->>'document_id' = :document_id
                    ORDER BY created_at ASC
                    """
                ),
                {"document_id": document_id},
            )
            .mappings()
            .fetchall()
        )

        claim_rows = (
            session.execute(
                sa_text(
                    """
                    SELECT tc.id AS id, tc.chunk_id AS chunk_id, tc.claim_type AS claim_type,
                           tc.text AS text, tc.support_status AS support_status,
                           tc.evidence_text AS evidence_text, tc.review_status AS review_status,
                           tc.created_at AS created_at
                    FROM theory_claims tc
                    LEFT JOIN chunks c ON c.id = tc.chunk_id
                    WHERE tc.document_id = :document_id
                    ORDER BY c.chunk_index ASC NULLS LAST, tc.created_at ASC
                    """
                ),
                {"document_id": document_id},
            )
            .mappings()
            .fetchall()
        )

        figure_rows = (
            session.execute(
                sa_text(
                    """
                    SELECT id, figure_label, figure_key, caption_text, extraction_method,
                           page, status
                    FROM document_figures
                    WHERE document_id = :document_id
                    ORDER BY page ASC NULLS LAST, created_at ASC
                    """
                ),
                {"document_id": document_id},
            )
            .mappings()
            .fetchall()
        )

        annotation_rows = session.execute(
            sa_text(
                """
                SELECT element_type, element_id, status, COUNT(*) AS cnt
                FROM element_annotations
                WHERE document_id = :document_id
                GROUP BY element_type, element_id, status
                """
            ),
            {"document_id": document_id},
        ).fetchall()

        session_rows = session.execute(
            sa_text(
                """
                SELECT element_type, element_id, COUNT(*) AS cnt
                FROM deliberation_sessions
                WHERE document_id = :document_id
                GROUP BY element_type, element_id
                """
            ),
            {"document_id": document_id},
        ).fetchall()

        identity_rows = session.execute(
            sa_text(
                """
                SELECT instance_element_type, instance_element_id, status, COUNT(*) AS cnt
                FROM element_identity_links
                WHERE instance_document_id = :document_id
                GROUP BY instance_element_type, instance_element_id, status
                """
            ),
            {"document_id": document_id},
        ).fetchall()
    finally:
        session.close()

    # equation は独立テーブルを持たない。既存 refs.equation_records() をそのまま呼ぶ
    # （実装を複製しない。設計書 §8）。
    equation_records = refs_mod.equation_records(document_id)

    return build_inventory(
        document_id,
        component_rows=component_rows,
        claim_rows=claim_rows,
        equation_records=equation_records,
        figure_rows=figure_rows,
        annotation_rows=annotation_rows,
        session_rows=session_rows,
        identity_rows=identity_rows,
    )
