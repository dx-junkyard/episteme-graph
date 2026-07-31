"""Prompt construction for DiscussOpeningAgent.

規約準拠: ``core/llm_worker/`` 系統（tension / structure_anchor / reconstruction /
doubt / deliberation.standardization）と同じく **user ロール1本**に instruction と
入力を連結する（`system` ロール・`temperature` は使わない — 開発ルール4）。

言語は入力の ``language``（既定 ja、``DISCUSS_OPENING_LANGUAGE``）で明示指定する。
二層説明のような「教材の言語に合わせる」方式は使えない: 開幕画面は学習者向けの
散文であり、素材（論文）は英語のことが多いため（設計書 §4.1）。
"""
from __future__ import annotations

import json

from .input_builder import DiscussOpeningInputBuilder
from .schema import (
    DiscussOpeningInput,
    FORBIDDEN_PHRASES,
    GROUNDING_ASSUMPTION,
    GROUNDING_AUTHOR_CHOICE,
    GROUNDING_THESIS,
    MAX_EVIDENCE_QUOTE_CHARS,
    MAX_SEED_BODY_CHARS,
    TARGET_MAX_SEEDS,
    TARGET_MIN_SEEDS,
)

_LANGUAGE_NAMES = {"ja": "Japanese (日本語)", "en": "English"}

_INSTRUCTION = """\
あなたは、解析済みの学術論文について「学習者が立場を取れる問い」を作る担当です。
論文の内容そのものを作り出すことはせず、与えられた素材の中にある
**未検証の前提**と**著者が選んだ手（operation）**を、議論のきっかけになる問いへ
言い換えるだけを行います。

## 作るもの
`discussion_seed` を {min_seeds}〜{max_seeds} 件。各件は次を持ちます。

1. `body` — 学習者に**立場を求める問い**。{language_name} で書く。{max_body} 文字以内。
   必ず疑問文で終える（「？」または「?」で終わる1文）。
   - 良い形: 「この前提が別の条件で成り立たないとしたら、結論のどこが変わると考えますか？」
   - 悪い形（作らない）: 事実の要約 / 用語の定義を問うだけのクイズ /
     「この論文は誤りである」等の断定 / 答えが素材に書いてある知識確認
2. `evidence_quote` — 与えられた素材からの**逐語引用**。翻訳・要約・言い換えを
   一切せず、原文の文字列をそのままコピーする（英語素材なら英語のまま）。
   {max_quote} 文字以内。素材に無い文字列を書いてはいけない。
3. `reason` — なぜこれが議論のきっかけになるかを一言（{language_name}）。
4. `confidence` — 0.0〜1.0。
5. `grounded_in` — 火種の出所。次のいずれか1つ:
   `{grounding_assumption}`（未検証の前提）/ `{grounding_author_choice}`（著者が選んだ手）/
   `{grounding_thesis}`（中心命題）。

## 硬い制約
- **素材に無いことを書かない。** 論文が主張していないことを論文の主張として書かない。
  分野の一般知識から補完しない。
- **断定しない・煽らない。** 「誤りだ」「疑え」のような命令・評価は書かない。
  次の語句は使用禁止: {forbidden}
- `untested_assumptions` と `author_choices` の**両方が空**なら seed を作らず、
  `seeds` を空配列にし `skipped_reason` に短い snake_case の理由を入れる
  （根拠の無い火種を創作しない）。
- 中心命題（thesis）だけを根拠にした seed は最大1件までにする。主たる火種は
  未検証の前提と著者が選んだ手である。
- 返すのは **JSON のみ**。前置き・マークダウンのコードフェンスを付けない。
"""

_OUTPUT_SCHEMA = {
    "seeds": [
        {
            "body": "立場を求める問い（疑問文・指定言語）",
            "evidence_quote": "素材からの逐語引用（翻訳しない）",
            "reason": "なぜ議論のきっかけになるか（一言）",
            "confidence": 0.0,
            "grounded_in": f"{GROUNDING_ASSUMPTION} | {GROUNDING_AUTHOR_CHOICE} | {GROUNDING_THESIS}",
        }
    ],
    "skipped_reason": "seeds が空のときだけ snake_case の理由。生成できたときは null",
}


class DiscussOpeningPromptFactory:
    def __init__(self, input_builder: DiscussOpeningInputBuilder | None = None) -> None:
        self._input_builder = input_builder or DiscussOpeningInputBuilder()

    def build_content(
        self,
        item: DiscussOpeningInput,
        cartridge=None,
    ) -> str:
        """LLM に渡す user メッセージ1本分の文字列を組み立てる。"""
        parts: list[str] = [self._instruction(item)]
        parts.append("\n## 素材（この中の文字列だけを引用してよい）")
        parts.append(
            json.dumps(
                self._input_builder.prepare_for_prompt(item),
                ensure_ascii=False,
                indent=2,
            )
        )
        if cartridge is not None and getattr(cartridge, "aliases", None):
            parts.append("\n## 分野語彙（任意の補助。無理に使わない）")
            for canonical, aliases in list(cartridge.aliases.items())[:20]:
                parts.append(f"- {canonical}: {', '.join(aliases)}")
        parts.append("\n## 出力スキーマ")
        parts.append(json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        return "\n".join(parts)

    def _instruction(self, item: DiscussOpeningInput) -> str:
        language_name = _LANGUAGE_NAMES.get(item.language, item.language)
        max_seeds = min(TARGET_MAX_SEEDS, max(1, int(item.max_seeds)))
        min_seeds = min(TARGET_MIN_SEEDS, max_seeds)
        return _INSTRUCTION.format(
            min_seeds=min_seeds,
            max_seeds=max_seeds,
            language_name=language_name,
            max_body=MAX_SEED_BODY_CHARS,
            max_quote=MAX_EVIDENCE_QUOTE_CHARS,
            grounding_assumption=GROUNDING_ASSUMPTION,
            grounding_author_choice=GROUNDING_AUTHOR_CHOICE,
            grounding_thesis=GROUNDING_THESIS,
            forbidden="、".join(f"「{p}」" for p in FORBIDDEN_PHRASES),
        )
