# PDF 解析パイプライン概要

[← ドキュメント目次](../README.md)

> **更新注記（2026-08-14）:** §2 のステージ表を現行の `_PIPELINE_STEPS` に合わせて更新済み
> （`contextual_explanation` / `discuss_opening` / `landscape_placement` を追補）。
> ステージ構成の一次情報は常に `backend/core/document_pipeline/orchestrator.py` の
> `PIPELINE_STAGES` / `_PIPELINE_STEPS`。

教材としてアップロードされた PDF を、再利用可能な理論コンポーネント／コース教材へ変換する
**ドキュメントファースト・パイプライン**の全体像です。

- オーケストレータ: `backend/core/document_pipeline/orchestrator.py` の `run_document_pipeline()`
- 各 Agent 実装: `src/episteme_graph/agents/<agent_name>/`
- 各 Agent の詳細: [PDF 解析 Agent 詳細](agents.md)
- 「論文をどの単位で切り出しているか」を縦串で読むなら: [論文の抽出単位](extraction-units.md)

---

## 1. 設計思想

- **コース作成と切り離す** — ドキュメントを解析して再利用可能なコンポーネントを作る工程は、コース化とは独立。
- **ステージ単位で再開可能** — 各ステージは JSON 成果物を PostgreSQL（`document_analysis_runs.stage_outputs`）に永続化。`start_stage` / `target_stage` で途中から再実行できる。
- **structure-first → LLM-first** — 文書構造の復元はパーサ/レイアウト優先（意味解釈しない）。それ以降の高次判断（生成・採否・関係付け）は LLM-first、入力整形/検証/修復は非 LLM。
- **evidence-based / 情報を落とさない** — 各出力に `reason` と `confidence`、ソースバッキング状態を付与。不明は `unknown` / `deferred` / `review_required` で保持し削除しない。
- **maturity の最終確定禁止** — LLM が提案しても確定はせず provisional に留める。
- **domain-independent** — 特定分野・特定論文の語彙をコードにハードコードしない。ドメイン知識は[カートリッジ](cartridges.md)から読む。

---

## 2. パイプライン 29 ステージ

`orchestrator.py` の `_PIPELINE_STEPS` は 31 要素 = **名前付き 29 ステージ**（`PIPELINE_STAGES`
の 30 要素から終端マーカー `completed` を除いた分）+ between-stage 決定論的後処理の
`_hook_*` フック2件（`PIPELINE_STAGES` に対応エントリを持たない = `name=None`）。
LLM=LLM-first、Det=決定論的（非 LLM）。

| # | ステージ | 担当 Agent / 処理 | 種別 | 出力（要旨） |
|---|---|---|---|---|
| 1 | `save_pdf` | PDF を一時ファイルへ | Det | — |
| 2 | `grobid_parse` | GROBID で TEI-XML 抽出（失敗時 PyMuPDF へフォールバック、非致命的） | Det | TEI-XML |
| 3 | `document_structure` | **DocumentStructureAgent** 文書構造復元 | structure-first | DocumentStructureResult（blocks, sections, metadata） |
| 4 | `figure_image_extraction` | PyMuPDF 埋め込み画像抽出 + caption 近傍の領域レンダリング fallback（常時実行） | Det | document_figures（MinIO `figure-images`） |
| 5 | `source_chunking` | ブロックからチャンク生成 | Det | チャンク |
| 6 | `source_embedding` | チャンクを pgvector へ保存 | Det | — |
| 7 | `paper_skeleton` | **PaperSkeletonAgent** 論文 backbone 仮説化 | LLM | PaperSkeletonResult |
| 8 | `rhetorical_role` | **RhetoricalRoleAgent** 論理役割判定 | LLM | RhetoricalRoleResult |
| 9 | `claim_qualification` | **ClaimQualificationAgent** Claim 採否・区分 + atomic rewrite | LLM | ClaimQualificationResult |
| 10 | `equation_semantics` | **EquationSemanticsAgent** 数式の意味役割復元 | LLM | EquationSemanticsResult |
| 11 | `evidence_registry` | **EvidenceRegistryBuilder** PDF 原文 evidence の一元管理 | Det | EvidenceRegistryResult |
| 12 | `claim_object_builder` | **ClaimObjectBuilder** 最終 claims.json 組立 | Det | ClaimObjectBuildResult |
| — | （フック） `_hook_claim_equation_canonicalization` | claim/equation の正規化後処理 | Det | — |
| 13 | `symbol_registry` | **SymbolRegistryBuilder** 数式記号の定義・表記ゆれ管理 | Det | SymbolRegistryResult |
| 14 | `derivation_chain` | **DerivationChainAgent** 式間導出チェーン構築 | Det | DerivationChainResult |
| — | （フック） `_hook_equation_claim_synthesis` | 式↔claim の合成後処理 | Det | — |
| 15 | `figure_table_semantics` | **FigureTableSemanticsAgent** 図表の意味復元 | caption-first | FigureTableSemanticsResult |
| 16 | `apparatus_semantics` | **ApparatusSemanticsAgent**（L層）装置・パーツ候補抽出（`analyze_images=true` 時のみ、常に `review_required`） | vision LLM | ApparatusSemanticsResult |
| 17 | `thesis_reconstruction` | **ThesisReconstructionAgent** 中心命題・支持構造の再構成 | LLM | ThesisReconstructionResult |
| 18 | `dsl_linking` | **DSLLinkingAgent** Claim/Equation/Thesis → DSL グラフ接続 | LLM | DSLLinkingResult |
| 19 | `dsl_embedding` | DSL を pgvector へ保存（検索用） | Det | — |
| 20 | `component_assembly` | **ComponentAssemblyAgent** 再利用可能コンポーネント生成 | LLM | ComponentAssemblyResult |
| 21 | `component_graph` | **ComponentGraphAgent** 理論操作グラフ構築 | Det+LLM | ComponentGraphResult |
| 22 | `narrative_annotator` | **NarrativeAnnotator** main graph への narrative 注釈（構造非変更） | LLM | NarrativeAnnotationResult |
| 23 | `contextual_explanation` | **ContextualExplanationAgent** 要素の二層説明（contextual / generic）生成 | LLM | `element_explanations` の candidate（stage_outputs に件数・上限情報） |
| 24 | `discuss_opening` | **DiscussOpeningAgent** discuss 開幕の「議論のきっかけ」生成（1 document = 1 コール） | LLM | `element_explanations`（`role='discussion_seed'`）の candidate |
| 25 | `landscape_placement` | **LandscapePlacementAgent** 論文を凍結済み基準地図へ配置（+ カテゴリギャップ候補） | LLM | `landscape_placements`（`status='inferred'`）/ `landscape_gap_signals` |
| 26 | `course_mapping` | **CourseMappingAgent** Component → Course topic 接続 | Det | CourseMappingResult |
| 27 | `blueprint` | **BlueprintAgent** ナラティブアーク合成 | Det | Blueprint |
| 28 | `export_validation` | **ExportValidationGate** 最終検証ゲート | Det | 検証結果 |
| 29 | `persist_claims_components_graph` | claims/components/graph を PostgreSQL へ永続化 | Det | — |
| — | `completed` | ラン完了マーク | — | — |

