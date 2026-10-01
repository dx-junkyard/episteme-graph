"""解析 run の生成言語（run options ``language``）を A層 agent の prompt に足す正本。

IK-0571: 日本語の画面に A層の英語の生成文が出る問題の是正。教員がアップロード /
URL 取得 / 再解析で選んだ ``document_analysis_runs.options.language``（``ja`` / ``en``）を、
**生成した文章のフィールドだけ**その言語で書かせる指示として system メッセージの末尾に足す。

不変条項（設計書 ``docs/features/generation_language_design.md``）:

- **未指定なら何も足さない** — ``apply_generation_language(messages, None, ...)`` は
  受け取ったリストをそのまま返し、prompt は従来と 1 バイトも変わらない（A層の既定の
  挙動を変えない）。
- **LLM 呼び出しを増やさない** — 同じ 1 コールの指示が変わるだけ。
- **論文由来のものは翻訳させない** — 逐語引用（evidence_quote / evidence_text）・記号・
  LaTeX・ID・語彙値（enum）・論文から取った名前や表記は原文のまま。
- **決定論の後段が読む文章は対象にしない** — 対象フィールドは agent ごとに明示列挙し、
  英語のキーワード照合で構造（operation / support_role / 分割推奨 / 概念照合）を
  導く後段の入力になっているフィールドは含めない（言語の指定で graph の構造が
  変わらないようにする）。

stdlib のみ依存（backend からも import できる）。
"""
from __future__ import annotations

from typing import Iterable

#: 生成言語の語彙（backend ``orchestrator.RUN_GENERATION_LANGUAGES`` と一致させる）。
GENERATION_LANGUAGES: tuple[str, ...] = ("ja", "en")

_LANGUAGE_NAMES = {"ja": "Japanese (日本語)", "en": "English"}

#: 足す節の見出し（テストと検索の目印）。
SECTION_HEADING = "## Output Language"


def normalize_generation_language(language: object) -> str | None:
    """語彙内（``ja`` / ``en``）なら小文字の値、それ以外（未指定・語彙外）は ``None``。"""
    text = str(language or "").strip().lower()
    return text if text in GENERATION_LANGUAGES else None


def generation_language_section(language: object, fields: Iterable[str]) -> str:
    """言語指定の節の本文。未指定・語彙外なら空文字。"""
    lang = normalize_generation_language(language)
    if not lang:
        return ""
    name = _LANGUAGE_NAMES[lang]
    field_list = ", ".join(str(f) for f in fields if str(f).strip())
    return (
        f"{SECTION_HEADING}\n"
        f"Write these generated prose fields in {name}, regardless of the language "
        f"of the source material: {field_list}.\n"
        "Keep everything else exactly as instructed above: verbatim quotes "
        "(evidence_quote / evidence_text), symbols, LaTeX, IDs, enum values from the "
        "allowed vocabularies, and names or labels taken from the paper stay in their "
        "original form. Do not translate the JSON keys."
    )


def apply_generation_language(
    messages: list[dict],
    language: object,
    fields: Iterable[str],
) -> list[dict]:
    """``messages`` の system メッセージ末尾に言語指定の節を足す。

    未指定・語彙外なら ``messages`` を**そのまま**返す（従来の prompt と同一）。
    指定ありなら新しいリストを返し、入力は mutate しない。system が無ければ
    先頭メッセージに足す。
    """
    section = generation_language_section(language, fields)
    if not section or not messages:
        return messages
    out = [dict(m) for m in messages]
    target = next((m for m in out if m.get("role") == "system"), out[0])
    target["content"] = f"{target.get('content', '')}\n\n{section}"
    return out
