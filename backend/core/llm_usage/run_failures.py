"""解析 run 内の LLM 呼び出し失敗の要約（IK-0362）。

解析の途中で AI 提供元が落ちる（課金残高切れ・利用上限・認証失敗など）と、各ステージは
失敗を自分の縮退語彙（``fallback`` / ``skipped_reason: repair_failed`` / 黙った完了）で
吸収し、run は ``completed`` になる。完了判定そのものは変えず、U層の台帳
（``llm_usage_events``）に残った **この run の失敗行** を完了時に集計して、
``stage_outputs["llm_failures"]`` に事実のブロックとして残す。

設計上の約束:
  - U1: 数値（失敗件数）は run 内部の ``stage_outputs`` にだけ置き、``note`` には書かない
    （教員向けの事実文に件数を出さない — 原則4 / G6）。
  - 提供元の生メッセージ・URL を保存しない。U層が持つのは例外の**クラス名**
    （``error_type``）だけで、ここでもクラス名以外を読まない。
  - 失敗が無ければ ``None``（呼び出し側は何も書かない）。
  - 例外を外に出さない（呼び出し側の fail-soft を二重に守る）。

このモジュールは FastAPI / LLM クライアントを import しない（U4）。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Mapping

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

#: ``stage_outputs`` に書くキー。G層 ``material.analysis_llm_failed`` がこれを読む。
LLM_FAILURES_KEY = "llm_failures"

#: 失敗を検出したときの ``status``（語彙はこの1つだけ。失敗が無ければブロック自体を書かない）。
STATUS_PROVIDER_FAILURES_DETECTED = "provider_failures_detected"

#: 失敗の性質の分類（事実文の出し分けにのみ使う）。
CAUSE_RATE_LIMIT = "rate_limit"
CAUSE_AUTH = "auth"
CAUSE_OTHER = "other"

_ERROR_TYPE_MAX_LEN = 80
_FEATURE_MAX_LEN = 120
_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.:\-]")

#: 事実文（数値・提供元の生メッセージ・URL を含めない）。
NOTE_RATE_LIMIT = (
    "解析中に AI 提供元の呼び出しが、利用上限または残高の制限により受け付けられませんでした。"
    "成果の一部が欠けている可能性があります。"
)
NOTE_AUTH = (
    "解析中に AI 提供元の呼び出しが、認証または権限の問題により受け付けられませんでした。"
    "成果の一部が欠けている可能性があります。"
)
NOTE_OTHER = (
    "解析中に AI 提供元の呼び出しが失敗しました。成果の一部が欠けている可能性があります。"
)

_FAILED_ROWS_SQL = sa_text(
    """
    SELECT feature, COALESCE(error_type, '') AS error_type, count(*) AS failed
    FROM llm_usage_events
    WHERE run_id = CAST(:run_id AS uuid) AND success = FALSE
    GROUP BY feature, COALESCE(error_type, '')
    ORDER BY feature, COALESCE(error_type, '')
    """
)


_CLASS_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def _safe_token(value: Any, max_len: int) -> str:
    """feature 名に使える文字だけを残す（空白・記号・URL の区切りを落とす）。"""
    text = _SAFE_TOKEN_RE.sub("", str(value or ""))
    return text[:max_len]


def _safe_error_type(value: Any) -> str:
    """例外クラス名（先頭の識別子）だけを残す。後ろに続くメッセージ・URL は捨てる。"""
    match = _CLASS_NAME_RE.match(str(value or "").strip())
    return match.group(0)[:_ERROR_TYPE_MAX_LEN] if match else ""


def classify_error_type(error_type: str) -> str:
    """例外クラス名から失敗の性質を分類する（純関数）。"""
    name = str(error_type or "")
    lowered = name.lower()
    if "ratelimit" in lowered or "quota" in lowered or "insufficient" in lowered:
        return CAUSE_RATE_LIMIT
    if "authentication" in lowered or "permissiondenied" in lowered:
        return CAUSE_AUTH
    return CAUSE_OTHER


def note_for_causes(causes: Iterable[str]) -> str:
    """失敗の性質の集合から事実文を1つ選ぶ（利用上限 > 認証 > その他の優先順）。"""
    cause_set = set(causes)
    if CAUSE_RATE_LIMIT in cause_set:
        return NOTE_RATE_LIMIT
    if CAUSE_AUTH in cause_set:
        return NOTE_AUTH
    return NOTE_OTHER


def build_llm_failures_block(rows: Iterable[Mapping[str, Any]]) -> dict | None:
    """``(feature, error_type, failed)`` 行の列から ``stage_outputs`` 用のブロックを組む（純関数）。

    失敗行が無ければ ``None``。``features`` / ``error_types`` / ``stages`` は決定論順
    （昇順）。件数は ``failed_calls`` と ``by_feature`` にだけ置き、``note`` には書かない。
    """
    by_feature: list[dict] = []
    features: set[str] = set()
    error_types: set[str] = set()
    causes: set[str] = set()
    total = 0
    for row in rows or ():
        try:
            failed = int(row.get("failed") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if failed <= 0:
            continue
        feature = _safe_token(row.get("feature"), _FEATURE_MAX_LEN) or "unattributed"
        error_type = _safe_error_type(row.get("error_type")) or "unknown"
        features.add(feature)
        error_types.add(error_type)
        causes.add(classify_error_type(error_type))
        total += failed
        by_feature.append({"feature": feature, "error_type": error_type, "failed": failed})
    if total <= 0:
        return None
    stages = sorted(
        f.split(":", 1)[1] for f in features if f.startswith("pipeline:") and f.split(":", 1)[1]
    )
    by_feature.sort(key=lambda item: (item["feature"], item["error_type"]))
    return {
        "status": STATUS_PROVIDER_FAILURES_DETECTED,
        "features": sorted(features),
        "stages": stages,
        "error_types": sorted(error_types),
        "rate_limited": CAUSE_RATE_LIMIT in causes,
        "note": note_for_causes(causes),
        # run 内部の記録（教員向けの事実文には出さない）
        "failed_calls": total,
        "by_feature": by_feature,
    }


def _buffered_rows_for_run(run_id: str) -> list[dict]:
    """まだ DB へ書かれていない（recorder のバッファに残っている）失敗イベントを集計する。"""
    from core.llm_usage import recorder

    counts: dict[tuple[str, str], int] = {}
    with recorder._lock:  # noqa: SLF001 — 同パッケージ内の読み取りのみ
        events = list(recorder._buffer)  # noqa: SLF001
    for event in events:
        if str(getattr(event, "run_id", "") or "") != run_id:
            continue
        if getattr(event, "success", True):
            continue
        key = (str(event.feature or ""), str(event.error_type or ""))
        counts[key] = counts.get(key, 0) + 1
    return [
        {"feature": feature, "error_type": error_type, "failed": failed}
        for (feature, error_type), failed in counts.items()
    ]


def _merge_rows(*row_lists: Iterable[Mapping[str, Any]]) -> list[dict]:
    merged: dict[tuple[str, str], int] = {}
    for rows in row_lists:
        for row in rows or ():
            key = (str(row.get("feature") or ""), str(row.get("error_type") or ""))
            try:
                merged[key] = merged.get(key, 0) + int(row.get("failed") or 0)
            except (TypeError, ValueError):
                continue
    return [
        {"feature": feature, "error_type": error_type, "failed": failed}
        for (feature, error_type), failed in merged.items()
    ]


def summarize_run_llm_failures(session, run_id: str | None, *, flush: bool = True) -> dict | None:
    """この run の LLM 呼び出し失敗を要約する。失敗が無い・確認できないときは ``None``。

    手順:
      1. recorder のバッファを同期 flush する（U層は非同期書き込みなので、完了時点では
         直近の失敗がまだ DB に無いことがある）。
      2. flush 用ロックを保持したまま「バッファ残り（flush に失敗した分）」と DB の失敗行を
         読む。ロック中は flusher がバッファを DB へ移せないので二重計上しない。

    例外は外に出さない（読めなかったことは ``None`` として扱い、run の状態を変えない）。
    """
    run_id = str(run_id or "").strip()
    if not run_id:
        return None
    try:
        from core.llm_usage import recorder

        if flush:
            try:
                recorder.flush_now()
            except Exception:  # noqa: BLE001
                logger.debug("llm_usage.run_failures: flush failed", exc_info=True)

        with recorder._flush_lock:  # noqa: SLF001
            buffered = _buffered_rows_for_run(run_id)
            db_rows: list[dict] = []
            if session is not None:
                try:
                    result = session.execute(_FAILED_ROWS_SQL, {"run_id": run_id})
                    db_rows = [dict(r) for r in result.mappings().fetchall()]
                except Exception:  # noqa: BLE001
                    logger.debug("llm_usage.run_failures: query failed", exc_info=True)
                    try:
                        session.rollback()
                    except Exception:  # noqa: BLE001
                        pass
        return build_llm_failures_block(_merge_rows(db_rows, buffered))
    except Exception:  # noqa: BLE001
        logger.debug("llm_usage.run_failures: summary failed", exc_info=True)
        return None


__all__ = [
    "LLM_FAILURES_KEY",
    "STATUS_PROVIDER_FAILURES_DETECTED",
    "NOTE_RATE_LIMIT",
    "NOTE_AUTH",
    "NOTE_OTHER",
    "classify_error_type",
    "note_for_causes",
    "build_llm_failures_block",
    "summarize_run_llm_failures",
]
