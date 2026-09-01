# PDF 解析 Agent 詳細

[← ドキュメント目次](../README.md) ｜ [← パイプライン概要](overview.md)

> **更新注記（2026-08-14）:** **ContextualExplanationAgent** / **DiscussOpeningAgent** /
> **LandscapePlacementAgent** の3 agent を §2 に追補し、実在しない `graph_narrative/` の記述を
> 削除、§3 の共有モジュール表に `figure_modes.py` を追加した。

各 Agent の役割・入出力・LLM/決定論の区別をまとめます。実装は `src/episteme_graph/agents/<agent_name>/`。

---

## 1. Agent ディレクトリの共通構造

各 Agent ディレクトリは最低限、次のファイルを持ちます（実装ルール: `backend/` ではなく `src/episteme_graph/agents/` に置く）。

```
<agent_name>/
  __init__.py
  agent.py            # Agent 本体クラス。run() が公開インターフェース
  cartridge_loader.py # CartridgeLoader（共通インターフェース）
  input_builder.py    # LLM 入力の構築
  prompt.py           # プロンプト定義
  llm_client.py       # LLM API 呼び出し（structured output）
  schema.py           # dataclass / Pydantic モデル
  validator.py        # 出力スキーマ検証
  repair.py           # validation 失敗時の再試行
  examples/           # サンプル入出力 JSON
```

共通ルール:
1. **cartridge-aware** — active cartridge があれば語彙・検証に使う。無くても単独動作する（すべて Optional）。
2. **structured output** — LLM 出力は必ず JSON スキーマ検証し、失敗時は repair/retry。
3. **evidence-based** — 各フィールドに `reason` と `confidence`(0.0〜1.0)。
4. **情報を落とさない** — 不明は `unknown` / `deferred` で保持。
5. **maturity/review の最終確定禁止** — provisional に留める。

出力 dataclass は `to_dict()` で JSON シリアライズ可能。多くの Agent は `make_fallback(...)` を持ち、致命的でない失敗時にも検証 issue を付けた結果を返します。

---

## 2. 各 Agent

### DocumentStructureAgent（#216）— structure-first
`document_structure/agent.py`。PDF（または TEI-XML）から **ブロック・セクション・メタデータ**を復元。パーサ/レイアウト優先で意味解釈はしない。曖昧なブロックのみ LLM 補助。
- 入力: PDF bytes / TEI-XML
- 出力: `DocumentStructureResult`（blocks, sections, metadata）

### EvidenceRegistryBuilder（#237）— 決定論
`evidence_registry/builder.py`。PDF **原文由来の evidence** を一元管理。`evidence_id → evidence_text`（ソース位置つき）のインデックスを作り、重複排除。
- 出力: `EvidenceRegistryResult`。以降の claim/equation はここを `source_evidence_ids` で参照。

### PaperSkeletonAgent（#217）— LLM-first
`paper_skeleton/agent.py`。論文の backbone（headline claim, 問題設定, 核となる関係, 結論, セクション役割）を仮説化。
- 入力: `DocumentStructureResult`
- 出力: `PaperSkeletonResult`

### RhetoricalRoleAgent（#218）— LLM-first
`rhetorical_role/agent.py`。chunk/span の論理役割（assumption / derivation / result など）をブロック単位で判定。
- 入力: DocumentStructure + PaperSkeleton
- 出力: `RhetoricalRoleResult`

### ClaimQualificationAgent（#219, #317）— LLM-first
`claim_qualification/agent.py`。役割付き span の **採否・区分（paper_core/supporting/background）・粒度**を判定し、**atomic rewrite**（1 claim = 1 最小命題への再構成）を実施。
- 出力: `ClaimQualificationResult`。`QualifiedSpanRecord.atomic_claims`（text / normalized_text / claim_type_candidate / atomicity / status / source_span_id / evidence_quote / qualification_reason / confidence）。
- atomic 化できない箇所は `atomicity="non_atomic"` / `status="review_required"` で保持。

