"""Prompt construction for LandscapePlacementAgent.

規約準拠: ``core/llm_worker/`` 系統（tension / structure_anchor / reconstruction /
doubt / deliberation.standardization / discuss_opening）と同じく **user ロール1本**に
instruction と入力を連結する（`system` ロール・`temperature` は使わない — 開発ルール4）。

方針（設計書 §7.3）:

- **複数領域×複数観点への配置が既定**。1つに絞らせない（LS1: 地図は正解ではなく投影）。
- **根拠は逐語引用**。素材に無い文字列を書かせない（LS4 の捏造ガード）。
- **無理に配置しない**。適合しないドメインは ``unplaced_domains`` に理由付きで申告する
  （LS10: 配置不能は失敗ではなく信号）。
- ``reason`` は日本語の事実文。断定・煽り・評価をしない。

カテゴリギャップ候補（``docs/features/category_gap_candidates_design.md`` §5.1）:

- 骨格は ``region → concepts[]`` の**ネスト**で提示し、各領域に
  ``concept_slots_remaining`` を添える（「この領域の概念はこの N 件だけ」という閉世界）。
- 候補は**任意・最後・上限あり**。配置（placements）の判定を犠牲にしてまで出させない。
- 既存概念の言い換えを新概念として申告させない（捏造ガード）。
"""
from __future__ import annotations

import json

from .input_builder import LandscapePlacementInputBuilder
from .schema import (
    GAP_LAYERS,
    LandscapePlacementInput,
    MAX_EVIDENCE_QUOTE_CHARS,
    MAX_PROPOSED_LABEL_CHARS,
    MAX_REASON_CHARS,
    PERSPECTIVE_HINTS,
    PERSPECTIVES,
    TARGET_MAX_PLACEMENTS,
    TARGET_MIN_PLACEMENTS,
)

_INSTRUCTION = """\
あなたは、解析済みの学術論文を「分野の基準地図（凍結済みの骨格）」のどこに位置づけるかを
提案する担当です。地図は唯一の正しい分類ではなく投影です。**1つの論文が複数の領域に、
異なる観点で関係するのが普通**です。無理に1箇所へ押し込めないでください。

地図は `domains[].regions[]`（領域）と、その配下の `concepts[]`（概念）として提示します。
**ある領域の概念は、そこに並んでいるものだけです**（省略していません）。
`concept_slots_remaining` は、その領域にあと何件の概念を追加できるかの残り枠です。

## 作るもの
### 1. `placements` — 配置候補（{min_placements}〜{max_placements} 件程度が目安、最大 {hard_max} 件）
各件は次を持ちます。

1. `domain_key` — 下の `domains` に**実在する** domain_key をそのまま書く。
2. `node_id` — その domain_key の `regions[]` または `regions[].concepts[]` に
   **実在する** node_id をそのまま書く。
   別ドメインの node_id を混ぜてはいけない。新しい node を作ってはいけない。
3. `perspective` — 次のいずれか1つ。
{perspective_lines}
4. `weight` — その領域との関連の強さ 0.0〜1.0（必須。省略しない）。
5. `reason` — なぜその領域・その観点と関係するかを**日本語の事実文**で1〜2文
   （{max_reason} 文字以内）。断定・評価・煽りをしない
   （「重要である」「画期的だ」等の価値判断を書かない）。
6. `evidence_quote` — 与えられた素材からの**逐語引用**。翻訳・要約・言い換えを一切せず、
   原文の文字列をそのままコピーする（英語素材なら英語のまま）。{max_quote} 文字以内。
   素材に無い文字列を書いてはいけない。
7. `claim_id` — 引用元が `claims` の1件なら、その `claim_id` を書く。
   論文全体の記述（title / 問い / 命題 / goal）から引いた場合は null。
8. `confidence` — 0.0〜1.0。

同じ `domain_key` の複数ノードに置いてよく、同じノードに違う観点で置いてもよい。
ただし `(domain_key, node_id, perspective)` の組は重複させないでください。

### 2. `unplaced_domains` — 配置できなかったドメインの申告
どのノードにも当てはまらないドメインは、**当てはまらないことを申告してください**
（無理な配置より価値があります）。各件は `domain_key` と、当てはまらない理由を
日本語の事実文で書いた `reason` を持ちます。

### 3. `category_gaps` — 地図にまだ無いカテゴリの候補（任意・最大 {gap_max} 件）
{gap_body}
## 硬い制約
- **素材に無いことを書かない。** 論文が扱っていない対象・方法を扱っているように書かない。
  分野の一般知識で素材を補完しない。
- **`domain_key` / `node_id` を創作しない。** 提示された一覧の文字列だけを使う。
- **どの領域にも置けないなら `placements` を空配列にし、その理由を
  `unplaced_domains` に全ドメイン分書く。** 適当な配置で埋めない。
- **配置（placements）の判定を最優先**してください。`category_gaps` は任意で、
  最後に考えるものです（候補を作るために配置の判定を歪めない）。
- **既存の概念の言い換えを新しい概念として申告しないでください。** 提示済みの領域・概念と
  同じものに別の言い方を付けただけの候補は作らない。
- 返すのは **JSON のみ**。前置き・マークダウンのコードフェンスを付けない。
"""

