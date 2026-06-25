# PDF 解析 Agent 詳細

[← ドキュメント目次](../README.md) ｜ [← パイプライン概要](overview.md)

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

### FigureTableSemanticsAgent（#237）— caption-first
`figure_table_semantics/agent.py`。図表の意味を caption 優先で復元（LLM enricher は任意）。
- 出力: `FigureTableSemanticsResult`

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
- 出力: `ComponentGraphResult`（2 層: main / equation_detail、ソースバッキング状態つき）

### NarrativeAnnotator（#360）— LLM-first（構造非変更）
`narrative_annotator/agent.py`。main graph に reader 向けの narrative（`narrative_role`, `transition_text`, `graph_summary`）を**注釈として**付与。グラフ構造は一切変更しない（スナップショット比較で検証、違反は hard error）。出力はすべて `llm_proposed`。

### CourseMappingAgent（#237）— 決定論
`course_mapping/agent.py`。Component → Course topic へマッピング（既定は 1 component = 1 topic）。
- 出力: `CourseMappingResult`（topics with linked_component_ids, learning_objectives, prerequisites）→ `course_info.json`

### BlueprintAgent — 決定論
`blueprint/`。CourseMapping + Component からナラティブアーク（学習順序とステップラベル）を合成。

### ExportValidationGate — 決定論
`backend/core/document_pipeline/export_validation_gate.py`。最終検証。成果物の完全性・ソースバッキング整合性・スキーマ妥当性をチェック。非 atomic / split_pending な claim が main graph に強い backing として使われていないか等を明示 report（一部は hard error）。

---

## 3. 共有モジュール（agents/ 直下）

複数 Agent が使うライブラリ群。

| モジュール | 役割 |
|---|---|
| `theory_operations.py` | ドメイン中立な操作ファミリー分類（introduce_entity / define_relation / impose_assumption / construct_model / transform_representation / derive_consequence / evaluate_condition / compare_cases / validate_or_test / apply_to_context / state_limitation）。`classify_operation()` がキーワードからファミリーへ |
| `claim_selection.py` | LLM ステージへ渡す claim の選択ポリシー。tier（paper_core→…）+ 関連度（headline 重なり）+ confidence で並べ、上限を超えた分は理由つきで除外 |
| `content_normalization.py` | 横断再利用のための claim/equation ハッシュ。散文は casefold、数式トークンは大文字小文字保持。`CONTENT_HASH_VERSION` でルール変更を検知 |
| `id_canonicalization.py` | 横断参照を canonical claim_id に揃える。`canonical_claim_id_for_span()` / `canonicalize_claim_refs()`。解決不能な参照は drop + warning |
| `cartridge_paths.py` | カートリッジのパス解決（`EPISTEME_CARTRIDGES_DIR` → 自動探索） |
| `llm_json_client.py` | プロバイダ対応の JSON LLM クライアント（`core.llm.generate_text` をラップ、タイムアウト/構造化出力/raw 追跡） |

---

## 4. 関連

- ドメイン語彙の注入: [カートリッジシステム](cartridges.md)
- 理論操作グラフの内部構造: [DSL と理論操作グラフ](theory-graph.md)
- パイプライン全体の流れ: [パイプライン概要](overview.md)

---

[← パイプライン概要](overview.md) ｜ 次へ: [カートリッジシステム →](cartridges.md)