---

## 3. データの依存関係（抜粋）

```
PDF
 └▶ DocumentStructure ─┬▶ PaperSkeleton ─┬▶ RhetoricalRole ─┬▶ ClaimQualification ─┐
                       │                 │                  │                       │
                       │                 │                  └▶ EquationSemantics ──┐│
                       │                                                           ││
   EvidenceRegistry ◀──┘  (PDF原文 evidence を集約)                                ││
        │                                                                          ││
        └▶ ClaimObjectBuilder ◀─────────────────────────────(atomic_claims)───────┘│
                  │                                                                  │
   SymbolRegistry ◀──────────────────────────────────────────(equations)───────────┘
        │
   DerivationChain ◀──(equations + claim objects)
        │
   ThesisReconstruction ─▶ DSLLinking ─▶ ComponentAssembly ─▶ ComponentGraph
                                                                     │
                                                  NarrativeAnnotator ─┤(注釈のみ)
                                                                     │
                                                       CourseMapping ─▶ Blueprint ─▶ ExportValidation
```

> この図は**実行順ではなくデータ依存関係**を示す（矢印は「どの成果物に依存するか」）。実際の実行順は §2 のステージ表が正。特に `evidence_registry` はステージ 11 で、`claim_qualification`（9）・`equation_semantics`（10）の**後**に走り、それらの採択スパン・式に絞って逐語根拠を張る（`_build_evidence_registry` は `structure` に加え `qualified` と `equations` を入力に取る）。

責務分担の要点:
- **Claim の atomic 化は ClaimQualificationAgent（LLM）が担当**。ClaimObjectBuilder は候補を変換・リンク・検証するだけ（atomic rewrite はしない）。非 atomic / split_pending は `review_required` で保持。
- **Evidence は PDF 原文由来のみ**を EvidenceRegistry が一元管理。各 claim/equation は `source_evidence_ids` で参照する。
- **理論操作グラフ（ComponentGraph）**は導出チェーンから決定論的に構築し、ソースバッキング状態とレビュー理由を必ず付与する。詳細 → [DSL と理論操作グラフ](theory-graph.md)。
- **`contextual_explanation` / `discuss_opening` / `landscape_placement`（23〜25）は非致命**。グラフ・narrative が揃った位置に置かれ、既存成果物の解決済みテキストだけを読んで**候補**（`candidate` / `inferred`）を書く。ここで失敗しても `course_mapping` 以降（永続化）を止めない。確定は必ず教員が行う。

> 上表の各出力を「論文の抽出単位（ブロック → チャンク / エビデンス → span → atomic claim → 理論部品）」という縦串で読み直すなら → [論文の抽出単位](extraction-units.md)。

---

## 4. 実行と監視

- 起動: 教材アップロード（`POST /api/admin/materials/upload`）後、または `POST /api/admin/materials/{id}/document-pipeline/run` / `POST /api/admin/documents/{id}/reanalyze`。
- 進捗: `GET /api/admin/materials/{id}/document-pipeline/status`、`GET /api/admin/tasks/{task_id}`。
- 実行履歴は `document_analysis_runs`（`current_stage`, `stage_outputs`, `status`）。リビジョン機能で再解析候補を並存させ、`documents.active_analysis_run_id` でアクティブを切替（[データモデル](../architecture/data-model.md#パイプライン実行リビジョン)）。
- ステージ失敗時は `PipelineStageError` がどのステージで失敗したかを保持し、UI に返します。

---

[← RAG チャットフロー](../backend/rag-chat.md) ｜ 次へ: [PDF 解析 Agent 詳細 →](agents.md)
