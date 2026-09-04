# アーキテクチャ概要

[← ドキュメント目次に戻る](../README.md)

このページでは、システム全体の構成・コンポーネント・データストアの役割分担をまとめます。

---

## 1. 技術スタック

| レイヤー | 技術 |
|---|---|
| フロントエンド | Vanilla JS SPA + nginx（フレームワーク不使用） |
| API サーバー | FastAPI (Python 3.11) |
| RDB + ベクトル検索 | PostgreSQL 16 + pgvector（cosine, 次元数は `LLM_EMBEDDING_DIM`、既定 3072） |
| オブジェクトストレージ | MinIO（S3 互換） |
| PDF 構造解析 | GROBID（TEI-XML）／フォールバックで PyMuPDF |
| LLM | OpenAI API または Google Gemini / Vertex AI（`LLM_PROVIDER` で切替） |
| TTS 音声合成 | OpenAI TTS（tts-1）または Google Cloud Text-to-Speech |
| 認証 | JWT (HS256) + bcrypt |

---

## 2. コンポーネント構成

システムは Docker Compose で複数サービスとして起動します。
**外部に公開されるのは frontend（nginx, 3000 番）のみ**で、API・各 DB は Docker 内部ネットワーク `episteme` 経由でのみ到達できます。

| サービス | 役割 | 公開ポート |
|---|---|---|
| `frontend` | nginx。静的 SPA 配信 + `/api/*` を api-server へリバースプロキシ | 3000（唯一の外部公開） |
| `api-server` | FastAPI 本体。`backend/api` + `backend/core` + `src/episteme_graph/agents` | （内部のみ 8001） |
| `postgres` | PostgreSQL 16 + pgvector。正本データ | （内部のみ。本番はマネージド DB） |
| `minio` | PDF 原本・図画像の保存 | 9001（コンソール、開発時） |
| `grobid` | PDF → TEI-XML 解析 | 8070（開発時） |

> `postgres` サービスは本体 `docker-compose.yml` には含まれず、`docker-compose.local.yml`（`pgvector/pgvector:pg16`）で提供されるか、本番ではマネージド DB を `DB_HOST` で参照します。詳細は [デプロイ構成](deployment.md)。

---

## 3. リポジトリのディレクトリ構成

