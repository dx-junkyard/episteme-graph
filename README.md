# episteme-graph

大学院生の学習プロセスを支援する知識グラフ管理システム。
PDF文献から概念・主張・数式・関係性を自動抽出してナレッジグラフ／理論操作グラフを構築し、RAGベースの対話型学習・インタラクティブ講義・ハンズフリー音声会話を実現します。

> **詳細ドキュメント:** 設計・動作解説は [docs/](docs/README.md) にまとまっています
> （アーキテクチャ / データモデル / API / PDF解析パイプライン / 機能解説）。

## コンセプト

研究者・大学院生が直面する「散在する先行研究の統合」「前提知識の体系的習得」という課題を解決するため、次の層で設計されています。

1. **知識の構造化（A層）** — PDFをアップロードするだけで、概念・主張・数式・導出を PDF解析エージェントパイプラインが構造化し、概念グラフ・理論操作グラフを自動生成する
2. **適応的学習（B層）** — 習得状態・関心痕跡・違和感（tension）を追跡し、前提知識に応じた問いかけで理解を深める
3. **没入型講義** — TTS音声＋カラオケ式ハイライトで、論文をセミナー形式に変換する
4. **承認・共有（C層）** — 教員が説明バージョン単位で査読承認し、教員間で解釈を共有する
5. **検証・位置づけ・運用の層群** — 疑義と検証の台帳（D層/SL層）、分野の地図と論文の位置づけ（Field Atlas / ランドスケープ）、要素検討（W層）、版管理（V層）、ガイダンス・AI運用基盤（G/U/M層・Copilot・help_kb）

設計思想の全体像（知識観・学習観・AIの役割・横断14原則）は
[docs/vision.md](docs/vision.md) を参照。

## 技術スタック

| レイヤー | 技術 |
|---|---|
| フロントエンド | Vanilla JS SPA + nginx |
| APIサーバー | FastAPI (Python 3.11) |
| RDB + ベクトル検索 | PostgreSQL 16 + pgvector (cosine, 次元数は `LLM_EMBEDDING_DIM`、既定 3072) |
| オブジェクトストレージ | MinIO（S3互換） |
| PDF構造解析 | GROBID（TEI-XML）／フォールバックで PyMuPDF |
| LLM | OpenAI API または Google Gemini / Vertex AI（`LLM_PROVIDER` で切替） |
| TTS 音声合成 | OpenAI TTS (tts-1) または Google Cloud Text-to-Speech |
| 音声文字起こし | OpenAI Whisper 系（`LLM_TRANSCRIBE_MODEL`、ハンズフリー音声会話用） |
| 認証 | JWT (HS256) + bcrypt |

## セットアップ

### 前提条件

- Docker & Docker Compose
- LLM APIキー（OpenAI または Google Gemini）

### 起動手順

```bash
# 1. 環境変数を設定
cp .env.example .env
# .env を編集: LLM_API_KEY (または OPENAI_API_KEY / GEMINI_API_KEY), ADMIN_PASSWORD, JWT_SECRET を必ず設定
# Gemini を使う場合は LLM_PROVIDER=gemini, LLM_EMBEDDING_DIM=768 なども設定

# 2. 全サービスを起動（本番 / CI 用）
docker compose up -d

# 3. ログ確認（初回はマイグレーション完了を確認）
docker compose logs -f api-server
```

> **ネットワーク設計:** 外部に公開されるポートは `frontend:3000` (Nginx) のみです。
> `api-server` や各種データベースへの直接アクセスは Docker 内部ネットワーク経由のみで行われます。

> **PostgreSQL について:** ベースの `docker-compose.yml` には `postgres` サービスが含まれません。
> 本番ではマネージド PostgreSQL を `DB_HOST` で参照し、ローカル開発では
> `docker-compose.local.yml` の `postgres`（pgvector/pgvector:pg16）を併用します。

### アクセス先（本番 / 共通）

| サービス | URL |
|---|---|
| 学習UI | http://localhost:3000 |
| 管理UI | http://localhost:3000/admin.html |

### ローカル開発（postgres + ngrok + デバッグポート公開）

開発時は `docker-compose.local.yml` を併用することで、postgres コンテナ・DBクライアント・ngrok トンネルが利用できます。

