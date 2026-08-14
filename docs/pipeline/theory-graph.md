# DSL と理論操作グラフ

[← ドキュメント目次](../README.md) ｜ [← カートリッジシステム](cartridges.md)

知識を表現する 2 つの構造 — **SMILES 風 DSL** と **理論操作グラフ（TheoryOperationGraph）** — を解説します。

---

## 1. ナレッジグラフ DSL（SMILES 形式）

化学の SMILES に着想を得た独自記法で、概念間の抽象構造を 1 行で表します。

```
(varID:OntologyType:value) ==[CorePredicate:verb:polarity]=> (...)
```

例:
```
(L:MathematicalObject:Lagrangian Density) ==[DEFINES:specifies:+]=> (QFT:TheoreticalFramework:Quantum Field Theory)
(QFT:TheoreticalFramework:...) ==[REQUIRES:builds_on:+]=> (sym:Symmetry:Gauge Symmetry)
```

| 要素 | 種別 | 例 | 役割 |
|---|---|---|---|
| varID | 文字列 | `L`, `QFT` | DSL 内で一意の変数識別子 |
| OntologyType | enum | `MathematicalObject` | ノードのカテゴリ（[OntologyType](cartridges.md#ビルトイン語彙との関係)） |
| value | 文字列 | "Lagrangian Density" | 具体的な概念ラベル |
| CorePredicate | enum | `DEFINES` | 標準の関係種別（横断・抽象層） |
| verb | 文字列 | "specifies" | ドメイン固有の動詞（仕上げ層） |
| polarity | `+ / - / +/- / ?` | `+` | 極性（正/負/条件付き/不明） |

### CorePredicate の意味（`schema.py`）
| 述語 | 意味 |
|---|---|
| `CAUSES` | 直接生成・トリガ |
| `INHIBITS` | 抑制・阻害 |
| `CORRELATES` | 方向のない共変 |
| `DEFINES` | 定義・特徴づけ |
| `MEASURES` | 操作化・定量化 |
| `TRANSFORMS` | 状態の変換 |
| `REQUIRES` | 依存・前提条件 |
| `CONTAINS` | 親子（マクロ-ミクロ）包含 |
| `EQUIVALENT` | 同レベルの等価/対比 |

対応する Pydantic モデルは `CausalEdge`（source, target, core_predicate, domain_verb, polarity, ontology_level, is_core）と
`AbstractStructure`（variables, edges, smiles_dsl）、`PaperStructure`（title, hypothesis, methodology, abstract_structure …）。

### .isom シリアライズ（`isom.py`）
`PaperStructure` を `.isom` ファイル（YAML front-matter + SMILES DSL 本体）に書き出し、外部 OSL パイプライン連携に使います。

```yaml
---
source_id: "https://arxiv.org/abs/..."
title: "..."
domain: "particle_physics"
patterns: ["DEFINES:establishes", "REQUIRES:presupposes"]
---
(L:MathematicalObject:Lagrangian Density) ==[DEFINES:specifies:+]=> (QFT:TheoreticalFramework:...)
```

---

## 2. 理論操作グラフ（TheoryOperationGraph）

ComponentGraphAgent（`component_graph/normalizer.py`）が、DerivationChain から**決定論的に**構築する、
論文の「理論操作の構造」を表すグラフです。**特定分野・特定論文の用語をハードコードしない**のが鉄則。

### 語彙は operation から導出
`component_graph/schema.py::classify_operation(operation)` が operation の prefix から edge_type を
決定します（返り値は `(verb, edge_type, is_generic)`）。
**同名の `agents/theory_operations.py::classify_operation(operation_text, cartridge=...)` とは別物**で、
そちらは操作を「操作ファミリー（+ カートリッジ由来 subtype）」へ分類する共有モジュール
（→ [PDF 解析 Agent 詳細 §3](agents.md#3-共有モジュールagents-直下)）。本節の記述は
すべて component_graph 側を指します。
```
define_*     → defines        linearize_* → linearizes     solve_*     → solves
eliminate_*  → eliminates     derive_*    → derives        constrain_* → constrains
diagnose_*   → diagnoses      compare_*   → compares
```
`transform` / `relate` / `connect` / `support` など抽象的な operation は generic 扱いで warning。

### 2 層 + デバッグ層（#306, #308）
| layer | 内容 | ノード型 | バッキング要件 |
|---|---|---|---|
| `main` | 理論構成の少数の集約ノード（5〜8 個程度のバックボーン） | `TheoryOperationNode` | `source_backed` または `teacher_review_required` のみ |
| `equation_detail` | 式単位の step | `EquationOperationNode` | `partially_source_backed` / `inferred` も可 |
| `debug` | fallback / inferred ノード | — | `source_backed` 禁止（hard error）。publish からは除外 |

- detail node は `parent_component_id`、main node は `member_component_ids` で相互参照。
- main は **theory stage 単位**で集約。`schema.stage_for_edge_type()` が edge_type family から domain-neutral に stage を導出:
  ```
  defines                         → theory_basis
  constructs / normalizes         → observable_construction
  linearizes/approximates/substitutes → equation_system
  solves / eliminates             → elimination
  derives / constrains            → consistency_relation
  diagnoses / compares            → diagnostic_application
  ```
  stage 語彙（`schema.THEORY_STAGES`）は上記 6 つに **`observation_model`（label: `Observation model`）** を
  加えた 7 段。observation_model は edge_type マッピングからは導出されない予約ステージで、
  main graph の上から下への正準順序（theory_basis → observation_model → observable_construction → …）に含まれる。
- main node の label は **stage label そのもの**（`Theory basis` / `Equation system` …）。長い説明は `description` へ。
  `Define eq_2_7` のような equation-id ラベルや generic operation の main ノードは validator が hard error 検出。

### ソースバッキング（#311, #306）
各 node / edge は **どのソースに裏付けられているか**を必ず明示します。

- node: `linked_equation_ids` / `linked_derivation_ids` / `linked_claim_ids` / `linked_evidence_ids` と
  `source_backing_status`（`source_backed` / `partially_source_backed` / `inferred` / `review_required`）。
- edge: `evidence_equation_ids` / `evidence_derivation_ids` / `evidence_claim_ids` / `source_evidence_ids` と
  同じ語彙の `source_backing_status`、そこから `review_status_for_backing()` で導出される `review_status`。

`review_status` の導出（`schema.review_status_for_backing()`）:
```
source_backed            → source_backed
partially_source_backed  → teacher_review_required
inferred / review_required → review_required
```

### review 理由を必ず付与
`review_required` の node/edge は `review_reasons` を空にしない（`missing_atomic_claim` /
`missing_evidence_link` / `missing_equation_link` / `missing_derivation_link` /
`equation_needs_math_review` / `edge_not_source_backed` / `fallback_or_inferred_node` /
`source_span_missing` から選ぶ）。

### atomic claim を優先
node の主たる backing は **atomic claim**（短く evidence_text 非空、paper-level でない）を優先。
無ければ `missing_atomic_claim` を付け、equation ID だけの label は `partially_source_backed` に留める。
空の evidence_text を強い backing として扱わない。

### UI 表示（admin.js）
`source_backing_status` で表示を区別（source_backed=通常 / partially=細線 / review_required=点線枠 / inferred・fallback=薄色+⚠）。
グラフ層トグル（主グラフ / 式の詳細 / すべて）で `graph_layer` を切替、既定は main 優先。
→ [管理機能](../features/admin.md)。

---

## 3. 構造的同型性評価（削除済み）

旧 `batch.py` は、新しい `AbstractionPattern` が登録されたときに過去論文群へ**クロスドメイン**でパターンを当て、
一致したら Neo4j に `MATCHES_PATTERN` エッジを作成する機能だったが、本番呼び出し元が存在しなかった
（新パイプラインは `AbstractionPattern` を生成しない）ため 2026-07 に Neo4j ごと削除済み。
論文収集自体は引き続き `harvester.py`（arXiv API、商業出版社フィルタ）が担う。

---

## 4. 理論コンポーネント抽出（`theory_components.py`）

`extract_theory_components_from_dsl()` がチャンクメタデータの DSL（`chunks.smiles_dsl`）から候補
コンポーネントを作ります。edge の述語に応じて component_type を決め（MEASURES→observation,
DEFINES→concept, CAUSES/REQUIRES→mechanism）、inputs/outputs/preconditions/dependencies を関係から
導出します。出力は `theory_components` テーブルへ。

> **これは実質レガシー経路です（2026-08-14 追記）。** 現行 A層パイプラインが書くチャンクには
> `smiles_dsl` が入りません — `document_pipeline/persistence.py` の `INSERT INTO chunks (...)` の
> 列は `id / document_id / chunk_index / text / embedding / display_text / spoken_text / formulas /
> latex_formulas / material_id / page_start / page_end / section_id / block_ids / source_metadata` で、
> `smiles_dsl` を含みません（`smiles_dsl` を書くのは旧 `core/embedder.py::embed_and_store()` だけで、
> こちらは本番呼び出し元がありません。`dsl_embedding` ステージが書くのはチャンクではなく
> `document_embeddings` の `dsl_graph` 行です）。
> したがって本関数は入力が空になり `[]` を返すのが通常で、現行の理論コンポーネント生成の本流は
> **ComponentAssemblyAgent（`component_assembly` ステージ）** です。関数自体は
> `POST /api/admin/chunks/{chunk_id}/theory-components/extract`（`routes/theory_components.py`）から
> まだ到達可能なため残置されています。

---

[← カートリッジシステム](cartridges.md) ｜ 次へ: [動的スキーマ進化 →](schema-evolution.md)
