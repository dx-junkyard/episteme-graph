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
`GENERIC_OPERATIONS`（`transform` / `relate` / `connect` / `support` / `associate` / 空文字）は
generic 扱い（`edge_type="requires_review"`）。上の prefix 表にも完全一致表にも当たらない
**未知の operation** も捏造せず保持したうえで generic（`edge_type="transforms"`）に落とす。

**generic でも式 backing があれば捨てない（#361）**: 入力式と出力式が**両方**ある generic step は、
`equation_detail` 層に `partially_source_backed`（それ以上には決してしない）+
`review_reasons=["generic_operation"]` で残す。式 backing が片側でも欠ける generic step だけが
`debug` 層の `inferred` に落ちる。残した generic step には、導出順で最も近い非 generic step の
main ノードを親（`parent_component_id`）として与える。generic operation は main ノードにはしない
（validator が `main_graph_generic_operation` を hard error 検出）。

### 2 層 + デバッグ層（#306, #308）
| layer | 内容 | ノード型 | バッキング要件 |
|---|---|---|---|
| `main` | 理論構成の少数の集約ノード（5〜8 個程度のバックボーン） | `TheoryOperationNode` | `source_backed` または `teacher_review_required` のみ |
| `equation_detail` | 式単位の step（式 backing のある generic step を含む） | `EquationOperationNode` | `partially_source_backed` / `inferred` も可 |
| `debug` | fallback / inferred ノード（式 backing の無い generic step を含む） | — | `source_backed` 禁止（hard error）。publish からは除外 |

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

claim の `evidence_text` は呼び出し側（`orchestrator._component_graph_claims`）が EvidenceRegistry から
解決して渡す（`ClaimObjectRecord` は `source_evidence_ids` の参照しか持たない）。解決できない参照・
`support_status` が strong 系（`source_backed`）でない claim は空のまま＝強い backing にしない。
なお `claim_level` はパイプライン経路では供給されないため、atomic 判定で実際に効くのは
`is_atomic` と text 長の2条件。

### step ⇄ claim 参照のバックフィル契約
node の `linked_claim_ids` は derivation step の claim 参照が供給元だが、`derivation_chain` は
式由来の合成 claim（`synth_claim_*`）を作る**前**に走るため、合成 claim は agent 自身には
決して結べない。散文 claim に `equation_ids` が付かない文書では step の `required_claim_ids` が
空のままになる。そこで結び直しは合成フック（`orchestrator._hook_equation_claim_synthesis` →
`_backfill_derivation_claim_refs`）の責務とし、新規実行・resume のどちらでも実行する
（フックは artifact ゲートを持たないので restart 起点に依存しない。ただし
`target_stage` が hook より手前で止まる単一ステージ実行では hook 自体が走らない）。

処理は2段:
- **追加** — `equation_id → claim_id` 索引（正本 `orchestrator._claim_equation_link_index`）から
  step の**出力式および入力式**に紐づく claim を `required_claim_ids` へ additive に足す。
  agent の `_walk_back` は出力式のみだが、`chain_type="system_level"` の step は出力式が
  空になり得るため入力式からも引く。追加先は `required_claim_ids` だけで
  `input_claim_ids` / `output_claim_ids` には書き足さない（それらは agent 側が埋める）。
- **掃除** — `_canonicalize_derivation_claim_refs` に委譲。legacy 形式 id の正規化と、
  最終 claim 集合に解決できない参照（旧版合成の stale な ID 等）の除去を step の claim 参照
  4フィールドすべてに対して行い、落とした参照は `unresolved_claim_ref_dropped` の
  ValidationIssue として記録する（黙って捨てない）。
  既知の限界: 位置決めの synth id が別の式へ再割り当てされた場合は id として解決してしまうため
  検出できない（canonicalization 自体と同じ限界）。

変更があったときだけ `derivation_chain` artifact を再保存する（冪等 — 2回目の通過は no-op）。
合成が例外で失敗した run ではバックフィルを実行しない（未確定の claim 集合で生きた参照を
落とさないため）。

