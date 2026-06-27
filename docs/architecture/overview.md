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
| グラフ DB | Neo4j 5（概念グラフ走査専用） |
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
| `neo4j` | 概念グラフ走査専用 | （内部のみ） |
| `minio` | PDF 原本・抽出構造の保存 | 9001（コンソール、開発時） |
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
│   │   └── js/{app.js, admin.js} # 学習SPA / 管理SPA
│   └── nginx.conf                # リバースプロキシ
│
├── backend/
│   ├── api/                      # FastAPI（→ backend/api.md）
│   │   ├── main.py               # アプリ本体・起動時マイグレーション
│   │   ├── dependencies.py       # 認証・RBAC 依存関係
│   │   ├── schemas.py            # API 固有 Pydantic モデル
│   │   ├── services.py           # 共通ビジネスロジック
│   │   └── routes/               # auth / learning / admin / lecture / lecture_studio / groups
│   ├── core/                     # コアエンジン（→ backend/core-engine.md）
│   │   ├── schema.py             # 全 Pydantic モデル（OntologyType, CorePredicate など）
│   │   ├── extractor.py / embedder.py / chat.py / lecture.py / tts.py
│   │   ├── llm.py / config.py / storage.py / postgres.py / db.py / models.py
│   │   ├── schema_registry.py / meta_analyzer.py / simulator.py / reextractor.py
│   │   ├── theory_components.py / isom.py / harvester.py / batch.py
│   │   └── document_pipeline/    # Agent パイプライン オーケストレータ
│   ├── cartridges/               # ドメインカートリッジ（particle_physics）
│   ├── db/                       # SQL マイグレーション（init.sql, 002〜019）
│   └── tests/                    # pytest（FastAPI / core）
│
├── src/episteme_graph/agents/    # PDF解析 Agent 群（→ pipeline/agents.md）
├── src/tests/agents/             # Agent 用 pytest
├── docker-compose.yml            # 本番 / CI 用
├── docker-compose.local.yml      # 開発用（postgres, ngrok 等を追加）
└── .env.example
```

---

## 4. データストアの役割分担

知識の **正本は PostgreSQL** に置き、Neo4j はグラフ走査専用、MinIO はバイナリ/大きな JSON 専用、という明確な分離がこのシステムの基本方針です。

### PostgreSQL + pgvector（正本）
- ユーザー・認証・セッション
- 教材（`documents`）メタデータ、テキストチャンク本文 + 埋め込みベクトル（`chunks.embedding`）
- 学習者状態（習得・つまずき・誤解概念）、コース（`learning_courses`）、対話履歴
- コースビルダーセッション、講義スクリプト/音声キャッシュ
- スキーマ進化（`schema_*`, `schema_proposals`, `reextraction_jobs`）
- 理論コンポーネント・理論操作グラフ（`theory_*`）
- Agent パイプライン実行履歴・リビジョン（`document_analysis_runs`）

詳細なテーブル一覧は [データモデル](data-model.md) を参照。

### Neo4j（グラフ走査専用）
- 概念ノードとエッジ（`REQUIRES` / `RELATES_TO` / `CONTAINS`）
- チャンク↔概念のクロスリンク
- 構造的同型パターンとのマッチ（`MATCHES_PATTERN`）、システムメタ提案

### MinIO（S3 互換オブジェクトストレージ）
- `raw-papers` — PDF 原本
- `raw-texts` — フォールバックで抽出した素のテキスト
- `extracted-structures` — 抽出済み `PaperStructure` JSON（`{paper_id}.json`）

### GROBID / LLM / TTS（外部・補助）
- GROBID: PDF → TEI-XML（落ちていても PyMuPDF で継続）
- LLM: 抽出・RAG・コース生成・講義スクリプト生成（OpenAI / Gemini / Vertex AI）
- TTS: 講義音声生成（OpenAI tts-1 / Google Cloud TTS）

---

## 5. 主要なサブシステム

| サブシステム | 概要 | 詳細 |
|---|---|---|
| PDF 解析パイプライン | アップロードされた PDF を 23 ステージの Agent 群で構造化 | [pipeline/overview.md](../pipeline/overview.md) |
| RAG チャット | pgvector 検索 + PaperStructure でコンテキストを組み、LLM が回答 | [backend/rag-chat.md](../backend/rag-chat.md) |
| 動的スキーマ進化 | 未回答クエリから新しい OntologyType/CorePredicate を提案・検証・反映 | [pipeline/schema-evolution.md](../pipeline/schema-evolution.md) |
| 理論操作グラフ | 導出チェーンから理論の操作構造を 2 層グラフで表現 | [pipeline/theory-graph.md](../pipeline/theory-graph.md) |
| 学習・講義 | 適応的 RAG 学習 + TTS インタラクティブ講義 | [features/learning.md](../features/learning.md) |
| 認証・開示範囲 | JWT + RBAC（STUDENT/TEACHER/SYSTEM_ADMIN）+ グループ + Visibility | [features/auth-visibility.md](../features/auth-visibility.md) |

---

[← ドキュメント目次](../README.md) ｜ 次へ: [デプロイ構成 →](deployment.md)