### EquationSemanticsAgent（#220）— LLM-first
`equation_semantics/agent.py`。数式ブロックの意味役割（equation_type、定義/使用シンボル、仮定、入出力式）を復元。`to_equations_export()` で `equations.json` 化。受理判定は `EquationAcceptanceGate`。
- 出力: `EquationSemanticsResult`（EquationRecord のリスト）

### ClaimObjectBuilder（#237, #317）— 決定論
`claim_object_builder/builder.py`。ClaimQualification の atomic 候補 + EvidenceRegistry から **最終 claims.json** を組立。atomic rewrite はせず、変換・リンク・検証に責務を限定。`source_evidence_ids` を付与し atomicity を検証。**最終 claim_id の真実の源**（ID 正規化の基準）。

### SymbolRegistryBuilder（#355）— 決定論
`symbol_registry/builder.py`。数式記号の定義・表記ゆれ・スコープを一元管理。LaTeX 正規化（`\mathrm` 除去、`\beta`→`β`、数式の大文字小文字は保持）+ カートリッジ aliases で重複排除、再定義検出。
- 出力: `SymbolRegistryResult`

### DerivationChainAgent（#237）— 決定論
`derivation_chain/agent.py`。EquationSemantics のリンクから **式間導出チェーン**を構築（leaf-first 後方走査）。任意で claim チェーンも。
- 出力: `DerivationChainResult`（steps, operations, claims）
- step ⇄ claim 参照（`required_claim_ids`）は本 agent の実行時点では未確定（式由来の合成 claim は
  本 agent より後に生まれるため結べない）。合成フックが実行後に結び直し（出力式 ∪ 入力式）、
  未解決参照を掃除して `derivation_chain` artifact を再保存する
  → [DSL と理論操作グラフ](theory-graph.md)「step ⇄ claim 参照のバックフィル契約」。

### FigureTableSemanticsAgent（#237）— caption-first
`figure_table_semantics/agent.py`。図表の意味を caption 優先で復元（LLM enricher は任意）。
- 出力: `FigureTableSemanticsResult`

### ApparatusSemanticsAgent（L層）— vision LLM・オプトイン
`apparatus_semantics/agent.py`。図画像（`document_figures` + MinIO `figure-images`）から
装置・パーツ候補を抽出する vision LLM agent（正本:
[画像パイプライン + L層 設計書](../features/image_pipeline_knowledge_library_design.md) §5）。
`figure_table_semantics` 直後に実行されるが、**アップロード時の `analyze_images`
チェックボックスがオン（既定 off）のときのみ**動く。off の run は
`{"skipped_by_option": true}` を `stage_outputs` に正直に記録する。
- 入力: 画像 + caption + 近傍本文（`figure_context.py` の決定論的収集）+ 図中ラベル
  （`document_figures.inner_labels`、migration 051）+ 略語辞書 + L層ライブラリ**凍結版**の
  retrieval 候補（0 件でも単独動作 — `match_status ∈ {novel, unknown}` に縮退）
- 出力: `ApparatusSemanticsResult`（`apparatus_records`: 装置同定候補・parts・connections。
  #496 以降は図の分類 `suggested_mode` / `analysis_profile` も同一 vision コールで出力）
- **label_ref grounding**: `ApparatusPart.label_ref`（図中ラベルへの参照）は validator が
  `inner_labels` 実在を hard error 検査し、`bbox` / `expanded_name` は **LLM 出力からは
  取らず** `agent.py::_attach_label_grounding` が label_ref → inner_labels / abbreviations の
  突合で決定論的に付与する
- **review_required 徹底**: 全出力は常に `review_status='review_required'` 系で、
  `source_backed` を自動付与しない（確定は人間のみ）。role は本文からの verbatim quote で
  裏付け、根拠のない役割は書かせない。2 回修復失敗した図も `match_status='unknown'` /
  `confidence=0.0` の 1 レコードとして保持（P4）
