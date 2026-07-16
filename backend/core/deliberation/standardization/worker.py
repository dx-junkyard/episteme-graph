"""標準化判定 worker（Phase S）— 三角測量の実行 + element_annotations への書き込み。

トリガーは手動バッチ API のみ（v1。``core/tension/worker.py`` と同じ ``threading.Thread``
方式だが、tension のような自動トリガーは持たない — 呼び出し元（route 層）が明示的に
``schedule_assess_shared_part``/``schedule_assess_domain`` を呼んだときだけ動く）。

冪等性: 対象エントリに ``status ∈ (candidate, committed)`` の standardization 注釈が
既にあれば非LLMでスキップする（``force=True`` で再評価）。冪等性チェック自体は
DB 読み取りのみで軽いため、route 層が daemon thread を起動する**前**に同期で呼んで
（``has_pending_or_committed_assessment``）、スキップ時は thread を立てずに正直な
note を即座に返せるようにしている。

コスト上限: ``STDPART_MAX_CALLS_PER_DAY``（他機能と独立の日次 in-memory カウンタ。
``core.llm_worker.cost_gate.CostGate`` を再利用）。上限超過時はその共通部品のLLM証拠を
呼ばず（＝skip）、後続の共通部品も処理を打ち切る。

本モジュールは FastAPI にも ``routes``/``services`` にも依存しない（core 共通ルール）。
L層への書き込みは一切行わない（``core.library.store`` の読み取り関数
``get_entry``/``list_entries`` のみを使い、``create_entry``/``update_entry``/
``freeze_entry``/``retire_entry``/``restore_entry`` のような書き込み関数は import しない）
——LLM がライブラリへ直接書き込む経路を作らない、という L層既存ガードレールを維持する。
書くのは常に ``element_annotations`` の candidate 行のみで、``library_entries`` への反映は
教員の commit（``core.deliberation.annotations._commit_standardization``）を経由する。
"""

from __future__ import annotations

import logging
import threading

from core.config import get_settings
from core.deliberation import store as delib_store
from core.deliberation.schema import (
    ANNOTATION_KIND_STANDARDIZATION,
    ANNOTATION_STATUS_CANDIDATE,
    ANNOTATION_STATUS_COMMITTED,
    ELEMENT_SHARED_PART,
    SCOPE_DOMAIN,
    ElementRef,
)
from core.deliberation.standardization.aggregate import decide
from core.deliberation.standardization.agent import StandardizationAgent
from core.deliberation.standardization.input_builder import build_target_context
from core.library import store as library_store
from core.llm_usage.context import bind_usage_context
from core.llm_worker.cost_gate import CostGate, today_str

logger = logging.getLogger(__name__)

_FEATURE = "deliberation:standardization"

_cost_gate = CostGate()


def _check_and_count_llm_call() -> bool:
    settings = get_settings()
    per_day = int(getattr(settings, "stdpart_max_calls_per_day", 10))
    return _cost_gate.check_and_count(daily_limit=per_day, daily_key=today_str(), prune_stale_daily=True)


def has_pending_or_committed_assessment(entry_id: str, domain_key: str) -> bool:
    """既に candidate/committed の standardization 注釈があるか（冪等性チェック・非LLM）。"""
    items = delib_store.list_annotations_for_element(
        ELEMENT_SHARED_PART, entry_id, domain_key=domain_key,
    )
    return any(
        a.get("kind") == ANNOTATION_KIND_STANDARDIZATION
        and a.get("status") in (ANNOTATION_STATUS_CANDIDATE, ANNOTATION_STATUS_COMMITTED)
        for a in items
    )


def _assess_entry(entry: dict) -> dict | None:
    """1エントリを評価し candidate 注釈を書く。cost gate 超過時は None（スキップ）。"""
    if not _check_and_count_llm_call():
        logger.info(
            "standardization assessment daily cap reached; skipping entry %s", entry.get("id"),
        )
        return None

    context = build_target_context(entry)
    llm_evidence = StandardizationAgent().run(context)
    assessment = decide(llm_evidence, context.library_similarity, context.corpus_repetition)

    ref = ElementRef(
        scope=SCOPE_DOMAIN,
        element_type=ELEMENT_SHARED_PART,
        element_id=str(entry.get("id") or ""),
        domain_key=str(entry.get("domain_key") or ""),
    )
    body = {
        "standardization_status": assessment.standardization_status,
        "claimed_canonical_name": assessment.claimed_canonical_name,
        "evidence_summary": assessment.evidence_summary,
    }
    return delib_store.create_annotation(
        ref,
        kind=ANNOTATION_KIND_STANDARDIZATION,
        body=body,
        evidence=assessment.evidence,
        reason=assessment.reason,
        confidence=assessment.confidence,
        session_id=None,
        created_by=None,
    )


def run_assess_shared_part(entry_id: str, *, force: bool = False) -> dict:
    """1つの共通部品（library_entry）を評価する同期実行本体（daemon thread 内で呼ばれる）。"""
    bind_usage_context(_FEATURE, document_id=None)
    entry = library_store.get_entry(entry_id)
    if entry is None:
        logger.warning("standardization assessment: shared_part not found: %s", entry_id)
        return {"assessed": 0}
    if entry.get("status") != "active":
        logger.info("standardization assessment: skipped (not active) for %s", entry_id)
        return {"assessed": 0, "skipped": True}
    if not force and has_pending_or_committed_assessment(entry_id, entry.get("domain_key", "")):
        logger.info(
            "standardization assessment: skipped (existing candidate/committed) for %s", entry_id,
        )
        return {"assessed": 0, "skipped": True}
    annotation = _assess_entry(entry)
    return {"assessed": 1 if annotation else 0}


def run_assess_domain(domain_key: str, *, force: bool = False) -> dict:
    """domain 内の active エントリすべてを評価する同期実行本体（daemon thread 内で呼ばれる）。"""
    bind_usage_context(_FEATURE, document_id=None)
    entries = library_store.list_entries(domain_key=domain_key)
    assessed = 0
    for entry in entries:
        if not force and has_pending_or_committed_assessment(entry["id"], entry.get("domain_key", "")):
            continue
        annotation = _assess_entry(entry)
        if annotation:
            assessed += 1
    return {"assessed": assessed}


def schedule_assess_shared_part(entry_id: str, *, force: bool = False) -> None:
    """1つの共通部品の評価をバックグラウンド daemon thread で開始する（P6 相当:
    同期パスに LLM を入れない）。"""
    thread = threading.Thread(
        target=run_assess_shared_part,
        kwargs={"entry_id": entry_id, "force": force},
        name="deliberation-standardization",
        daemon=True,
    )
    thread.start()


def schedule_assess_domain(domain_key: str, *, force: bool = False) -> None:
    """domain 内 active エントリすべての評価をバックグラウンド daemon thread で開始する。"""
    thread = threading.Thread(
        target=run_assess_domain,
        kwargs={"domain_key": domain_key, "force": force},
        name="deliberation-standardization-domain",
        daemon=True,
    )
    thread.start()
