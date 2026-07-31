"""対話による図の生成・調整（設計書 §4.2・§6.3）。

1ターン = 1 LLM コール（FG6）。``core.llm.generate_conversation_turn`` の
structured output で「教員への返答 + 完全な SVG + title/caption/figure_kind」を同時に取る。

守っている不変条項:

- **毎ターン完全な SVG を出させる**（差分パッチ形式にしない）。パッチ適用の失敗モードを
  避け、サニタイザを毎回全量通せるようにする。
- **サニタイズはこのターン応答の時点で実行する**。拒否されたら**同一関数内で1回だけ**
  理由をフィードバックして作り直させ、それでも通らなければ svg 無しの reply だけを返す
  （プレビューは前回版のまま。FG3）。
- **LLM 失敗は degraded 事実文で返す**（500 にしない。チャット型 AI の共通規約）。
- **U層計測はここで張る**（``admin:figure_studio``）。route 側では張らない（二重計上防止）。
- 履歴は ``core.llm_worker.history.window_history`` を通す（head_keep=1 = grounding 注入
  先の先頭 user メッセージを保護。W層対話と同型）。会話履歴の永続化はしない
  （ブラウザ内のみ・atlas-assist の前例）。

コスト上限（CostGate）は**このモジュールに置かない** — 設計書 §4.3 のとおり
route 層（``api/routes/lecture_studio/_shared.py`` の rewrite ゲートと同型）の責務。

FastAPI は import しない（core/ 共通ルール）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel

from core.llm import generate_conversation_turn
from core.llm_usage import usage_context
from core.llm_worker.client import resolve_model as _resolve_model_key
from core.llm_worker.history import window_history
from core.teaching_figures import prompt, schema
from core.teaching_figures.sanitizer import SvgRejected, sanitize_svg

logger = logging.getLogger(__name__)

_MODEL_SETTING_KEY = "figure_studio_llm_model"

FEATURE_FIGURE_STUDIO = "admin:figure_studio"

# 履歴ウィンドウ（正本: docs/features/assistant_common_infra_design.md §2）。
# SVG 全文が履歴に載るため max_chars は W層対話より大きめだが、head_keep=1 で
# grounding 注入先の先頭 user メッセージを保護する点は同じ。
HISTORY_MAX_MESSAGES = 12
HISTORY_MAX_CHARS = 6000
HISTORY_HEAD_KEEP = 1

_DEGRADED_REPLY = (
    "図の生成に失敗しました。もう一度指示を送るか、指示を短く分けて試してください。"
    "現在表示されている図は変更されていません。"
)

_SANITIZE_FAILED_REPLY_PREFIX = (
    "図を作成しましたが、保存できない書き方が含まれていたため反映しませんでした。"
    "現在表示されている図は変更されていません。理由: "
)


class _FigureTurnOutput(BaseModel):
    """structured output スキーマ。

    ``str | None`` にしない — ``client.beta.chat.completions.parse`` の strict schema
    では全フィールドが required 化されるため、W層 ``_AnnotationCandidateOut`` と同じ
    「非 nullable + 空既定」慣例に従う（設計書 §4.2）。
    """

    reply: str = ""
    svg_source: str = ""  # 完全な SVG（空 = 変更なしターン）
    title: str = ""
    caption: str = ""
    figure_kind: str = ""


@dataclass
class FigureTurnResult:
    """1ターンの結果。

    ``svg_source`` は**サニタイズ通過済み**の完全な SVG か、空文字列
    （変更なしターン / 生成失敗 / サニタイズ2回失敗）。空のときフロントは
    前回版のプレビューを維持する。
    """

    reply: str
    svg_source: str = ""
    title: str = ""
    caption: str = ""
    figure_kind: str = ""
    degraded: bool = False


def resolve_model() -> str:
    """``FIGURE_STUDIO_LLM_MODEL`` があればそれを、無ければ fast tier のモデルを使う。"""
    return _resolve_model_key(_MODEL_SETTING_KEY)


def _normalized_kind(value: str) -> str:
    """LLM 出力の figure_kind を語彙内に丸める（語彙外は空 = 呼び出し側の既定に委ねる）。"""
    candidate = str(value or "").strip()
    return candidate if schema.is_valid_figure_kind(candidate) else ""


def build_llm_messages(
    history: list[dict] | None,
    *,
    user_instruction: str,
    current_svg: str,
    grounding: str,
) -> list[dict]:
    """会話履歴 + 新しい指示から LLM 送信用メッセージ列を組み立てる（純粋関数）。

    指示ヘッダ・参考資料・現在の SVG は**今回の user メッセージ**に載せる
    （毎ターン最新の図を土台にするため。履歴内の古い SVG に引きずられない）。
    """
    windowed = window_history(
        history,
        max_messages=HISTORY_MAX_MESSAGES,
        max_chars=HISTORY_MAX_CHARS,
        head_keep=HISTORY_HEAD_KEEP,
        current_message=user_instruction,
    )
    content = prompt.build_turn_content(
        grounding=grounding or "",
        current_svg=current_svg or "",
        user_instruction=user_instruction or "",
    )
    return list(windowed) + [{"role": "user", "content": content}]


def run_figure_turn(
    *,
    history: list[dict],
    user_instruction: str,
    current_svg: str,
    grounding: str,
    model: str | None,
    user_id: str,
    course_id: str,
    max_svg_bytes: int,
) -> FigureTurnResult:
    """対話1ターンを実行する（1 LLM コール + 必要なら同一コール内で1回の修復リトライ）。

    Returns:
        :class:`FigureTurnResult`。``degraded=True`` は LLM 呼び出し自体が失敗した場合
        （HTTP は 200 のまま事実文を返す運用）。サニタイズ2回失敗は ``degraded=False`` で
        ``svg_source=""`` + 理由付き reply（LLM は動いており、図だけが採用されない）。
    """
    resolved_model = model or resolve_model()
    messages = build_llm_messages(
        history,
        user_instruction=user_instruction,
        current_svg=current_svg,
        grounding=grounding,
    )

    with usage_context(FEATURE_FIGURE_STUDIO, user_id=user_id or None, course_id=course_id or None):
        try:
            parsed = generate_conversation_turn(
                messages, _FigureTurnOutput, model=resolved_model
            )
        except Exception:  # noqa: BLE001 — 同期パスは 500 にしない（チャット型の共通規約）
            logger.warning(
                "figure studio: LLM turn failed (course=%s)", course_id, exc_info=True
            )
            return FigureTurnResult(reply=_DEGRADED_REPLY, degraded=True)

        reply = (parsed.reply or "").strip()
        raw_svg = (parsed.svg_source or "").strip()
        title = (parsed.title or "").strip()
        caption = (parsed.caption or "").strip()
        figure_kind = _normalized_kind(parsed.figure_kind)

        if not raw_svg:
            # 変更なしターン（言葉で答えるだけ）。前回版のプレビューを維持する。
            return FigureTurnResult(
                reply=reply or "図は変更していません。",
                title=title,
                caption=caption,
                figure_kind=figure_kind,
            )

        try:
            sanitized = sanitize_svg(raw_svg, max_bytes=max_svg_bytes)
        except SvgRejected as first_rejection:
            logger.info(
                "figure studio: SVG rejected, retrying once (course=%s): %s",
                course_id,
                first_rejection.reason,
            )
            repair_messages = messages + [
                {"role": "assistant", "content": raw_svg[:HISTORY_MAX_CHARS]},
                {
                    "role": "user",
                    "content": prompt.build_repair_instruction(first_rejection.reason),
                },
            ]
            try:
                repaired = generate_conversation_turn(
                    repair_messages, _FigureTurnOutput, model=resolved_model
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "figure studio: repair turn failed (course=%s)", course_id, exc_info=True
                )
                return FigureTurnResult(reply=_DEGRADED_REPLY, degraded=True)

            repaired_svg = (repaired.svg_source or "").strip()
            reply = (repaired.reply or "").strip() or reply
            title = (repaired.title or "").strip() or title
            caption = (repaired.caption or "").strip() or caption
            figure_kind = _normalized_kind(repaired.figure_kind) or figure_kind
            if not repaired_svg:
                return FigureTurnResult(
                    reply=_SANITIZE_FAILED_REPLY_PREFIX + first_rejection.reason,
                    title=title,
                    caption=caption,
                    figure_kind=figure_kind,
                )
            try:
                sanitized = sanitize_svg(repaired_svg, max_bytes=max_svg_bytes)
            except SvgRejected as second_rejection:
                logger.info(
                    "figure studio: SVG rejected twice (course=%s): %s",
                    course_id,
                    second_rejection.reason,
                )
                return FigureTurnResult(
                    reply=_SANITIZE_FAILED_REPLY_PREFIX + second_rejection.reason,
                    title=title,
                    caption=caption,
                    figure_kind=figure_kind,
                )

    return FigureTurnResult(
        reply=reply or "図を更新しました。",
        svg_source=sanitized.svg,
        title=title,
        caption=caption,
        figure_kind=figure_kind,
    )


__all__ = [
    "FEATURE_FIGURE_STUDIO",
    "FigureTurnResult",
    "build_llm_messages",
    "resolve_model",
    "run_figure_turn",
]