- 上限: `APPARATUS_MAX_IMAGES_PER_DOCUMENT`（既定 20）/ `APPARATUS_MAX_CALLS_PER_DAY`
  （既定 30）。超過分は `skipped_by_limit` で保持しステージは正常完了
- vision 呼び出しは `core/llm.py` の `generate_structured_with_images()`（v1 は OpenAI 経路のみ）。
  装置候補は ComponentAssembly 経由で `status='candidate'` の theory_components になるが、
  **TheoryOperationGraph には組み込まない**（式 backing が無いため）

### ThesisReconstructionAgent（#221）— LLM-first
`thesis_reconstruction/agent.py`。中心命題（headline thesis）と支持構造（supporting claims）を再構成。
- 入力: PaperSkeleton + ClaimQualification + EquationSemantics（+ claim objects）
- 出力: `ThesisReconstructionResult`

### DSLLinkingAgent（#222）— LLM-first
`dsl_linking/agent.py`。Claim/Equation/Thesis を **DSL グラフ**（predicate 付きノード・エッジ）に接続。`DSLGraphCleanup` で整形。
- 出力: `DSLLinkingResult`（nodes, edges with predicate, source_ids）

### ComponentAssemblyAgent（#223）— LLM-first + cartridge-aware
`component_assembly/agent.py`。claims/equations/thesis/DSL から **再利用可能コンポーネント**を生成（粒度のリファイン、決定論的フォールバックあり）。
- 出力: `ComponentAssemblyResult`

### ComponentGraphAgent（#266, #302）— 決定論 + LLM エッジ
`component_graph/agent.py` + `normalizer.py`。DerivationChain から **理論操作グラフ（TheoryOperationGraph）** を構築。ノード生成は決定論的、エッジ推論に LLM。詳細は [DSL と理論操作グラフ](theory-graph.md)。
- 入力: `components` / `dsl` / `derivations` / `claims`（`orchestrator._component_graph_claims` が
  EvidenceRegistry から `evidence_text` を解決して enrich 済み）/ `evidence_snippets`
- 出力: `ComponentGraphResult`（2 層: main / equation_detail、ソースバッキング状態つき）

### NarrativeAnnotator（#360）— LLM-first（構造非変更）
`narrative_annotator/agent.py`。main graph に reader 向けの narrative（`narrative_role`, `transition_text`, `graph_summary`）を**注釈として**付与。グラフ構造は一切変更しない（スナップショット比較で検証、違反は hard error）。出力はすべて `llm_proposed`。

### ContextualExplanationAgent（二層説明）— LLM-first
`contextual_explanation/agent.py`。パイプライン要素（figure / theory_component / theory_claim /
equation）に **2 層の説明**を生成する（正本:
[二層説明 設計書](../features/hierarchical_context_explanation_design.md) §5.1）。
- 入力: `ElementExplanationInput` のリスト。上位（何を支えるか）/ 下位（何から組み立てられているか）の
  文脈は**呼び出し側が解決済みテキストにして渡す**（構築は
  `backend/core/document_pipeline/contextual_explanation_inputs.py`）。agent 自身は不透明 ID を
  解決しない（設計原則 E4）
- 出力: `ContextualExplanationResult`。要素ごとに `contextual_explanation`（**この論文の中での役割**）と
  `generic_explanation`（**一般に何であるか**）
- **E3 幻覚ガード**: `generic_explanation` は L層ライブラリの確定リンク（`library_excerpt`）が
  供給された要素にだけ生成する。リンクが無ければ、モデルが「知っていても」生成しない（validator の hard error）
- バッチ: 既定 8 要素 = 1 コール（`DEFAULT_MAX_ELEMENTS_PER_CALL`）。repair は検証に落ちた要素だけを
  対象にし、同じバッチの正しい兄弟説明を巻き添えにしない。修復後も説明できない要素は
  `skipped_reason` 付きで結果に残す（P4）
