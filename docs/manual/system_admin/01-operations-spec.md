---
audience: system_admin
---

# 仕様編（運用・アーキテクチャ）

[← マニュアル目次](../README.md)

このページは episteme-graph の全体アーキテクチャと、ロール・権限の運用上の詳細を、
システム管理者（SYSTEM_ADMIN）の立場から解説します。学生・教員にも共通する全体像
（システム概要・データフロー・開示範囲・用語集）は先に
[仕様編（学生にも共通する全体像）](../student/01-specification.md) を参照してください。

より詳しい設計・実装解説は [docs/](../../README.md) にまとまっています。本ページはその要点を利用者視点で再構成したものです。

---

## 1. 全体アーキテクチャ {#architecture}

### 1.1 アーキテクチャ図 {#architecture-diagram}

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
        └──┬──────────┬──────────┬──────────────────────────────┘
           │          │          │
   ┌───────▼──┐ ┌─────▼────┐ ┌───▼─────────┐
   │PostgreSQL│ │ MinIO    │ │ LLM / TTS   │
   │+ pgvector│ │(S3互換   │ │ OpenAI /    │
   │ (正本)   │ │ 原本)    │ │ Gemini      │
   └──────────┘ └──────────┘ └─────────────┘
                              ┌─────────────┐
                              │ GROBID      │
                              │ (PDF解析)   │
                              └─────────────┘
```

外部に公開されるポートは `frontend`（nginx, 3000番）のみで、API サーバーや各データストアへは Docker 内部ネットワーク経由でのみ到達できます。

### 1.2 技術スタック {#tech-stack}

| レイヤー | 技術 |
|---|---|
| フロントエンド | Vanilla JS SPA + nginx（フレームワーク不使用） |
| API サーバー | FastAPI（Python 3.11） |
| RDB + ベクトル検索 | PostgreSQL 16 + pgvector（cosine 類似度、次元数は `LLM_EMBEDDING_DIM`、既定 3072） |
| オブジェクトストレージ | MinIO（S3 互換。PDF 原本・図画像を保存） |
| PDF 構造解析 | GROBID（TEI-XML）／利用不可時は PyMuPDF にフォールバック |
| LLM | OpenAI API または Google Gemini / Vertex AI（`LLM_PROVIDER` で切替） |
| TTS 音声合成 | OpenAI TTS（tts-1）または Google Cloud Text-to-Speech |
| 音声文字起こし | OpenAI Whisper 系（`LLM_TRANSCRIBE_MODEL`、ハンズフリー音声会話用） |
| 認証 | JWT（HS256）+ bcrypt |

知識の正本は PostgreSQL に一本化されており、MinIO は PDF 原本・図画像などバイナリの保存専用です。

アクセス先の一覧・環境変数の詳細は [4. 主要環境変数](04-system-admin.md#env-vars) を参照してください。

---

## 2. ロールと権限 {#roles-and-permissions}

システムは 3 段階のロールベースアクセス制御（RBAC）を採用しています。権限は上位ロールほど広く、**受講者（STUDENT）⊂ 教員（TEACHER）⊂ システム管理者（SYSTEM_ADMIN）** という累積構造になっています（上位ロールは下位ロールの権限をすべて含みます）。

| ロール | できること |
|---|---|
| 受講者（STUDENT） | 学習UIの利用、RAGチャット、コース受講登録 |
| 教員（TEACHER） | 受講者の権限に加えて、教材アップロード、コース作成・公開、グループ管理、理論コンポーネントの承認・共有（C層）、学生アカウント作成 |
| システム管理者（SYSTEM_ADMIN） | 教員の権限に加えて、教師アカウント作成を含む全権限 |

権限チェックは `_require_teacher()`（TEACHER 未満は 403）・`_require_system_admin()`（SYSTEM_ADMIN 以外は 403）という依存関数で実装されています。受講者アカウントは教員以上が管理UIから作成します。教員アカウントの作成はシステム管理者のみが行えます。初期のシステム管理者は起動時に環境変数 `ADMIN_PASSWORD` で作成されます。受講者が自分でアカウントを新規登録することはできません。

詳細は [認証・権限・開示範囲](../../features/auth-visibility.md) を参照してください。

---

[← マニュアル目次](../README.md)
