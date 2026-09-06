# PDF 解析パイプライン概要

[← ドキュメント目次](../README.md)

> **更新注記（2026-09-03）:** §2 のフック列を現行の `_PIPELINE_STEPS`（フック 3 件）に合わせ、
> 種別欄を各行の `llm_kind` / `model_policy` 宣言に合わせて訂正。§4 に restart 時の
> 欠落 artifact 補完（`stage_outputs.resume.backfilled_stages`）を追記した。
> ステージ構成の一次情報は常に `backend/core/document_pipeline/orchestrator.py` の
> `PIPELINE_STAGES` / `_PIPELINE_STEPS`（判定用の集合 `LLM_STAGE_NAMES` /
> `LLM_CALLING_STAGE_NAMES` / `VISION_STAGE_NAMES` は `_PIPELINE_STEPS` からの導出値）。
> 整合は `backend/tests/test_pipeline_stage_registry.py` が固定する。

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

`orchestrator.py` の `_PIPELINE_STEPS` は 32 要素 = **名前付き 29 ステージ**（`PIPELINE_STAGES`
の 30 要素から終端マーカー `completed` を除いた分）+ between-stage 決定論的後処理の
`_hook_*` フック 3 件（`PIPELINE_STAGES` に対応エントリを持たない = `name=None`。
`report_start` / `finish_target_stage` を持たず、artifact ゲートも通らない）。

種別欄の凡例（正本は `_PIPELINE_STEPS` 各行の `llm_kind` / `model_policy` 宣言）:

| 表記 | 意味 |
|---|---|
| LLM | text LLM を呼び、M層のステージ別モデル選択の対象（`llm_kind="text"`。**`LLM_STAGE_NAMES` は `model_policy=True` のみから導出**され、vision ステージ（`apparatus_semantics`）も含む — 「text である」ことは条件ではない） |
| vision LLM | vision LLM を呼ぶ（`llm_kind="vision"` = `VISION_STAGE_NAMES`。現状 `apparatus_semantics` のみ） |
| LLM（M層対象外） | LLM は呼ぶが `model_policy=False` で、ステージ別モデル選択と `_stage_models` 記録の対象外（現状 `component_graph` のみ。`LLM_CALLING_STAGE_NAMES` と `LLM_STAGE_NAMES` の唯一の差分） |
| Emb | embedding API を呼ぶ（`llm_kind="embedding"`。モデル選択の対象外 — pgvector の次元と結合しているため） |
| Det | LLM も embedding も呼ばない決定論的処理（`llm_kind="none"`） |

