# コアエンジン（backend/core/）

[← ドキュメント目次](../README.md)

`backend/core/` は FastAPI に依存しない再利用可能なコアロジック群です。
ここでは各モジュールの責務と、どのデータストア・外部サービスと話すかをまとめます。

> 設計ルール: `core/` に FastAPI を import しない（テスト容易性。層ごとのガードレールテストが
> 構造的に固定しています）。`llm.py` / `storage.py` は `@lru_cache` 等でシングルトン化。
> PostgreSQL は `postgres.get_session()` を使い `try/finally` で必ず close。

> **網羅性（2026-09-03 時点）**: `backend/core/` 直下の全モジュールと全サブパッケージを本ページの
> 表と突合済み。モジュールを追加したら該当節に行を足すこと。各層の不変条項・DB スキーマ・
> API 仕様の正本は `docs/features/*_design.md` 側にあり、本ページは**どこに何があるか**の索引です。

---

## 1. データストア接続

| モジュール | 役割 |
|---|---|
| `postgres.py` | SQLAlchemy エンジン/セッション管理（`get_engine()`, `get_session()`, `check_connection()`） |
| `models.py` | SQLAlchemy ORM 定義（users, documents, chunks, learning_courses, background_tasks など。[データモデル](../architecture/data-model.md) 参照） |
| `storage.py` | MinIO ラッパー（`StorageManager`）。バケット `raw-papers` / `raw-texts` / `figure-images` の upload/get/remove |
| `migrations.py` | マイグレーションランナー。正本は `backend/db/*.sql`（init.sql + 番号順ファイル群）で、**毎起動・番号順に全ファイルを冪等再実行**する（pg_advisory_lock で多重起動排他・ファイル単位トランザクション） |

---

## 2. LLM / 設定の抽象化

### `llm.py` — LLM アダプタ
OpenAI / Gemini(REST) / Vertex AI を 1 つのインターフェースで扱います。
**全 LLM 呼び出しの計測点**でもあり、U層（`llm_usage/`）のフックとM層（`llm_policy.py`）の
モデル解決はここに一元化されています（呼び出し側にモデル決定ロジックを新規に書かない）。

- `get_llm_params(mode)` — `"fast" | "standard" | "deep"` に応じてモデルと reasoning effort を返す
- `generate_text(messages, ...)` — プロバイダを判定して生成。**推論モデル互換対応**を内蔵（`system`→`developer` 変換、`temperature`/`max_tokens` 除去、`max_completion_tokens` 使用）
- `generate_text_with_structured_output(messages, response_format)` — Pydantic モデルで構造化出力（OpenAI は `response_format`、Gemini/Vertex は事後 JSON 抽出）
- `generate_structured_with_images(...)` — vision 付き構造化出力（v1 は OpenAI 経路のみ）
- `generate_conversation_turn(...)` — マルチターン対話の1ターン（W層対話・教材図スタジオが使用）
- `generate_embeddings(texts)` — 埋め込みベクトル生成（次元は `llm_embedding_dim`）
- `transcribe_audio(audio_bytes, filename, ...)` — 音声の文字起こし（`LLM_TRANSCRIBE_MODEL`、既定 whisper-1。openai プロバイダのみ、他プロバイダは RuntimeError）

### `config.py` — 設定
`Settings`（pydantic-settings）。`LLM_PROVIDER` / `LLM_API_KEY` / 各モデル名 / `LLM_EMBEDDING_DIM` / GCP（Vertex ADC）/ `DATABASE_URL` / `MINIO_*` / `GROBID_URL` などを環境変数から読み込みます。互換のため `OPENAI_API_KEY` 等の別名も受け付けます。

