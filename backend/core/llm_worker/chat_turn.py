"""同期「1ターン会話」型 AI の共通骨格。

W層の要素対話（``core/deliberation/dialogue.py``）・グラフ全体対話
（``core/deliberation/graph_dialogue.py``）・教材図スタジオ（
``core/teaching_figures/generator.py``）は、いずれも

1. 指示ヘッダ + grounding を**指定したターンにだけ**注入してメッセージ列を組む
2. ``usage_context``（U層帰属）の内側で **1 回だけ** structured output を呼ぶ
3. 例外は 500 にせず**固定の縮退文**（degraded）を返す（同期パスを重くしない）
4. 応答本文に制御シーケンス除去（``core.text_hygiene``）をかける

という同型の骨格を持ち、うち2つ（要素対話・グラフ全体対話）は逐語のフォークだった。
本モジュールはその骨格だけを集約する（正本:
docs/architecture/consolidation_survey_2026-07.md の Tier2 提案6 と同じ作法）。

**ドメイン固有のものは置かない**: 環境変数名・feature 文字列・縮退文の本文・
プロンプト本文・コスト上限（CostGate）・DB 書き込みは各モジュールに残る。ここに
あるのは「どのターンに grounding を載せるか」「読み上げ用スキーマの作り方」
「1コール + 縮退」の制御フローと、**表記契約の共有テキスト**（数式区切り・
読み上げ契約）だけ。

FastAPI / ベンダ SDK は import しない（LLM 呼び出しは ``core.llm`` の公開関数経由）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, create_model

from core.llm import generate_conversation_turn
from core.llm_usage import usage_context
from core.text_hygiene import strip_control_sequences

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 共有の表記契約（正本はここ1箇所。各モジュールはヘッダに連結して使う）
# ---------------------------------------------------------------------------

#: 数式の表記（2026-09-10）: 画面・読み上げの両方が ``$…$`` 前提のレンダラ/除去規則を
#: 持つため、``\(…\)`` を使わせない（生 LaTeX が画面・音声に漏れる事故の再発防止）。
MATH_DELIMITER_INSTRUCTION = r"数式は必ず `$…$` で区切ってください（`\(…\)` は使わない）。"

#: 読み上げ用の別テキスト（``spoken`` モードのときだけヘッダ末尾に足す。text 経路の
#: 入力は一字も変えない = 各系統の回帰テストで固定）。1ターン=1 LLM コールは不変。
SPOKEN_CONTRACT = (
    "音声で読み上げるための spoken を別に返してください。"
    "結論を先に、3〜5文、箇条書き・見出し・記号・LaTeX を使わず、"
    "数式は言葉で読み下してください（例: 密度ゆらぎ デルタ、波数 k のフーリエ変換）。"
)


# ---------------------------------------------------------------------------
# 読み上げモードの structured output スキーマ
# ---------------------------------------------------------------------------


def spoken_variant(
    model: type[BaseModel],
    *,
    name: str | None = None,
    doc: str | None = None,
) -> type[BaseModel]:
    """``model`` に ``spoken: str = ""`` を足しただけの派生スキーマを作る。

    text 経路のスキーマは**一切変えない**（既存プロンプト・スキーマをバイト単位で
    維持するため別クラスにする）という各系統の規約を、手書きのサブクラスではなく
    1箇所の生成器で満たす。

    ``name`` / ``doc`` は生成されるクラスの ``__name__`` / ``__doc__``。構造化出力の
    JSON schema には ``title``（クラス名）と ``description``（docstring）が載るため、
    **既存の手書きサブクラスから移行するときは元の名前と docstring をそのまま渡す**
    こと（LLM への入力を1バイトも変えないため）。
    """
    variant = create_model(  # type: ignore[call-overload]
        name or f"{model.__name__}Spoken",
        __base__=model,
        __doc__=doc,
        spoken=(str, ""),
    )
    # 生成元（各系統のモジュール）に属して見えるようにする（repr / traceback の可読性。
    # JSON schema には影響しない）。
    variant.__module__ = model.__module__
    return variant  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# メッセージ組み立て（純粋関数）
# ---------------------------------------------------------------------------

#: grounding をどのターンに注入するか。
#: ``first_user``: 会話の最初の user メッセージ（セッションの係留。W層の既定規約）。
#: ``current_user``: 今回の user メッセージ（毎ターン最新の素材を載せる系統向け）。
#: ``none``: 注入しない（呼び出し側が本文へ埋め込み済み）。
InjectMode = Literal["first_user", "current_user", "none"]


def build_turn_messages(
    prior_messages: list[dict[str, str]] | None,
    user_content: str,
    *,
    header: str,
    grounding_text: str,
    inject: InjectMode = "first_user",
    spoken: bool = False,
    spoken_contract: str = SPOKEN_CONTRACT,
) -> list[dict[str, str]]:
    """会話履歴 + 新規発話から LLM 送信用メッセージ列を組み立てる。

    ``grounding_text`` が空なら**ヘッダごと注入しない**（従来実装と同じ。grounding が
    無い会話に指示ヘッダだけを足さない）。``spoken=True`` のときだけ読み上げ契約を
    ヘッダ末尾へ足す。
    """
    full_header = header + spoken_contract if spoken else header
    turns = list(prior_messages or []) + [{"role": "user", "content": user_content}]
    messages: list[dict[str, str]] = [
        {"role": turn.get("role", "user"), "content": turn.get("content", "")}
        for turn in turns
    ]
    if not grounding_text or inject == "none":
        return messages

    target_index: int | None = None
    if inject == "first_user":
        for index, message in enumerate(messages):
            if message["role"] == "user":
                target_index = index
                break
    elif inject == "current_user":
        target_index = len(messages) - 1
    else:  # pragma: no cover — Literal 外の値は呼び出し側のバグ
        raise ValueError(f"unknown inject mode: {inject!r}")

    if target_index is None:
        return messages
    messages[target_index]["content"] = (
        full_header + "\n\n" + grounding_text + "\n\n---\n\n" + messages[target_index]["content"]
    )
    return messages


# ---------------------------------------------------------------------------
# 1ターン実行
# ---------------------------------------------------------------------------


class _Unset:
    """「引数が渡されなかった」を ``None`` と区別するための番兵。"""


_UNSET = _Unset()


@dataclass
class TurnResult:
    """1ターンの共通結果。ドメイン側の結果 dataclass はこれから組み立てる。

    - ``reply``: hygiene 適用後の応答本文（縮退時は ``degraded_reply``）。
    - ``parsed``: structured output のインスタンス（縮退時は ``None``）。
    - ``degraded``: LLM 呼び出し自体が失敗した（HTTP は 200 のまま事実文を返す運用）。
    - ``spoken``: 読み上げ用テキスト（``spoken=False`` のときは ``None``）。
    - ``stance_label``: 返答全体に付く留保ラベル（保存しない。表示のみ）。
    - ``raw``: hygiene 前の生の応答本文（縮退時は ``None``）。
    """

    reply: str
    parsed: Any = None
    degraded: bool = False
    spoken: str | None = None
    stance_label: str | None = None
    raw: str | None = None


def structured_turn(
    messages: list[dict[str, Any]],
    output_model: type[BaseModel],
    *,
    feature: str,
    degraded_reply: str,
    model: str | Callable[[], str | None] | None = None,
    user_id: str | None = None,
    document_id: str | None = None,
    course_id: str | None = None,
    images: list[bytes] | None = None,
    spoken: bool = False,
    spoken_resolver: Callable[[Any, str], str] | None = None,
    stance_label: str | None = None,
    hygiene: Callable[[str], str] | None = strip_control_sequences,
    empty_reply_fallback: str | None | _Unset = _UNSET,
    call: Callable[..., Any] | None = None,
    log_label: str = "chat turn",
) -> TurnResult:
    """``usage_context`` の内側で structured output を1回だけ呼ぶ（失敗は縮退）。

    Parameters
    ----------
    model:
        文字列ならそのまま使う。**callable なら ``usage_context`` の内側で呼んで
        解決する** — M層のユーザー別ポリシー（解決順③）が
        ``current_usage_context().user_id`` を見るため、外側で解決すると常に
        None 扱いになる（要素対話の m2 で確定した規約）。
    spoken_resolver:
        ``(llm が返した spoken, 確定した reply) -> 読み上げテキスト``。``spoken=True``
        かつ未指定なら reply / 縮退文をそのまま使う。
    empty_reply_fallback:
        応答本文が空のときに使う文字列。既定（未指定）は ``degraded_reply``
        （``degraded`` フラグは立てない = LLM は動いている）。``None`` を明示すると
        空文字のまま返す。
    call:
        LLM 呼び出しの実体。既定は ``core.llm.generate_conversation_turn``。
        **各モジュールは自分の module-level 参照を渡す**（monkeypatch 可能性を
        保つため。``core.llm`` の公開関数以外を渡さないこと）。
    """
    caller = call or generate_conversation_turn
    with usage_context(
        feature, user_id=user_id, document_id=document_id, course_id=course_id
    ):
        resolved_model = model() if callable(model) else model
        try:
            parsed = caller(messages, output_model, images=images, model=resolved_model)
        except Exception:  # noqa: BLE001 — 同期パスは 500 にしない（チャット型の共通規約）
            logger.warning("%s: LLM turn failed", log_label, exc_info=True)
            degraded_spoken: str | None = None
            if spoken:
                degraded_spoken = (
                    spoken_resolver("", degraded_reply) if spoken_resolver else degraded_reply
                )
            return TurnResult(
                reply=degraded_reply,
                degraded=True,
                spoken=degraded_spoken,
                stance_label=stance_label,
            )

    raw_reply = str(getattr(parsed, "reply", "") or "")
    reply = (hygiene(raw_reply) if hygiene else raw_reply).strip()
    if not reply:
        fallback = degraded_reply if isinstance(empty_reply_fallback, _Unset) else empty_reply_fallback
        if fallback is not None:
            reply = fallback

    spoken_text: str | None = None
    if spoken:
        spoken_text = (
            spoken_resolver(getattr(parsed, "spoken", ""), reply)
            if spoken_resolver
            else reply
        )
    return TurnResult(
        reply=reply,
        parsed=parsed,
        degraded=False,
        spoken=spoken_text,
        stance_label=stance_label,
        raw=raw_reply,
    )


__all__ = [
    "InjectMode",
    "MATH_DELIMITER_INSTRUCTION",
    "SPOKEN_CONTRACT",
    "TurnResult",
    "build_turn_messages",
    "spoken_variant",
    "structured_turn",
]
