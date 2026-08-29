# episteme-graph ドキュメント

> 大学院生の学習プロセスを支援する **知識グラフ管理システム** の設計・動作解説ドキュメント。
> ソースコードを読み解いて整理した、開発者・運用者向けの解説集です。

> **利用者向けマニュアル:** 立場別（受講者 / 教員 / システム管理者）の使い方と仕様の
> まとめは [manual/](manual/README.md) を参照してください。以下は開発者・運用者向けの
> 設計・動作解説です。

このディレクトリは「**ビジョン → 全体像 → 機能群別の詳細**」という構成になっています。

---

## 0. まず読むもの

| 目的 | ドキュメント |
|---|---|
| このシステムの**思想・設計原則**を知りたい | [ビジョンと思想（正本）](vision.md) — ミッション / 知識観・学習観 / AIの役割 / 横断14原則 / 機能群マップ |
| **なぜこのシステムが必要か**を知りたい（外部ステークホルダー向け） | [サービスデザイン](service_design.md) — 現状認識 → 課題 → gap → UX → 機能への対応表 |
| **どの層が何の機能で、どの migration か**を知りたい | [レイヤー索引表](architecture/layer_registry.md) |
| 開発ルール・各層の実装規約を知りたい | [CLAUDE.md](../CLAUDE.md)（リポジトリルート） |

---

## 1. このシステムは何をするのか

研究者・大学院生が直面する **「散在する先行研究の統合」** と **「前提知識の体系的習得」** を
解決するために、PDF 論文を投入するだけで知識をグラフ構造に変換し、それを土台に
対話型・**産出型**の学習体験を提供します。

利用者から見た大枠は次の 7 つの機能群です（思想との対応は [vision.md](vision.md) §8）。

| 群 | やること | 主なキーワード |
|---|---|---|
| 1. 知の構造化 | PDF を概念・主張・数式・導出・理論操作グラフに自動構造化 | A層パイプライン / L層（図・装置）/ カートリッジ |
| 2. 学びの対話と講義 | 構造の上での RAG チャット・論文との議論・音声講義 | discuss / casual音声 / レクチャー |
| 3. 理解の産出と痕跡 | 予測・再構成・違和感・問いを本人の痕跡として育てる | R層 / 理解サイクル / tension / わたしの地図 |
| 4. 地図と位置づけ | 分野の中の「いまここ」と論文の位置づけ | Field Atlas / ランドスケープ / カテゴリギャップ |
| 5. 疑いと検証 | 合意と検証を分離した認識的地位の台帳 | D層 / SL層（賭け金の台帳） |
| 6. 教員の検討と共同体 | 説明の並存・承認・要素検討・共通部品化 | C層 / W層 / ライブラリ / 図スタジオ |
| 7. 運営基盤 | 権限・版管理・通知・ガイダンス・AI運用 | V層 / G層 / Copilot / U層 / M層 / help_kb |

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
        │   backend/core/  … 抽出・埋め込み・RAG・講義・各層コア │
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

各データストアの役割分担：

- **PostgreSQL + pgvector（正本）** — ユーザー・認証、教材/ドキュメントメタデータ、チャンク本文+埋め込みベクトル、学習者状態・関心痕跡、コース、対話履歴、理論コンポーネント/claim/操作グラフ、各層のテーブル群（承認・疑義・版管理・通知・台帳 …）
- **MinIO（S3 互換）** — PDF 原本、図画像（抽出図・教材図）など
- **GROBID** — PDF → TEI-XML の構造解析（利用不可時は PyMuPDF にフォールバック）
- **LLM / TTS** — OpenAI または Gemini（`LLM_PROVIDER` で切替）、TTS は OpenAI / Google。モデルの場面別選択は M層（`core/llm_policy.py`）が単一正本

> 旧 Neo4j は書き込み経路がなく実質未使用だったため 2026-07 に撤去済み。
> マイグレーションは `backend/db/*.sql`（init + 002〜067、毎起動・番号順・冪等再実行）が唯一の正本。

詳細は [アーキテクチャ概要](architecture/overview.md) と [デプロイ構成](architecture/deployment.md) を参照。

