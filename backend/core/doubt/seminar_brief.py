"""ゼミ前ブリーフ（Seminar Brief, 提案1 v1）の読み時合成（非LLM・読み取り専用）。

正本: docs/features/seminar_brief_mirroring_design.md §1（不変条項 SB1〜SB4）。

輪講の前に教員が対象論文の「賭け金」を10分で把握するための read-only 合成ビュー。
新テーブル・新LLMはゼロ（SB1）で、既存投影の組み合わせだけで組み立てる:

  ① 脆い前提       — D層 open-assumptions（``compile_open_assumptions`` の document 絞り込み）
  ② 一点吊りの支持線 — SL層 ``support_paths`` の ``level == "single"`` の事実文
  ③ 晴れ間          — 「このコーパスの中では検証記録が見つかりません。」の閉世界事実文（SL1）
  ④ 学習者からの問い — v1 は空欄予約（SB3。手渡しチャネル本体は v2 の例外設計書を経る）

原則:
  - SB2 数値を見せない — 件数・人数の生値を返さない（レンジ・段階ラベル・事実文のみ）。
    投影はホワイトリストで組み立て、最後に再帰安全網（``_strip_numeric_keys``）を通す。
  - SB4 誰が何を挙げたかの集計を作らない — 学習者個人・学習者別件数のクエリ経路を
    このモジュールに書かない。claim つまづき補助は既存の k-匿名集約
    （``core/reconstruction/stumble.py::get_stumble_summary``）の再利用のみ。
  - SL1 閉世界語彙 — 検証記録の不在について言えるのは「このコーパスの中では」だけ
    （分野レベルの不在言明を作らない。本モジュールは
    ``test_stakes_ledger_guardrails.py`` の denylist 検査対象に登録済み）。
  - FastAPI / LLM を import しない（テスタビリティ確保）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.open_assumptions import compile_open_assumptions, target_label
from core.doubt.schema import LOAD_LEVEL_LABELS
from core.doubt.support_paths import build_support_context, compute_support_lines_from_context
from core.reconstruction.stumble import get_stumble_summary

logger = logging.getLogger(__name__)

# 晴れ間（区画③）の閉世界固定文（SL1）。「このコーパスの中では」を必ず含む肯定形 —
# 台帳はコーパスの射影であって分野の射影ではない。晴れ間は発見の候補地であって発見ではない。
FACT_LINE_NO_VERIFICATION_RECORD = "このコーパスの中では検証記録が見つかりません。"

# 第4区画（学習者からの問い）の空欄予約文（SB3）。警告色・催促文にしない（空欄は発見の流儀）。
LEARNER_HANDOFF_RESERVED_NOTE = "（この区画は、学習者からの手渡しの仕組みの実装後に使われます）"

# 各区画の上限（順位づけの演出はしない。並び順は投影元の決定論順をそのまま使う）
_MAX_FRAGILE_ASSUMPTIONS = 8
_MAX_SINGLE_SUPPORT_LINES = 5
_MAX_CLEAR_SKIES = 8

# SB2 の再帰安全網: 万一投影に混入しても落とす生数値キー
# （core/discuss/opening.py::_strip_numeric_keys と同型。ホワイトリスト投影が主で、
#  これは最後の安全網）。
_FORBIDDEN_NUMERIC_KEYS = frozenset({
    "dependent_count", "n_items", "load_score", "confidence", "score",
    "n", "n_users", "count", "weight",
})


def _strip_numeric_keys(value: Any) -> Any:
    """レスポンスを再帰走査して生数値キーを除去する（SB2 の最後の安全網）。"""
    if isinstance(value, dict):
        return {
            k: _strip_numeric_keys(v) for k, v in value.items() if k not in _FORBIDDEN_NUMERIC_KEYS
        }
    if isinstance(value, list):
        return [_strip_numeric_keys(v) for v in value]
    return value


def _resolve_document_id(session, document_ref: str) -> str:
    """document_ref（documents.id UUID か source_path=material_id）を正準 document_id に解決する。

    theory_component_graphs / epistemic_ledger / theory_claims の document_id 列は
    documents.id（テキスト表現）を持つため、ここで正準化してから各投影に渡す。
    """
    document_ref = str(document_ref or "").strip()
    if not document_ref:
        return ""
    try:
        row = session.execute(
            sa_text("""
                SELECT id::text
                FROM documents
                WHERE id::text = :ref OR source_path = :ref
                LIMIT 1
            """),
            {"ref": document_ref},
        ).fetchone()
    except Exception:
        logger.warning("seminar brief document resolution failed for %s", document_ref, exc_info=True)
        return ""
    return str(row[0]) if row else ""


def _derive_course_id(session, document_id: str) -> str:
    """document の解析グラフから course_id を導出する（``ledger_builder`` と同じ経路）。"""
    try:
        rows = session.execute(
            sa_text("""
                SELECT course_id
                FROM theory_component_graphs
                WHERE document_id = :doc
            """),
            {"doc": document_id},
        ).fetchall()
    except Exception:
        logger.warning("seminar brief course derivation failed for %s", document_id, exc_info=True)
        return ""
    for row in rows:
        course_id = str(row[0] or "").strip()
        if course_id:
            return course_id
    return ""


def _project_fragile_item(item: dict) -> dict:
    """区画①の1件をホワイトリスト投影する（SB2）。

    ``compile_open_assumptions`` の item から段階ラベル・事実のみを転記し、
    ``dependent_count`` 等の生数値キーは持ち込まない。SL 4キー
    （has_falsification_condition / falsification_not_formulable /
    reachability_summary / support_line_level）は段階表示のままそのまま通す。
    """
    load_level = str(item.get("load_level") or "")
    return {
        "target_id": str(item.get("target_id") or ""),
        "target_type": str(item.get("target_type") or ""),
        "statement": str(item.get("statement") or ""),
        "verification_status": str(item.get("verification_status") or ""),
        "scope_coverage": str(item.get("scope_coverage") or ""),
        "load_level": load_level,
        "load_level_label": LOAD_LEVEL_LABELS.get(load_level, load_level),
        "challenge_count_label": str(item.get("challenge_count_label") or ""),
        "challenge_types": list(item.get("challenge_types") or []),
        "has_verification_proposal": bool(item.get("has_verification_proposal")),
        "has_naive_signal": bool(item.get("has_naive_signal")),
        # SL 4キー（賭け金の台帳の段階表示。数値は元から含まれない）
        "has_falsification_condition": bool(item.get("has_falsification_condition")),
        "falsification_not_formulable": bool(item.get("falsification_not_formulable")),
        "reachability_summary": str(item.get("reachability_summary") or ""),
        "support_line_level": str(item.get("support_line_level") or ""),
    }


def _attach_stumble_axes(document_id: str, fragile_items: list[dict]) -> None:
    """区画①の claim target に、claim つまづきサマリーの**段階ラベルのみ**を添える。

    既存の k-匿名集約（``get_stumble_summary``、k=3・レンジ/段階ラベル）の再利用のみで、
    学習者個人・学習者別件数のクエリはここに書かない（SB4）。``n_items`` 等の生数値は
    落とす（SB2）。取得失敗はブリーフ本体を壊さず補助情報なしで返す（fail-soft）。
    """
    claim_ids = [i["target_id"] for i in fragile_items if i.get("target_type") == "claim"]
    if not claim_ids:
        return
    try:
        summary = get_stumble_summary(document_id, claim_ids=claim_ids)
    except Exception:
        logger.debug("seminar brief stumble summary lookup failed for %s", document_id, exc_info=True)
        return
    axes_by_claim: dict[str, dict] = {}
    for row in summary.get("claims", []):
        if isinstance(row, dict) and row.get("claim_id"):
            # axes は段階ラベル・レンジのみ（error_rate の rate_level 等）。n_items は転記しない。
            axes_by_claim[str(row["claim_id"])] = {
                "axes": row.get("axes") or {},
                "has_data": bool(row.get("has_data")),
            }
    for item in fragile_items:
        stumble = axes_by_claim.get(item["target_id"])
        if item.get("target_type") == "claim" and stumble:
            item["stumble"] = stumble


def _single_support_lines(session, course_id: str, document_id: str) -> list[dict]:
    """区画②: document の台帳対象のうち独立支持経路が1本（level=single）のものの事実文。

    共有文脈（``build_support_context``）は**1回だけ**構築し、対象ごとに
    ``compute_support_lines_from_context`` を回す（``compute_support_lines`` を
    対象数分呼んでグラフを N 回再構築する既知のアンチパターンを避ける）。
    """
    try:
        rows = session.execute(
            sa_text("""
                SELECT target_id, target_type
                FROM epistemic_ledger
                WHERE document_id = :doc
            """),
            {"doc": document_id},
        ).fetchall()
    except Exception:
        logger.warning("seminar brief ledger target lookup failed for %s", document_id, exc_info=True)
        return []
    if not rows:
        return []

    ctx = build_support_context(session, course_id=course_id, document_id=document_id)
    if ctx is None:
        return []

    out: list[dict] = []
    for target_id, target_type in sorted((str(r[0]), str(r[1])) for r in rows):
        try:
            lines = compute_support_lines_from_context(ctx, target_type, target_id)
        except Exception:  # noqa: BLE001
            logger.debug(
                "seminar brief support line computation failed for %s/%s",
                target_type, target_id, exc_info=True,
            )
            continue
        if not lines or str(lines.get("level") or "") != "single":
            continue
        out.append({
            "target_id": target_id,
            "target_type": target_type,
            "statement": target_label(session, target_type, target_id),
            # level=single の事実文をそのまま使う（数値は元から含まれない, SL4）
            "fact_line": str(lines.get("fact_line") or ""),
        })
        if len(out) >= _MAX_SINGLE_SUPPORT_LINES:
            break
    return out


def _clear_skies(assumption_items: list[dict]) -> list[dict]:
    """区画③: open_assumptions 投影のうち untested × スコープ空欄の対象に閉世界固定文を添える。"""
    out: list[dict] = []
    for item in assumption_items:
        if str(item.get("verification_status") or "") != "untested":
            continue
        if not item.get("scope_count_is_zero"):
            continue
        out.append({
            "target_id": str(item.get("target_id") or ""),
            "target_type": str(item.get("target_type") or ""),
            "statement": str(item.get("statement") or ""),
            "fact_line": FACT_LINE_NO_VERIFICATION_RECORD,
        })
        if len(out) >= _MAX_CLEAR_SKIES:
            break
    return out


def build_seminar_brief(session, document_ref: str) -> dict:
    """ゼミ前ブリーフを読み時合成する（書き込みなし・LLM 0回）。

    document_ref → document 解決と course_id 導出ができない場合は
    ``{"available": False, "reason": ...}`` の正直縮退（エラーにしない）。
    """
    document_id = _resolve_document_id(session, document_ref)
    if not document_id:
        return {
            "available": False,
            "reason": "指定された文献が見つかりませんでした。",
        }
    course_id = _derive_course_id(session, document_id)
    if not course_id:
        return {
            "available": False,
            "document_id": document_id,
            "reason": (
                "この文献にはまだ解析グラフ由来のコース対応が無いため、"
                "ブリーフを合成できません。解析パイプラインの完了後にもう一度お試しください。"
            ),
        }

    # 区画①の投影元（教員向けブリーフだが疑義者名は載せない — include_challenger_names=False 固定）
    assumption_items = compile_open_assumptions(
        session, course_id, include_challenger_names=False, document_id=document_id,
    )
    fragile = [_project_fragile_item(i) for i in assumption_items[:_MAX_FRAGILE_ASSUMPTIONS]]
    _attach_stumble_axes(document_id, fragile)

    brief = {
        "available": True,
        "document_id": document_id,
        "course_id": course_id,
        # ① 脆い前提（未検証 × 下流影響「高」— 段階ラベルのみ）
        "fragile_assumptions": fragile,
        # ② 一点吊りの支持線（level=single の事実文）
        "single_support_lines": _single_support_lines(session, course_id, document_id),
        # ③ 晴れ間（閉世界の固定事実文）
        "clear_skies": _clear_skies(assumption_items),
        # ④ 学習者からの問い — v1 は空欄で予約（SB3）
        "learner_handoff": {"reserved": True, "note": LEARNER_HANDOFF_RESERVED_NOTE},
    }
    return _strip_numeric_keys(brief)
