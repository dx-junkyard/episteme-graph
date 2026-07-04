# episteme-graph ドキュメント

> 大学院生の学習プロセスを支援する **知識グラフ管理システム** の設計・動作解説ドキュメント。
> ソースコードを読み解いて整理した、開発者・運用者向けの解説集です。

このディレクトリは「全体概要 → 観点別の詳細」という構成になっています。
まずこのページで全体像をつかみ、深掘りしたいテーマのリンクをたどってください。

---

## 1. このシステムは何をするのか

研究者・大学院生が直面する **「散在する先行研究の統合」** と **「前提知識の体系的習得」** を解決するために、
PDF 論文を投入するだけで知識をグラフ構造に変換し、それを土台に対話型・没入型の学習体験を提供します。

設計は次の 3 層で構成されています。

| 層 | やること | 関連ドキュメント |
|---|---|---|
| ① 知識の構造化 | PDF をアップロードすると、概念・主張・数式・関係性を自動抽出してナレッジグラフ／理論操作グラフを構築する | [パイプライン概要](pipeline/overview.md) |
| ② 適応的学習 | 習得状態を追跡し、前提知識に応じた問いかけ（RAG チャット）で理解を深める | [学習機能](features/learning.md) / [RAG チャット](backend/rag-chat.md) |
| ③ 没入型講義 | 論文チャンクを TTS 音声 + カラオケ式ハイライトのセミナー形式に変換する | [学習機能](features/learning.md) |

加えて、教員・管理者向けに **コースビルダー / Lecture Studio / 動的スキーマ進化** といった
コンテンツ生成・運用機能を備えています（[管理機能](features/admin.md)）。

---

## 2. 全体アーキテクチャ

```
┌────────────────────────────────────────────────────────────┐
│ ブラウザ                                                    │
│   index.html + app.js   … 学習UI（学生, ES6+ SPA）          │
│   admin.html + admin.js … 管理UI（教員/管理者, ES5互換 SPA） │
└───────────────┬────────────────────────────────────────────┘
                │ HTTP (REST/JSON, JWT)
        ┌───────▼────────┐  ← 外部公開はこの 3000 番ポートのみ
        │ frontend       │  nginx：静的配信 + /api リバースプロキシ
        │ (nginx :3000)  │
        └───────┬────────┘
                │ (Docker内部ネットワーク episteme)
        ┌───────▼─────────────────────────────────────────────┐
        │ api-server (FastAPI :8001)                           │
        │   backend/api/   … 認証・学習・管理エンドポイント     │
        │   backend/core/  … 抽出・埋め込み・RAG・講義・スキーマ │
        │   src/episteme_graph/agents/ … PDF解析Agent群        │
        └──┬──────────┬──────────┬──────────┬──────────────────┘
           │          │          │          │
   ┌───────▼──┐ ┌─────▼────┐ ┌───▼────┐ ┌───▼─────────┐
   │PostgreSQL│ │  Neo4j   │ │ MinIO  │ │ LLM / TTS   │
   │+ pgvector│ │(グラフ   │ │(S3互換 │ │ OpenAI /    │
   │ (正本)   │ │ 走査専用)│ │ 原本)  │ │ Gemini      │
   └──────────┘ └──────────┘ └────────┘ └─────────────┘
                                          ┌─────────────┐
                                          │ GROBID      │
                                          │ (PDF解析)   │
                                          └─────────────┘
```

各データストアの役割分担：

- **PostgreSQL + pgvector（正本）** — ユーザー・認証、教材/ドキュメントメタデータ、チャンク本文+埋め込みベクトル、学習者状態、コース、対話履歴、スキーマ進化、講義スクリプト、理論コンポーネント、リビジョン履歴
- **Neo4j（グラフ走査専用）** — 概念グラフ（REQUIRES / RELATES_TO / CONTAINS）、構造的同型パターンマッチ
- **MinIO（S3 互換）** — PDF 原本、抽出済み `PaperStructure` JSON など
- **GROBID** — PDF → TEI-XML の構造解析（利用不可時は PyMuPDF にフォールバック）
- **LLM / TTS** — OpenAI または Gemini（`LLM_PROVIDER` で切替）、TTS は OpenAI / Google

