"""構造の降下路 — 要素参照の解決（コース sources 内限定・fail-closed）。

正本設計書は ``docs/features/structure_descent_design.md``（§2 降下エンジン）。
学習者向けの解決は常に**コースの sources が指す document 集合内**に限定する
（``core/element_context.py`` / ``core/component_context.py`` と同じ fail-closed 原則。
agent 側 ID は論文ごとに独立採番されるため、コース外文書の同名要素へ誤って一致する
余地を SQL の ``document_id = ANY(:doc_ids)`` で断つ）。

- equation: 独立テーブルを持たないため、コース document 集合を順に走査して
  ``equation_semantics`` artifact 内の ``equation_id`` 一致で解決する
  （``core/element_context.py::_resolve_equation`` と同型）。
- component / claim: ``theory_components`` / ``theory_claims`` を
  ``learner_context_common.scoped_id_match_sql``（DB UUID / ``source_scope.legacy_ids``
  の両対応）で1行解決する。

本モジュールは FastAPI / LLM / routes / services を import しない（開発ルール2）。
DB 読みは SELECT のみ（書き込み経路なし — 閲覧を記録しない。SD5）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text as sa_text

from core.course_data import course_source_material_ids, course_sources
from core.deliberation.refs import document_run_artifacts, equation_records
from core.learner_context_common import normalized_document_ids, scoped_id_match_sql
from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)

# 公開 element_type 語彙（API のクエリパラメータに現れる値。W層内部語彙とは別）。
ELEMENT_TYPE_EQUATION = "equation"
ELEMENT_TYPE_COMPONENT = "component"
ELEMENT_TYPE_CLAIM = "claim"
SUPPORTED_ELEMENT_TYPES = (
    ELEMENT_TYPE_EQUATION,
    ELEMENT_TYPE_COMPONENT,
    ELEMENT_TYPE_CLAIM,
)


@dataclass
class ResolvedElement:
    """解決済みの要素参照（梯子・降下路の共通入力）。

    - ``element_id``: 正準 ID（component / claim は DB UUID、equation は equation_id）。
    - ``document_id``: 要素が属する document（コース sources 内であることが保証済み）。
    - ``match_ids``: グラフノード・claim リンクとの突合キー候補
      （DB UUID ∪ ``source_scope.legacy_ids`` ∪ span_id。保存形式の世代差を吸収する）。
    """

    element_type: str
    element_id: str
    document_id: str
    match_ids: set[str] = field(default_factory=set)


def course_document_ids(course_data: dict | None) -> set[str]:
    """コースの ``sources[]`` が指す ``documents.id``（テキスト表現）の集合を返す。

    ``services.list_course_source_document_ids`` と同じ意味論の core 側実装
    （core は services を import できないため。①明示 ``document_id`` はそのまま
    ②``material_id`` は ``documents.source_path`` 経由で1 SQL 解決）。解決に失敗した
    material_id は黙って落とす（fail-closed — 検索範囲を勝手に膨らませない）。
    """
    if not isinstance(course_data, dict):
        return set()
    explicit_ids = {
        str(s["document_id"]) for s in course_sources(course_data) if s.get("document_id")
    }
    material_ids = course_source_material_ids(course_data)
    if not material_ids:
        return explicit_ids
    try:
        session = _pg_session()
        try:
            rows = session.execute(
                sa_text("SELECT id::text FROM documents WHERE source_path = ANY(:mids)"),
                {"mids": list(material_ids)},
            ).fetchall()
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        logger.warning(
            "descent: material_id resolution failed (fail-closed, explicit ids only)",
            exc_info=True,
        )
        return explicit_ids
    return explicit_ids | {str(r[0]) for r in rows if r[0]}


def _resolve_equation(element_id: str, document_ids: list[str]) -> ResolvedElement | None:
    """equation をコース document 集合の走査で解決する（fail-soft に次の document へ進む）。"""
    raw_id = str(element_id or "").strip()
    if not raw_id:
        return None
    for document_id in document_ids:
        try:
            records = equation_records(
                document_id, artifacts=document_run_artifacts(document_id)
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "descent: equation records read failed for document %s",
                document_id,
                exc_info=True,
            )
            continue
        for record in records:
            if isinstance(record, dict) and str(record.get("equation_id") or "") == raw_id:
                return ResolvedElement(
                    element_type=ELEMENT_TYPE_EQUATION,
                    element_id=raw_id,
                    document_id=document_id,
                    match_ids={raw_id},
                )
    return None


def _resolve_row(
    table: str, element_id: str, document_ids: list[str]
) -> tuple[str, str, dict] | None:
    """theory_components / theory_claims を1行解決する（DB UUID / legacy_ids 両対応）。

    ``core/component_context.py::_resolve_component_row`` と同じ
    「``ORDER BY (id::text = :raw_id) DESC`` + ``LIMIT 1``」規約（UUID 完全一致を優先）。
    戻り値は ``(db_uuid, document_id, source_scope)``。
    """
    where_clause, params = scoped_id_match_sql(element_id, document_ids)
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                SELECT id::text AS id, document_id, source_scope
                FROM {table}
                WHERE document_id = ANY(:doc_ids) AND ({where_clause})
                ORDER BY (id::text = :raw_id) DESC, created_at ASC, id::text ASC
                LIMIT 1
                """
            ),
            params,
        ).fetchone()
    finally:
        session.close()
    if not row:
        return None
    scope = row[2] if isinstance(row[2], dict) else {}
    return str(row[0]), str(row[1] or ""), scope


def _match_ids_from_scope(db_uuid: str, scope: dict) -> set[str]:
    """突合キー候補 = DB UUID ∪ legacy_ids ∪ span_id（保存形式の世代差を吸収する）。"""
    ids = {db_uuid}
    for legacy_id in scope.get("legacy_ids") or []:
        key = str(legacy_id or "").strip()
        if key:
            ids.add(key)
    span_id = scope.get("span_id")
    if span_id:
        ids.add(str(span_id))
    return ids


def resolve_element(
    element_type: str, element_id: str, course_data: dict | None
) -> ResolvedElement | None:
    """要素をコース sources 内限定で解決する（解決不能は ``None`` — fail-closed）。"""
    if element_type not in SUPPORTED_ELEMENT_TYPES:
        return None
    document_ids = normalized_document_ids(course_document_ids(course_data))
    if not document_ids:
        return None
    if element_type == ELEMENT_TYPE_EQUATION:
        return _resolve_equation(element_id, document_ids)
    table = "theory_components" if element_type == ELEMENT_TYPE_COMPONENT else "theory_claims"
    resolved = _resolve_row(table, element_id, document_ids)
    if resolved is None:
        return None
    db_uuid, document_id, scope = resolved
    return ResolvedElement(
        element_type=element_type,
        element_id=db_uuid,
        document_id=document_id,
        match_ids=_match_ids_from_scope(db_uuid, scope),
    )