### `llm_policy.py` / `llm_policy_store.py` — 場面別モデル選択（M層, migration 061）
**モデル決定の正本**。scene キー（U層の feature 文字列を再利用）ごとに
「呼び出し引数 > 実行時 override（contextvar）> user 行 > system 行 > `*_LLM_MODEL` env > tier 既定」
の順で解決します。`llm_policy_store.py` が DB バックエンド（fail-open・20秒 TTL キャッシュ）。
**「env を読んでモデルを決める」処理を他所に新規に書かない**（M1）。詳細は
[docs/features/llm_model_selection_design.md](../features/llm_model_selection_design.md)。

---

## 3. 抽出・埋め込み・検索

### `extractor.py` — PDF → GROBID 変換
- `extract_tei_xml_from_pdf_bytes()` — GROBID `/api/processFulltextDocument` に投げて TEI-XML を得る（`document_pipeline/orchestrator.py` が使用）。**現在このモジュールの公開関数はこれ 1 本だけ**です

> 旧仮説駆動型の逐次 LLM 構造抽出（`extract_paper_structure()` とその内部ステップ）と、
> 旧構造 diff/merge（`compute_structure_diff()` / `evaluate_and_merge_proposals()`。参照していた
> `backend/tests/core/test_diff_merge.py` も同時に削除）は、本番呼び出し元が存在しなかったため
> 2026-07 に削除済み。教材アップロード後の本格的な構造化は、**Agent パイプライン**
> （`document_pipeline/`）が担います。[パイプライン概要](../pipeline/overview.md) を参照。

### `embedder.py` — pgvector への保存
- `embed_and_store(chunks, material_id, extracted_structure)` — チャンクを 100 件単位で埋め込み、`documents` を upsert、`chunks` に `embedding(halfvec 3072)` + `smiles_dsl` / `variables` / `ancestors` を保存

### `chat.py` — **レガシー**（現行の呼び出し元なし）
tier 付き chunk 検索ユーティリティ（`search_chunks()` / `_embed_query()`）。
**本番コードからの呼び出し元はありません**（参照するのは
`backend/tests/test_learner_experience_layer.py` のみ）。実 RAG チャットは
`routes/learning.py`、可視性ゲート付き検索は `api/services.py::search_chunks_with_metadata`
が担います（→ [RAG チャットフロー](rag-chat.md)）。削除候補。

---

## 4. 講義（レクチャーモード）

### `lecture.py`
- `generate_spoken_text_and_formulas()` — チャンクから display_text（OCR 補正・数式を `[[FORMULA_N]]` で置換）と spoken_text（LaTeX を読み上げ文へ）、formulas 配列（`{id, latex, spoken, is_display}`）を生成。レート制限のリトライ付き
- `lecture_uses_topic_material(topic)` — **表示ソース判定の正本**。トピックが `student_material` / `content` / `summary` / `spoken_script` を持てばトピック教材経路、無ければ PDF チャンク経路。受講表示・音声生成・readiness の3者が同じ述語を使う
- `split_slides()` / `build_topic_slides()` / `auto_paginate_slides()` — **スライド分割の正本**（決定論的・LLM 非使用）。プレビュー（`POST /api/admin/lecture-studio/preview-split`）と配信が同じ実装を通る。**クライアント側に分割ロジックを再実装しない**
- `compute_material_audio_readiness()` — 音声準備完了判定の正本（スライド単位 + 言語一致）。`status/projector.py` と `routes/lecture.py` の両方がこれを呼ぶ
- `build_lecture_sequence()` — チャンクを導入→詳細→まとめの講義フローに編成
- `get_user_mastered_concepts()` — 習得済み概念で既知チャンクをスキップ判定

### `lecture_wm.py` — WMレンズ（教員支援）
スライドの相互作用性を段階ラベルで返す静かな計器。`preview-split` のレスポンスに
`wm: {level, level_label, fact, degraded?}` として相乗りします（最低段はキー自体を付けない）。
正本は [docs/features/teacher_triage_instruments_design.md](../features/teacher_triage_instruments_design.md) §3.2。

