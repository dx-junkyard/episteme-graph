"""面③「対話的検討」— grounding 構築 + 1ターン実行（設計書 §5・§7）。

- grounding（面①内訳 + 面②位置づけ）は :func:`build_grounding` が
  ``decomposition.build`` / ``positioning.build`` を再利用して組み立てる。
  cross_corpus レンズ・shared_part の exemplar_images は per-user 権限判定が
  必要なため（W5）、本モジュールではフィルタしない — 呼び出し側（route 層）が
  ``routes/deliberation.py`` の既存ゲート（``_apply_cross_corpus_gate`` /
  ``_apply_exemplar_image_gate``、overview と同じもの）を適用してから
  :func:`grounding_to_text` へ渡すこと。
- :func:`run_turn` が1ターンを実行する（W6: 1応答=1 LLM コール）。grounding は
  **セッションの最初の user メッセージにのみ**注入する（設計書 §5）。
- 候補注釈の構造化抽出は応答と同じ1コールの structured output で同時に取る
  （``_DialogueTurnOutput``）。LLM 呼び出し自体が失敗した場合は
  ``core.llm_worker.repair.run_with_repair`` を使わず（同期パスを重くしない・W6）、
  注釈なし・応答本文のみの縮退（``degraded=True``）にする。個々の候補注釈の
  W3 検証（evidence/reason 必須）は ``core.deliberation.annotations`` に委譲する。

figure 要素は vision（画像 + caption + 近傍本文）。画像バイト列は MinIO
``figure-images`` バケットから読む（``core.storage`` 経由。FastAPI 非依存）。

本モジュールは FastAPI にも ``routes``/``services`` にも依存しない（開発ルール2）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

from core.config import get_settings
from core.llm import generate_conversation_turn
from core.llm_usage import usage_context
from core.llm_worker.client import resolve_model as _resolve_model_key
from core.llm_worker.cost_gate import CostGate, today_str
from core.postgres import get_session
from core.storage import get_storage_client
from core.deliberation import decomposition, positioning
from core.deliberation.schema import ElementRef, SCOPE_DOCUMENT

logger = logging.getLogger(__name__)

_MODEL_SETTING_KEY = "deliberation_llm_model"

_FEATURE_CHAT = "deliberation:chat"
_FEATURE_VISION = "deliberation:vision"

_DEGRADED_REPLY = (
    "AI 応答を生成できませんでした。内訳と位置づけは overview を参照してください。"
)

# grounding 整形時に省く冗長な入れ子構造（プロンプト肥大化を避ける。生の値は
# overview API で別途参照可能なので情報は失われない）。
_GROUNDING_SKIP_FIELD_KEYS = ("frozen_content", "graph_node", "apparatus_candidates")

_INSTRUCTION_HEADER = (
    "あなたは大学院生の学習支援システムの教員向け機能「要素検討ワークスペース」の対話補助です。"
    "以下は教員が深く検討している1つの要素の内訳と位置づけです。これを踏まえて教員の質問・"
    "コメントに答えてください。断定は避け「〜の可能性がある」「〜と考えられる」のような"
    "仮説的な言い回しにしてください。もし対話の中で注釈として記録する価値がある解釈・"
    "意味づけ・内訳の補足・他の共通部品との同一性の気づきなどがあれば、annotations に"
    "候補として追加してください（0件でも構いません）。annotations の各項目には必ず"
    "kind（'meaning'|'decomposition'|'positioning_note'|'interpretation'|'identity'|"
    "'standardization' のいずれか）、body（自由文の説明。identity/standardization の場合は"
    "JSON文字列で {\"shared_part_id\":...,\"local_expression\":{...}} または "
    "{\"standardization_status\":...} を含める）、evidence（本文からの逐語引用の配列。"
    "空配列は不可）、reason（そう判断した理由）、confidence（0.0〜1.0）を含めてください。"
    "根拠（evidence・reason）を示せない項目は annotations に含めないでください。"
)


def resolve_model() -> str:
    """DELIBERATION_LLM_MODEL があればそれを、無ければ fast tier のモデルを使う。"""
    return _resolve_model_key(_MODEL_SETTING_KEY)


# ---------------------------------------------------------------------------
# コスト上限（§11: session/day の2段。他機能とは独立の in-memory カウンタ）
# ---------------------------------------------------------------------------

_cost_gate = CostGate()


def check_and_count_llm_call(session_id: str, user_id: str | None) -> bool:
    """1セッション・1ユーザー1日あたりの LLM コール上限内なら True を返し消費する。

    上限超過なら False（呼び出し側は HTTP 429 + 事実文 detail にマッピングする。
    数値レンジ・残数は返さない）。
    """
    settings = get_settings()
    per_session = int(getattr(settings, "deliberation_max_calls_per_session", 8))
    per_day = int(getattr(settings, "deliberation_max_calls_per_day", 40))
    daily_key = (today_str(), user_id or "")
    ok = _cost_gate.check_and_count(
        session_limit=per_session,
        session_key=session_id,
        daily_limit=per_day,
        daily_key=daily_key,
    )
    if not ok:
        logger.info("deliberation dialogue turn skipped: session/day cap reached")
    return ok


# ---------------------------------------------------------------------------
# grounding 構築（純粋部と DB 読み出し部を分離）
# ---------------------------------------------------------------------------


def build_grounding(ref: ElementRef) -> dict[str, Any]:
    """面①内訳 + 面②位置づけを束ねた grounding 素材を返す（フィルタ前）。

    positioning 全体が失敗しても grounding 自体は内訳だけで返す（overview と同じ
    fail-soft。§4 冒頭のレンズ単位 fail-soft は positioning.py 内で既に効いている）。
    """
    breakdown = decomposition.build(ref)
    try:
        positioning_payload: dict[str, Any] = {"available": True, "lenses": positioning.build(ref)}
    except Exception:  # noqa: BLE001
        logger.warning(
            "deliberation dialogue: positioning failed for %s:%s", ref.element_type, ref.element_id,
            exc_info=True,
        )
        positioning_payload = {
            "available": False,
            "note": "位置づけレンズの取得に失敗したため内訳のみ返す",
        }
    return {"breakdown": breakdown, "positioning": positioning_payload}


def _format_field_value(value: Any) -> str | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (list, tuple)):
        joined = "、".join(str(v) for v in value if str(v or "").strip())
        return joined or None
    return str(value)


def grounding_to_text(grounding: dict[str, Any]) -> str:
    """grounding dict を LLM prompt 用のテキストへ整形する（純粋関数・テスト容易）。"""
    breakdown = grounding.get("breakdown") or {}
    positioning_payload = grounding.get("positioning") or {}

    lines: list[str] = []
    lines.append(f"[要素] {breakdown.get('label', '')}（{breakdown.get('element_type', '')}）")

    fields = breakdown.get("fields") or {}
    for key, value in fields.items():
        if key in _GROUNDING_SKIP_FIELD_KEYS:
            continue
        formatted = _format_field_value(value)
        if formatted is None:
            continue
        lines.append(f"- {key}: {formatted}")

    for note in breakdown.get("notes") or []:
        lines.append(f"(注記) {note}")

    if positioning_payload.get("available"):
        lenses = positioning_payload.get("lenses") or {}
        for lens_name, lens in lenses.items():
            if not isinstance(lens, dict):
                continue
            items = lens.get("items") or []
            if not items:
                continue
            lines.append(f"[位置づけ:{lens_name}]")
            for item in items:
                lines.append(f"- {item.get('label', '')}: {item.get('value', '')}")
    else:
        note = positioning_payload.get("note")
        if note:
            lines.append(f"(注記) {note}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# figure の画像バイト列取得（vision セッション用）
# ---------------------------------------------------------------------------

_FIGURE_IMAGES_BUCKET = "figure-images"


def figure_image_bytes(figure_id: str) -> bytes | None:
    """document_figures.minio_key から図画像バイト列を読む（best-effort）。

    取得できなければ ``None``（呼び出し側はテキストのみで対話を続行する）。
    """
    session = get_session()
    try:
        row = session.execute(
            sa_text("SELECT minio_key FROM document_figures WHERE id = CAST(:id AS uuid) LIMIT 1"),
            {"id": figure_id},
        ).fetchone()
    finally:
        session.close()
    minio_key = str(row[0]) if row and row[0] else ""
    if not minio_key:
        return None
    try:
        return get_storage_client().get_object(_FIGURE_IMAGES_BUCKET, minio_key)
    except Exception:  # noqa: BLE001
        logger.warning("deliberation dialogue: failed to load figure image %s", figure_id, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# LLM 構造化出力スキーマ
# ---------------------------------------------------------------------------


class _AnnotationCandidateOut(BaseModel):
    kind: str = ""
    body: str = ""
    evidence: list[str] = Field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0


class _DialogueTurnOutput(BaseModel):
    reply: str = ""
    annotations: list[_AnnotationCandidateOut] = Field(default_factory=list)


@dataclass
class DialogueTurnResult:
    reply: str
    annotations: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False


# ---------------------------------------------------------------------------
# メッセージ組み立て（純粋関数・テスト容易）
# ---------------------------------------------------------------------------


def build_llm_messages(
    prior_messages: list[dict[str, str]],
    user_content: str,
    grounding_text: str,
) -> list[dict[str, str]]:
    """会話履歴 + 新規ユーザー発話から LLM 送信用メッセージ列を組み立てる。

    grounding_text は**最初の user メッセージにのみ**注入する（設計書 §5）。
    """
    turns = list(prior_messages) + [{"role": "user", "content": user_content}]
    messages: list[dict[str, str]] = []
    first_user_injected = False
    for turn in turns:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if not first_user_injected and role == "user":
            first_user_injected = True
            if grounding_text:
                content = _INSTRUCTION_HEADER + "\n\n" + grounding_text + "\n\n---\n\n" + content
        messages.append({"role": role, "content": content})
    return messages


# ---------------------------------------------------------------------------
# 1ターン実行
# ---------------------------------------------------------------------------


def run_turn(
    ref: ElementRef,
    *,
    prior_messages: list[dict[str, str]],
    user_content: str,
    grounding_text: str,
    images: list[bytes] | None = None,
    model: str | None = None,
    user_id: str | None = None,
) -> DialogueTurnResult:
    """1ターンを実行する（W6: 1応答=1 LLM コール）。

    LLM 呼び出しが失敗した場合（API エラー・構造化出力パース失敗等）は
    ``run_with_repair`` を使わず、注釈なし・応答本文のみの縮退（``degraded=True``）で
    返す（同期パスを重くしない）。
    """
    llm_messages = build_llm_messages(prior_messages, user_content, grounding_text)
    resolved_model = model or resolve_model()
    feature = _FEATURE_VISION if images else _FEATURE_CHAT
    document_id = ref.document_id if ref.scope == SCOPE_DOCUMENT else None

    with usage_context(feature, user_id=user_id, document_id=document_id):
        try:
            parsed = generate_conversation_turn(
                llm_messages, _DialogueTurnOutput, images=images, model=resolved_model,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "deliberation dialogue: LLM turn failed for %s:%s", ref.element_type, ref.element_id,
                exc_info=True,
            )
            return DialogueTurnResult(reply=_DEGRADED_REPLY, annotations=[], degraded=True)

    raw_annotations = [a.model_dump() for a in (parsed.annotations or [])]
    reply = (parsed.reply or "").strip() or _DEGRADED_REPLY
    return DialogueTurnResult(reply=reply, annotations=raw_annotations, degraded=False)


__all__ = [
    "DialogueTurnResult",
    "build_grounding",
    "grounding_to_text",
    "build_llm_messages",
    "run_turn",
    "figure_image_bytes",
    "resolve_model",
    "check_and_count_llm_call",
]
