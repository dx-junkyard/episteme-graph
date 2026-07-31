"""学習者信号の読み出しアダプタ（設計書 §5.1(a)）。

**読むだけ**の層。新しい収集は一切しない — 既存の k-匿名集約
（R層 ``core/reconstruction/stumble.py`` と D層 ``core/doubt/naive_signal.py``）の
結果を、LLM へ渡せる**事実文のリスト**に整形するだけ。

守っている不変条項:

- **FG8 数値を見せない・学習者データを漏らさない**: 事実文に入れられるのは
  k-匿名（k=3、正本 ``core/privacy.py``）を通過した**段階ラベルと件数レンジのみ**。
  生数値・個人単位の行・逐語の質問文は一切扱わない（下流の LLM へ渡るのは本関数の
  戻り値だけなので、ここが egress の絞り口になる）。
- **不透明 ID を渡さない**: anchor の実体名（anchor_label / claim の短い抜粋）が
  引けないシグナルは、UUID を出す代わりに粒度の日本語ラベル（「主張」「数式」等）に
  縮退させる（``core/discuss/authoring.py`` と同じ方針）。
- **fail-soft**: 片方の集約が失敗しても、もう片方の事実文は返す（提案生成を止めない）。

k-匿名の閾値・レンジ表記はこのモジュールで再定義しない（各集約モジュールが既に
``core/privacy.py`` 正本に委譲済みで、返ってくる値がすでにレンジ・段階ラベル）。

FastAPI は import しない（core/ 共通ルール）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.naive_signal import aggregate_naive_signals
from core.reconstruction.stumble import get_stumble_summary
from core.structure_anchor.schema import ANCHOR_TYPE_LABELS, DOUBT_TYPE_LABELS

logger = logging.getLogger(__name__)

# get_stumble_summary が「データなし」を表すために使う文字列
# （core/reconstruction/stumble.py::_NO_DATA と同値。ここは表示文言の照合のみ）。
_NO_DATA = "まだデータなし"

# 4軸（core/reconstruction/stumble.py の axes）→ 事実文のラベル。
_STUMBLE_AXIS_LABELS: tuple[tuple[str, str], ...] = (
    ("error_rate", "誤り率"),
    ("symbol_descent", "記号への降下"),
    ("verdict_self_check_divergence", "判定と自己確認の乖離"),
)

# 事実文の件数上限（プロンプト膨張の防波堤）。
MAX_SIGNAL_LINES = 12

# claim ラベルの最大長（教員向けの短い抜粋。stumble.py の label と同じ 80 字）。
_LABEL_CHARS = 80


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def topic_claim_ids(topic: Any) -> list[str]:
    """トピックに紐づく claim id（``evidence_links`` の kind='claim'）。

    原稿スタジオ ``admin-lecture-studio.js::lsStumbleClaimIdsForTopic`` と**同じ対応規則**
    をサーバ側で実装したもの（設計書 §5.1(a)）。順序は出現順・重複除去。
    """
    ids: list[str] = []
    seen: set[str] = set()
    for link in _as_list((topic or {}).get("evidence_links") if isinstance(topic, dict) else []):
        if not isinstance(link, dict) or link.get("kind") != "claim":
            continue
        target = str(link.get("target_id") or link.get("id") or "").strip()
        if target and target not in seen:
            seen.add(target)
            ids.append(target)
    return ids


def topic_anchor_ids(topic: Any) -> set[str]:
    """トピックに紐づく構造要素 id の集合（naive signals の絞り込みに使う）。

    ``evidence_links`` の全 kind の target と、``linked_*_ids`` / チャンク id を合わせる。
    集合が空のときは naive signals を使わない（コース全体の学習者集計をトピックの
    プロンプトへ流し込まない fail-closed）。
    """
    if not isinstance(topic, dict):
        return set()
    ids: set[str] = set()
    for link in _as_list(topic.get("evidence_links")):
        if not isinstance(link, dict):
            continue
        target = str(link.get("target_id") or link.get("id") or "").strip()
        if target:
            ids.add(target)
    for key in (
        "linked_claim_ids",
        "linked_component_ids",
        "linked_equation_ids",
        "linked_chunk_ids",
        "material_chunk_ids",
    ):
        for raw in _as_list(topic.get(key)):
            value = str(raw or "").strip()
            if value:
                ids.add(value)
    return ids


def resolve_claim_labels(
    session: Any,
    claim_ids: list[str],
    *,
    max_chars: int = _LABEL_CHARS,
) -> dict[str, str]:
    """claim id → 短い抜粋（不透明 ID を事実文・プロンプトに出さないためのラベル解決）。

    引けない id は結果に現れない（呼び出し側が縮退させる）。失敗は空 dict
    （ラベルが無いだけで処理は続く・非致命）。``core/teaching_figures/suggest.py`` も
    プロンプト用の claim テキスト解決にこれを使う（同じクエリを二重に書かない）。
    """
    ids = [cid for cid in claim_ids if cid]
    if not ids:
        return {}
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT id::text, text
                  FROM theory_claims
                 WHERE id::text = ANY(:ids)
                """
            ),
            {"ids": ids},
        ).fetchall()
    except Exception:  # noqa: BLE001 — ラベル解決の失敗は事実文の縮退で足りる（非致命）
        logger.warning("teaching figure signals: claim label lookup failed", exc_info=True)
        return {}
    labels: dict[str, str] = {}
    limit = max(1, int(max_chars))
    for row in rows:
        text = str(row[1] or "").strip()
        if not text:
            continue
        labels[str(row[0])] = text[:limit] + "…" if len(text) > limit else text
    return labels