### `tts.py`
- `generate_tts_audio(spoken_text)` — プロバイダ分岐（OpenAI tts-1 / Google Cloud TTS）で MP3 バイト列を返す。認証失敗は `TtsFatalError`、一時エラーは `None`
- `strip_text_for_speech(text, limit=4000)` — 回答テキストを読み上げ向けに整形（LaTeX・markdown 記号・出典マーカーの除去）。学習側 `/api/learning/voice/speak` と管理側 `/api/admin/deliberation/voice/speak` が使用

→ 学習 UI 側の動き: [学習機能](../features/learning.md)。

---

## 5. 学習支援・正規化・ペルソナ

| モジュール | 役割 |
|---|---|
| `learning_support_agent.py` | 学習の寄り道（前提復習・詳細展開）を散文でなく明示的な状態として構造化。`LearningSupportResult`（answer, mode, origin, next_actions） |
| `personas.py` | ナレーション/応答のトーン（一般⇄専門 × フレンドリー⇄フォーマルの 4 種）。`persona_prompt(persona_id, target)` |
| `concept_normalizer.py` | 数式記号・別名の正規化（λ→"lambda" 等、snake_case 化）。カートリッジの aliases / notation_patterns を利用 |
| `symbol_notation.py` | SymbolRegistry の表記正規化キーへの読み取り専用の橋（`core/deliberation/` から A層を直接 import しないための境界） |
| `course_content_builder.py` | パイプライン成果物（CourseMapping / ComponentAssembly）でコースの topics を肉付け |
| `document_sections.py` | チャンクから階層セクション構造を復元（見出し検出、section_id 付与） |
| `learning_experience.py` | 学習体験レイヤー（B層）の共通ロジック（OutOfSourceGuard・tier 集約など） |
| `component_candidates.py` | 質問→理論コンポーネント候補の生成（C層。AI は候補提示まで、確定は教員） |
| `cartridges.py` | ドメインカートリッジローダー（`backend/cartridges/<id>/` の JSON/Markdown 一式を統合） |
| `graphs/` | 学生向けリアルタイム対話のグラフ組み立て（`student_graph.py` / `state.py`） |

### 学習者向け要素文脈（読み取り専用・非LLM）

| モジュール | 役割 |
|---|---|
| `learner_context_common.py` | **共通正本**: ID解決スコープ強制・candidate 除外・レーン上限・ITEM 射影・内部 ID / 生 TeX の遮断・`navigable` の fail-closed・`strip_confidence`。**学習者向け文脈の射影・遮断を再実装しない** |
| `component_context.py` | コーススコープ component 文脈（instance / shared_part / graph）。上記への委譲 |
| `element_context.py` | 学習者向け claim / equation 文脈（W層レンズの射影）。上記への委譲 |
| `text_excerpt.py` | 切り詰め（excerpt）と TeX 数式判定の**単一正本** |
| `element_vocab.py` | 統制語彙キー → 日本語表示名のサーバ側正本（ミラーは `frontend/public/js/element-vocab.js`） |

---

## 6. スキーマ進化・パターンマッチ

| モジュール | 役割 | 詳細 |
|---|---|---|
| `schema.py` | 全 Pydantic モデル（`OntologyType`, `CorePredicate`, `CausalEdge`, `AbstractStructure`, `PaperStructure` …）と監査 entity_type カタログ（`AUDIT_ENTITY_*` / `AUDIT_ENTITY_TYPES`。**正本はコード**） | [DSL と理論操作グラフ](../pipeline/theory-graph.md) |
| `schema_registry.py` | OntologyType/CorePredicate を DB から動的ロード（60 秒キャッシュ）、ビルトイン seed | [動的スキーマ進化](../pipeline/schema-evolution.md) |
| `meta_analyzer.py` | 未回答クエリ → スキーマ拡張提案を LLM で生成 | 〃 |
| `simulator.py` | 提案承認前の Shadow Testing（Before/After 差分） | 〃 |
| `reextractor.py` | スキーマ更新後、既存ドキュメントを再抽出するバックグラウンドジョブ | 〃 |
| `theory_components.py` | DSL から理論コンポーネント抽出、TheoryOperationGraph 関連 | [理論操作グラフ](../pipeline/theory-graph.md) |
| `isom.py` | `PaperStructure` を `.isom`（YAML front-matter + SMILES DSL）へシリアライズ | 〃 |
| `harvester.py` | arXiv API から論文収集（商業出版社フィルタ付き）、MinIO 保存 | 〃 |