---

## 3. データの流れ（ハイレベル）

### 教材投入（教員）
```
PDF アップロード
  → MinIO 保存 → GROBID / PyMuPDF でテキスト化
  → PDF解析 Agent パイプライン（named 29 ステージ）で
     構造・主張・数式・導出・理論操作グラフ・文脈説明・discuss開幕素材・
     ランドスケープ配置を段階的に生成
  → チャンク+埋め込みを PostgreSQL(pgvector) へ
  → 理論コンポーネント・操作グラフを PostgreSQL へ / 図画像を MinIO へ
  → コースビルダーでコース化 → 地図バインディング → リリース前確認 → 公開
```
→ [パイプライン概要](pipeline/overview.md) / [Agent 詳細](pipeline/agents.md)

### 学習（学生）
```
公開コースを受講登録（learning_states に1行。コース本体は複製しない）
  → トピック選択 → RAG チャット / discuss（論文と議論）/ レクチャー受講
  → 予測・再構成・違和感・問いが本人確定の痕跡として蓄積
  → 「わたしの地図」・分野の地図で自分の位置と旅を確認
```
→ [学習機能](features/learning.md) / [RAG チャット](backend/rag-chat.md)

---

## 4. ドキュメント一覧

### アーキテクチャ / 基盤

- [ビジョンと思想（正本）](vision.md) — ミッション・認識論・横断設計原則・機能群マップ
- [アーキテクチャ概要](architecture/overview.md) — システム全体構成、ディレクトリ構成
- [デプロイ構成](architecture/deployment.md) — Docker Compose、環境変数、ネットワーク設計
- [データモデル](architecture/data-model.md) — PostgreSQL テーブル設計、マイグレーション一覧
- [レイヤー索引表](architecture/layer_registry.md) — 全レイヤーの正本設計書/実装場所/migration 対応
- [candidate → confirm 共通プリミティブ](features/candidate_flow_design.md) — 候補→確定ワークフローの共通制御フロー（`core/candidate_flow.py`）
- [段階ラベル辞書の正本](features/label_vocab_design.md) — 生値→段階ラベルの境界と共有語彙表（`core/label_vocab.py`）
- [新機能 PR チェックリスト](development_checklist.md) — docs 3点セット・境界回帰テスト・アンカー整合

### バックエンド

- [API とルーティング](backend/api.md) — エンドポイント一覧、認証・RBAC・開示範囲
- [コアエンジン](backend/core-engine.md) — `backend/core/` 各モジュールの責務
- [RAG チャットフロー](backend/rag-chat.md) — 検索 → 生成 → 誤解検出、出所判定、casual、tension

### 群1: 知の構造化（A層・L層・カートリッジ）

