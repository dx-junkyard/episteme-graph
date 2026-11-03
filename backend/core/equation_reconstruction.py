"""式の復元の状態を、生きた式の記録から読み時に引く（第 25 周 第 6 波・IK-0895・FastAPI 非 import）。

凍結した写し（コースの content_blocks の式・根拠リンク・講義の式）は、凍結の時点で式の記録が
持っていた復元の状態を載せていないことがある（旧い freeze）。部品の文脈とチャットは生きた記録を
読むので、同じ式に画面ごとに違う印が付いた（固定コースの講義の式 253 件で復元 0 件・部品の文脈では
復元）。ここでは document ごとに

- 採用 run の ``equation_semantics`` artifact の式の記録（``persistence.document_run_artifacts`` と
  同じ adopted ポリシ — 成果物 run の選び方を増やさない）
- ``chunks.formulas`` の平坦形（``reconstructed``）

を読み、``learner_context_common.build_equation_reconstruction_index`` の索引にする。判定そのものは
``learner_context_common.equation_latex_is_reconstructed``（印の正本）で、ここは記録を集めるだけ。

``must_not_treat_as_source_extracted`` は印の判定に単独では使わない — PDF 由来という一律の前提
（``needs_math_review``）でも立つため、印を付け過ぎる。復元の有無は同じ記録の
``reconstruction.status`` と原文との照合で判定する（第 2 波で決めた正本のまま）。

失敗は空の索引（印は写し自身の判定に戻る — 表示は止めない）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Iterable

from core.learner_context_common import (
    EquationReconstructionIndex,
    build_equation_reconstruction_index,
)

logger = logging.getLogger(__name__)

#: document ごとの記録のキャッシュ（秒）。再解析の採用は数分単位なので短く持つ。
CACHE_TTL_SECONDS = 120
#: 1 回の索引で読む document の上限（コースの論文数として十分）。
MAX_DOCUMENTS = 40

_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def _load_records(document_ids: list[str]) -> dict[str, list[dict]]:
    from sqlalchemy import text as sa_text

    from core.document_pipeline.persistence import resolve_artifact_runs
    from core.postgres import get_session

    out: dict[str, list[dict]] = {doc: [] for doc in document_ids}
    session = get_session()
    try:
        try:
            resolved = resolve_artifact_runs(session, document_ids)
        except Exception:  # noqa: BLE001
            session.rollback()
            resolved = {}
        for doc, entry in resolved.items():
            stage_outputs = (entry or {}).get("stage_outputs") or {}
            artifacts = stage_outputs.get("_artifacts") if isinstance(stage_outputs, dict) else None
            eq_stage = artifacts.get("equation_semantics") if isinstance(artifacts, dict) else None
            records = eq_stage.get("equations") if isinstance(eq_stage, dict) else None
            if isinstance(records, list):
                out.setdefault(doc, []).extend(r for r in records if isinstance(r, dict))
        try:
            rows = session.execute(
                sa_text(
                    """
                    SELECT document_id::text, formulas FROM chunks
                    WHERE document_id::text = ANY(:ids) AND formulas IS NOT NULL
                    """
                ),
                {"ids": document_ids},
            ).fetchall()
        except Exception:  # noqa: BLE001
            session.rollback()
            rows = []
        for row in rows or []:
            doc = str(row[0] or "")
            formulas = row[1]
            if isinstance(formulas, str):
                try:
                    formulas = json.loads(formulas)
                except (ValueError, TypeError):
                    formulas = None
            if doc and isinstance(formulas, list):
                out.setdefault(doc, []).extend(f for f in formulas if isinstance(f, dict))
    finally:
        session.close()
    return out


def load_equation_reconstruction_index(document_ids: Iterable[Any]) -> EquationReconstructionIndex:
    """document 群の生きた式の記録から索引を作る（キャッシュ付き・失敗は空の索引）。"""
    ids = sorted({str(d).strip() for d in (document_ids or []) if str(d or "").strip()})[:MAX_DOCUMENTS]
    if not ids:
        return EquationReconstructionIndex()
    now = time.monotonic()
    records: dict[str, list[dict]] = {}
    missing: list[str] = []
    with _lock:
        for doc in ids:
            hit = _cache.get(doc)
            if hit and now - hit[0] < CACHE_TTL_SECONDS:
                records[doc] = hit[1]
            else:
                missing.append(doc)
    if missing:
        try:
            loaded = _load_records(missing)
        except Exception:  # noqa: BLE001 — 索引が作れないなら写し自身の判定に戻る（キャッシュしない）
            logger.debug("equation reconstruction index load failed", exc_info=True)
            loaded = None
        with _lock:
            for doc in missing:
                recs = (loaded or {}).get(doc) or []
                if loaded is not None:
                    _cache[doc] = (now, recs)
                records[doc] = recs
    return build_equation_reconstruction_index(records)


def clear_cache() -> None:
    """テスト・再解析の採用直後用。"""
    with _lock:
        _cache.clear()