---

## 7. Agent パイプライン オーケストレータ（document_pipeline/）

`backend/core/document_pipeline/` が PDF 解析 Agent 群を順番に駆動します。

| ファイル | 役割 |
|---|---|
| `orchestrator.py` | `run_document_pipeline()`。名前付き 29 ステージ（2026-09-03 時点。`PIPELINE_STAGES` は終端マーカー `completed` を含め 30 エントリ。+ between-stage フック）を順次実行。ステージ単位で再開可能、進捗コールバック、`PipelineStageError` でステージ名付きエラー。ステージ構成の正本は `PIPELINE_STAGES` / `_PIPELINE_STEPS`。M層のステージ別モデル選択対象は `LLM_STAGE_NAMES`（`_PIPELINE_STEPS` の `model_policy=True` からの導出値）、実際に LLM を呼ぶ集合は `LLM_CALLING_STAGE_NAMES`、vision は `VISION_STAGE_NAMES` |
| `chunker.py` | ブロックからチャンク生成（決定論的） |
| `persistence.py` | 成果物の PostgreSQL 永続化 |
| `export_validation_gate.py` | 最終検証ゲート（成果物完全性・ソースバッキング整合性） |
| `figure_images.py` | **L層**: `figure_image_extraction` ステージ本体（非LLM）。PyMuPDF の埋め込み画像抽出 + caption 近傍の領域レンダリング fallback、図中ラベル（`inner_labels`）抽出。保存先は MinIO `figure-images` + `document_figures` |
| `figure_context.py` | **L層**: 図ごとの周辺本文（caption 直近 / `Fig. N` 参照メンション / 同一セクション本文）と略語辞書を決定論的に収集し、`apparatus_semantics` の LLM 入力にする |
| `contextual_explanation_inputs.py` | `contextual_explanation` ステージの入力構築。components / claims / equations / figures / thesis から**不透明 ID を解決済みテキストに展開**した `ElementExplanationInput` を組む（設計原則 E4） |
| `completeness.py` / `dsl_text.py` / `tex_archive.py` / `revision/` | 完全性チェック、DSL テキスト化、TeX アーカイブ処理、リビジョン |

**新ステージは `_stage_<name>(ctx)` 関数 + `_PIPELINE_STEPS` への登録**で追加します（インライン展開に
戻さない。Tier 3-19）。詳細は [パイプライン概要](../pipeline/overview.md) と
[PDF 解析 Agent 詳細](../pipeline/agents.md)。

---

## 8. 横断基盤（共有ユーティリティの正本）

同型実装のコピペ増殖を止めるための正本モジュール群。**新機能で同種の処理を書くときは必ずこれらを
使う**（実施記録は `docs/architecture/consolidation_survey_2026-07.md`）。