```
episteme-graph/
├── frontend/                     # SPA + nginx
│   ├── public/
│   │   ├── index.html            # 学習UI（3パネル）
│   │   ├── admin.html            # 管理UI
│   │   ├── css/styles.css        # デザインシステム
│   │   └── js/                   # app.js（学習SPA）/ admin.js（管理SPA）+ 機能別モジュール
│   │                             #   （atlas-*, personal-map-*, discuss, deliberation,
│   │                             #    admin-lecture-studio, admin-graph-review,
│   │                             #    admin-paper-discovery/radar, corpus-sea …）
│   │                             #   一覧の正は `ls frontend/public/js/`・読み込み順は各 html 末尾
│   └── nginx.conf                # リバースプロキシ（/api/* の location 群。正は nginx.conf）
│
├── backend/
│   ├── api/                      # FastAPI（→ backend/api.md）
│   │   ├── main.py               # アプリ本体・起動時マイグレーション
│   │   ├── dependencies.py       # 認証・RBAC 依存関係
│   │   ├── schemas.py            # API 固有 Pydantic モデル
│   │   ├── services.py           # 共通ビジネスロジック
│   │   ├── routes/               # auth / learning / admin / lecture / groups / deliberation /
│   │   │                         #   descent / corpus / paper_discovery / atlas_vectors /
│   │   │                         #   atlas_edges / seminar_brief / my_records ほか
│   │   │                         #   一覧の正は `ls backend/api/routes/` + lecture_studio/ パッケージ
│   │   └── ingest_worker.py      # 論文ディスカバリー取り込みキューの worker（lifespan 起動）
│   ├── core/                     # コアエンジン（→ backend/core-engine.md）
│   │   ├── schema.py             # 全 Pydantic モデル（OntologyType, CorePredicate など）
│   │   ├── extractor.py / embedder.py / chat.py / lecture.py / tts.py
│   │   ├── llm.py / config.py / storage.py / postgres.py / models.py / migrations.py
│   │   ├── schema_registry.py / meta_analyzer.py / simulator.py / reextractor.py
│   │   ├── theory_components.py / component_candidates.py / isom.py / harvester.py
│   │   ├── learning_experience.py / learning_support_agent.py / personas.py
│   │   ├── course_data.py / revision_store.py / privacy.py / llm_policy.py（横断基盤の正本）
│   │   ├── document_pipeline/    # Agent パイプライン オーケストレータ（revision/ を含む）
│   │   ├── graphs/               # 学生向けグラフ組み立て（student_graph）
│   │   ├── tension/ structure_anchor/ doubt/ reconstruction/ deliberation/ …（各層の実装）
│   │   ├── atlas_vectors/ atlas_edges/ atlas_gaps/ landscape/ paper_discovery/
│   │   ├── corpus_view.py descent/ cycle/ discuss/ graph_paper_layer/ personal_graph/
│   │   ├── account_lifecycle.py account_status.py auth_events.py url_fetch.py
│   │   └── ほか多数（一覧の正は `ls backend/core/`・層別の索引は layer_registry.md）
│   ├── cartridges/               # ドメインカートリッジ（particle_physics）
│   ├── atlas_domains/            # 骨格専用バンドルドメイン（astrophysics の skeleton.yaml）
│   ├── config/                   # M層モデルカタログ（llm_models.json）
│   ├── db/                       # SQL マイグレーション（init.sql + 番号順ファイル群。正は `ls backend/db/`）
│   └── tests/                    # pytest（FastAPI / core）
│
├── src/episteme_graph/agents/    # PDF解析 Agent 群（→ pipeline/agents.md）
├── src/tests/agents/             # Agent 用 pytest
├── docker-compose.yml            # 本番 / CI 用
├── docker-compose.local.yml      # 開発用（postgres 等を追加）
├── docker-compose.prod.yml       # 本番補助（ngrok 等）
├── docs/                         # 本ドキュメント群（イメージには admin_operations/ と manual/ のみ COPY）
└── .env.example
```

---

## 4. データストアの役割分担

知識の **正本は PostgreSQL** に置き、MinIO はバイナリ（PDF 原本・図画像）専用、という明確な分離がこのシステムの基本方針です
（旧 Neo4j はグラフ走査用に導入されていたが、書き込み経路がなく実質未使用だったため 2026-07 に撤去済み）。

### PostgreSQL + pgvector（正本）
- ユーザー・認証・セッション
- 教材（`documents`）メタデータ、テキストチャンク本文 + 埋め込みベクトル（`chunks.embedding`）
- 学習者状態（習得・つまずき・誤解概念）、コース（`learning_courses`）、対話履歴
- 学習者体験の関心痕跡（`interest_traces` — 質問・寄り道・誤答・違和感(tension)候補, B層）
- コースビルダーセッション、講義スクリプト/音声キャッシュ
- スキーマ進化（`schema_*`, `schema_proposals`, `reextraction_jobs`）
- 理論コンポーネント・理論操作グラフ（`theory_*`）
- 承認・共有レイヤー（`component_explanations` / `component_endorsements` / `component_citations`, C層）
- Agent パイプライン実行履歴・リビジョン（`document_analysis_runs`）

詳細なテーブル一覧は [データモデル](data-model.md) を参照。

### MinIO（S3 互換オブジェクトストレージ）
- `raw-papers` — PDF 原本
- `raw-texts` — フォールバックで抽出した素のテキスト
- `figure-images` — 図画像抽出パイプライン（L層）が抽出した図の画像（`document_figures`）に加え、
  教材図スタジオが生成した説明図 SVG の配信スナップショット（`teaching/{course_id}/{figure_id}.svg`。
  正本は `course_teaching_figures.svg_source`）も同じバケットに置かれる

### GROBID / LLM / TTS（外部・補助）
- GROBID: PDF → TEI-XML（落ちていても PyMuPDF で継続）
- LLM: 抽出・RAG・コース生成・講義スクリプト生成・tension 分類（OpenAI / Gemini / Vertex AI）
- 音声: TTS 音声生成（OpenAI tts-1 / Google Cloud TTS）と音声文字起こし（Whisper 系, `LLM_TRANSCRIBE_MODEL`）

