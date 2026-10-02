# 生成言語を A層の生成文へ運ぶ（run options `language` の拡張・IK-0571 / IK-0546）

- 状態: **実装済み（2026-10-01・§6 追補 2026-10-02・migration なし）**。砂場での再解析による確認は未実施
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
| `component_assembly` | `components[].summary`（§6 で追加）・`components[].teaching_takeaway`・`components[].dependencies[].reason`・`components[].review_notes`・`assembly_hints[].reason`・`review_notes` | `label` / `reason` / `inputs・outputs・preconditions・cautions` の文（下記 §2・§6） |
| `figure_table_semantics`（§6 で追加・LLM なし） | 図・表の `teaching_takeaway`（決定論のテンプレート文。`ja` なら日本語のテンプレート） | `caption`（論文の原文）/ `figure_type`・`table_type`・`comparison_axes` の語彙値 |

**対象外の agent**:

- `equation_semantics` — `summary` / `reason` は決定論の後段が英語のキーワードで読む
  （`derivation_chain._infer_operation` / `system_derivation._operation_for_group` が
  `lineariz` / `eliminat` / `solve` 等から operation を決め、TheoryOperationGraph の edge_type と
  stage を決める。`symbol_registry.link_normalizer` は `Eq. (3)` 型の参照を読む）。訳すと graph の
  構造が変わる。
- `claim_qualification` — 主張文（atomic rewrite の `text`）と `evidence_quote` は論文の言語のまま
  （逐語の根拠の規律）。
- `derivation_chain` — LLM を呼ばない。step の `reason` は決定論のテンプレート。
- `figure_table_semantics` の LLM enricher — 未配線（`figure_enricher` / `table_enricher` を渡す経路が
  orchestrator に無い）ので、言語を運ぶ先の生成文が無い。決定論テンプレートの `teaching_takeaway` だけを
  §6 で言語に合わせた。
- `apparatus_semantics` — 出力（装置同定・パーツ・図の分類・反復照合の項目）は教員のレビュー面
  （図モーダル・「深く検討」・ライブラリ昇格）にしか出ず、学習者には届かない。vision の 5 種の prompt に
  節を足しても呼び出し回数は増えないが、学習者向けの効果が無いので v1 は対象外。
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
  graph の構造が変わらないようにする。後段を**宣言された語彙値を先に読む**形に寄せた
  フィールドは対象にしてよい（§6。component の `summary`）。対象外の現行リスト:
  component の `label`（stable_key と名前照合の材料）/ `reason` / 入出力・前提・注意の `text`、
  `equation_semantics` の `summary` / `reason`、claim の主張文と逐語引用。

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
- `component_assembly` の `label` と `equation_semantics` の `summary` は英語のまま（GL4・§6.3）。
  component の `summary` は §6 で対象にした。
- 生成言語を変えて再解析すると `central_thesis.text` が変わるので、discuss 開幕の承認済み
  「議論のきっかけ」は指紋不一致で `stale` 表示になる（内容が変わったので正しい挙動）。

ガードレール: `src/tests/agents/test_generation_language.py` / `src/tests/agents/test_generation_language_structure.py` /
`backend/tests/test_generation_language_agents.py`（+ 既存 `test_generation_language_option.py`）。

## 6. 後段を語彙値に寄せて component の summary を対象に加える（2026-10-02 追補）

§5 の残りのうち、component の `summary` を英語のキーワードで読んで構造を導いていた後段を洗い出し
（`grep` で `summary` / `teaching_takeaway` を `lower()` / 部分一致で読む箇所を全件）、**LLM が同じ
コールで宣言する語彙値**（`operation` / `primary_operation` / `responsibility_type`。prompt の出力
スキーマで必須の 1 component = 1 operation / 1 responsibility）を先に読む形に寄せた。語彙値は
生成言語の指定で翻訳されない（GL3）ので、宣言のある component では構造が言語に依らない。

### 6.1 依存表