| モジュール | 役割 |
|---|---|
| `llm_worker/` | 非同期 LLM worker の共通骨格。`client.py`（`BaseJSONLLMClient` — `core.llm` 経由で U層計測を維持）/ `repair.py`（`run_with_repair` = 1+2回試行）/ `cost_gate.py`（`CostGate`（session+daily）/ `InMemoryCounterGate`）/ `history.py`（`window_history()` — **会話履歴を LLM に渡すときは必ずこれを通す**。保存用の履歴はウィンドウ化しない） |
| `candidate_flow.py` | 候補→確定ワークフローの共通制御フロー（`CandidateVocabulary` / `CandidateFlow`（confirm / dismiss / supersede と監査記帳の順序）/ `select_supersedable`）。語彙・SQL・トリガはドメイン側に残す |
| `label_vocab.py` | 段階ラベル・共有語彙表の正本（`GradedScale` = 数値→段階ラベル。None/非数値は必ず最も慎重な末尾ラベルへ）。**数値→段階ラベルの変換表・enum→日本語の語彙表を新規に直書きしない** |
| `privacy.py` | k-匿名ゲートの正本（`K_ANONYMITY = 3` / `meets_k_anonymity` / `bucket_count_range`(3-5 / 6-10 / 11+)）。**k=3 をリテラルで再定義しない** |
| `revision_store.py` | draft/freeze/楽観ロックの共通プリミティブ（`RevisionConflictError` / `update_with_revision_lock` / `idempotent_seed_import`）。`atlas_store` と `library/store` が委譲 |
| `course_data.py` | `learning_courses.data` JSONB の正本スキーマ + アクセサ（`course_topics` / `iter_all_topics` / `course_sources` / `find_course_topic` など。全モデル `extra="allow"`）。**course_data への素の dict アクセスを新規に書かない** |
| `notification_recipients.py` | 通知宛先解決（所有者 / group member）の共通 JOIN プリミティブ。宛先集合の方針は各層に残す |
| `trace_registry.py` | `interest_traces` の **kind 登録簿の正本**（各 kind の露出3宣言 = 問いの軌跡 / 教員向け k-匿名集約 / わたしの地図 + 主要消費者の方式宣言）。新しい kind・消費者は登録簿に宣言する |
| `trace_ledger.py` | 主権台帳v1「わたしの記録」の合成（本人の全痕跡の一望 + 持ち出し JSON。読み取り専用） |
| `url_fetch.py` | URL 教材取得の SSRF ガードの正本（ドット境界のドメイン照合・`getaddrinfo` の全アドレス検査・リダイレクト各ホップ再検証・実バイトのマジックによる形式判定・100MB / 60秒上限）。`allowed_domains` は必須引数で空は専用エラー |
| `account_status.py` / `account_lifecycle.py` / `auth_events.py` | アカウントライフサイクル（migration 068/069）。トークン世代の照合（30秒 TTL キャッシュ）/ `PURGE_TABLES`・`RETAIN_TABLES` と移管 / 認証イベント語彙の正本 |
| `migrations.py` | 上記「1. データストア接続」参照 |

---

## 9. 層別サブパッケージ

各層の不変条項・DB・API は `docs/features/*_design.md` が正本です。ここは所在の索引。

### 学習者側（B層 / UCサイクル / 個人地図 / 回遊）

