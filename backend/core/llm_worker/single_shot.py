"""単発 LLM 呼び出しと JSON 取り出しの共通骨格（FastAPI 非 import）。

非同期 worker 系統の骨格（``client.py`` の ``BaseJSONLLMClient`` + ``repair.py`` の
``run_with_repair``）とは別に、「1回だけ LLM を呼んで応答テキストから JSON を
取り出す」同型実装が backend 全域に十数箇所コピーされていた（原稿スタジオ rewrite /
コース内容生成 / 理論コンポーネント抽出 / revision 監査・提案 / 学習チャットの
確認問題 / スキーマ分析・シミュレータ / コースビルダー 等）。本モジュールは
その **取り出しアルゴリズム** と **「構造化出力 → テキスト JSON への降格」**
という2つの制御フローだけを集約する。

正本の抽出アルゴリズムは ``client.py::parse_json_response``
（フェンス除去 → ``json.loads`` → 最外 ``{...}`` フォールバック → ValueError）。
:func:`extract_json` はそれに、コピー群が個別に持っていた2つの opt-in
（LaTeX バックスラッシュの修復 / 切り詰め応答の復元）を足したものであり、
既定経路の挙動は正本と同じである。

ドメイン側に残すもの（本モジュールは関知しない）:

- プロンプトの組み立てと入力の整形
- パース結果の正規化（``_normalize_*``）と必須キー検証
- 失敗時の扱い（``{}`` へ縮退 / 502 / degraded 固定文 / 決定論フォールバック）
- コスト上限（``CostGate``）・U層計測（``usage_context``）・モデル解決（M層）

**LLM 関数は呼び出し側から注入する**（``call=`` / ``structured_fn=`` /
``text_fn=``）。各呼び出しモジュールの ``generate_text`` /
``generate_text_with_structured_output`` を単体テストが monkeypatch する運用の
ため、本モジュール側で ``core.llm`` を import して呼ぶとその patch 面が消える。
注入されなかった場合にだけ ``core.llm`` の同名関数へ遅延解決する。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)

#: ``degraded`` 引数の「未指定」を表す番兵（``None`` は正当な縮退値なので使えない）。
_UNSET: Any = object()

_FENCE_HEAD_RE = re.compile(r"^```[a-zA-Z]*\s*")
_FENCE_TAIL_RE = re.compile(r"\s*```$")
_OUTER_OBJECT_RE = re.compile(r"\{[\s\S]*\}")
#: JSON が許す escape 以外の単独バックスラッシュ（LaTeX の ``\Lambda`` 等）を2重化する。
_LONE_BACKSLASH_RE = re.compile(r'\\(?!["\\/bfnrtu])')


class LLMSingleShotError(RuntimeError):
    """単発 LLM 呼び出しが有効な JSON を得られなかったことを表す。

    ``raw``（最後に得られた応答テキスト）と ``cause``（最後の例外）を保持する。
    呼び出し側はこれを捕まえて 502 / degraded 固定文 / 決定論フォールバックへ
    落とす（どの落とし方をするかはドメインの判断）。
    """

    def __init__(self, message: str, *, raw: str | None = None, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.raw = raw
        self.cause = cause


def strip_code_fence(text: str) -> str:
    """markdown コードフェンスで包まれた応答から中身だけを取り出す。

    ``parse_json_response`` と同一のアルゴリズム（先頭が ``` のときだけ、先頭の
    フェンス（言語タグ込み）と末尾のフェンスを落とす）。
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = _FENCE_HEAD_RE.sub("", raw)
        raw = _FENCE_TAIL_RE.sub("", raw)
    return raw.strip()


def _recover_truncated(text: str) -> dict | None:
    """切り詰められた JSON の復元（唯一の実装へ委譲）。

    実装は ``src/episteme_graph/agents/llm_json_client.py::recover_truncated_json``
    （A層の agent 群が共有する状態機械）。core → episteme_graph の import は
    orchestrator と同じ慣行だが、パッケージ未配置の環境（PYTHONPATH に src/ が
    無い単体テスト）でも本モジュールが import できるよう**関数内で遅延 import**
    し、失敗は復元不能として扱う。
    """
    try:
        from episteme_graph.agents.llm_json_client import recover_truncated_json
    except Exception:  # pragma: no cover - PYTHONPATH に src/ が無い環境
        logger.debug("truncation recovery unavailable (episteme_graph not importable)")
        return None
    try:
        return recover_truncated_json(text)
    except Exception:
        logger.debug("truncation recovery failed", exc_info=True)
        return None


def extract_json(
    text: str,
    *,
    repair_backslashes: bool = False,
    recover_truncated: bool = False,
) -> dict:
    """LLM 応答テキストから JSON オブジェクトを取り出す。

    既定経路は ``client.py::parse_json_response`` と同じ
    （フェンス除去 → ``json.loads`` → 最外 ``{...}``）。差分は2点だけ:

    - ``strict=False`` で読む（移行元のコピー群がすべてそうしていた。制御文字を
      含む応答を落とさないための緩和であり、strict が通す入力はすべて通る）。
    - 戻り値は **dict に限る**（配列やスカラは「取り出せなかった」として次の
      候補へ進み、最終的に ``ValueError``）。呼び出し側はいずれも dict を期待する。

    Parameters
    ----------
    repair_backslashes:
        JSON の escape 規則に合わない単独バックスラッシュ（LaTeX の ``\\Lambda``
        等）を2重化した候補も試す。授業用ドラフト生成のように本文へ LaTeX が
        混ざる経路で使う。
    recover_truncated:
        すべての候補が失敗したとき、切り詰め復元（A層の状態機械）を最後に試す。

    Raises
    ------
    ValueError
        どの候補からも JSON オブジェクトを取り出せなかったとき。
    """
    raw = strip_code_fence(text)

    candidates: list[str] = [raw]
    match = _OUTER_OBJECT_RE.search(raw)
    if match and match.group() != raw:
        candidates.append(match.group())
    if repair_backslashes:
        for candidate in list(candidates):
            repaired = _LONE_BACKSLASH_RE.sub(r"\\\\", candidate)
            if repaired != candidate:
                candidates.append(repaired)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate, strict=False)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed

    if recover_truncated:
        recovered = _recover_truncated(raw)
        if isinstance(recovered, dict):
            return recovered

    raise ValueError("LLM output is not a JSON object")


def _as_messages(messages_or_content: str | Sequence[dict]) -> list[dict]:
    """``str``（user 1本）と messages 配列のどちらでも受ける。"""
    if isinstance(messages_or_content, str):
        return [{"role": "user", "content": messages_or_content}]
    return list(messages_or_content)


def _forwarded(**kwargs: Any) -> dict:
    """**呼び出し側が渡した引数だけ**を素通しする（``None`` も含む）。

    ``_UNSET`` の引数は LLM 関数に渡さない。``model=None`` を明示的に渡すことには
    意味がある（M層の解決を入口に委ねる意思表示。呼び出し面をテストが検査して
    いる経路がある）ので、「省略」と「明示的な None」を潰さずに区別する。
    """
    return {key: value for key, value in kwargs.items() if value is not _UNSET}


def _default_text_call(**kwargs: Any) -> str:
    from core.llm import generate_text

    return generate_text(**kwargs)


def _default_structured_call(**kwargs: Any) -> Any:
    from core.llm import generate_text_with_structured_output

    return generate_text_with_structured_output(**kwargs)


def json_call(
    messages_or_content: str | Sequence[dict],
    *,
    call: Callable[..., str] | None = None,
    model: Any = _UNSET,
    temperature: Any = _UNSET,
    max_tokens: Any = _UNSET,
    reasoning_effort: Any = _UNSET,
    validate: Callable[[dict], Any] | None = None,
    repair_prompt: str | None = None,
    degraded: Any = _UNSET,
    repair_backslashes: bool = False,
    recover_truncated: bool = False,
    log_label: str = "llm",
) -> Any:
    """テキスト生成を1回（必要なら修復でもう1回だけ）呼び、JSON dict を返す。

    Parameters
    ----------
    call:
        ``call(messages=[...], **kwargs) -> str``。**呼び出し側モジュールの
        ``generate_text`` を渡すこと**（テストの monkeypatch 面を保つため）。
        省略時のみ ``core.llm.generate_text`` に遅延解決する。
    model / temperature / max_tokens / reasoning_effort:
        渡されたものだけを LLM 関数へ素通しする（``None`` も渡す）。省略すれば
        引数自体を渡さない（M層の scene 解決に委ねる呼び出し面）。
    validate:
        パース結果の検証。例外を投げれば失敗扱い（修復 or 縮退へ進む）。
    repair_prompt:
        指定時のみ、失敗後に **1回だけ** 同じ会話へこの指示を足して再呼び出しする。
    degraded:
        指定時は最終失敗で例外を投げずこの値を返す（``None`` も有効な縮退値）。

    Raises
    ------
    LLMSingleShotError
        ``degraded`` 未指定で、修復を含めても有効な JSON を得られなかったとき。
    """
    call_fn = call or _default_text_call
    messages = _as_messages(messages_or_content)
    kwargs = _forwarded(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
    )

    attempts: list[list[dict]] = [messages]
    if repair_prompt:
        attempts.append(messages + [{"role": "user", "content": repair_prompt}])

    raw: str | None = None
    last_error: BaseException | None = None
    for index, attempt_messages in enumerate(attempts):
        try:
            raw = call_fn(messages=attempt_messages, **kwargs)
        except Exception as exc:
            last_error = exc
            logger.warning("%s: LLM call failed (attempt %d): %s", log_label, index + 1, exc)
            continue
        try:
            parsed = extract_json(
                raw,
                repair_backslashes=repair_backslashes,
                recover_truncated=recover_truncated,
            )
            if validate is not None:
                validate(parsed)
            return parsed
        except Exception as exc:
            last_error = exc
            logger.warning("%s: JSON extraction failed (attempt %d): %s", log_label, index + 1, exc)

    if degraded is not _UNSET:
        return degraded
    raise LLMSingleShotError(f"{log_label}: could not obtain valid JSON", raw=raw, cause=last_error)


def structured_call(
    messages_or_content: str | Sequence[dict],
    response_model: Any,
    *,
    structured_fn: Callable[..., Any] | None = None,
    text_fn: Callable[..., str] | None = None,
    model: Any = _UNSET,
    text_fallback: bool = False,
    reasoning_effort: Any = _UNSET,
    repair_backslashes: bool = False,
    recover_truncated: bool = False,
    degraded: Any = _UNSET,
    log_label: str = "llm",
) -> Any:
    """構造化出力で呼び、失敗時に（要求されていれば）テキスト JSON へ1回だけ降格する。

    戻り値は構造化出力の戻り（多くは pydantic インスタンス）か、降格経路では
    ``dict``。**正規化は呼び出し側の責務**（両方を受ける ``_normalize_*`` が
    既に各所にある）。

    ``structured_fn`` / ``text_fn`` は呼び出し側モジュールの名前を渡すこと
    （テストの monkeypatch 面を保つため）。省略時のみ ``core.llm`` へ遅延解決する。
    """
    structured = structured_fn or _default_structured_call
    messages = _as_messages(messages_or_content)

    try:
        return structured(
            messages=messages,
            response_format=response_model,
            **_forwarded(model=model),
        )
    except Exception as structured_exc:
        if not text_fallback:
            if degraded is not _UNSET:
                logger.warning("%s: structured output failed; degrading: %s", log_label, structured_exc)
                return degraded
            raise LLMSingleShotError(
                f"{log_label}: structured output failed", cause=structured_exc
            ) from structured_exc
        logger.warning(
            "%s: structured output failed; retrying as text JSON: %s", log_label, structured_exc
        )

    text = text_fn or _default_text_call
    raw: str | None = None
    try:
        raw = text(messages=messages, **_forwarded(model=model, reasoning_effort=reasoning_effort))
        return extract_json(
            raw,
            repair_backslashes=repair_backslashes,
            recover_truncated=recover_truncated,
        )
    except Exception as exc:
        if degraded is not _UNSET:
            logger.warning("%s: text JSON fallback failed; degrading: %s", log_label, exc)
            return degraded
        raise LLMSingleShotError(
            f"{log_label}: text JSON fallback failed", raw=raw, cause=exc
        ) from exc


__all__ = [
    "LLMSingleShotError",
    "extract_json",
    "json_call",
    "strip_code_fence",
    "structured_call",
]