def _stumble_lines(document_id: str, claim_ids: list[str]) -> tuple[list[str], dict[str, str]]:
    """R層つまづきサマリー → 事実文のリストと ``claim_id -> label`` の対応。

    段階ラベル（低/中/高）・件数レンジ（3-5 等）以外の値は事実文に入れない。
    """
    if not document_id or not claim_ids:
        return [], {}
    try:
        summary = get_stumble_summary(document_id, claim_ids)
    except Exception:  # noqa: BLE001 — 集約失敗で提案生成を止めない（fail-soft）
        logger.warning(
            "teaching figure signals: stumble summary failed for document=%s",
            document_id,
            exc_info=True,
        )
        return [], {}

    lines: list[str] = []
    labels: dict[str, str] = {}
    for claim in summary.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id") or "")
        label = str(claim.get("label") or "").strip()
        if claim_id and label:
            labels[claim_id] = label
        if not claim.get("has_data"):
            continue
        axes = claim.get("axes") or {}
        parts: list[str] = []
        for key, axis_label in _STUMBLE_AXIS_LABELS:
            value = str(axes.get(key) or "").strip()
            if value and value != _NO_DATA:
                parts.append(f"{axis_label}:{value}")
        faq = axes.get("faq") or {}
        if faq.get("has_data"):
            questions = str(faq.get("questions") or "").strip()
            if questions and questions != _NO_DATA:
                parts.append(f"質問・誤解:{questions}人")
        if not parts:
            continue
        subject = f"この主張「{label}」" if label else "この主張"
        lines.append(f"{subject}で、学習者の再構成に次の記録があります: " + " / ".join(parts))
    return lines, labels


def _naive_lines(course_id: str, allowed_ids: set[str], labels: dict[str, str]) -> list[str]:
    """D層 naive signals（anchor 単位 k-匿名）→ 事実文のリスト。

    ``allowed_ids`` が空なら何も返さない（コース全体の集計をトピックへ流さない）。
    """
    if not course_id or not allowed_ids:
        return []
    try:
        aggregated = aggregate_naive_signals(course_id)
    except Exception:  # noqa: BLE001 — fail-soft（もう片方の事実文は返す）
        logger.warning(
            "teaching figure signals: naive signal aggregation failed for course=%s",
            course_id,
            exc_info=True,
        )
        return []

    lines: list[str] = []
    for signal in aggregated.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        anchor_id = str(signal.get("anchor_id") or "").strip()
        if anchor_id not in allowed_ids:
            continue
        anchor_type = str(signal.get("anchor_type") or "").strip()
        type_label = ANCHOR_TYPE_LABELS.get(anchor_type, "教材の箇所")
        # 不透明 ID は出さない（label が引けなければ粒度の日本語ラベルへ縮退）。
        label = str(signal.get("anchor_label") or "").strip() or labels.get(anchor_id, "")
        subject = f"この{type_label}「{label}」" if label else f"この{type_label}"
        count_range = str(signal.get("count_range") or "").strip()
        if not count_range:
            continue
        detail_parts: list[str] = []
        for doubt in signal.get("doubt_types") or []:
            if not isinstance(doubt, dict):
                continue
            doubt_label = DOUBT_TYPE_LABELS.get(
                str(doubt.get("doubt_type") or ""), "未分類の引っかかり"
            )
            doubt_range = str(doubt.get("count_range") or "").strip()
            if doubt_range:
                detail_parts.append(f"{doubt_label}:{doubt_range}人")
        detail = f"（内訳 {' / '.join(detail_parts)}）" if detail_parts else ""
        lines.append(f"{subject}に問いを立てた学習者:{count_range}人{detail}")
    return lines


def topic_gap_signals(
    session: Any,
    *,
    course_id: str,
    topic_id: str,
    document_id: str,
    topic: Any,
) -> list[str]:
    """トピックに関係する学習者信号を**事実文のリスト**として返す（FG8 の egress 絞り口）。

    戻り値の各要素は、段階ラベル（低/中/高）と件数レンジ（3-5 / 6-10 / 11+）以外の
    学習者由来の値を含まない。1件も無ければ空リスト（提案は本文のみで動く =
    ``signal_basis='text_analysis'`` に縮退する）。

    ``session`` は claim ラベル解決にのみ使う（commit / close はしない）。
    ``topic_id`` は現状フィルタに使っていないが、呼び出し規約（course/topic 単位の
    問い合わせ）を明示するために受け取る。
    """
    claim_ids = topic_claim_ids(topic)
    stumble_lines, stumble_labels = _stumble_lines(str(document_id or ""), claim_ids)

    labels = dict(stumble_labels)
    missing = [cid for cid in claim_ids if cid not in labels]
    if missing:
        labels.update(resolve_claim_labels(session, missing))

    naive_lines = _naive_lines(str(course_id or ""), topic_anchor_ids(topic), labels)
    return (stumble_lines + naive_lines)[:MAX_SIGNAL_LINES]


__all__ = [
    "MAX_SIGNAL_LINES",
    "resolve_claim_labels",
    "topic_anchor_ids",
    "topic_claim_ids",
    "topic_gap_signals",
]
