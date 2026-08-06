"""ギャップ検出 + 図タイプ提案（設計書 §5.1(b)）。

トピック本文（+ 数式・主張・学習者信号の事実文）を入力に、1 LLM コールで
「どこに」「なぜ」「どんな図を」の候補を最大 ``max_items`` 件返す。

守っている不変条項:

- **FG2 確定は人間**: この関数は候補を組み立てるだけで **DB に書き込まない**
  （保存は route → ``store.create_suggestions``。挿入は常に ``status='candidate'``）。
- **捏造ガード**: ``anchor_excerpt`` が本文の**逐語部分文字列**であることを検証し、
  不一致は repair 1 回 → それでも直らない行は破棄して ``dropped`` に正直に計上する
  （discuss_opening の evidence_quote verbatim 検査と同型）。``figure_kind`` の語彙外も破棄。
- **FG8**: LLM へ渡せる学習者由来の情報は ``signals.topic_gap_signals`` が返した
  k-匿名通過後の事実文だけ（このモジュールは学習者テーブルを読まない）。
- **FG6 同期パスを重くしない**: LLM コールは最大2回（初回 + repair 1回）。
  コスト上限（CostGate）は route 層の責務（設計書 §4.3）。

FastAPI は import しない（core/ 共通ルール）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from core.llm import generate_text_with_structured_output
from core.llm_usage import usage_context
from core.llm_worker.client import resolve_model as _resolve_model_key
from core.teaching_figures import prompt, schema, signals

logger = logging.getLogger(__name__)

_MODEL_SETTING_KEY = "figure_studio_llm_model"

FEATURE_FIGURE_SUGGEST = "admin:figure_suggest"

# anchor_excerpt の長さ（短すぎると位置が特定できず、長すぎると「箇所」でなくなる）。
MIN_ANCHOR_EXCERPT_CHARS = 8
MAX_ANCHOR_EXCERPT_CHARS = 300

# プロンプトへ載せる補助素材の上限（膨張の防波堤）。
MAX_PROMPT_EQUATIONS = 8
MAX_PROMPT_CLAIMS = 8
MAX_CLAIM_TEXT_CHARS = 240
MAX_SOURCE_TEXT_CHARS = 12000

_WS_RE = re.compile(r"\s+")


def normalize_for_quote_match(text: str) -> str:
    """引用照合用の正規化（空白の畳み込みのみ。語形・記号・大文字小文字は触らない）。

    ``src/episteme_graph/agents/discuss_opening/input_builder.py::
    normalize_for_quote_match`` と同じ規約（改行・連続空白だけが LLM 側で崩れやすい）。
    """
    return _WS_RE.sub(" ", str(text or "")).strip()


@dataclass
class SuggestResult:
    """提案生成の結果。

    - ``suggestions``: ``store.create_suggestions`` にそのまま渡せる dict のリスト
      （``created_by`` だけ route 側で足す）
    - ``dropped``: 検証で破棄した件数（正直に返す。0 でも隠さない）
    - ``degraded``: LLM 呼び出し自体が失敗した（候補は空。route が事実文で返す）
    """

    suggestions: list[dict] = field(default_factory=list)
    dropped: int = 0
    degraded: bool = False


class _SuggestionOut(BaseModel):
    """structured output の1項目（strict schema のため全フィールド非 nullable + 空既定）。"""

    anchor_excerpt: str = ""
    gap_reason: str = ""
    figure_kind: str = ""
    figure_brief: str = ""
    confidence: float = 0.0
    uses_learner_signal: bool = False


class _SuggestionsOutput(BaseModel):
    suggestions: list[_SuggestionOut] = Field(default_factory=list)


def resolve_model() -> str:
    """``FIGURE_STUDIO_LLM_MODEL`` があればそれを、無ければ fast tier のモデルを使う。"""
    return _resolve_model_key(_MODEL_SETTING_KEY)


# ---------------------------------------------------------------------------
# 入力の組み立て（純粋関数）
# ---------------------------------------------------------------------------


def topic_source_text(topic: Any) -> str:
    """提案の対象本文（``student_material.source_text`` 優先 → content → summary）。

    verbatim 検査の haystack もこの文字列（教員が見ている本文と検査対象を一致させる）。
    """
    if not isinstance(topic, dict):
        return ""
    material = topic.get("student_material")
    if isinstance(material, dict):
        source_text = str(material.get("source_text") or "").strip()
        if source_text:
            return source_text[:MAX_SOURCE_TEXT_CHARS]
    for key in ("content", "summary"):
        value = str(topic.get(key) or "").strip()
        if value:
            return value[:MAX_SOURCE_TEXT_CHARS]
    return ""


def topic_equation_labels(topic: Any) -> list[str]:
    """トピックの ``content_blocks`` から数式の表示テキストを集める（非LLM・決定論的）。"""
    if not isinstance(topic, dict):
        return []
    labels: list[str] = []
    seen: set[str] = set()
    for block in topic.get("content_blocks") or []:
        if not isinstance(block, dict):
            continue
        for equation in block.get("equations") or []:
            if isinstance(equation, dict):
                label = str(
                    equation.get("plain_text")
                    or equation.get("latex")
                    or equation.get("label")
                    or ""
                ).strip()
            else:
                label = str(equation or "").strip()
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
            if len(labels) >= MAX_PROMPT_EQUATIONS:
                return labels
    return labels


def _claim_texts(session: Any, topic: Any) -> list[str]:
    claim_ids = signals.topic_claim_ids(topic)[:MAX_PROMPT_CLAIMS]
    if not claim_ids:
        return []
    labels = signals.resolve_claim_labels(session, claim_ids, max_chars=MAX_CLAIM_TEXT_CHARS)
    return [labels[cid] for cid in claim_ids if cid in labels]


def _signal_basis(signal_lines: list[str], uses_learner_signal: bool) -> str:
    """``signal_basis`` を正直に決める。

    学習者信号が1件も供給されていなければ ``text_analysis``（設計書 §5.1(b) の縮退）。
    供給があり LLM がそれを根拠に含めたと言えば ``both``（本文も必ず読んでいるので
    ``learner_signals`` 単独にはならない — v1 のこの経路は ``learner_signals`` を
    出力しない。語彙は DB CHECK と将来の非LLM経路のために保持する）。
    """
    if not signal_lines:
        return schema.SIGNAL_BASIS_TEXT_ANALYSIS
    return (
        schema.SIGNAL_BASIS_BOTH
        if uses_learner_signal
        else schema.SIGNAL_BASIS_TEXT_ANALYSIS
    )


# ---------------------------------------------------------------------------
# 検証（捏造ガード）
# ---------------------------------------------------------------------------


def validate_suggestions(
    parsed: _SuggestionsOutput,
    *,
    course_id: str,
    topic_id: str,
    source_text: str,
    signal_lines: list[str],
    max_items: int,
) -> tuple[list[dict], list[str]]:
    """LLM 出力を検証し ``(採用行, エラーメッセージ)`` を返す（純粋関数）。

    採用行は ``store.create_suggestions`` に渡せる形。エラーは repair プロンプトへ
    そのまま渡す（1件でもあれば1回だけ作り直させる）。
    """
    haystack = normalize_for_quote_match(source_text)
    rows: list[dict] = []
    errors: list[str] = []

    for index, entry in enumerate(parsed.suggestions or []):
        field_label = f"suggestions[{index}]"
        excerpt = str(entry.anchor_excerpt or "").strip()
        gap_reason = str(entry.gap_reason or "").strip()
        figure_kind = str(entry.figure_kind or "").strip()
        figure_brief = str(entry.figure_brief or "").strip()

        if not excerpt:
            errors.append(f"{field_label}: anchor_excerpt が空です")
            continue
        if len(excerpt) < MIN_ANCHOR_EXCERPT_CHARS:
            errors.append(
                f"{field_label}: anchor_excerpt が短すぎます"
                f"（{MIN_ANCHOR_EXCERPT_CHARS} 文字以上の逐語引用が必要）"
            )
            continue
        if len(excerpt) > MAX_ANCHOR_EXCERPT_CHARS:
            errors.append(
                f"{field_label}: anchor_excerpt が長すぎます"
                f"（{MAX_ANCHOR_EXCERPT_CHARS} 文字以内の引用にしてください）"
            )
            continue
        if not haystack or normalize_for_quote_match(excerpt) not in haystack:
            errors.append(
                f"{field_label}: anchor_excerpt が本文の逐語部分文字列ではありません"
                "（本文の文字列をそのままコピーしてください）"
            )
            continue
        if not schema.is_valid_figure_kind(figure_kind):
            errors.append(f"{field_label}: figure_kind が語彙外です（{figure_kind!r}）")
            continue
        if not gap_reason:
            errors.append(f"{field_label}: gap_reason が空です")
            continue
        if not figure_brief:
            errors.append(f"{field_label}: figure_brief が空です")
            continue

        rows.append(
            {
                "course_id": course_id,
                "topic_id": topic_id,
                "anchor_excerpt": excerpt,
                "gap_reason": gap_reason,
                "signal_basis": _signal_basis(signal_lines, bool(entry.uses_learner_signal)),
                "figure_kind": figure_kind,
                "figure_brief": figure_brief,
                "confidence": schema.normalize_confidence(entry.confidence),
            }
        )

    limit = max(1, int(max_items))
    if len(rows) > limit:
        # 上限超過は「破棄」ではなく切り詰め（エラーにはしない）。
        rows = rows[:limit]
    return rows, errors


# ---------------------------------------------------------------------------
# 生成本体
# ---------------------------------------------------------------------------


def generate_suggestions(
    session: Any,
    *,
    course_id: str,
    topic_id: str,
    topic: Any,
    signal_lines: list[str],
    model: str | None,
    user_id: str,
    max_items: int,
) -> SuggestResult:
    """トピックのギャップ候補を生成する（1 LLM コール + 必要なら repair 1回）。

    **DB には書き込まない**（保存は route が ``store.create_suggestions`` で行う）。
    ``session`` はプロンプト用の claim テキスト解決にのみ使う（commit / close はしない）。
    """
    source_text = topic_source_text(topic)
    lines = [str(line) for line in (signal_lines or []) if str(line or "").strip()]

    if not source_text:
        # 本文が無ければ提案の根拠が無い（LLM を呼ばない）。
        logger.info(
            "figure suggest: skipped (empty topic material) course=%s topic=%s",
            course_id,
            topic_id,
        )
        return SuggestResult()

    base_content = prompt.build_suggestion_content(
        topic_title=str((topic or {}).get("title") or "") if isinstance(topic, dict) else "",
        source_text=source_text,
        equations=topic_equation_labels(topic),
        claims=_claim_texts(session, topic),
        signal_lines=lines,
        max_items=max_items,
    )
    resolved_model = model or resolve_model()

    def _call(content: str) -> _SuggestionsOutput:
        return generate_text_with_structured_output(
            [{"role": "user", "content": content}],
            _SuggestionsOutput,
            model=resolved_model,
        )

    with usage_context(FEATURE_FIGURE_SUGGEST, user_id=user_id or None, course_id=course_id or None):
        try:
            parsed = _call(base_content)
        except Exception:  # noqa: BLE001 — 単発操作でも 500 にせず degraded を返す
            logger.warning(
                "figure suggest: LLM call failed course=%s topic=%s",
                course_id,
                topic_id,
                exc_info=True,
            )
            return SuggestResult(degraded=True)

        rows, errors = validate_suggestions(
            parsed,
            course_id=course_id,
            topic_id=topic_id,
            source_text=source_text,
            signal_lines=lines,
            max_items=max_items,
        )
        if not errors:
            return SuggestResult(suggestions=rows, dropped=0)

        logger.info(
            "figure suggest: validation failed, repairing once course=%s topic=%s: %s",
            course_id,
            topic_id,
            errors,
        )
        repair_content = base_content + "\n\n" + prompt.build_suggestion_repair(
            parsed.model_dump_json(), errors
        )
        try:
            repaired = _call(repair_content)
        except Exception:  # noqa: BLE001 — repair 失敗なら初回の有効行だけを返す
            logger.warning(
                "figure suggest: repair call failed course=%s topic=%s",
                course_id,
                topic_id,
                exc_info=True,
            )
            return SuggestResult(suggestions=rows, dropped=len(errors))

        repaired_rows, repaired_errors = validate_suggestions(
            repaired,
            course_id=course_id,
            topic_id=topic_id,
            source_text=source_text,
            signal_lines=lines,
            max_items=max_items,
        )

    # repair が初回より少ない有効行しか返さなかった場合は初回の結果を採る
    # （修復で良い候補を失わない。どちらを採ったかに合わせて dropped も揃える）。
    if len(repaired_rows) >= len(rows):
        return SuggestResult(suggestions=repaired_rows, dropped=len(repaired_errors))
    return SuggestResult(suggestions=rows, dropped=len(errors))


__all__ = [
    "FEATURE_FIGURE_SUGGEST",
    "MAX_ANCHOR_EXCERPT_CHARS",
    "MIN_ANCHOR_EXCERPT_CHARS",
    "SuggestResult",
    "generate_suggestions",
    "normalize_for_quote_match",
    "resolve_model",
    "topic_equation_labels",
    "topic_source_text",
    "validate_suggestions",
]