**限界（意図的）**: バックフィルが回復するのはノードの **claim 接続**であって強い backing では
ない。合成 claim は `support_status="equation_backed"` で strong backing ゲート
（`source_backed` のみ）の外なので `evidence_text` は供給されず、ノードには
`missing_atomic_claim` が残る（上記「空の evidence_text を強い backing として扱わない」の帰結）。

### step ⇄ evidence 参照のバックフィル契約
node の `linked_evidence_ids` は derivation step の `source_evidence_ids` が供給元で、step は
式レコードの `semantics.source_evidence_ids` をそのまま写す。ところがこのフィールドは
`equation_semantics` の LLM 出力項目でありながら prompt / validator が一切要求しないため
**常に空**で agent を出る。式ブロックの逐語 evidence（`equation_quote`。EvidenceRegistry が
式ブロックごとに登録済み）との結線は `to_equations_export(evidence_index=...)` の中にしか無く、
**export ルートにしか流れていなかった** — evidence は存在するのに結線だけが欠け、グラフの
全ノードが `missing_evidence_link` で出るという処理の欠陥だった（2026-09 修正）。

決定論的に（LLM を追加で呼ばず）2箇所で補う:
- **式レコードへ** — `orchestrator._hook_equation_evidence_backfill`（`evidence_registry` の直後・
  `claim_object_builder` の前に走る between-stage フック）が
  `_backfill_equation_evidence_refs` で block 索引（`_evidence_ids_by_block`。
  `to_equations_export` と同一規則）から `semantics.source_evidence_ids` へ additive に還流させる。
  以降の consumer（derivation_chain / symbol_registry / component_assembly / 式 claim 合成 /
  component_graph）が同じ evidence を見る。
- **derivation step へ** — `_stage_derivation_chain` の防御的後処理
  `_backfill_derivation_evidence_refs` が step の入出力式の evidence を additive に補う。
  式側が空だった run（上記フック以前の artifact からの resume）でも結線が戻る。

いずれも変更があったときだけ artifact を再保存する（冪等 — 2回目の通過は no-op）。索引に
無い式・evidence を持たない step は空のまま（捏造しない）。

evidence が結ばれると `_node_backing` の強い backing 条件が満たされ、式 backing のある node は
`source_backed`（= `review_status: source_backed`）になる。ただし最小命題の claim が無い限り
`missing_atomic_claim` は warning として残る（#306 の意図した帰結。上記「限界（意図的）」参照）。

### 論文層（Paper Layer）— 読み時射影

main ノードのラベルは #308 の規律で theory stage 名に固定されているため、グラフ単体では
「このノードは論文の何なのか」が読めません。**論文層**は `graph_json` を一切書き換えずに、
既存 artifact（document_structure / equation_semantics / evidence_registry / claim_object_builder /
symbol_registry / derivation_chain / figure_table_semantics / paper_skeleton /
thesis_reconstruction / component_assembly）と `element_explanations` / `document_figures` を
読み時に join し、①フレーム→論文（ノードごとの論文側の顔）②論文→フレーム（章立て・式番号・
図番号の論文順の背骨）③被覆（フレームに掛かっていない章・式・図・claim）を返します。

- 実装: `backend/core/graph_paper_layer/`（FastAPI / sqlalchemy / LLM を import しない純関数）。
  配信は `GET /api/admin/documents/{document_id}/paper-layer`（既存の component-graph
  レスポンスは不変）。
- **決定論・非 LLM で、リンクの無いものに対応を推定しない**。章の解決は実所在
  （式の `source_location.section_id` → evidence の `section_id` → claim の `section_id` →
  `block_id` → `document_structure.blocks[].section_id`）だけを辿り、辿れなければ `unlocated`。
- 結び付けの粒度は `equation_detail` 層で、main は `member_component_ids` の合算（多対多を隠さない）。
- artifact が欠けたときは 500 にせず `available:false` + 事実文で返す（fail-soft）。
- 正本: [グラフの論文層 設計書](../features/graph_paper_layer_design.md)。

