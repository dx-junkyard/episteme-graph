# 論文の抽出単位（Extraction Units）

[← ドキュメント目次](../README.md) ｜ [← パイプライン概要](overview.md)

[パイプライン概要](overview.md)・[Agent 詳細](agents.md) は工程を**ステージ／エージェント別**に並べたものです。
本ドキュメントはそれを**「論文をどの単位で切り出しているか」という縦串**で説明します。
パイプラインは PDF を一発で解釈するのではなく、**粗い物理的な塊から意味を持つ最小命題へ、さらに再利用可能な理論部品へと下から積み上げる**二段構えになっています。

---

## 0. 単位の階層（全体像）

```
PDF
└─ ブロック TypedBlock        … 原文の物理的な最小単位（段落 / 式 / キャプション / 見出し。bbox・page 付き）
   ├─ セクション Section       … ブロックが帰属する見出し階層
   ├─ チャンク SourceChunk     … ブロックを ~1800 字に束ねた埋め込み・RAG 検索単位（block_ids で遡及可）
   └─ エビデンス EvidenceRecord … ブロック全体 / 文単位の逐語根拠（span）
        ↓ ── ここから意味抽出（LLM-first）──
   ├─ スパン span              … ブロック内を論理役割で切った断片（文字オフセット）
   ├─ atomic claim            ★ 意味抽出の中核：1 主張 = 1 最小命題（evidence に紐づく）
   ├─ 数式 / 記号 / 導出        … EquationRecord / SymbolRecord / DerivationChain
   ├─ 図・表                   … 1 キャプション = 1 図 / 表（FigureRecord / TableRecord）
   ├─ central thesis          … 論文全体の中心命題 1 個 + 支持構造
   └─ theory component        ★ 最終成果：claim / 式 / thesis を束ねた再利用可能な理論部品
```

**第 1 段（物理単位への分解）** はパーサ／レイアウト優先で意味解釈をしない（structure-first、ほぼ非 LLM）。
**第 2 段（意味的構成要素の抽出）** は LLM-first で、生成・採否・関係付けの高次判断を LLM が担い、入力整形・検証・修復は非 LLM が担う。

**全体を貫く 3 原則**:

1. **土台はブロック、中核は atomic claim、頂点は theory component** ── 中核単位を「1 主張 = 1 命題」に正規化するのがこのシステムの肝。
2. **すべての単位が `block_id` / 逐語エビデンスに遡れる** ── 各要素が「原文にどう書いてあったか」を保持し続ける（トレーサビリティ）。
3. **LLM は提案まで、確定は人間** ── 各要素は `reason` + `confidence` + `review_status` を持ち、AI が勝手に `source_backed`（確定）にしない。曖昧・不明は捨てず `unknown` / `deferred` / `non_atomic` で保持する（情報を落とさない）。

---

## 第 1 段：PDF →「物理的な単位」への分解

意味を解釈せず、原文を忠実に機械的な塊へ切り出す工程。担当ステージは `save_pdf` → `grobid_parse` → `document_structure` → `source_chunking` → `source_embedding`（+ 常時実行の `figure_image_extraction`）。

### ① ブロック TypedBlock ── 最小の物理単位