---

## 5. 主要なサブシステム

> **層の索引の正本は [layer_registry.md](layer_registry.md)**（各層の記号・migration 帰属・
> 設計書へのリンク）と [vision.md](../vision.md)。以下はそこへの入口として1行ずつ要約したものです。

| サブシステム | 概要 | 詳細 |
|---|---|---|
| PDF 解析パイプライン | アップロードされた PDF を 29 ステージ（`_PIPELINE_STEPS` の named stage。`PIPELINE_STAGES` は終端マーカー `completed` を含め 30 エントリ）の Agent 群で構造化 | [pipeline/overview.md](../pipeline/overview.md) |
| RAG チャット | pgvector 検索（tier 付き）でコンテキストを組み、LLM が回答 | [backend/rag-chat.md](../backend/rag-chat.md) |
| 動的スキーマ進化 | 未回答クエリから新しい OntologyType/CorePredicate を提案・検証・反映 | [pipeline/schema-evolution.md](../pipeline/schema-evolution.md) |
| 理論操作グラフ | 導出チェーンから理論の操作構造を 2 層グラフで表現 | [pipeline/theory-graph.md](../pipeline/theory-graph.md) |
| 学習・講義 | 適応的 RAG 学習 + TTS インタラクティブ講義 + ハンズフリー音声会話 | [features/learning.md](../features/learning.md) |
| TensionMiningAgent | 対話ログから「理解した上での引っかかり（tension）」候補を検出し本人が確定 | [backend/rag-chat.md](../backend/rag-chat.md) |
| 承認・共有レイヤー（C層） | 教員による説明バージョン単位の査読承認と教員間共有 | [features/endorsement-sharing.md](../features/endorsement-sharing.md) |
| 認証・開示範囲 | JWT + RBAC（STUDENT/TEACHER/SYSTEM_ADMIN）+ グループ + Visibility | [features/auth-visibility.md](../features/auth-visibility.md) |
| 疑義レイヤー（D層） | 合意の強さと検証の強さを分離した認識的地位台帳・暗黙前提・疑義・反実仮想 | [features/doubt_layer_issues.md](../features/doubt_layer_issues.md) |
| 賭け金の台帳（SL層） | 反証条件レジストリ・観測の反実仮想・独立支持経路（D層の上に積む） | [features/stakes_ledger_design.md](../features/stakes_ledger_design.md) |
| 再構成ループ（R層） | 学習者に予測・言い直しをさせ、A層の claim を答えキーに構造照合する閉ループ | [features/reconstruction_loop_design.md](../features/reconstruction_loop_design.md) |
| 共有物のバージョン管理（V層） | 生成物・コースを不変の発行版にし、共有先を一方的更新・削除から保護 | [features/shared_versioning_design.md](../features/shared_versioning_design.md) |
| 要素検討ワークスペース（W層） | 任意の1要素の内訳・文脈4レンズ・AI 対話を束ね、解釈を候補として付与 | [features/element_deliberation_workspace_design.md](../features/element_deliberation_workspace_design.md) |
| 画像パイプライン + ナレッジライブラリ（L層） | PDF 内図画像の抽出・装置候補の vision 解析と、分野別の教員共同ライブラリ | [features/image_pipeline_knowledge_library_design.md](../features/image_pipeline_knowledge_library_design.md) |
| LLM 使用量推計（U層） | 全 LLM 呼び出しのトークン消費を実測・推計に分けて記録（append-only） | [features/llm_usage_metering_design.md](../features/llm_usage_metering_design.md) |
| ガイダンス層（G層） | 「次にやること」バッジ + サーバ状態から毎回導出する To-Do（完了フラグを持たない） | [features/guidance_layer_design.md](../features/guidance_layer_design.md) |
| 場面別 LLM モデル選択（M層） | 場面（scene）ごとに実モデル名を選択。解決の正本は `core/llm_policy.py` | [features/llm_model_selection_design.md](../features/llm_model_selection_design.md) |
| 分野の地図（Field Atlas） | 学習中の箇所が分野全体のどこかを示すオーバーレイ + 常設ミニマップ | [features/field_atlas_skeleton.md](../features/field_atlas_skeleton.md) |
| 知識ランドスケープ | 論文を分野の地図のアンカーへ複数観点で配置し、置けなかった主題を候補化 | [features/knowledge_landscape_design.md](../features/knowledge_landscape_design.md) |
| 個人知識ネットワーク | 本人の確定痕跡から毎回導出する個人の地図と「旅」（保存物を持たない） | [features/personal_knowledge_network_design.md](../features/personal_knowledge_network_design.md) |
| discuss（論文と話す） | コース順路をたどらず、ソース論文と最初から議論する係留付きモード | [features/discussion_mode_design.md](../features/discussion_mode_design.md) |
| 教材図スタジオ | わかりづらい箇所に AI 対話で説明図（SVG）を生成し `![[figure:id]]` で埋め込む | [features/teaching_figure_studio_design.md](../features/teaching_figure_studio_design.md) |
| Admin Copilot（横断ユーティリティ層） | 管理画面横断の AI アシスタント。capability registry で権限 fail-closed | [features/admin_assistant_design.md](../features/admin_assistant_design.md) |
| 状態管理・通知基盤 | 状態の読み取りモデル + 遷移検知 watcher + 統合通知インボックス | [features/status_notification_design.md](../features/status_notification_design.md) |
| 利用者マニュアル KB（help_kb） | docs/manual を AI アシスタントの知識源にする非ベクトル KB（+ ベクトル補助層） | [features/manual_help_kb_design.md](../features/manual_help_kb_design.md) |
| 分野マップのベクトル係留（VA層） | 骨格ノードのプロトタイプ埋め込み・別名レジストリ・配置プレフィルタ・着地予測 | [features/atlas_vector_anchoring_design.md](../features/atlas_vector_anchoring_design.md) |
| 分野マップの関係表示（RE追補） | 辺候補の教員レビューと学習者向け「推定の糸」（点線・既定オフ） | [features/atlas_relation_edges_design.md](../features/atlas_relation_edges_design.md) |
| グラフ対話レビュー / 論文層 | 教材行から開くグラフ起点の承認画面と、フレームに論文を肉付けする読み時射影 | [features/graph_dialogue_review_design.md](../features/graph_dialogue_review_design.md) ／[features/graph_paper_layer_design.md](../features/graph_paper_layer_design.md) |
| 論文ディスカバリー / レーダー | arXiv 分野購読と教材起点の類似論文探索。発見は自動・取り込みは教員の明示承認のみ | [features/paper_discovery_design.md](../features/paper_discovery_design.md) ／[features/paper_radar_design.md](../features/paper_radar_design.md) |
| コーパス回遊層 | コースの外から論文の海（コーパス地図）を歩き、コース無しで論文と議論する | [features/corpus_roaming_design.md](../features/corpus_roaming_design.md) |
| URL指定による教材取得 | 許可リスト + SSRF ガード付きダウンローダで既存アップロード経路へ合流 | [features/url_material_upload_design.md](../features/url_material_upload_design.md) |
| アカウントライフサイクル管理 | 一覧・停止/再開・パスワードリセット・削除（移管→墓標化→purge）・認証イベント台帳 | [features/account_lifecycle_management_design.md](../features/account_lifecycle_management_design.md) |
| 主権台帳（わたしの記録） | `interest_traces` kind 登録簿と、本人だけが読める痕跡の一覧・持ち出し | [features/trace_registry_sovereignty_ledger_design.md](../features/trace_registry_sovereignty_ledger_design.md) |
| 制度指標カタログ | 集約計器の定義・目的・宛先・粒度・非利用を宣言し、値の宛先は変えずに定義だけを全当事者へ公開 | [features/indicator_governance_design.md](../features/indicator_governance_design.md) |
| 確定文脈の記帳 | 一括確定に「何が提示され・何が選べ・どこから再審できるか」を必須記帳（新テーブルなし） | [features/decision_context_design.md](../features/decision_context_design.md) |

---

[← ドキュメント目次](../README.md) ｜ 次へ: [デプロイ構成 →](deployment.md)