| # | ステージ | 担当 Agent / 処理 | 種別 | 出力（要旨） |
|---|---|---|---|---|
| 1 | `save_pdf` | 入力バイト列を一時ファイルへ | Det | — |
| 2 | `grobid_parse` | GROBID で TEI-XML 抽出（失敗時 PyMuPDF へフォールバック、非致命的） | Det | TEI-XML |
| 3 | `document_structure` | **DocumentStructureAgent** 文書構造復元 | Det（structure-first） | DocumentStructureResult（blocks, sections, metadata） |
| 4 | `figure_image_extraction` | PyMuPDF 埋め込み画像抽出 + caption 近傍の領域レンダリング fallback（常時実行） | Det | document_figures（MinIO `figure-images`） |
| 5 | `source_chunking` | ブロックからチャンク生成 | Det | チャンク |
| 6 | `source_embedding` | チャンクを pgvector へ保存 | Emb | — |
| 7 | `paper_skeleton` | **PaperSkeletonAgent** 論文 backbone 仮説化 | LLM | PaperSkeletonResult |
| 8 | `rhetorical_role` | **RhetoricalRoleAgent** 論理役割判定 | LLM | RhetoricalRoleResult |
| 9 | `claim_qualification` | **ClaimQualificationAgent** Claim 採否・区分 + atomic rewrite | LLM | ClaimQualificationResult |
| 10 | `equation_semantics` | **EquationSemanticsAgent** 数式の意味役割復元（信用できない数式候補のみ切り出し画像を添付＝条件付き vision。M層では text 扱い） | LLM | EquationSemanticsResult |
| 11 | `evidence_registry` | **EvidenceRegistryBuilder** PDF 原文 evidence の一元管理 | Det | EvidenceRegistryResult |
| — | （フック） `_hook_equation_evidence_backfill` | 式レコードへ `source_evidence_ids` を還流（決定論・追加のみ） | Det | — |
| 12 | `claim_object_builder` | **ClaimObjectBuilder** 最終 claims.json 組立 | Det | ClaimObjectBuildResult |
| — | （フック） `_hook_claim_equation_canonicalization` | claim/equation の正規化後処理 | Det | — |
| 13 | `symbol_registry` | **SymbolRegistryBuilder** 数式記号の定義・表記ゆれ管理 | Det | SymbolRegistryResult |
| 14 | `derivation_chain` | **DerivationChainAgent** 式間導出チェーン構築 | Det | DerivationChainResult |
| — | （フック） `_hook_equation_claim_synthesis` | 式↔claim の合成後処理 + step ⇄ claim 参照のバックフィル | Det | — |
| 15 | `figure_table_semantics` | **FigureTableSemanticsAgent** 図表の意味復元（caption-first。LLM enricher は任意で現状未配線） | Det | FigureTableSemanticsResult |
| 16 | `apparatus_semantics` | **ApparatusSemanticsAgent**（L層）装置・パーツ候補抽出（`analyze_images=true` 時のみ、常に `review_required`。既定は反復照合モード #499） | vision LLM | ApparatusSemanticsResult |
| 17 | `thesis_reconstruction` | **ThesisReconstructionAgent** 中心命題・支持構造の再構成 | LLM | ThesisReconstructionResult |
| 18 | `dsl_linking` | **DSLLinkingAgent** Claim/Equation/Thesis → DSL グラフ接続 | LLM | DSLLinkingResult |
| 19 | `dsl_embedding` | DSL を pgvector（`document_embeddings`）へ保存（検索用） | Emb | — |
| 20 | `component_assembly` | **ComponentAssemblyAgent** 再利用可能コンポーネント生成 | LLM | ComponentAssemblyResult |
| 21 | `component_graph` | **ComponentGraphAgent** 理論操作グラフ構築（ノード生成は決定論、エッジ推論に LLM） | LLM（M層対象外） | ComponentGraphResult |
| 22 | `narrative_annotator` | **NarrativeAnnotator** main graph への narrative 注釈（構造非変更） | LLM | NarrativeAnnotationResult |
| 23 | `contextual_explanation` | **ContextualExplanationAgent** 要素の二層説明（contextual / generic）生成 | LLM | `element_explanations` の candidate（stage_outputs に件数・上限情報） |
| 24 | `discuss_opening` | **DiscussOpeningAgent** discuss 開幕の「議論のきっかけ」生成（1 document = 1 コール） | LLM | `element_explanations`（`role='discussion_seed'`）の candidate |
| 25 | `landscape_placement` | **LandscapePlacementAgent** 論文を凍結済み基準地図へ配置（+ カテゴリギャップ候補） | LLM | `landscape_placements`（`status='inferred'`）/ `landscape_gap_signals` |
| 26 | `course_mapping` | **CourseMappingAgent** Component → Course topic 接続 | Det | CourseMappingResult |
| 27 | `blueprint` | **BlueprintAgent** ナラティブアーク合成 | Det | Blueprint |
| 28 | `export_validation` | **ExportValidationGate** 最終検証ゲート | Det | 検証結果 |
| 29 | `persist_claims_components_graph` | claims/components/graph を PostgreSQL へ永続化 | Det | — |
| — | `completed` | ラン完了マーク | — | — |

> **入力の種別**: `source_kind` は `"pdf"` と `"tex_archive"`（arXiv の TeX ソース `.tar.gz`）の
> 2 種で、それ以外は `ValueError`。`tex_archive` の場合は 3 の `document_structure` で
> DocumentStructureAgent を通さず `core/document_pipeline/tex_archive.py::build_structure_from_tex_archive`
> が同じ `DocumentStructureResult` を組み立てる（以降のステージは共通）。

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
- **Claim の atomic 化は ClaimQualificationAgent（LLM）が担当**。ClaimObjectBuilder は候補を変換・リンク・検証するだけ（atomic rewrite はしない）。非 atomic / split_required（旧称 `split_pending` / `non_atomic` は legacy エイリアスで、正本語彙は `claim_object_builder/schema.py` の `split_required`）は `review_required` で保持。
- **Evidence は PDF 原文由来のみ**を EvidenceRegistry が一元管理。各 claim/equation は `source_evidence_ids` で参照する。
- **理論操作グラフ（ComponentGraph）**は導出チェーンから決定論的に構築し、ソースバッキング状態とレビュー理由を必ず付与する。詳細 → [DSL と理論操作グラフ](theory-graph.md)。
- **`contextual_explanation` / `discuss_opening` / `landscape_placement`（23〜25）は非致命**。グラフ・narrative が揃った位置に置かれ、既存成果物の解決済みテキストだけを読んで**候補**（`candidate` / `inferred`）を書く。ここで失敗しても `course_mapping` 以降（永続化）を止めない。確定は必ず教員が行う。