#### 事前準備

1. [ngrok](https://ngrok.com) でアカウント作成・固定ドメインを取得
2. `.env` に以下を追加:

```
NGROK_AUTHTOKEN=your_ngrok_auth_token
NGROK_DOMAIN=your-subdomain.ngrok-free.app
# ngrok 経由アクセスを CORS で許可（必要な場合）
CORS_ORIGINS=https://your-subdomain.ngrok-free.app,http://localhost:3000
```

#### 起動コマンド

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d
```

#### アクセス先（ローカル開発時のみ）

| サービス | URL |
|---|---|
| 学習UI（ローカル） | http://localhost:3000 |
| 管理UI（ローカル） | http://localhost:3000/admin.html |
| 学習UI（ngrok） | https://your-subdomain.ngrok-free.app |
| ngrok Web UI | http://localhost:4040 |
| MinIO コンソール | http://localhost:9001 |
| PostgreSQL | localhost:5432（psql / TablePlus など） |

> **セキュリティ注意:** `api-server` のポート（8001）はローカル開発時も直接公開されません。
> API へのアクセスは必ず Nginx（3000番）経由で行ってください。

### 初期アカウント

起動直後は `ADMIN_PASSWORD` で設定したパスワードで管理者としてログインできます。
教師アカウント・学生アカウントは管理UIから作成します。

## 主な機能

### 1. 学習UI（学生向け）

3パネルレイアウト（学習パス・チャット・コンテキスト/進捗/出典）で学習を進めます。
→ 詳細: [docs/features/learning.md](docs/features/learning.md)

- **RAGチャット** — pgvector検索で関連チャンクを取得し、LLMが回答。送信ボタンは「質問」1つに統合
- **回答の出所表示（content_grounding）** — 回答が「教材」「別の資料」「AIの一般知識」のどれに基づくかをバッジ表示
- **誤解検出** — 応答に訂正シグナルが含まれると個人レイヤーへ自動記録
- **前提知識チェック** — コース内 `prerequisites` に基づく適応的ルーティング（未習得なら逆質問）
- **学習進捗トラッキング** — 習得概念・学習中概念・問いの軌跡（関心痕跡）
- **違和感（tension）ダイジェスト** — 対話ログから TensionMiningAgent が「理解した上での引っかかり」候補を検出し、本人の確定（そう、これ / 違う）を経て問いの軌跡に昇格。教員へは k-匿名化集計のみ
- **ハンズフリー音声会話** — 🤖 ボタンで「気軽に話せる先生」モード。無音検知で発話を区切り、Whisper文字起こし → カジュアル対話（`intent_mode='casual'`）→ TTS読み上げのループ
- **コース受講登録** — 公開されたテンプレートコースへの受講登録（マスターと個人進捗を分離）

#### インタラクティブ・レクチャーモード

論文チャンクをセミナー形式の音声講義に変換する没入型学習機能。

- TTS音声（OpenAI tts-1 / Google Cloud TTS）＋カラオケ風ワードハイライト
- 数式のLaTeX表示と音声読み上げテキスト自動生成
- 習得状態に基づく適応的シーケンス（既知チャンクのスキップ・簡易版変換）
- **中断チャット** — 再生を一時停止して質問 → コンテキスト保持で回答 → 再開

---

### 2. PDF → ナレッジグラフ パイプライン

```
PDF アップロード → MinIO 保存
  → GROBID TEI-XML 解析（利用不可時は PyMuPDF フォールバック）
  → PDF解析エージェントパイプライン（src/episteme_graph/agents/, named 29 ステージ）
     文書構造復元 → 主張（claim）の採否・atomic 化 → 数式の意味・導出チェーン
     → 中心命題の再構成 → DSL 接続 → 再利用可能コンポーネント → 理論操作グラフ
  → テキストチャンク → PostgreSQL pgvector（次元数は `LLM_EMBEDDING_DIM` 準拠）
  → 成果物 JSON → MinIO / PostgreSQL
```

→ 詳細: [docs/pipeline/overview.md](docs/pipeline/overview.md) / [docs/pipeline/agents.md](docs/pipeline/agents.md)

- **理論操作グラフ（TheoryOperationGraph）** — 導出チェーンから理論の操作構造を main / equation_detail / debug の層で表現。全ノード・エッジにソースバッキング（`source_backed` / `partially_source_backed` / `inferred` / `review_required`）を明示
- **DSL（SMILES形式）** — 抽象構造を `(varID:OntologyType:value) ==[CorePredicate:verb:polarity]=> (...)` で表現
- **カートリッジシステム** — ドメイン固有の語彙・検証ルールを JSON で注入（`backend/cartridges/particle_physics/`）
- **構造的同型性評価** — パターン登録時にバックグラウンドで過去論文群とのクロスドメインマッチングを非同期実行
- **arXiv ハーベスター** — 商業出版社フィルタリング付きでarXiv論文を自動収集
- **.isom シリアライズ** — OSLパイプライン連携用の `.isom` ファイル出力

---

### 3. 管理UI（教師・管理者向け）

→ 詳細: [docs/features/admin.md](docs/features/admin.md)

#### 教材管理

- PDFアップロード → 非同期バックグラウンドタスクで抽出・グラフ化（`GET /api/admin/tasks/{task_id}` でポーリング）
- 教材詳細で抽出された概念グラフ・理論操作グラフを閲覧
- 教材・コースの開示範囲（Public / Group / Private）設定
- **リビジョンラン** — 解析結果の反復改善（audit → proposed → accept/reject、アクティブなランは常に1つ）

#### AI支援コースビルダー

- LLMとの対話形式でコース構造（章・トピック・前提知識・到達目標）を設計
- セッション履歴をPostgreSQLに永続化（ページリロード後も継続可能）
- 承認時に `is_template = TRUE` でコースを登録し、学生への公開が可能

#### Lecture Script Studio

教員向けのレクチャー原稿事前構築・編集機能。

- コースに紐づくチャンクへの**バッチスクリプト生成**・**バッチ音声生成**（非同期＋ポーリング）
- AIによるスクリプト書き換え（トーン・難易度・長さ調整）、手動保存
- ナレーション/応答ペルソナの設定

#### 理論コンポーネントと承認・共有レイヤー（C層）

- チャンク・コースから再利用可能な理論コンポーネントを抽出・管理
- 1コンポーネントに複数の**説明バージョン**（標準説明＋教員の独自解釈）を並存
- 教員による**説明バージョン単位の承認**（endorsement）と教員間の引用・共有ダッシュボード
- 承認の重みは段階ラベルで表示し、学習者に数値スコアを見せない
→ 詳細: [docs/features/endorsement-sharing.md](docs/features/endorsement-sharing.md)

#### 動的スキーマ進化

固定のOntologyType/CorePredicateを超えてドメイン固有の概念・関係性を拡張できます。

1. 未回答クエリ（`unanswered_query_logs`）をメタ分析エンジンが自動解析
2. 不足しているスキーマ要素をLLMが提案（SchemaProposal）
3. Shadow Testing — 提案を承認前に既存教材でプレビュー検証（Before/After差分）
4. 教員が承認 → 新スキーマを `schema_registry` に追加
5. バックグラウンドで**再抽出ジョブ**を実行し、既存ナレッジグラフを新スキーマで再構築

#### グループ・ユーザー管理

- グループの作成・招待コード・メンバー管理（教材/コースのグループ単位共有）
- 学生アカウント作成（TEACHER以上）、教師アカウント作成（SYSTEM_ADMINのみ）
- エラーログ閲覧、関心・違和感の匿名化ダッシュボード

---

### 4. 認証・ロール・開示範囲

| ロール | 権限 |
|---|---|
| STUDENT | 学習UI、チャット、コース受講登録 |
| TEACHER | 上記＋教材アップロード、コース作成・公開、グループ管理、承認・共有、学生アカウント作成 |
| SYSTEM_ADMIN | 全権限（教師アカウント作成を含む） |

教材・コースは **Public / Group / Private** の開示範囲を持ちます。
→ 詳細: [docs/features/auth-visibility.md](docs/features/auth-visibility.md)

---

## 機能ドキュメント索引（docs/features/）

機能・レイヤーごとの解説/設計書の索引（ビジョン起点の機能群別・完全版は
[docs/README.md](docs/README.md) §4）。レイヤー ↔ migration の対応は
[docs/architecture/layer_registry.md](docs/architecture/layer_registry.md) を参照。

### 機能解説（実装ベース）

- [学習機能（学生UI）](docs/features/learning.md)
- [管理機能（教員/管理者UI）](docs/features/admin.md)
- [認証・権限・開示範囲](docs/features/auth-visibility.md)
- [承認・共有レイヤー（C層）](docs/features/endorsement-sharing.md)
- [構造帰属型の問い記録（B層）](docs/features/structure-anchored-questions.md)

### レイヤー設計書

- [疑義・認識的地位台帳（D層）](docs/features/doubt_layer_issues.md)
- [ガイダンス層（G層）](docs/features/guidance_layer_design.md)
- [画像パイプライン + ナレッジライブラリ（L層）](docs/features/image_pipeline_knowledge_library_design.md)
- [再構成ループ（R層）](docs/features/reconstruction_loop_design.md)
- [LLM トークン使用量推計（U層）](docs/features/llm_usage_metering_design.md)
- [共有物のバージョン管理（V層）](docs/features/shared_versioning_design.md)
- [要素検討ワークスペース（W層）](docs/features/element_deliberation_workspace_design.md)
  ／[外部レビュー](docs/features/element_deliberation_workspace_review.md)
- [段階的翻訳レイヤー（E層・未実装）](docs/features/exposition_layer_design.md)
- [Admin Copilot（横断ユーティリティ層）](docs/features/admin_assistant_design.md)
- [状態管理・通知基盤](docs/features/status_notification_design.md)
- [個人知識ネットワーク（Phase P）](docs/features/personal_knowledge_network_design.md)
  ／[外部レビュー](docs/features/personal_knowledge_network_review.md)
- [知識ネットワークビジョン（W層・個人知識ネットワークの親文書）](docs/features/knowledge_network_vision.md)
- [場面別 LLM モデル選択（M層）](docs/features/llm_model_selection_design.md)
- [賭け金の台帳（SL層）](docs/features/stakes_ledger_design.md)
- [理解サイクル（UCサイクル）](docs/features/understanding_cycle_design.md)
- [知識ランドスケープ（配置層）](docs/features/knowledge_landscape_design.md)
- [カテゴリギャップ候補](docs/features/category_gap_candidates_design.md)
- [教材図スタジオ](docs/features/teaching_figure_studio_design.md)
- [利用者マニュアル KB（help_kb）](docs/features/manual_help_kb_design.md)
- [リリース前の確認フロー](docs/features/release_review_flow_design.md)
- [二層説明（generic/contextual）](docs/features/hierarchical_context_explanation_design.md)
- [Atlas バインディング該当なしUX + ドメインライフサイクル](docs/features/atlas_binding_lifecycle_design.md)
- [チャット型AI支援の共通基盤](docs/features/assistant_common_infra_design.md)

### discuss（論文と話す）

- [ディスカッションモード本体](docs/features/discussion_mode_design.md) /
  [対話の歩調合わせ](docs/features/discuss_dialogue_alignment_design.md) /
  [開幕素材のオーサリング](docs/features/discuss_opening_authoring_design.md) /
  [観測基盤](docs/features/discuss_observation_design.md)

### 学習UI・要素文脈

- [component 根拠カードのチップ化 + 文脈API](docs/features/component_evidence_redesign.md)
- [要素文脈の提示再設計](docs/features/element_context_presentation_redesign.md) /
  [学習者向け要素文脈API](docs/features/learner_element_context_design.md)
- [数式ホバー内容](docs/features/equation_hover_content_design.md) /
  [数式文脈パネル](docs/features/equation_context_panel_display_design.md)
- [学習画面UI再編 + インスペクト/ホバー係留](docs/features/learning_ui_inspect_hover_design.md)

### 分野の地図（Field Atlas）

- [骨格](docs/features/field_atlas_skeleton.md) /
  [コース⇄地図バインディング](docs/features/field_atlas_binding.md) /
  [修正報告](docs/features/field_atlas_correction_reports.md) /
  [DB 管理化](docs/features/field_atlas_db_managed_skeleton.md) /
  [詳細パネル](docs/features/field_atlas_detail_panel.md) /
  [骨格エディタ強化](docs/features/field_atlas_skeleton_editor_upgrade.md)

### レクチャー・図解析ほか

- [レクチャースライド同期 + 音声言語切替](docs/features/lecture_slide_sync_design.md)
- [音声生成の準備確認フロー（#491）](docs/features/lecture_audio_generation_readiness.md)
- [教員指示付き図再解析](docs/features/guided_figure_reanalysis_design.md)
- [図解析の反証型反復検証（#499）](docs/features/contextual_figure_analysis_iterative_verification.md)
- [図⇄概念構造の接続](docs/features/figure_concept_linking_design.md)
- [要素インベントリ](docs/features/element_inventory_design.md)
- [要素中心コンテキストレンズ（#498）](docs/features/element_context_lens_design.md)

---

## ディレクトリ構成

```
episteme-graph/
├── frontend/
│   ├── public/
│   │   ├── index.html         # 学習UI（3パネルレイアウト）
│   │   ├── admin.html         # 管理UI
│   │   ├── css/styles.css     # デザインシステム
│   │   └── js/
│   │       ├── app.js         # 学習SPA（ES6+）
│   │       └── admin.js       # 管理SPA（ES5互換）
│   ├── nginx.conf             # リバースプロキシ設定
│   └── Dockerfile
├── backend/
│   ├── api/
│   │   ├── main.py            # FastAPI アプリ本体・起動時マイグレーション
│   │   ├── dependencies.py    # 認証依存関係
│   │   ├── schemas.py         # API固有 Pydantic モデル
│   │   ├── services.py        # ビジネスロジック共通関数
│   │   └── routes/
│   │       ├── auth.py        # /api/auth/*
│   │       ├── learning.py    # /api/learning/*（チャット・tension・voice など）
│   │       ├── admin.py       # /api/admin/*
│   │       ├── lecture.py     # /api/learning/lecture/*
│   │       ├── lecture_studio/      # /api/admin/...（Lecture Studio。_shared/scripts/pipeline/topics に分割）
│   │       ├── theory_components.py # /api/admin/...（理論コンポーネント・C層承認共有）
│   │       ├── cartridges.py        # /api/admin/cartridges/*
│   │       ├── revisions.py         # /api/admin/documents/{id}/revisions/*
│   │       ├── groups.py            # /api/groups/*, /api/me/*
│   │       ├── error_logs.py        # /api/admin/error-logs
│   │       ├── export.py            # /api/courses|documents/{id}/export-bundle
│   │       └── export_artifacts.py  # export のヘルパー
│   ├── core/
│   │   ├── schema.py          # 全 Pydantic モデル（OntologyType, CorePredicate など）
│   │   ├── schema_registry.py # 動的スキーマ（DBから読み込み・キャッシュ）
│   │   ├── models.py          # SQLAlchemy ORM モデル
│   │   ├── postgres.py        # PostgreSQL セッション管理
│   │   ├── extractor.py       # PDF → 構造化データ抽出
│   │   ├── embedder.py        # pgvector ベクトル保存・検索
│   │   ├── chat.py            # RAG チャットロジック
│   │   ├── lecture.py         # レクチャーシーケンス生成・TTS補助
│   │   ├── tts.py             # TTS（OpenAI / Google Cloud）・読み上げテキスト整形
│   │   ├── llm.py             # LLM アダプタ（OpenAI / Gemini 切替、Whisper 文字起こし）
│   │   ├── storage.py         # MinIO S3互換ストレージ
│   │   ├── learning_experience.py   # 学習体験レイヤー（B層）共通ロジック
│   │   ├── learning_support_agent.py # 学習支援（寄り道・前提復習の構造化）
│   │   ├── personas.py        # ナレーション/応答ペルソナ
│   │   ├── theory_components.py     # 理論コンポーネント抽出
│   │   ├── component_candidates.py  # 質問→コンポーネント候補生成（C層）
│   │   ├── course_content_builder.py # パイプライン成果物からコース内容生成
│   │   ├── concept_normalizer.py    # 概念・記号の正規化
│   │   ├── document_sections.py     # セクション構造復元
│   │   ├── cartridges.py      # カートリッジのロード
│   │   ├── batch.py           # 構造的同型性評価バッチ
│   │   ├── meta_analyzer.py   # 未回答クエリ → スキーマ拡張提案
│   │   ├── simulator.py       # スキーマ提案の Shadow Testing
│   │   ├── reextractor.py     # スキーマ更新後の再抽出ワークフロー
│   │   ├── harvester.py       # arXiv API 連携
│   │   ├── isom.py            # .isom DSL シリアライズ
│   │   ├── config.py          # 設定管理
│   │   ├── document_pipeline/ # Agent パイプライン オーケストレータ（revision/ 含む）
│   │   ├── graphs/            # 学生向け/教員向けグラフ組み立て
│   │   └── tension/           # TensionMiningAgent（B層: prefilter/agent/worker …）
│   ├── cartridges/            # ドメインカートリッジ（particle_physics）
│   ├── db/                    # SQLマイグレーション（init.sql, 002〜067）
│   └── tests/                 # pytest テスト（FastAPI / core）
├── src/
│   ├── episteme_graph/agents/ # PDF解析エージェント群（document_structure, paper_skeleton,
│   │                          #  claim_qualification, equation_semantics, derivation_chain,
│   │                          #  thesis_reconstruction, dsl_linking, component_assembly,
│   │                          #  component_graph, narrative_annotator, course_mapping …）
│   └── tests/                 # agents 用 pytest
├── docs/                      # 設計・動作解説ドキュメント
├── docker-compose.yml         # 本番 / CI 用ベース
├── docker-compose.local.yml   # ローカル開発用（postgres, ngrok, デバッグポート）
├── docker-compose.prod.yml    # 本番補助（ngrok トンネル）
└── .env.example
```

マイグレーションは `backend/db/`（init.sql 〜 067）。一覧と各テーブルの説明は
[docs/architecture/data-model.md](docs/architecture/data-model.md) を参照してください。

## API エンドポイント概要

全エンドポイントの一覧・権限は [docs/backend/api.md](docs/backend/api.md) を参照してください。主なグループ:

| グループ | プレフィックス | 内容 |
|---|---|---|
| 認証 | `/api/auth` | register / login / me |
| 学習 | `/api/learning` | コースCRUD・受講登録・進捗・RAGチャット・理解度チェック・問いの軌跡・tension ダイジェスト（confirm/dismiss/connect）・音声（transcribe/speak）・承認済み説明の閲覧 |
| レクチャー | `/api/learning/lecture` | 適応的シーケンス・TTS・中断チャット |
| 管理 | `/api/admin` | 教材管理・コースビルダー・コース公開/権限・ユーザー管理・スキーマ進化・タスク・関心ダッシュボード |
| Lecture Studio | `/api/admin`（`lecture_studio/` パッケージ） | スクリプト/音声のバッチ生成・ペルソナ設定・コース構造編集・ドキュメントパイプライン実行 |
| 理論コンポーネント / C層 | `/api/admin`（`theory_components.py`） | コンポーネントCRUD・component-graph・説明バージョン・承認（endorse）・引用・共有ダッシュボード |
| カートリッジ | `/api/admin/cartridges` | オントロジー・コンポーネント型・関係型などの参照 |
| リビジョン | `/api/admin/documents/{id}/revisions` | リビジョン作成・実行・レポート・承認/棄却 |
| グループ | `/api/groups`, `/api/me` | グループCRUD・招待コード・メンバー・招待の受諾 |
| エクスポート | `/api/courses|documents/{id}/export-bundle` | コース/ドキュメントのバンドル出力 |
| エラーログ | `/api/admin/error-logs` | 5xx エラーの記録参照 |

## 開発

```bash
# APIサーバーをコード変更後に再ビルド
docker compose up -d --build api-server

# ログ確認
docker compose logs -f api-server

# テスト実行（FastAPI / core, 全件）
cd backend && pytest backend/tests/

# テスト実行（単一ファイル）
cd backend && pytest backend/tests/test_diff_merge.py -v

# agents（PDF解析パイプライン）のテスト
pytest src/tests/
```

開発ルール（環境変数・Pydanticスキーマ・LLM呼び出し・フロントエンド規約など）は
[CLAUDE.md](CLAUDE.md) と [docs/](docs/README.md) を参照してください。

## ライセンス

Apache-2.0