# 候補セクションの本文（上限 > 0 のとき）。1件のフィールドは backend 側の
# ``landscape_gap_signals`` との契約なので名前を変えない（設計書 §5.1 / §5.2）。
_GAP_BODY = """\
論文が扱っている主題が、提示された地図では言い表せなかった場合にのみ申告します。
**無ければ空配列**にしてください（埋める必要はありません）。各件は次を持ちます。

1. `layer` — `{gap_layers}` のいずれか。
   - `concept`: 配置した領域には当たるが、**その領域の `concepts[]` が
     この論文の対象を覆っていない**場合。
   - `region`: **どの領域にも当たらない**場合のみ。
2. `domain_key` — 提示された domain_key をそのまま書く。
3. `parent_region_id` — `layer` が `concept` のときは、親となる**実在する領域の**
   node_id（必須）。`layer` が `region` のときは空文字列 `""`。
4. `proposed_label` — 提案するカテゴリ名（{max_label} 文字以内の短い名詞句。
   説明文にしない）。
5. `reason` — なぜこの地図では言い表せないかを日本語の事実文で1〜2文
   （{max_reason} 文字以内）。地図を欠陥として評価する言い方をせず、事実だけを書く。
6. `evidence_quote` — 素材からの**逐語引用**（翻訳・要約・言い換えをしない、
   {max_quote} 文字以内）。素材に無い文字列は書かない。
7. `confidence` — 0.0〜1.0。
"""

_GAP_BODY_DISABLED = """\
今回は候補を申告しません。`category_gaps` は**空配列**にしてください。
"""


def _output_schema(*, include_gaps: bool = True) -> dict:
    schema = {
        "placements": [
            {
                "domain_key": "提示された domain_key",
                "node_id": "その domain の regions[] / regions[].concepts[] に実在する node_id",
                "perspective": " | ".join(PERSPECTIVES),
                "weight": 0.0,
                "reason": "なぜこの領域・この観点と関係するか（日本語の事実文）",
                "evidence_quote": "素材からの逐語引用（翻訳しない）",
                "claim_id": "引用元 claim の id、無ければ null",
                "confidence": 0.0,
            }
        ],
        "unplaced_domains": [
            {
                "domain_key": "配置できなかった domain_key",
                "reason": "当てはまらない理由（日本語の事実文）",
            }
        ],
        # 任意セクション（省略・空配列可）。placements / unplaced_domains の
        # 既存契約は変えない（設計書 §5.1）。
        "category_gaps": [
            {
                "layer": " | ".join(GAP_LAYERS),
                "domain_key": "提示された domain_key",
                "parent_region_id": "layer=concept のとき親領域の node_id、region のとき \"\"",
                "proposed_label": "提案するカテゴリ名（短い名詞句）",
                "reason": "この地図では言い表せない理由（日本語の事実文）",
                "evidence_quote": "素材からの逐語引用（翻訳しない）",
                "confidence": 0.0,
            }
        ],
    }
    if not include_gaps:
        schema["category_gaps"] = []
    return schema


class LandscapePlacementPromptFactory:
    def __init__(self, input_builder: LandscapePlacementInputBuilder | None = None) -> None:
        self._input_builder = input_builder or LandscapePlacementInputBuilder()

    def build_content(
        self,
        item: LandscapePlacementInput,
        cartridge=None,
    ) -> str:
        """LLM に渡す user メッセージ1本分の文字列を組み立てる。"""
        parts: list[str] = [self._instruction(item)]
        parts.append(
            "\n## 素材と配置先（`evidence_quote` は paper / claims の文字列だけから引用してよい）"
        )
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
        parts.append(
            json.dumps(
                _output_schema(include_gaps=self._gap_max(item) > 0),
                ensure_ascii=False,
                indent=2,
            )
        )
        return "\n".join(parts)

    def _instruction(self, item: LandscapePlacementInput) -> str:
        hard_max = max(1, int(item.max_placements or 1))
        max_placements = min(TARGET_MAX_PLACEMENTS, hard_max)
        min_placements = min(TARGET_MIN_PLACEMENTS, max_placements)
        perspective_lines = "\n".join(
            f"   - `{name}`: {PERSPECTIVE_HINTS.get(name, '')}" for name in PERSPECTIVES
        )
        gap_max = self._gap_max(item)
        gap_body = (
            _GAP_BODY.format(
                gap_layers="` / `".join(GAP_LAYERS),
                max_label=MAX_PROPOSED_LABEL_CHARS,
                max_reason=MAX_REASON_CHARS,
                max_quote=MAX_EVIDENCE_QUOTE_CHARS,
            )
            if gap_max > 0
            else _GAP_BODY_DISABLED
        )
        return _INSTRUCTION.format(
            min_placements=min_placements,
            max_placements=max_placements,
            hard_max=hard_max,
            perspective_lines=perspective_lines,
            max_reason=MAX_REASON_CHARS,
            max_quote=MAX_EVIDENCE_QUOTE_CHARS,
            gap_max=gap_max,
            gap_body=gap_body,
        )

    @staticmethod
    def _gap_max(item: LandscapePlacementInput) -> int:
        try:
            return max(0, int(item.max_gaps_per_document))
        except (TypeError, ValueError):
            return 0