- 上限: `CTXEXPL_MAX_ELEMENTS_PER_DOCUMENT`（既定 40）/ `CTXEXPL_MAX_CALLS_PER_DAY`（既定 20）。
  日次上限に達した run は LLM を呼ばず `skipped_by_limit` を `stage_outputs` に記録
- 保存: orchestrator が `element_explanations` へ `status='candidate'` で書く（確定は教員）

### DiscussOpeningAgent — LLM-first
`discuss_opening/agent.py`。discuss（論文と議論する）開幕画面のうち、既存成果物の投影では
作れない唯一の生成物「**議論のきっかけ**」（立場を求める問い）を **1 document = 1 LLM コール**で
生成する（正本:
[discuss 開幕素材のオーサリング 設計書](../features/discuss_opening_authoring_design.md) §4.1）。
- 入力: `DiscussOpeningInput`。`backend/core/discuss/authoring.py` が解決済みテキストへ展開した
  D層の未検証前提（`epistemic_ledger` の `untested` / `unknown`。初回解析で台帳が空のときは
  in-run artifact から同じ保守的マッピングで導出）+ derivation の `operation` 列（＝著者が
  別様にもできた選択）+ thesis の合成文
- 出力: `DiscussOpeningResult`（`DiscussionSeed` のリスト。狙いは 2〜3 件、上限は
  `DISCUSS_OPENING_MAX_ITEMS_PER_DOCUMENT`（既定 4）。溢れは `truncated` / `truncated_count` で申告）
- **素材が無い document は LLM を呼ばない** — `skipped_reason='no_source_material'` を記録する
  （根拠の無い火種を創作しない）
- validator: `evidence_quote` の verbatim 包含と、煽り語の denylist（`FORBIDDEN_PHRASES`＝
  「疑え」「ノーベル賞」等、D層と同じ禁止語彙）を hard error
- 上限・設定: `DISCUSS_OPENING_MAX_CALLS_PER_DAY`（既定 20）/ `DISCUSS_OPENING_LANGUAGE`（既定 `ja`）
- 保存: orchestrator が `element_explanations` へ `element_type='document'` / `role='discussion_seed'` の
  candidate として書く（再解析で candidate は superseded、approved は不変）

### LandscapePlacementAgent — LLM-first
`landscape_placement/agent.py`。論文を**凍結済みの基準地図（atlas 骨格）のアンカー**へ、複数観点
（`perspective`: subject / question / method / theory / observation / application）で配置する候補を
生成する。**全ドメインを 1 コール**で扱い、ドメイン同士を相対評価させる（正本:
[知識ランドスケープ 設計書](../features/knowledge_landscape_design.md) §7.3）。
- 入力: `LandscapePlacementInput`。`backend/core/landscape/builder.py` が渡す解決済みテキスト
  （論文タイトル / thesis / goal / claim テキスト）+ 各ドメインの骨格ノード一覧
- 出力: `LandscapePlacementResult`（`PlacementCandidate`: `node_id` / `perspective` / `reason` /
  `evidence_quote` ほか。配置できなかったドメインは理由付きで `unplaced_domains` に申告）
- validator の hard error: `node_id` が供給された骨格に実在すること・`evidence_quote` の verbatim・
  perspective 語彙。**地図を勝手に増やさない**（LS7）
- **「置けない」は失敗ではなく信号**（LS10）。無理に当てはめず `unplaced_domains` に残す
- agent は status を出さない。`builder.py` が `status='inferred'` で書き、確定は教員のみ（LS3）
- **カテゴリギャップ候補**（`category_gaps`）は**同一コール**に相乗りする追加出力
  （正本: [カテゴリギャップ候補 設計書](../features/category_gap_candidates_design.md) §5.1）。
  検証は **warning-only の soft collector** で、不正な候補が配置を巻き添えにしない。骨格への反映は
  教員の明示操作のみ