- [パイプライン概要](pipeline/overview.md) — PDF → ナレッジグラフの全ステージ
- [論文の抽出単位](pipeline/extraction-units.md) — ブロック → チャンク／evidence → atomic claim → 理論部品
- [PDF 解析 Agent 詳細](pipeline/agents.md) — 各 Agent の役割・入出力・LLM/決定論の区別
- [カートリッジシステム](pipeline/cartridges.md) — ドメイン固有語彙・検証ルールの注入
- [DSL と理論操作グラフ](pipeline/theory-graph.md) — SMILES 風 DSL、TheoryOperationGraph、ソースバッキング
- [動的スキーマ進化](pipeline/schema-evolution.md) — 未回答クエリ → 提案 → Shadow Testing → 再抽出
- [画像パイプライン + ナレッジライブラリ（L層）](features/image_pipeline_knowledge_library_design.md)
- [図解析の反証型反復検証（#499）](features/contextual_figure_analysis_iterative_verification.md)
- [教員指示付き図再解析](features/guided_figure_reanalysis_design.md)
- [図⇄概念構造の接続](features/figure_concept_linking_design.md)

### 群2: 学びの対話と講義

- [学習機能（学生UI）](features/learning.md) — 3パネルUI、RAGチャット、音声会話、レクチャー
- [「論文と話す」discuss モード](features/discussion_mode_design.md)
  ／[対話の歩調合わせ](features/discuss_dialogue_alignment_design.md)
  ／[開幕素材のオーサリング](features/discuss_opening_authoring_design.md)
  ／[観測基盤](features/discuss_observation_design.md)
  ／[コーパス回遊層（コース無し議論・コーパス地図・地図の端）](features/corpus_roaming_design.md)
- [レクチャースライド同期 + 音声言語切替](features/lecture_slide_sync_design.md)
  ／[音声生成の準備確認フロー（#491）](features/lecture_audio_generation_readiness.md)
- [学習画面UI再編 + インスペクト/ホバー係留](features/learning_ui_inspect_hover_design.md)

### 群3: 理解の産出と痕跡

- [再構成ループ（R層）](features/reconstruction_loop_design.md)
- [理解サイクル（UCサイクル）](features/understanding_cycle_design.md) — OPEN→ELICIT→…→REVISIT
- [構造帰属型の問い記録（B層）](features/structure-anchored-questions.md)
- [個人知識ネットワーク（わたしの地図・旅）](features/personal_knowledge_network_design.md)
  ／[外部レビュー](features/personal_knowledge_network_review.md)
  ／[いまここの周り（近傍関係ビュー）](features/personal_map_nearby_design.md)
  ／[広がり装置（名前のある霧ほか）](features/personal_map_curiosity_design.md)
- [component 根拠カードのチップ化 + 文脈API](features/component_evidence_redesign.md)
- [学習者向け要素文脈API](features/learner_element_context_design.md)
  ／[要素文脈の提示再設計](features/element_context_presentation_redesign.md)
- [数式ホバー内容](features/equation_hover_content_design.md)
  ／[数式文脈パネル表示](features/equation_context_panel_display_design.md)
- [痕跡kind登録簿と主権台帳v1「わたしの記録」](features/trace_registry_sovereignty_ledger_design.md) — パーソナライズ実装計画 Phase 1
- [帰還の扉（帰還の三段 v1）](features/return_door_design.md) — 同 Phase 2
- [構造の降下路（足場ダイヤル・楽屋 v1）](features/structure_descent_design.md) — 同 Phase 3

### 群4: 地図と位置づけ

- 分野の地図（Field Atlas）: [骨格](features/field_atlas_skeleton.md) /
  [バインディング](features/field_atlas_binding.md) /
  [修正報告](features/field_atlas_correction_reports.md) /
  [DB 管理化](features/field_atlas_db_managed_skeleton.md) /
  [詳細パネル](features/field_atlas_detail_panel.md) /
  [骨格エディタ強化](features/field_atlas_skeleton_editor_upgrade.md) /
  [バインディング該当なしUX + ドメインライフサイクル](features/atlas_binding_lifecycle_design.md)
- [知識ランドスケープ（配置層）](features/knowledge_landscape_design.md)
- [カテゴリギャップ候補（地図を論文から育てる）](features/category_gap_candidates_design.md)
- [分野マップのベクトル係留層（VA層 — アンカー埋め込み・別名レジストリ・着地予測）](features/atlas_vector_anchoring_design.md)
- [分野マップの関係表示（辺候補レビューと推定の糸）](features/atlas_relation_edges_design.md)
  ・親: [表示原則の討議記録](architecture/field_map_display_principles_2026-08-29.md)
- [リリース前の確認フロー](features/release_review_flow_design.md)

### 群5: 疑いと検証

- [疑義・認識的地位台帳（D層）](features/doubt_layer_issues.md)
- [賭け金の台帳（SL層）](features/stakes_ledger_design.md) — 反証条件・観測反実仮想・独立支持経路・晴れ間

### 群6: 教員の検討と共同体

- [承認・共有レイヤー（C層）](features/endorsement-sharing.md)
- [要素検討ワークスペース（W層）](features/element_deliberation_workspace_design.md)
  ／[外部レビュー](features/element_deliberation_workspace_review.md)
  ／[要素中心コンテキストレンズ（#498）](features/element_context_lens_design.md)
  ／[要素インベントリ](features/element_inventory_design.md)
  ／[グラフ対話レビュー（教材起点のグラフ確認・承認画面）](features/graph_dialogue_review_design.md)
- [二層説明（generic/contextual）+ 図のコース流通](features/hierarchical_context_explanation_design.md)
- [教材図スタジオ（AI対話SVG生成）](features/teaching_figure_studio_design.md)
- [管理機能（教員/管理者UI）](features/admin.md)
- [宣言された弁と静かな計器（教員支援 v1）](features/teacher_triage_instruments_design.md) — パーソナライズ実装計画 Phase 4
- [ゼミ前ブリーフと鏡面化](features/seminar_brief_mirroring_design.md) — 同 Phase 5

### 群7: 運営基盤

- [認証・権限・開示範囲](features/auth-visibility.md)
  ／[アカウントライフサイクル管理（一覧・停止・削除・リセット・利用実績照会）](features/account_lifecycle_management_design.md)
  ／[オブジェクトスコープ権限是正（Security Phase 3）指示書](features/security_and_context_phase3_implementation_directive.md)
  ・[完了報告](features/security_and_context_phase3_completion_report.md)
- [URL指定による教材取得（取得先ドメイン許可リスト + SSRF ガード）](features/url_material_upload_design.md)
- [論文ディスカバリー層（arXiv 分野購読とコーパス成長ループ）](features/paper_discovery_design.md)
  ／[論文レーダー（教材起点の類似論文探索と比較分析）](features/paper_radar_design.md)

- [共有物のバージョン管理（V層）](features/shared_versioning_design.md)
- [状態管理・通知基盤](features/status_notification_design.md)
- [ガイダンス層（G層）](features/guidance_layer_design.md)
- [Admin Copilot（統合AIアシスタント）](features/admin_assistant_design.md)
  ／[チャット型AI支援の共通基盤](features/assistant_common_infra_design.md)
- [利用者マニュアル KB（help_kb）](features/manual_help_kb_design.md)
- [LLM トークン使用量推計（U層）](features/llm_usage_metering_design.md)
- [場面別 LLM モデル選択（M層）](features/llm_model_selection_design.md)

### ビジョン文書・将来構想

- [知識ネットワークビジョン（KN-1〜4、W層/個人知識ネットワークの親文書）](features/knowledge_network_vision.md)
- [ビジョン拡張提案（7分野専門家パネル討論, 2026-08）](features/vision_expansion_proposals_2026-08.md)
- [ビジョン拡張・UX再設計提案書（理解サイクルの原案）](features/vision_expansion_ux_proposal_revised_2026-08-13.md)
- [段階的翻訳レイヤー（E層・**未実装**）](features/exposition_layer_design.md)

### 調査・レビュー記録（完了済みのスナップショット）

いずれも実施当時の記録であり、指摘の多くは解消済み。「やることリスト」として読まないこと。

- [アーキテクチャ整理調査 2026-07（提案1〜20 全実施済み）](architecture/consolidation_survey_2026-07.md)
- [ビジョン×UXギャップ調査 2026-07-16（22/25 修正済み）](architecture/vision_ux_gap_survey_2026-07.md)
  ／[再調査 2026-07-17（🔧/💬 全件解消済み）](architecture/vision_ux_gap_survey_2026-07-17.md)
- [対ユーザー支援エージェント調査（推奨1〜6 実施済み）](architecture/user_assistant_agents_survey_2026-07.md)
- [管理画面UX課題 2026-08-01（全件実装済み）](architecture/admin_ux_issues_2026-08-01.md)
- [Issue #494 実装レビュー（指摘6件は現行コードで解消済み）](architecture/issue_494_implementation_review_2026-07-16.md)
- [ドキュメント総点検 2026-08-13（不具合報告）](architecture/doc_review_findings_2026-08-13.md)
  ／[機能整備提案](architecture/feature_consolidation_proposals_2026-08-13.md)

### フロントエンド

- [フロントエンド構成](frontend/overview.md) — SPA 構成、画面フロー、API 連携
  （注: JSモジュール一覧は 2026-07 時点の記載。現状は31ファイル — 更新候補）

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
