# 生成言語を A層の生成文へ運ぶ（run options `language` の拡張・IK-0571 / IK-0546）

- 状態: **実装済み（2026-10-01・migration なし）**。砂場での再解析による確認は未実施
  （IK-0571 は open のまま）。
- 親文書: `discuss_opening_authoring_design.md` §14（run options `language` の新設。
  discuss_opening / contextual_explanation の 2 ステージ）
- 課題: IK-0571（A層の英語の生成文が日本語の画面に出る）/ IK-0546（主グラフの stage 名が英語）

## 1. 何を足したか

§14 で解析 run に足した `document_analysis_runs.options.language`（`ja` / `en`）を、生成した
文章が教員・学習者の画面に届く残りの A層 agent 4 つへ運ぶ。

| ステージ | 言語指定の対象（生成した文章） | 対象外にしたもの（理由） |
|---|---|---|
| `paper_skeleton` | `paper_goal.text/reason`・`central_question.text/reason`・`headline_claim.reason`・`supporting_subclaims[].reason`・`logical_blocks[].label/summary/reason`・`excluded_regions[].reason`・`review_notes` | `headline_claim.text` / `supporting_subclaims[].text`（主張文。後段の主張採否の文脈になるので論文の言語のまま） |
| `thesis_reconstruction` | `central_thesis.text/reason`・`alternative_theses[].text/reason`・`support_structure.*[].text/reason`・`excluded_from_core[].reason`・`review_notes` | — |
| `narrative_annotator` | `graph_summary`・`node_narratives[].narrative_role/reason`・`edge_narratives[].transition_text/reason`・`review_notes` | — |
| `component_assembly` | `components[].teaching_takeaway`・`components[].dependencies[].reason`・`components[].review_notes`・`assembly_hints[].reason`・`review_notes` | `label` / `summary` / `reason` / `inputs・outputs・preconditions・cautions` の文（下記 §2） |

**対象外の agent**:

- `equation_semantics` — `summary` / `reason` は決定論の後段が英語のキーワードで読む
  （`derivation_chain._infer_operation` / `system_derivation._operation_for_group` が
  `lineariz` / `eliminat` / `solve` 等から operation を決め、TheoryOperationGraph の edge_type と
  stage を決める。`symbol_registry.link_normalizer` は `Eq. (3)` 型の参照を読む）。訳すと graph の
  構造が変わる。
- `claim_qualification` — 主張文（atomic rewrite の `text`）と `evidence_quote` は論文の言語のまま
  （逐語の根拠の規律）。
- `derivation_chain` / `figure_table_semantics` — LLM を呼ばない（figure_table_semantics の LLM
  enricher は未配線）。step の `reason` は決定論のテンプレート。
- `component_graph` — 生成文は `reasoning`（debug 用）だけ。main の label は #308 の規律。
- `dsl_linking` — ノード名は概念名（主張の概念接地 K-2 が語境界一致で照合する）。

## 2. 不変条項

- **GL1 未指定なら何も変わらない**: `language` が無い・語彙外なら prompt は従来と 1 バイトも
  同じ（system は各 prompt の `_SYSTEM_CONTENT` のまま）で、orchestrator は `agent.run()` に
  `language` kwarg を渡さない。
- **GL2 LLM 呼び出しを増やさない**: 同じ 1 コールの system 末尾に `## Output Language` 節が
  付くだけ。CostGate・`LLM_CALLING_STAGE_NAMES` は不変。
- **GL3 論文由来のものは翻訳させない**: 逐語引用・記号・LaTeX・ID・語彙値・論文から取った
  名前は原文のまま（節の本文が明示する）。user メッセージ（論文素材の区画）には足さない。
- **GL4 決定論の後段が読む文章は対象にしない**: 英語のキーワード照合で構造
  （operation / support_role / 分割推奨 / cartridge 別名による概念照合）を導く後段の入力に
  なっているフィールドは、agent ごとの `GENERATED_PROSE_FIELDS` に入れない。言語の指定で
  graph の構造が変わらないようにする。

## 3. 実装

- 正本は `src/episteme_graph/agents/generation_language.py`（stdlib のみ。`apply_generation_language`
  / `generation_language_section` / `normalize_generation_language`）。contextual_explanation の
  `## Language` 節（§14）は prompt 本文に組み込まれた別方式のまま残す（既定文言の逐語一致を
  テストが固定しているため）。
- 各 prompt factory は `language` 属性と `GENERATED_PROSE_FIELDS` を持ち、`build_messages` /
  `build_repair_messages` が `apply_generation_language` を通す（修復の再試行にも同じ節が付く）。
  各 agent の `run(..., language=None)` が factory に設定する（次の run の未指定で戻る）。
- orchestrator は `_run_language(ctx)` が run options を読み、`_language_run_kwargs(agent, ctx)`
  が「指定あり かつ run が受け取れるとき」だけ `{"language": ...}` を返す（差し替えられた
  テスト用 agent が受けないときは渡さない — apparatus_semantics と同じ防御）。

## 4. IK-0546（主グラフの stage 名）

graph_json の main `label` は #308 の規律で英語の stage 名のまま（validator も不変）。表示側で
`core/element_vocab.theory_stage_display_label()`（`"<Stage>: 説明"` の旧形は説明を残す・stage に
引けないラベルはそのまま）を通す。2026-09-30 の IK-0566 でグラフレビューの canvas は
`display_label` で日本語になっていたが、次の 3 経路が英語の stage 名を出していたので同じ表に
通した。

- 学習者「わたしの地図」の旅 [1]（`core/personal_graph/journey.py`）— 「この◯◯は理論構成『Theory basis』の一部です」
- グラフ全体対話の grounding（`core/deliberation/graph_dialogue.py`）— 英語の段名が日本語の応答に混ざる
- SA層のグラフレビュー解決器（`core/assistant_context/resolvers/graph_review.py`）— 選択ノード・章→ノードの事実文

既に訳していた経路: 近傍ビュー（`nearby.node_display_label`）/ discuss 開幕 / 楽屋（descent）/
学習チャットの SA層解決器 / 理論モジュール / W層 context lens。

## 5. 残っていること

- 既存の教材は再解析しないと生成文が変わらない（言語を選んで再解析）。
- `component_assembly` の `summary` / `label` と `equation_semantics` の `summary` は英語のまま
  （GL4）。訳すには後段の英語キーワード照合を enum フィールド（`operation` /
  `responsibility_type` / `equation_type`）に寄せるか、表示専用の別フィールドを同じコールで
  足して永続化・表示まで配線する必要がある（別件）。
- 生成言語を変えて再解析すると `central_thesis.text` が変わるので、discuss 開幕の承認済み
  「議論のきっかけ」は指紋不一致で `stale` 表示になる（内容が変わったので正しい挙動）。

ガードレール: `src/tests/agents/test_generation_language.py` / `backend/tests/test_generation_language_agents.py`
（+ 既存 `test_generation_language_option.py`）。