`document_structure/`（[DocumentStructureAgent](agents.md#documentstructureagent216-structure-first)）が、パーサ出力を **1 段落・1 数式・1 キャプション・1 見出し**といった塊に型付けする。これがシステム全体の最小物理単位で、以降すべての要素はこの `block_id` に紐づいて遡れる。

- 一次パースは **GROBID（TEI-XML）**。失敗時は **PyMuPDF（fitz）** のレイアウト解析にフォールバック（非致命）。この時点では**文には割らない**（段落・カラム単位の塊のまま）。
- `TypedBlock` の主なフィールド（`src/episteme_graph/agents/document_structure/schema.py`）:
  `block_id` / `page` / `order` / `text` / `block_type` / `bbox`（座標）/ `section_id`（帰属）/ `equation_label` / `confidence` / `raw`（parser_source 等の来歴）
- `block_type`（9 種）: `section_heading` / `subsection_heading` / `body_paragraph` / `equation_block` / `figure_caption` / `table_caption` / `footnote` / `reference_entry` / `unknown`
- 型付けは**フォント比・太字・中央寄せ・数式記号密度などのレイアウト特徴のルール判定**（LLM ではない）。判断できないものは無理に決めず `unknown` に落とす。
- ブロックは見出し階層 `Section`（`section_id` / `title` / `level` / `order` / `page_start` / `page_end` / `parent_section_id`）にぶら下がる。

### ② チャンク SourceChunk ── 埋め込み・RAG 検索の単位

`backend/core/document_pipeline/chunker.py::build_source_chunks()` が、**ブロックをセクション境界内で束ねて**チャンクにする（**1 チャンク = 複数ブロックの多対一**）。

- 上限 `DEFAULT_MAX_CHARS = 1800` 字を超えたらチャンクを確定。セクションはまたがない。
- 単一ブロックが上限超過なら文字数で機械分割／隣接する数式ブロックは 1 式に結合／末尾の細切れ（`MIN_CHARS = 200` 未満）は前のチャンクに吸収。
- `SourceChunk` は `chunk_index` / `text` / `section_id` / `block_ids`（**元ブロックへの遡及リンク**）/ `page_start` / `page_end` / `metadata` / `formulas` を持つ。
- チャンクは pgvector（3072 次元）に埋め込まれ、RAG チャットの検索単位になる。詳細 → [RAG チャットフロー](../backend/rag-chat.md)。

### ③ エビデンス EvidenceRecord ── 逐語根拠の単位

`evidence_registry/`（[EvidenceRegistryBuilder](agents.md#evidenceregistrybuilder237-決定論)、非 LLM）が、後段の claim・数式に「PDF 原文のどこが根拠か」を紐づけるための**逐語引用の単位**を管理する。**2 階層**で保持する:

- **ブロック単位** ── 段落・式・キャプションまるごとの逐語引用。
- **文単位** ── ブロックを句読点ベースで文に割り（`eq.` / `fig.` / `e.g.` 等の略語で誤爆防止）、各文を `parent_evidence_id` で親ブロックに紐づける（issue #363）。
- `EvidenceRecord` は `evidence_id` / `source`（`EvidenceSource`: `page` / `section_id` / `block_id` / `span_start` / `span_end`）/ `evidence_text`（原文の逐語のみ）/ `evidence_role` / `parent_evidence_id` を持つ。
- `evidence_role`（6 種）: `source_quote` / `section_summary` / `equation_quote` / `figure_caption_quote` / `table_caption_quote` / `sentence_quote`。
- 各 claim / equation はここを `source_evidence_ids` で参照する。

> **実行順の注意**: エビデンス登録（stage 11 `evidence_registry`）は、実際には**採否判定と数式解析の後**に走る。`_build_evidence_registry(...)` は `structure` に加えて `qualified`（claim_qualification の採択スパン）と `equations`（equation_semantics の結果）を入力に取り、採択済みのブロック・式・キャプションに絞って逐語根拠を張る。[overview.md §2 のステージ表](overview.md#2-パイプライン-26-ステージ)の順序が正。

---

## 第 2 段：物理単位 →「意味的な構成要素」の抽出

ここからは LLM-first。各出力に `reason` と `confidence` が付き、採否・成熟度は provisional（確定は人間）。

### ④ スパン span ── 論理役割で切った断片

`rhetorical_role/`（[RhetoricalRoleAgent](agents.md#rhetoricalroleagent218-llm-first)）は**ブロックごとに LLM を 1 回呼び**、ブロック内部を**文字オフセットで区切ったスパン**に分けて論理役割を付ける。

- `SpanAnnotation`: `span_id` / `text` / `char_start` / `char_end` / `role_labels` / `is_claim_candidate` / `is_reject_candidate` / `confidence` / `reason`。
- 役割ラベルは 23 種（`definition` / `relation` / `assumption` / `approximation` / `derivation_step` / `result` / `diagnostic_claim` …）。うち claim 候補になりうる 15 種と、除外する 7 種（`prior_work` / `figure_narration` / `table_narration` / `section_meta` / `meta_discourse` / `citation_context` / `background_general`）に振り分けられる。

### ⑤ atomic claim ── 意味抽出の中核（1 主張 = 1 最小命題）

本システムで最も重要な単位。span を採否・区分し、**1 つの最小命題に書き直す（atomic rewrite）**。責務は 2 エージェントで分担する（issue #317）:

- `claim_qualification/`（[ClaimQualificationAgent](agents.md#claimqualificationagent219-317-llm-first)、LLM）が span 単位で採否（`accepted` / `rejected` / `deferred`）・区分（`paper_core` / `paper_supporting` / `background` / `prior_work` / `meta`）・粒度を判定し、**atomic rewrite** を実行する。
  - 出力 `QualifiedSpanRecord.atomic_claims`: `text` / `normalized_text` / `claim_type_candidate` / `atomicity` / `status` / `source_span_id` / `evidence_quote` / `qualification_reason` / `context_refs` / `confidence`。
  - atomic 化できない箇所は `atomicity="non_atomic"` / `status="review_required"` で保持する（捨てない）。
- `claim_object_builder/`（[ClaimObjectBuilder](agents.md#claimobjectbuilder237-317-決定論)、非 LLM）が候補を最終 `claims.json`（`ClaimObjectRecord`）に**変換・リンク・検証**する（atomic rewrite はしない）。複数命題は「compound 親 + atomic 子」の木構造にする。**最終 `claim_id` の真実の源**。
  - `ClaimObjectRecord` の主なフィールド: `claim_id` / `claim_type` / `text` / `source_evidence_ids`（③への根拠リンク）/ `support_status` / `review_status` / `is_atomic` / `parent_claim_id` / `subclaim_ids` / `concepts` / `equation_ids` / `figure_ids` …。
  - `support_status`: `source_backed` / `partially_source_backed` / `derived` / `inferred` / `review_required` / `external` / `unknown`。
  - `claim_type` は約 28 種のオントロジー（`definition` / `assumption` / `result` / `conclusion` / `causal_or_dependency_claim` …）。

### ⑥ 数式まわり ── 3 つの単位

- **数式ブロック単位** ── `equation_semantics/`（[EquationSemanticsAgent](agents.md#equationsemanticsagent220-llm-first)）。1 式ごとに種別（`definition` / `relation` / `transformation` / `approximation` / `result` / `constraint` / `unknown`）と定義記号・使用記号・入出力式を復元。`EquationRecord` は **「PDF 原文由来（`source_extraction`）」と「文脈補完で復元した部分（`reconstruction`）」を明確に分離**する（混同禁止）。
- **記号単位** ── `symbol_registry/`（[SymbolRegistryBuilder](agents.md#symbolregistrybuilder355-決定論)、非 LLM）。同じ記号の**表記ゆれ・種類・スコープ**（`equation_local` / `section` / `document`）を名寄せ。`SymbolRecord`: `symbol_id` / `canonical_symbol` / `notation_variants` / `kind` / `scope` / `defining_equation_ids` …。
- **導出チェーン単位** ── `derivation_chain/`（[DerivationChainAgent](agents.md#derivationchainagent237-決定論)、非 LLM）。式間リンクを終端式から逆にたどり、「1 ステップ = 式 1 個への操作」を並べた連鎖を組む。`chain_type`: `equation_chain` / `claim_chain` / `mixed_chain` / `system_level`。

### ⑦ 図・表 ── 1 キャプション = 1 図 / 表

`figure_table_semantics/`（[FigureTableSemanticsAgent](agents.md#figuretablesemanticsagent237-caption-first)、caption-first）が、`figure_caption` / `table_caption` ブロック 1 件を 1 つの図 / 表レコードとし、周辺本文を材料に入力・出力・比較軸・解釈を復元する。本文の「Fig. 5 参照」等のメンションから claim ⇄ 図をリンクする。画像そのものの装置・パーツ解析は [ApparatusSemanticsAgent](agents.md#apparatussemanticsagentl層-vision-llmオプトイン)（L 層）が `analyze_images=true` のオプトイン時のみ実行する。

### ⑧ central thesis ── 論文全体の中心命題

`thesis_reconstruction/`（[ThesisReconstructionAgent](agents.md#thesisreconstructionagent221-llm-first)）が、論文全体の中心命題を **1 つ**に絞り、それを支える claim / 式を「問題支持・導出支持・仮定支持・補正支持・不確かさ支持・診断的帰結・将来要件・結果支持」の複数カテゴリに整理する。代替解釈（`alternative_theses`）も捨てず保持する。

### ⑨ theory component ── 再利用可能な理論部品（最終成果）

`component_assembly/`（[ComponentAssemblyAgent](agents.md#componentassemblyagent223-llm-first--cartridge-aware)）が、採択済みの claim・式・thesis を束ねて **「再利用可能な理論部品」単位**に組み上げる（1 部品 = 複数 claim / 式のパッケージ）。これが DB の `theory_components` として永続化され、コース・説明生成で再利用される。

- `component_type`（組込み語彙）: `RelationComponent` / `AssumptionComponent` / `CorrectionComponent` / `UncertaintyComponent` / `DiagnosticComponent` / `MethodComponent` / `TheoryComponent` / `ClaimBundleComponent`（+ 画像パイプライン用 `apparatus` / `instrument` / `part`）。
- 型別リンク: `linked_claim_ids` / `linked_equation_ids` / `linked_evidence_ids` / `linked_derivation_ids` / `linked_dsl_node_ids` …。
- コンポーネント群からさらに**理論操作グラフ（TheoryOperationGraph）**が決定論的に構築される。詳細 → [DSL と理論操作グラフ](theory-graph.md)。

---

## まとめ表：単位 ↔ データ構造 ↔ 実装

| 段 | 単位 | データ構造 | 実装 | LLM/決定論 | 遡及先 |
|---|---|---|---|---|---|
| 1 | ブロック | `TypedBlock` | `document_structure/` | structure-first | ── (最小単位) |
| 1 | セクション | `Section` | `document_structure/` | structure-first | ブロック |
| 1 | チャンク | `SourceChunk` | `chunker.py` | 決定論 | `block_ids` |
| 1 | エビデンス | `EvidenceRecord` | `evidence_registry/` | 決定論 | `block_id` + span |
| 2 | スパン | `SpanAnnotation` | `rhetorical_role/` | LLM | ブロック内オフセット |
| 2 | **atomic claim** | `ClaimObjectRecord` | `claim_qualification/` + `claim_object_builder/` | LLM + 決定論 | `source_evidence_ids` / `source_span_ids` |
| 2 | 数式 | `EquationRecord` | `equation_semantics/` | LLM | `source_evidence_ids` |
| 2 | 記号 | `SymbolRecord` | `symbol_registry/` | 決定論 | `defining_equation_ids` |
| 2 | 導出チェーン | `DerivationChainRecord` | `derivation_chain/` | 決定論 | equation / claim ids |
| 2 | 図・表 | `FigureRecord` / `TableRecord` | `figure_table_semantics/` | caption-first | caption block |
| 2 | 中心命題 | `ThesisNode` | `thesis_reconstruction/` | LLM | claim / equation ids |
| 2 | **theory component** | `ComponentRecord` | `component_assembly/` | LLM | 全 `linked_*_ids` |

---

## 関連

- 工程順・依存関係: [パイプライン概要](overview.md)
- 各 Agent の入出力: [PDF 解析 Agent 詳細](agents.md)
- 理論操作グラフの内部構造: [DSL と理論操作グラフ](theory-graph.md)
- 永続化されるテーブル: [データモデル](../architecture/data-model.md)

---

[← パイプライン概要](overview.md) ｜ 次へ: [PDF 解析 Agent 詳細 →](agents.md)