### UI 表示（admin.js）
`source_backing_status` で表示を区別（source_backed=通常 / partially=細線 / review_required=点線枠 / inferred・fallback=薄色+⚠）。
グラフ層トグル（主グラフ / 式の詳細 / すべて）で `graph_layer` を切替、既定は main 優先。
→ [管理機能](../features/admin.md)。

`review_reasons` は**構築時の焼き込み値**なので、そのまま「要確認の理由」として出すと
教員が承認した構造や `source_backed` で確定した構造まで欠陥に見える。読み時射影
（`routes/theory_components.py::_normalize_stored_component_graph`。`graph_json` は書き換えない）が
次を添える:
- `review_reasons_at_analysis` — 教員が承認したノードの理由（`review_reasons` から移す。
  レビュー要求としては出さないが破棄もしない）。
- `review_reasons_advisory` — 参考情報として読むべき理由か（承認済み、または `source_backed`）。
- `graph_updated_at` — そのグラフが構築された時点（`theory_component_graphs.updated_at`）。
  パイプライン修正は**再解析まで既存グラフに反映されない**ため、古さの判断材料として返す。

同じ読み時射影で **ノードの `review_status` は「人間の判断」だけ live 値が焼き込み値に勝つ**。
`graph_json` のノード `review_status` は構築時の焼き込み値（常に非空）なので、教員が
コンポーネントを承認・却下しても再取得に反映されず、レビューのループが閉じなかった。現在は
`theory_components.review_status` が人間の判断語彙（`teacher_approved` / `teacher_reviewed` /
`endorsed` / `rejected` / `needs_revision`）であればそれを優先し、`source_backed` などの
**導出値は従来どおり stored 側を保つ**（表示互換）。グラフ全体対話の grounding 側にも同じ規則が
あり（`core/deliberation/graph_dialogue.py::merge_live_review_statuses`。取得失敗は焼き込み値の
まま fail-soft）、いずれも `graph_json` は書き換えない。
→ [グラフ対話レビュー 設計書](../features/graph_dialogue_review_design.md)

また main ノードのラベルは、legacy グラフに残る `Theory basis: <長い理由>` 形式を読み時に
短い stage label へ再正規化し、切り落とした長文は `description` に移す（#319。stored は不変）。

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

> **これは A層パイプラインとは別系統の副経路です（2026-09-03 更新）。**
> **A層パイプラインが書くチャンクには `smiles_dsl` が入りません** — `document_pipeline/persistence.py`
> の `INSERT INTO chunks (...)` の列は `id / document_id / chunk_index / text / embedding /
> display_text / spoken_text / formulas / latex_formulas / material_id / page_start / page_end /
> section_id / block_ids / source_metadata` で、`smiles_dsl` を含みません（`dsl_embedding` ステージが
> 書くのはチャンクではなく `document_embeddings` の `dsl_graph` 行です）。
>
> `chunks.smiles_dsl` / `documents.knowledge_graph` を埋めるのは、A層とは別の
> **「構造再解析」経路**（`POST /api/admin/courses/{course_id}/structure/reanalyze` →
> `services.reanalyze_course_structure_background`）だけです。これは既存チャンクの本文を保ったまま
> `build_knowledge_graph()`（旧 LLM 抽出）で DSL / variables / ancestors を後付けするもので、
> 動的スキーマ進化の Shadow Testing（`core/simulator.py` は `documents.knowledge_graph` を読む）と
> 本関数の入力を作ります。旧 `core/embedder.py::embed_and_store()` は本番呼び出し元がありません。
>
> したがって、構造再解析を回していない教材では本関数の入力が空になり `[]` を返します。
> **現行の理論コンポーネント生成の本流は ComponentAssemblyAgent（`component_assembly` ステージ）**で、
> 本関数は `POST /api/admin/chunks/{chunk_id}/theory-components/extract`
> （`routes/theory_components.py`）からの副経路として残置されています。

---

[← カートリッジシステム](cartridges.md) ｜ 次へ: [動的スキーマ進化 →](schema-evolution.md)