### 永続化されるもの / artifact に留まるもの

最終ステージ `persist_claims_components_graph`（`document_pipeline/persistence.py`）が DB に書くのは次の3系統だけです。

| 永続化先 | 元になる成果物 | 補足 |
|---|---|---|
| `theory_claims` | `claim_qualification` の `qualified_spans` | `persist_qualified_claims`。`evidence_text` は空文字で保存し、逐語根拠は EvidenceRegistry artifact に委譲する（#257） |
| `theory_components` / `theory_component_links` | `component_assembly` の components | `persist_components` |
| `theory_component_graphs` | `component_graph` の graph（+ `narrative_annotator` の注釈） | `persist_component_graph` |

**ClaimObjectBuilder が組み立てた `claim_objects`（atomic 子 claim・式由来の合成 claim `synth_claim_*`）は `theory_claims` に永続化されません。**
これらは `document_analysis_runs.stage_outputs` の artifact にのみ存在します。したがって graph の
`linked_claim_ids` には DB 行を持たない claim ID が混ざり得ます（グラフレビュー画面はこれを
artifact から読み時に解決して「未承認（解析結果）」として表示する →
[グラフの論文層 / グラフ対話レビュー](../features/admin.md)）。この非対称を解消するか（=
claim_objects を永続化するか）はオーナー判断の未決事項です。

なお `export_validation` が `failed_validation` を返した場合でも run は `completed` へ進み、
エラー種別に応じて **components / graph の保存だけを落として claims は保存**する縮退
（`_compute_persist_degradation_flags`。落とした段階は `degraded_stages` に記録）を行います。

> 上表の各出力を「論文の抽出単位（ブロック → チャンク / エビデンス → span → atomic claim → 理論部品）」という縦串で読み直すなら → [論文の抽出単位](extraction-units.md)。

---

## 4. 実行と監視

- 起動: 教材アップロード（`POST /api/admin/materials/upload`）後、または `POST /api/admin/materials/{id}/document-pipeline/run` / `POST /api/admin/documents/{id}/reanalyze`。
- 進捗: `GET /api/admin/materials/{id}/document-pipeline/status`、`GET /api/admin/tasks/{task_id}`。
- 実行履歴は `document_analysis_runs`（`current_stage`, `stage_outputs`, `status`）。リビジョン機能で再解析候補を並存させ、`documents.active_analysis_run_id` でアクティブを切替（[データモデル](../architecture/data-model.md#パイプライン実行リビジョン)）。
- ステージ失敗時は `PipelineStageError` がどのステージで失敗したかを保持し、UI に返します。

### resume / restart / 単一ステージ実行

`run_document_pipeline()` は `start_stage`（restart）と `target_stage`（単一ステージの点検実行）を取り、
前回 run の artifact（`stage_outputs._artifacts`）の再利用可否を `should_use_artifact()` で決めます。

- **restart（`start_stage` 指定）** — `start_stage` より前のステージは artifact を再利用する。
  ただし artifact が**無い**ステージは hard error にせず live 実行で補完する。ステージは後から追加される
  （例: `figure_image_extraction` は 2026-07 追加）ため、hard error だと「新ステージが増えるたびに
  古い run が restart 不能になる」ため。補完は無音では行わず、`logger.warning` と
  `stage_outputs.resume.backfilled_stages`（補完したステージ名の配列）に残す。
- **単一ステージ実行（`target_stage` 指定）** — 必要な先行 artifact が欠けていれば
  `PipelineStageError`（挙動は不変）。DB は更新しない点検用の経路。
- `_hook_*` フックは artifact ゲートを持たないため、restart の起点に関わらず毎回走る
  （ただし `target_stage` がフックより手前で止まる単一ステージ実行では到達しない）。
- **使用モデルの記録（M7）** — 実際に実行した LLM ステージ（`LLM_STAGE_NAMES`）だけ、解決済みモデル名を
  `stage_outputs._stage_models` に記録する。artifact を再利用したステージは前回の記録がそのまま残り、
  skip されたステージは記録しない。

---

[← RAG チャットフロー](../backend/rag-chat.md) ｜ 次へ: [PDF 解析 Agent 詳細 →](agents.md)