| パッケージ | 役割 |
|---|---|
| `tension/` | TensionMiningAgent（migration 022）。詳細は下記 §9.1 |
| `structure_anchor/` | 構造帰属型の問い記録（migration 025）。tension と同型の独立モジュール（agent / input_builder / prompt / llm_client / schema / validator / repair / worker）。コスト上限も tension とは独立 |
| `reconstruction/` | 再構成ループ（R層, migration 036）。`item_builder.py`（出題変換・非LLM）/ `diff.py`（実行時 DIFF + REFLECT 事実文・非LLM）/ `derivation_source.py`（式スケール ELICIT の決定論生成）/ `health.py`（レビューキュー）/ `stumble.py`（k-匿名つまづきサマリー）/ オーサリング LLM 一式 + `worker.py` |
| `cycle/` | 理解サイクル（UC）。`schema.py`（`INTENTION_ROLES` / `QUICK_LABELS` の正本）/ `queries.py` / `derive.py` / `map_diff.py`（「帰り道の景色」。肯定形の事実文のみ） |
| `descent/` | 構造の降下路（足場ダイヤル・楽屋）。`engine.py`（非LLM・決定論）/ `resolve.py`（コース sources 内限定の fail-closed 解決）。**閲覧をサーバに記録しない** |
| `discuss/` | 「論文と話す」ディスカッションモード。`context.py`（コース無し議論のセンチネル `_doc:{document_id}` の**組み立て正本**）/ `opening.py`（開幕投影・非LLM・LLM 0回）/ `authoring.py`（開幕素材の `source_fingerprint`）/ `mirroring.py`（鏡面化 move の決定論抽出）/ `observation.py`（discuss 観測基盤, migration 060） |
| `personal_graph/` | 個人知識ネットワーク（**保存物ではなく毎回導出**・本人のみ可視）。`queries.py`（DB 読み集約）/ `derive.py` / `journey.py`（旅）/ `nearby.py`（「いまここの周り」）/ `atlas_fog.py`（名前のある霧）/ `provisional.py`（暫定ノード）/ `bridges.py`（Phase B の k-匿名橋候補集約）/ `graph_data.py` |
| `corpus_view.py` | コーパス回遊（migration 073）の読み時導出。可視性交差は SQL 内 `= ANY(:doc_ids)` で強制し、空集合は SQL を発行しない |

#### 9.1 `tension/` — TensionMiningAgent（B層, migration 022）
対話ログから「理解した上での引っかかり（tension）」の**候補**を抽出するサブパッケージ。
LLM 出力は常に `status='candidate'` で、本人の confirm を経てのみ確定します（詳細は [RAG チャットフロー](rag-chat.md)）。

| ファイル | 役割 |
|---|---|
| `prefilter.py` | **Stage 0（同期・非LLM・数ms）**: `judge_tension_hint()`。ヘッジ/逆接マーカー・同語再訪でヒント判定し `payload.tension_hint` を付与 |
| `worker.py` | `threading.Thread` 方式の非同期バッチ。未解析ヒント累積 5 件 or セッション終了（20分無活動）で起動。冪等性は `analyzed_at`、コスト上限は `TENSION_MAX_CALLS_PER_SESSION` / `TENSION_MAX_CALLS_PER_DAY` |
| `agent.py` | `TensionMiningAgent.run()` — 会話窓 1 つにつき LLM 1 コールで tension 候補を分類 |
| `input_builder.py` | ヒント発話を核にした会話窓（ConversationWindow）の組み立て |
| `prompt.py` / `llm_client.py` | プロンプト定義と LLM 呼び出し（fast tier 既定、`TENSION_LLM_MODEL` で上書き） |
| `schema.py` | tension 型・status 語彙の定義（`TENSION_TYPES` など） |
| `validator.py` / `repair.py` | 構造化出力の検証と修復。`paraphrase` の推量形をハード強制。2 回修復失敗は `unclassified` / `confidence=0.0` で 1 行保持（情報を落とさない） |
| `examples/` | サンプル入出力 JSON |

### 教員側（D層 / SL層 / W層 / L層 / 図スタジオ）