詳細は [アーキテクチャ概要](architecture/overview.md) と [デプロイ構成](architecture/deployment.md) を参照。

---

## 3. データの流れ（ハイレベル）

### 教材投入（教員）
```
PDF アップロード
  → MinIO 保存 → GROBID / PyMuPDF でテキスト化
  → PDF解析 Agent パイプライン（23 ステージ）で
     構造・主張・数式・導出・理論操作グラフを段階的に生成
  → チャンク+埋め込みを PostgreSQL(pgvector) へ
  → 概念グラフを Neo4j へ / 成果物 JSON を MinIO・PostgreSQL へ
  → コースビルダーでコース化 → 公開
```
→ [パイプライン概要](pipeline/overview.md) / [Agent 詳細](pipeline/agents.md)

### 学習（学生）
```
公開コースを受講登録（クローン）
  → トピック選択 → RAG チャットで質問
     （pgvector 検索 + PaperStructure + 履歴 → LLM 回答）
  → 誤解検出・前提知識チェック・学習進捗トラッキング
  → レクチャーモードで TTS 音声 + ハイライト講義
```
→ [学習機能](features/learning.md) / [RAG チャット](backend/rag-chat.md)

---

## 4. ドキュメント一覧

### アーキテクチャ / 基盤
- [アーキテクチャ概要](architecture/overview.md) — システム全体構成、ディレクトリ構成、データストア役割分担
- [デプロイ構成](architecture/deployment.md) — Docker Compose 構成、環境変数、ネットワーク設計
- [データモデル](architecture/data-model.md) — PostgreSQL テーブル設計、マイグレーション一覧（init → 022）

### バックエンド
- [API とルーティング](backend/api.md) — エンドポイント一覧、認証・RBAC・開示範囲
- [コアエンジン](backend/core-engine.md) — `backend/core/` 各モジュールの責務
- [RAG チャットフロー](backend/rag-chat.md) — 検索 → コンテキスト構築 → 生成 → 誤解検出、カジュアル対話モード、出所判定（content_grounding）、TensionMiningAgent（違和感候補検出）

### PDF 解析パイプライン
- [パイプライン概要](pipeline/overview.md) — PDF → ナレッジグラフの全 23 ステージ
- [PDF 解析 Agent 詳細](pipeline/agents.md) — 各 Agent の役割・入出力・LLM/決定論の区別
- [カートリッジシステム](pipeline/cartridges.md) — ドメイン固有語彙・検証ルールの注入
- [DSL と理論操作グラフ](pipeline/theory-graph.md) — SMILES 風 DSL、TheoryOperationGraph、ソースバッキング
- [動的スキーマ進化](pipeline/schema-evolution.md) — 未回答クエリ → 提案 → Shadow Testing → 再抽出

### 機能
- [学習機能（学生UI）](features/learning.md) — 3 パネル UI、RAG チャット、レクチャーモード、ハンズフリー音声会話（カジュアル対話モード）、違和感（tension）ダイジェスト、回答の出所表示（content_grounding）
- [管理機能（教員/管理者UI）](features/admin.md) — 教材管理、コースビルダー、Lecture Studio
- [承認・共有レイヤー（C層）](features/endorsement-sharing.md) — 教員による査読承認、独自解釈の並存、教員間共有、質問→候補生成
- [認証・権限・開示範囲](features/auth-visibility.md) — JWT、RBAC、グループ、Visibility

### フロントエンド
- [フロントエンド構成](frontend/overview.md) — SPA 構成、画面フロー、API 連携

---

## 5. クイックスタート（参考）

```bash
# 環境変数を設定
cp .env.example .env
#  LLM_API_KEY / ADMIN_PASSWORD / JWT_SECRET などを設定

# 全サービス起動（postgres はマネージド or local compose 併用）
docker compose up -d

# ログ確認
docker compose logs -f api-server

# テスト
cd backend && pytest backend/tests/
```

アクセス先・開発手順の詳細は [デプロイ構成](architecture/deployment.md) を参照してください。

> 注: 本ドキュメントはソースコード（`backend/`, `src/`, `frontend/`）を読み解いて整理したものです。
> 実装と差異を見つけた場合は、ソースを正とし本ドキュメントを更新してください。
