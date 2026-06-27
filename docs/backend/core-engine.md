# コアエンジン（backend/core/）

[← ドキュメント目次](../README.md)

`backend/core/` は FastAPI に依存しない再利用可能なコアロジック群です。
ここでは各モジュールの責務と、どのデータストア・外部サービスと話すかをまとめます。

> 設計ルール: `core/` に FastAPI を import しない（テスト容易性）。`db.py` / `llm.py` / `storage.py` は
> `@lru_cache` 等でシングルトン化。PostgreSQL は `postgres.get_session()` を使い `try/finally` で必ず close。

---

## 1. データストア接続

| モジュール | 役割 |
|---|---|
| `postgres.py` | SQLAlchemy エンジン/セッション管理（`get_engine()`, `get_session()`, `check_connection()`） |
| `db.py` | Neo4j ドライバ（`get_driver()`）。概念グラフ走査・システムメタ提案の作成 |
| `models.py` | SQLAlchemy ORM 定義（users, documents, chunks, learning_courses, background_tasks など。[データモデル](../architecture/data-model.md) 参照） |
| `storage.py` | MinIO ラッパー（`StorageManager`）。バケット `raw-papers` / `raw-texts` / `extracted-structures` の upload/get |

---

## 2. LLM / 設定の抽象化

### `llm.py` — LLM アダプタ
OpenAI / Gemini(REST) / Vertex AI を 1 つのインターフェースで扱います。

- `get_llm_params(mode)` — `"fast" | "standard" | "deep"` に応じてモデルと reasoning effort を返す
- `generate_text(messages, ...)` — プロバイダを判定して生成。**推論モデル互換対応**を内蔵（`system`→`developer` 変換、`temperature`/`max_tokens` 除去、`max_completion_tokens` 使用）
- `generate_text_with_structured_output(messages, response_format)` — Pydantic モデルで構造化出力（OpenAI は `response_format`、Gemini/Vertex は事後 JSON 抽出）
- `generate_embeddings(texts)` — 埋め込みベクトル生成（次元は `llm_embedding_dim`）

### `config.py` — 設定
`Settings`（pydantic-settings）。`LLM_PROVIDER` / `LLM_API_KEY` / 各モデル名 / `LLM_EMBEDDING_DIM` / GCP（Vertex ADC）/ `DATABASE_URL` / `NEO4J_*` / `MINIO_*` / `GROBID_URL` などを環境変数から読み込みます。互換のため `OPENAI_API_KEY` 等の別名も受け付けます。

---

## 3. 抽出・埋め込み・検索

### `extractor.py` — PDF → 構造化データ
- `extract_tei_xml_from_pdf_bytes()` — GROBID `/api/processFulltextDocument` に投げて TEI-XML を得る
- `parse_tei_to_logical_chunks()` — TEI から Abstract + 本文 `<div>` を抽出（参考文献等は除外）、長すぎる節は文境界で分割
- `extract_paper_structure()` — **仮説駆動型のリファインメントループ**: 先頭チャンクから初期仮説を生成 → 後続チャンクで反復的に精緻化 → `PaperStructure`（変数 + SMILES DSL の因果エッジ）を確定
- `compute_structure_diff()` / `evaluate_and_merge_proposals()` — 構造差分とマージ評価

> なお、教材アップロード後の本格的な構造化は、より新しい **Agent パイプライン**（`document_pipeline/`）が担います。[パイプライン概要](../pipeline/overview.md) を参照。

### `embedder.py` — pgvector への保存
- `embed_and_store(chunks, material_id, extracted_structure)` — チャンクを 100 件単位で埋め込み、`documents` を upsert、`chunks` に `embedding(halfvec 3072)` + `smiles_dsl` / `variables` / `ancestors` を保存

### `chat.py` — RAG チャット
ユーザー質問への回答を組み立てるオーケストレータ。詳細は専用ページ → [RAG チャットフロー](rag-chat.md)。

---

## 4. 講義（レクチャーモード）