| パッケージ | 役割 |
|---|---|
| `doubt/` | D層（認識的地位台帳, migration 029〜033）+ SL層 賭け金の台帳（migration 067）。`ledger_builder.py`（非LLM バックフィル）/ `load_calculator.py`（下流到達集合・決定論）/ `naive_signal.py`（k-匿名集計）/ `counterfactual.py`（3区分の決定論伝播）/ `open_assumptions.py` / `dependency.py` / `scope_candidates/`・`assumption_mining/`・`falsification_conditions/`（いずれも非同期 LLM worker）/ `observation_targets.py` / `support_paths.py`（純 Python の単位容量 Edmonds–Karp。経路数を関数外に出さない）/ `seminar_brief.py`（ゼミ前ブリーフの読み時合成）/ `metrics.py` / `schema.py` |
| `deliberation/` | W層 要素検討ワークスペース。`refs.py`（ElementRef）/ `decomposition.py`（面①）/ `positioning.py`（面②4レンズ）/ `context_lens.py`（要素中心コンテキストレンズ）/ `dialogue.py`（対話・CostGate。音声の day-only ゲートも同居）/ `graph_dialogue.py`（グラフ全体対話の非LLM grounding 投影 = グラフ対話レビュー, migration 075）/ `annotations.py` / `identity_links.py` / `inventory.py`（要素インベントリ）/ `labels.py`（ラベルラダーの正本）/ `store.py` / `standardization/`（三角測量の判定） |
| `library/` | L層 ナレッジライブラリ（migration 042）。`store.py`（draft/freeze・`revision_store` へ委譲）/ `search.py`（retrieval は**凍結版のみ**）/ `seed.py`（カートリッジ同梱の冪等取込）/ `schema.py`（`standardization_status` 語彙の正本）。削除 API は無く `retired` 遷移のみ |
| `teaching_figures/` | 教材図スタジオ（migration 063）。`sanitizer.py`（**保存の唯一の入口**。lxml 固定・外部参照 / script / foreignObject / image / on* を拒否）/ `generator.py` / `prompt.py` / `store.py` / `suggest.py` / `signals.py`（学習者信号は k-匿名通過済みのレンジ・段階ラベルのみ） |
| `figure_presentation.py` / `figure_reanalysis.py` | 図の表示モード分類の永続化・API 投影（#496）と、教員指示付きの単図 vision 再解析（常に candidate） |
| `element_explanations.py` | `element_explanations` の DB プリミティブ（migration 056 / 062。二層説明 + discuss 開幕素材） |
| `teacher_triage.py` | 負荷順トリアージ（説明レビューキューと R層 item の並べ替え。教員支援 Phase 4） |
| `admin_assistant/` | Admin Copilot（migration 034）+ G層。`capabilities.py`（**capability registry = 画面横断の単一の真実源**）/ `intent.py` / `knowledge.py`（操作 KB 索引。`help_kb` へ委譲）/ `actions/`（capture_before / apply / revert）/ `action_store.py` / `next_steps.py`（G層 To-Do。完了フラグを持たず毎回導出） |

### 分野の地図（Atlas / ランドスケープ / ディスカバリー）