- 上限: `LANDSCAPE_MAX_PLACEMENTS_PER_DOCUMENT`（既定 8）/ `LANDSCAPE_MAX_CALLS_PER_DAY`（既定 20）/
  `LANDSCAPE_GAP_MAX_PER_DOCUMENT`（既定 3）

### CourseMappingAgent（#237）— 決定論
`course_mapping/agent.py`。Component → Course topic へマッピング（既定は 1 component = 1 topic）。
- 出力: `CourseMappingResult`（topics with linked_component_ids, learning_objectives, prerequisites）→ `course_info.json`

### BlueprintAgent — 決定論
`blueprint/`。CourseMapping + Component からナラティブアーク（学習順序とステップラベル）を合成。

### ExportValidationGate — 決定論
`backend/core/document_pipeline/export_validation_gate.py`。最終検証。成果物の完全性・ソースバッキング整合性・スキーマ妥当性をチェック。非 atomic / split_pending な claim が main graph に強い backing として使われていないか等を明示 report（一部は hard error）。

### 未統合のディレクトリ
- `document_unit_boundary/` — 文書のユニット境界検出 Agent。実装（agent / detector / validator / schema）は
  存在するが、`orchestrator.py` の `PIPELINE_STAGES` にはまだ組み込まれていない（設計案は
  DocumentStructureAgent → 本 Agent → SourceChunking）。

---

## 3. 共有モジュール（agents/ 直下）

複数 Agent が使うライブラリ群。

| モジュール | 役割 |
|---|---|
| `theory_operations.py` | ドメイン中立な操作ファミリー分類（introduce_entity / define_relation / impose_assumption / construct_model / transform_representation / derive_consequence / evaluate_condition / compare_cases / validate_or_test / apply_to_context / state_limitation）。`theory_operations.classify_operation(operation_text, cartridge=...)` がキーワード（+ カートリッジの subtype ヒント）からファミリーへ。**同名の `component_graph/schema.py::classify_operation(operation)` は別物**（operation → (verb, edge_type, is_generic)。→ [理論操作グラフ](theory-graph.md)） |
| `figure_modes.py` | 図の提示モード語彙の正本（`FIGURE_MODES` = functional_diagram / data_plot / descriptive_image / mixed / unknown）。`effective_mode(suggested, reviewed)`（教員の `reviewed_mode` 優先）と、vision 不在時の caption ヒューリスティック `infer_mode_from_text()`（判別不能は `unknown`）。分類は常に候補で、確定は `document_figures.reviewed_mode`（教員）のみ |
| `claim_selection.py` | LLM ステージへ渡す claim の選択ポリシー。tier（paper_core→…）+ 関連度（headline 重なり）+ confidence で並べ、上限を超えた分は理由つきで除外 |
| `content_normalization.py` | 横断再利用のための claim/equation ハッシュ。散文は casefold、数式トークンは大文字小文字保持。`CONTENT_HASH_VERSION` でルール変更を検知 |
| `id_canonicalization.py` | 横断参照を canonical claim_id に揃える。`canonical_claim_id_for_span()` / `canonicalize_claim_refs()`。解決不能な参照は drop + warning |
| `cartridge_paths.py` | カートリッジのパス解決（`EPISTEME_CARTRIDGES_DIR` → 自動探索） |
| `llm_json_client.py` | プロバイダ対応の JSON LLM クライアント（`core.llm.generate_text` をラップ、タイムアウト/構造化出力/raw 追跡） |

---

## 4. 関連

- 抽出単位で貫いた説明（ブロック → チャンク / エビデンス → atomic claim → 理論部品）: [論文の抽出単位](extraction-units.md)
- ドメイン語彙の注入: [カートリッジシステム](cartridges.md)
- 理論操作グラフの内部構造: [DSL と理論操作グラフ](theory-graph.md)
- パイプライン全体の流れ: [パイプライン概要](overview.md)

---

[← パイプライン概要](overview.md) ｜ 次へ: [カートリッジシステム →](cartridges.md)