### `lecture.py`
- `generate_spoken_text_and_formulas()` — チャンクから display_text（OCR 補正・数式を `[[FORMULA_N]]` で置換）と spoken_text（LaTeX を読み上げ文へ）、formulas 配列（`{id, latex, spoken, is_display}`）を生成。レート制限のリトライ付き
- `build_lecture_sequence()` — チャンクを導入→詳細→まとめの講義フローに編成
- `get_user_mastered_concepts()` — 習得済み概念で既知チャンクをスキップ判定

### `tts.py`
- `generate_tts_audio(spoken_text)` — プロバイダ分岐（OpenAI tts-1 / Google Cloud TTS）で MP3 バイト列を返す。認証失敗は `TtsFatalError`、一時エラーは `None`

→ 学習 UI 側の動き: [学習機能](../features/learning.md)。

---

## 5. 学習支援・正規化・ペルソナ

| モジュール | 役割 |
|---|---|
| `learning_support_agent.py` | 学習の寄り道（前提復習・詳細展開）を散文でなく明示的な状態として構造化。`LearningSupportResult`（answer, mode, origin, next_actions） |
| `personas.py` | ナレーション/応答のトーン（一般⇄専門 × フレンドリー⇄フォーマルの 4 種）。`persona_prompt(persona_id, target)` |
| `concept_normalizer.py` | 数式記号・別名の正規化（λ→"lambda" 等、snake_case 化）。カートリッジの aliases / notation_patterns を利用 |
| `course_content_builder.py` | パイプライン成果物（CourseMapping / ComponentAssembly）でコースの topics を肉付け |
| `document_sections.py` | チャンクから階層セクション構造を復元（見出し検出、section_id 付与） |

---

## 6. スキーマ進化・パターンマッチ

| モジュール | 役割 | 詳細 |
|---|---|---|
| `schema.py` | 全 Pydantic モデル（`OntologyType`, `CorePredicate`, `CausalEdge`, `AbstractStructure`, `PaperStructure` …） | [DSL と理論操作グラフ](../pipeline/theory-graph.md) |
| `schema_registry.py` | OntologyType/CorePredicate を DB から動的ロード（60 秒キャッシュ）、ビルトイン seed | [動的スキーマ進化](../pipeline/schema-evolution.md) |
| `meta_analyzer.py` | 未回答クエリ → スキーマ拡張提案を LLM で生成 | 〃 |
| `simulator.py` | 提案承認前の Shadow Testing（Before/After 差分） | 〃 |
| `reextractor.py` | スキーマ更新後、既存ドキュメントを再抽出するバックグラウンドジョブ | 〃 |
| `theory_components.py` | DSL から理論コンポーネント抽出、TheoryOperationGraph 関連 | [理論操作グラフ](../pipeline/theory-graph.md) |
| `isom.py` | `PaperStructure` を `.isom`（YAML front-matter + SMILES DSL）へシリアライズ | 〃 |
| `harvester.py` | arXiv API から論文収集（商業出版社フィルタ付き）、MinIO 保存 | 〃 |
| `batch.py` | 構造的同型性評価（新パターン登録時に過去論文へクロスドメインマッチ） | 〃 |

---

## 7. Agent パイプライン オーケストレータ（document_pipeline/）

`backend/core/document_pipeline/` が PDF 解析 Agent 群を順番に駆動します。

| ファイル | 役割 |
|---|---|
| `orchestrator.py` | `run_document_pipeline()`。23 ステージを順次実行。ステージ単位で再開可能、進捗コールバック、`PipelineStageError` でステージ名付きエラー |
| `chunker.py` | ブロックからチャンク生成（決定論的） |
| `persistence.py` | 成果物の PostgreSQL/Neo4j 永続化 |
| `export_validation_gate.py` | 最終検証ゲート（成果物完全性・ソースバッキング整合性） |
| `completeness.py` / `dsl_text.py` / `tex_archive.py` / `revision/` | 完全性チェック、DSL テキスト化、TeX アーカイブ処理、リビジョン |

詳細は [パイプライン概要](../pipeline/overview.md) と [PDF 解析 Agent 詳細](../pipeline/agents.md)。

---

[← API とルーティング](api.md) ｜ 次へ: [RAG チャットフロー →](rag-chat.md)