| モジュール・パッケージ | 役割 |
|---|---|
| `atlas.py` | 骨格の共通定義・上限（`MAX_REGIONS` 等） |
| `atlas_store.py` | 骨格の DB 正本（draft / 凍結版・楽観ロック・同梱骨格の冪等シード・domain 単位 advisory lock）。**骨格の読みは必ず `load_learner_skeleton()` を使う** |
| `atlas_generator.py` | 骨格 draft の LLM バッチ生成（出力は常に `status: draft`） |
| `atlas_state.py` | 状態導出バッチと `atlas_overlay_cache`（3層モデルの C 層） |
| `atlas_placement.py` | コーパス概念の骨格領域への最近傍割当（純関数・DB/FastAPI/LLM 非依存） |
| `atlas_path.py` | 学習パス提案カードの決定論的生成（リアルタイム LLM 生成をしない） |
| `atlas_reports.py` | 修正報告フロー（帰属つき・骨格バージョンつき） |
| `atlas_lifecycle.py` | ドメインライフサイクル（migration 057）。凍結前の影響プレビュー・freeze / retire の通知方針 |
| `atlas_gaps/` | カテゴリギャップ候補（migration 066）。`schema.py`（`normalize_label` / `cluster_key` の正本）/ `store.py`（`DELETE FROM` なし）/ `patching.py`（決定論 JSON Patch・**op は add のみ**）。**gap 系コードから `atlas_skeletons` へ書き込まない** |
| `atlas_vectors/` | VA層 ベクトル係留（migration 074）。`schema.py`（合成テキスト）/ `store.py`（全置換保存・別名 CRUD は状態遷移のみ）/ `builder.py`（1バッチ embed + 日次ゲート）/ `query.py`（純計算: cosine・プレフィルタ・着地予測）/ `annotate.py`（gap クラスタの近傍注記・fail-soft） |
| `atlas_edges/` | RE層 辺候補（migration 076）。`derive.py`（**毎回読み時導出**・embedding を呼ばない）/ `store.py`（遷移は `candidate_flow` 経由）/ `patching.py`（op=add `/edges/-`）/ `threads.py`（学習者向け「推定の糸」・fail-soft） |
| `landscape/` | 知識ランドスケープ 配置層（migration 065）。`schema.py`（perspective 6語彙 / status 5語彙の正本）/ `store.py`（DELETE 文なし・空 candidates は SQL 非発行）/ `builder.py`（パイプラインと教員の手動再提案が**同一経路・同一 CostGate**）/ `projection.py`（`weight` / `confidence` を落とす DTO） |
| `paper_discovery/` | 論文ディスカバリー（migration 071/072）+ 論文レーダー。`schema.py`（arXiv ID 正規化）/ `arxiv_client.py`（宛先固定・3秒スロットル）/ `citation_client.py` / `citation_search.py` / `vocab.py`（キーフレーズ供給の5語彙）/ `store.py`（`DELETE FROM` なし）/ `search.py`（条件ゼロなら外部 API を呼ばない・`closed_world_note` 必須）/ `ranking.py`（**発見層で唯一 `core.llm` に触れる2本のうち1本**）/ `compare.py`（同2本目）/ `corpus.py`（分野→document 解決の正本）/ `radar.py` / `ingest_queue.py` |

### 運用基盤（状態・通知・版・使用量・ヘルプ）

| パッケージ | 役割 |
|---|---|
| `status/` | 状態管理・通知基盤（migration 038）。`projector.py`（**教材・コース状態導出の正本**。バッチ導出付き）/ `events.py` / `watcher.py`（遷移検知）/ `inbox.py` / `notification_rules.py` / `cross_layer_notify.py` |
| `versioning/` | V層 共有物のバージョン管理（migration 037）。`releases.py` / `subscriptions.py` / `deletion.py` / `resolver.py` / `notifications.py` / `audit.py` / `worker.py`（削除猶予スイーパ。**アカウント削除予約の purge も相乗り**） |
| `llm_usage/` | U層 トークン使用量推計（migration 043）。`context.py`（contextvars による帰属）/ `recorder.py`（bounded buffer + flusher。例外を漏らさない）/ `estimator.py` / `observe.py` / `pricing.py`（価格表は `LLM_PRICE_TABLE_PATH` の JSON。**ハードコードしない**）/ `metrics.py` / `document_estimate.py` / `forecast.py` |
| `help_kb/` | 利用者マニュアル KB（migration 058/059）。`index.py`（見出し節分割 + 語彙重なり検索。**TODO マーカー入りチャンクは索引除外**）/ `manual.py`（`search_manual(..., audience=)` は audience 必須の上位継承）/ `ui_anchors.py` / `admin_ui_anchors.py`（**管理UIアンカー表の正本**。値は teacher/ か system_admin/ の節のみ）/ `validator.py` / `store.py`（draft/freeze）/ `vector.py`（ベクトル補助層。専用テーブルで chunks を汚染しない）/ `audit.py`（配信スナップショットの content-hash 記帳） |
| `graph_paper_layer/` | グラフの論文層（Paper Layer, migration なし）。`builder.py`（**純関数**。`build_paper_layer(graph, artifacts, *, figure_rows, explanation_rows)`。フレーム非改変・保存なし・LLM 0回・入力を mutate しない）/ `schema.py` |

---

[← API とルーティング](api.md) ｜ 次へ: [RAG チャットフロー →](rag-chat.md)
