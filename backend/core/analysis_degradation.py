"""解析 run の縮退の読み時投影（IK-0715）。

A層 agent の LLM 呼び出しが打ち切り（``AGENT_LLM_WALL_TIMEOUT_SECONDS``）や提供元の障害で
失敗すると、各ステージは決定論の代替（``make_deterministic_fallback``）か空の成果
（``make_fallback``）に落ちて**成功として**終わり、run は ``completed`` になる。完了判定は
変えず、「この run の成果のどこが AI の応答なしで作られたか」を 1 つの投影にまとめる。
教材行のチップ・グラフレビューのバナー・G層 ``material.analysis_llm_failed`` は
すべてこの関数を通す（recall.md §3 route-all-surfaces-through-one-point）。

縮退の信号（agent が書くものだけを読む。推測で補わない）:

  1. artifact の ``validation_issues[].rule_id`` が :data:`FALLBACK_RULE_KINDS` に載る
     （``*_failed`` = 成果が空 / ``*_deterministic_fallback`` = 決定論の代替 /
     ``*_enricher_failed`` = 一部が欠ける）。
  2. ``paper_skeleton`` は issue を持たないので ``review_notes`` の固定前置き
     ``"Skeleton generation failed:"``。
  3. ``component_assembly`` の ``diagnostics.fallback_reason`` と
     ``components[].maturity_source == "deterministic_fallback"``、および完了ペイロード
     ``stage_outputs.component_assembly.fallback``（artifact が無い経路でも拾う）。
  4. artifact / 完了ペイロード / その項目の ``skipped_reason`` が
     ``llm_call_failed`` / ``repair_failed``（discuss_opening・landscape_placement・
     contextual_explanation の語彙）。
  5. ``stage_outputs.llm_failures``（U層の失敗行の要約。IK-0362）。

``skipped_by_limit`` / ``skipped_reason`` のうち上限・素材なし系は**含めない** — それは
「AI が応答しなかった」ではなく取りこぼしで、参照の健全性（``core/coverage_facts.py``）が
既に事実文で報告している（同じ事実を二箇所で言わない）。

約束:
  - 事実文（``facts``）に数字を書かない。件数は ``details`` にだけ置き、学習者には出さない。
  - 提供元の生メッセージ・例外文（``fallback_reason`` の中身）を ``facts`` に出さない。
  - 例外を外に出さない（読めなければ ``unchecked``）。
  - FastAPI / sqlalchemy / LLM クライアントを import しない。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)

#: ``stage_outputs`` に完了時スナップショットを書くキー。
DEGRADATION_KEY = "analysis_degradation"

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_UNCHECKED = "unchecked"
STATUSES = (STATUS_OK, STATUS_DEGRADED, STATUS_UNCHECKED)

#: 縮退の種類（事実文の出し分けにのみ使う）。
KIND_FALLBACK = "fallback"          # 決定論の代替で作られた
KIND_EMPTY = "empty"                # 成果が作られていない（空のまま後段へ）
KIND_PARTIAL = "partial"            # 一部が欠けている
KIND_PROVIDER = "provider_failure"  # U層に提供元の失敗が記録されている
KINDS = (KIND_FALLBACK, KIND_EMPTY, KIND_PARTIAL, KIND_PROVIDER)

#: agent が縮退時に書く ``validation_issues[].rule_id`` → 種類。
FALLBACK_RULE_KINDS: dict[str, str] = {
    "role_generation_failed": KIND_EMPTY,
    "claim_qualification_failed": KIND_EMPTY,
    "equation_semantics_failed": KIND_EMPTY,
    "thesis_reconstruction_failed": KIND_EMPTY,
    "dsl_linking_failed": KIND_EMPTY,
    "dsl_linking_deterministic_fallback": KIND_FALLBACK,
    "component_assembly_failed": KIND_EMPTY,
    "component_assembly_deterministic_fallback": KIND_FALLBACK,
    "component_graph_failed": KIND_EMPTY,
    "narrative_annotation_failed": KIND_EMPTY,
    "figure_enricher_failed": KIND_PARTIAL,
    "table_enricher_failed": KIND_PARTIAL,
    "blueprint_rationale_enricher_failed": KIND_PARTIAL,
}

#: ``component_graph_failed`` のうち AI 非応答ではないもの（部品ゼロで呼ばなかった）。
_NOT_AI_FAILURE_MESSAGES = frozenset({"no components"})

#: AI の応答が得られなかったことを表す ``skipped_reason``。
AI_FAILURE_SKIPPED_REASONS = frozenset({"llm_call_failed", "repair_failed"})

_PAPER_SKELETON_FAILED_PREFIX = "Skeleton generation failed:"
_DETERMINISTIC_FALLBACK = "deterministic_fallback"

#: llm_policy.PIPELINE_STAGE_LABELS に無い（M層の選択対象外の）ステージの表示名。
#: 文言は進捗表示 routes/lecture_studio/pipeline.py::DOCUMENT_PIPELINE_STAGE_LABELS と同語。
_EXTRA_STAGE_LABELS: dict[str, str] = {
    "component_graph": "理論操作グラフの構築",
    "figure_table_semantics": "図表の意味復元",
    "blueprint": "コース設計案の生成",
}

#: 種類の重さ（同じステージに複数の信号があるときは重いほうの事実文を出す）。
_KIND_RANK = {KIND_EMPTY: 3, KIND_FALLBACK: 2, KIND_PARTIAL: 1, KIND_PROVIDER: 0}

#: グラフレビューのバナー前置き（UI はこの文をそのまま描く）。
BANNER_PREFIX = "この解析の一部は AI の応答が得られず決定論の代替で作られています："

FACT_UNCHECKED = "この解析で AI の応答が得られなかった箇所があるかは、まだ確認されていません。"


def stage_label(stage: str) -> str:
    """ステージの表示名（M層の正本 → 進捗表示と同語の補完 → キーそのまま）。"""
    try:
        from core.llm_policy import PIPELINE_STAGE_LABELS

        label = PIPELINE_STAGE_LABELS.get(stage)
    except Exception:  # noqa: BLE001 — 表示名が引けなくても投影は続ける
        label = None
    return label or _EXTRA_STAGE_LABELS.get(stage) or stage


def fact_for(stage: str, kind: str) -> str:
    """ステージ × 種類の事実文（数字・例外文を含めない）。"""
    label = stage_label(stage)
    if kind == KIND_FALLBACK:
        return f"「{label}」は AI の応答が得られず、決定論の代替で作られています。"
    if kind == KIND_EMPTY:
        return f"「{label}」は AI の応答が得られず、成果が作られていません。"
    if kind == KIND_PARTIAL:
        return f"「{label}」の一部は AI の応答が得られず、作られていません。"
    return f"「{label}」で AI 提供元の呼び出しが失敗した記録があります。"


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _iter_issue_rule_ids(payload: Mapping[str, Any]) -> Iterable[tuple[str, str]]:
    for issue in payload.get("validation_issues") or ():
        if isinstance(issue, dict):
            yield str(issue.get("rule_id") or ""), str(issue.get("message") or "")


def _skipped_ai_failures(payload: Mapping[str, Any]) -> tuple[bool, int]:
    """``(top_level_skipped, item_count)`` — AI 非応答の skipped_reason を数える。"""
    top = str(payload.get("skipped_reason") or "") in AI_FAILURE_SKIPPED_REASONS
    items = 0
    for value in payload.values():
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and str(item.get("skipped_reason") or "") in AI_FAILURE_SKIPPED_REASONS:
                items += 1
    return top, items


def _note(found: dict[str, dict], stage: str, kind: str, signal: str, count: int = 1) -> None:
    entry = found.setdefault(stage, {"stage": stage, "kind": kind, "signals": [], "count": 0})
    if _KIND_RANK.get(kind, 0) > _KIND_RANK.get(entry["kind"], 0):
        entry["kind"] = kind
    if signal not in entry["signals"]:
        entry["signals"].append(signal)
    entry["count"] += max(0, int(count))


def _scan_artifact(found: dict[str, dict], stage: str, payload: Mapping[str, Any]) -> None:
    for rule_id, message in _iter_issue_rule_ids(payload):
        kind = FALLBACK_RULE_KINDS.get(rule_id)
        if not kind:
            continue
        if rule_id == "component_graph_failed" and message.strip() in _NOT_AI_FAILURE_MESSAGES:
            continue
        _note(found, stage, kind, f"rule:{rule_id}")
    if stage == "paper_skeleton":
        for note in payload.get("review_notes") or ():
            if isinstance(note, str) and note.startswith(_PAPER_SKELETON_FAILED_PREFIX):
                _note(found, stage, KIND_EMPTY, "review_note:skeleton_generation_failed")
                break
    if stage == "component_assembly":
        diagnostics = _as_dict(payload.get("diagnostics"))
        fallback_components = [
            c for c in (payload.get("components") or ())
            if isinstance(c, dict) and str(c.get("maturity_source") or "") == _DETERMINISTIC_FALLBACK
        ]
        if diagnostics.get("fallback_reason") or fallback_components:
            _note(
                found, stage, KIND_FALLBACK, "fallback_components",
                count=len(fallback_components),
            )
    top, items = _skipped_ai_failures(payload)
    if top:
        _note(found, stage, KIND_EMPTY, "skipped_reason")
    elif items:
        _note(found, stage, KIND_PARTIAL, "item_skipped_reason", count=items)


def _scan_stage_payload(found: dict[str, dict], stage: str, payload: Mapping[str, Any]) -> None:
    if stage == "component_assembly" and payload.get("fallback") is True:
        try:
            count = int(payload.get("fallback_component_count") or 0)
        except (TypeError, ValueError):
            count = 0
        _note(found, stage, KIND_FALLBACK, "stage_payload:fallback", count=count)
    if str(payload.get("skipped_reason") or "") in AI_FAILURE_SKIPPED_REASONS:
        _note(found, stage, KIND_EMPTY, "stage_payload:skipped_reason")


def _normalize_snapshot(snapshot: Mapping[str, Any]) -> dict | None:
    status = str(snapshot.get("status") or "")
    if status not in STATUSES:
        return None
    facts = [str(f) for f in (snapshot.get("facts") or ()) if isinstance(f, str) and f]
    stages = [str(s) for s in (snapshot.get("stages") or ()) if isinstance(s, str) and s]
    return {
        "status": status,
        "facts": facts,
        "stages": stages,
        "details": _as_dict(snapshot.get("details")),
    }


def summarize_run_degradation(
    stage_outputs: Mapping[str, Any] | None,
    *,
    artifacts: Mapping[str, Any] | None = None,
) -> dict:
    """run の ``stage_outputs`` から縮退の事実を導く（純関数・例外を出さない）。

    Args:
        stage_outputs: run の ``stage_outputs``（``_artifacts`` が hydrate されていれば読む）。
        artifacts: 明示の artifact dict（完了時フックが in-memory のものを渡す）。

    Returns:
        ``{status, facts, stages, details}``。``status`` は ``ok`` / ``degraded`` /
        ``unchecked``。artifact が読めず完了時スナップショットも無いときは、
        軽い信号（``llm_failures`` / 完了ペイロード）で縮退が見つかれば ``degraded``、
        見つからなければ ``unchecked``（「問題なし」と偽装しない）。
    """
    try:
        return _summarize(stage_outputs, artifacts)
    except Exception:  # noqa: BLE001 — 投影は表示を止めない
        logger.debug("analysis degradation summary failed", exc_info=True)
        return {"status": STATUS_UNCHECKED, "facts": [], "stages": [], "details": {}}


def _summarize(stage_outputs: Mapping[str, Any] | None, artifacts: Mapping[str, Any] | None) -> dict:
    outputs = _as_dict(stage_outputs)
    if artifacts is None:
        hydrated = outputs.get("_artifacts")
        artifacts = hydrated if isinstance(hydrated, dict) and hydrated else None
    artifacts_checked = isinstance(artifacts, Mapping)

    if not artifacts_checked:
        snapshot = outputs.get(DEGRADATION_KEY)
        if isinstance(snapshot, dict):
            normalized = _normalize_snapshot(snapshot)
            if normalized is not None:
                return normalized

    found: dict[str, dict] = {}
    if artifacts_checked:
        for stage, payload in artifacts.items():
            if isinstance(payload, dict):
                _scan_artifact(found, str(stage), payload)
    for stage, payload in outputs.items():
        if stage.startswith("_") or stage in (DEGRADATION_KEY, "llm_failures"):
            continue
        if isinstance(payload, dict):
            _scan_stage_payload(found, str(stage), payload)

    facts: list[str] = []
    stages: list[str] = []
    details: dict[str, Any] = {"stages": {}}
    for stage in sorted(found, key=lambda s: (-_KIND_RANK.get(found[s]["kind"], 0), s)):
        entry = found[stage]
        stages.append(stage)
        fact = fact_for(stage, entry["kind"])
        if fact not in facts:
            facts.append(fact)
        details["stages"][stage] = {
            "kind": entry["kind"],
            "signals": list(entry["signals"]),
            "count": entry["count"],
        }

    llm_failures = outputs.get("llm_failures")
    if isinstance(llm_failures, dict):
        note = str(llm_failures.get("note") or "").strip()
        if note and not any(ch.isdigit() for ch in note) and note not in facts:
            facts.append(note)
        for stage in llm_failures.get("stages") or ():
            if isinstance(stage, str) and stage and stage not in stages:
                stages.append(stage)
        details["llm_failures"] = {
            "rate_limited": llm_failures.get("rate_limited") is True,
            "failed_calls": llm_failures.get("failed_calls"),
        }

    if facts:
        status = STATUS_DEGRADED
    elif artifacts_checked:
        status = STATUS_OK
    else:
        status = STATUS_UNCHECKED
    if not details["stages"]:
        details.pop("stages")
    return {"status": status, "facts": facts, "stages": stages, "details": details}


def public_view(summary: Mapping[str, Any] | None) -> dict:
    """教員向け表示用の投影 ``{status, facts}``（``details`` / ``stages`` を落とす）。"""
    data = _as_dict(summary)
    status = str(data.get("status") or "")
    if status not in STATUSES:
        status = STATUS_UNCHECKED
    facts = [str(f) for f in (data.get("facts") or ()) if isinstance(f, str) and f]
    if status == STATUS_UNCHECKED:
        facts = []
    return {"status": status, "facts": facts}


__all__ = [
    "AI_FAILURE_SKIPPED_REASONS",
    "BANNER_PREFIX",
    "DEGRADATION_KEY",
    "FACT_UNCHECKED",
    "FALLBACK_RULE_KINDS",
    "KINDS",
    "STATUSES",
    "STATUS_DEGRADED",
    "STATUS_OK",
    "STATUS_UNCHECKED",
    "fact_for",
    "public_view",
    "stage_label",
    "summarize_run_degradation",
]