| 箇所 | 読んでいた英語キーワード | 導くもの | 処置 |
|---|---|---|---|
| `component_assembly/enrichment.py::_flow_relation` | label+summary+reason の `eliminat` / `consistency` / `constraint` / `residual` / `diagnos` / `forecast` / `correct` | `internal_flow[].relation` | **語彙値へ**: `operation` / `primary_operation` を宣言していればそれだけで決める（`declared_operation_text`）。無い component だけ従来の本文照合 |
| `component_assembly/component_refiner.py::_infer_operation_from_text` | label+summary+reason × `_OPERATION_FAMILY_RULES` | `operation`（空のときの補完）→ `responsibility_type` | **語彙値へ**: `primary_operation` があればその family。無いときだけ本文照合 |
| `component_assembly/claim_centered_planner.py::_infer_support_role` | label+summary+reason+operation の `limitation` / `result` / `derive` 等 | `support_role`（LLM が空にしたときの補完）→ support_kind / 距離 / theory_bundle | **語彙値へ**: `responsibility_type` → 表、無ければ `operation` → 表（既定 `theory_base`）。どちらも無い component だけ本文照合 |
| `component_refiner.py::_component_text` / `granularity_analyzer.py::_component_text` | label+summary+reason+teaching_takeaway | （未使用） | **削除**（呼び出し元ゼロ） |
| `component_assembly/enrichment.py::_component_text`（`_fill_concepts`） | label+summary+reason+teaching_takeaway を cartridge 別名・claim 概念名と語境界一致 | `concepts`（→ `_teaching_granularity` の密度注記） | **置き換えられない**: 語彙の意味照合で語彙値に写せない。言語指定時は summary / teaching_takeaway からの一致が「その言語の別名」に限られる（claim 由来の概念は不変）。構造（node / edge / stage / 分割）は変えない — 概念の列は component の注記として保存・表示され、概念数は分割後の教えやすさ注記（warning）にだけ効く |
| `component_graph/normalizer.py`（`theory_object` / `display_label` / `_label_for_component` の fallback）・`input_builder.build_nodes`（label 空のとき） | — | 表示文字列 | 表示のみ。対象に加えても構造は不変（ノード ID・層・辺は summary を読まない） |
| `backend/core/course_content_builder.py::_component_refs_for_topic`（`_overlap_score`） | label+summary+teaching_takeaway とトピック題名のトークン重なり | 題名一致の救済 mapping | 言語中立（ASCII と CJK のトークン両方）なので処置不要。日本語の題名とは一致しやすくなる |
| `course_mapping` / `blueprint` / `theory_bundle` / 学習単位 / コース教材 | — | 表示文字列 | 表示のみ |
| stable_key（`component_stable_key`）・live 行同期の fallback 照合（`name`） | label | 同一性 | label は対象外のまま（不変） |
| `equation_semantics` の summary — `derivation_chain/agent.py::_infer_operation`・`system_derivation.py::_operation_for_group` | `lineariz` / `substitut` / `eliminat` / `solve` / `constraint` 等 | 導出 step の operation → TheoryOperationGraph の edge_type / stage | **置き換えられない**: 式の語彙値（`equation_type` / `secondary_types`）は線形化・代入・消去の区別を持たず、区別を持つ語彙値を足すには A層の出力スキーマを変える必要がある（本件の範囲外）。式の summary は対象外のまま |
| `equation_semantics/schema.py`（付録 × `main result` の整合）・`validator.py::_check_cartridge_terms` | `main result` / cartridge 別名 | review 理由・warning | 同上（式の summary を訳さないので不変） |

### 6.2 従来入力の結果

- `language` 未指定の prompt は 1 バイトも変わらない（GL1）。
- 語彙値を宣言していない component は従来の本文照合のままなので結果は同じ（実論文 3 本の
  component_assembly artifact — `backend/tests/fixtures/theory_modules/` — で旧式と突き合わせて固定）。
- 語彙値を宣言した component では、宣言と summary のキーワードが食い違うときに宣言が勝つ
  （例: `operation="derive"` で summary に "eliminate" とある — 従来は `eliminate_parameter`、
  いまは `derive_from`）。prompt が宣言を「その component の主たる操作」と定めているので宣言を正にした。

### 6.3 figure_table_semantics

LLM enricher は未配線なので、言語を運ぶ先は決定論テンプレートの `teaching_takeaway`
（"Use this {figure_type} to support: …"）だけ。`run(..., language=None)` を受け、`ja` なら
「この比較の図は「…」の裏付けに使えます。」の形にする（caption の抜粋は論文の言語のまま・語彙値は record に
原形で残す）。未指定なら従来の英語テンプレートと同一。orchestrator は `_build_figure_table_semantics(...,
language=_run_language(ctx))` → `_language_kwargs_for(agent, language)`（`_language_run_kwargs` の本体）で渡す。
LLM 呼び出しは無い。

### 6.4 残り

- refiner が分割で作る子 component の `summary` / `teaching_takeaway`（`_responsibility_summary` /
  `_suggested_takeaway` 等）と enrichment の `_fill_teaching_takeaway` / `role_in_thesis` の補完文は
  英語の決定論テンプレートのまま（言語を refiner / enrichment まで運ぶ経路が無い）。
- 語彙値を宣言しなかった component では、言語指定時に summary 由来のキーワードが拾えず補完が既定値に寄る。
- 既存教材は再解析しないと変わらない。砂場での確認は未実施。
