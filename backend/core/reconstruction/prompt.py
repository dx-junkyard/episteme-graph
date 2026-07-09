"""item オーサリング用プロンプト（LLM は司会者=出題者であって採点者ではない, §1.4）。

LLM は claim（ground truth）を見て、learner に「予測」または「言い直し」をさせる
問いと、predict の場合は選択肢 + 想定解（expected）を生成する。判定ロジックは
持たせない（判定は非LLM の diff.py が担う）。
"""

from __future__ import annotations


def build_instruction(preferred_mode: str) -> str:
    return f"""あなたは大学院の学習を支援する出題者です。以下の「主張（claim）」を答えキーとして、
学習者に理論を**自分で再構成**させる問いを1つ作ってください。あなたは採点者ではなく、
学習者に生成させ、そのズレを後で構造照合するための問いを用意する役割です。

出題モードは2種類:
- predict（予測）: 変数・量の関係を予測させる。選択肢（3〜4個）と想定解を用意する。
  例: 「δO は g に対してどう振る舞う？」→ 選択肢 [∝ g, ∝ 1/g, ∝ g², 依存しない]
- restate（言い直し）: この結果を学習者自身の言葉で一文に述べさせる（自由記述）。選択肢は空。

このclaimでは "{preferred_mode}" モードが下地として選ばれています。関係を選択肢に構造化
できないと判断したら restate に下げてかまいません。

厳守事項:
1. 問い文（prompt）に**答えそのもの（claim 本文・数式・結論）を書かない**。学習者が
   自分で再構成する余地を残す。
2. predict の選択肢は互いに排他的で、想定解（expected.option_id）は必ず選択肢の id の一つ。
   もっともらしい誤答（distractor）を含める。
3. 特定分野の用語を勝手に足さない。claim にある概念・記号だけを使う。
4. reason には「なぜこの問いが理解の再構成になるか」を一文で書く。confidence は 0.0〜1.0。

出力は次の JSON のみ（前後に説明文やコードフェンスを付けない）:
{{
  "elicit_mode": "predict" | "restate",
  "prompt": "学習者への問い（答えを含めない）",
  "response_space": [{{"id": "opt_a", "label": "..."}}, ...],   // restate は []
  "expected": {{"option_id": "opt_b", "relation_type": "..."}},  // restate は {{}}
  "claim_fields_used": ["concepts", "equation", ...],
  "author_confidence": 0.0,
  "reason": "この問いが再構成になる理由（一文）"
}}"""


def build_repair_prompt(previous_raw: str, errors: list[str]) -> str:
    joined = "\n".join(f"- {e}" for e in errors)
    return f"""前回の出力は以下の検証エラーで不合格でした:
{joined}

前回の出力:
{previous_raw}

エラーをすべて修正した JSON を、同じスキーマで1つだけ再出力してください（説明文なし）。"""
