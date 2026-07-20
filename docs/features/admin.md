# 管理機能（教員/管理者UI）

[← ドキュメント目次](../README.md)

教員・管理者向けの管理 UI を機能別に解説します。
実装: `frontend/public/admin.html` + `frontend/public/js/admin.js`（ES5 互換 SPA）+ 分離モジュール
（`admin-lecture-studio.js` / `admin-assistant.js` / `admin-next-steps.js` / `versioning.js` /
`deliberation.js` / `doubt-atlas.js` / `admin-llm-usage.js` / `atlas-draft-preview.js` /
`atlas-assist-panel.js`。モジュール一覧と DI 注入は [フロントエンド構成](../frontend/overview.md)）。
バックエンドは主に `/api/admin/*`（[API](../backend/api.md)）。

## タブ構成

タブはグループ別プルダウン（`admin.html` の `.admin-tab-group`）に束ねられている。
「動的」はログイン後に `admin.js` の `setupRoleBasedUI()` がロールに応じて DOM 追加するタブ。

| グループ | タブ（`data-tab`） | 表示条件 | 中身 |
|---|---|---|---|
| コンテンツ | 教材管理（`materials`） | TEACHER のみ※ | PDF/TeX アップロード・一覧・共有・要素インベントリ（深く検討） |
| コンテンツ | コース構築（`course-builder`） | TEACHER のみ※ | AI 支援コースビルダー |
| コンテンツ | コース管理（`course-management`） | TEACHER のみ※ | 公開・グループ共有・学習マップ編集・共有版 |
| コンテンツ | 原稿スタジオ（`lecture-studio`） | TEACHER のみ※ | レクチャー原稿・音声の事前構築（`admin-lecture-studio.js`） |
| 学習インサイト | つまづきデータ（`stumbles`） | 常時 | 未回答クエリ・つまずきログ |
| 学習インサイト | 関心集約（`interest-dashboard`） | 常時 | 関心・tension の k-匿名化集計 |
| 学習インサイト | 分野の地図（`atlas`） | 常時 | Field Atlas 骨格の生成・レビュー・凍結・修正報告 |
| 学習インサイト | 前提の地図（`doubt-atlas`） | 常時 | D層 Assumption Atlas（`doubt-atlas.js` が描画。分野の地図とは別機能） |
| ナレッジ基盤 | スキーマ提案（`schema-proposals`） | 常時 | 動的スキーマ進化（提案・Shadow Testing・承認） |
| ナレッジ基盤 | ナレッジライブラリ（`knowledge-library`） | 常時 | L層 分野別ライブラリ（draft/凍結・retire） |
| ナレッジ基盤 | DSL進化分析（`schema`、動的） | TEACHER / SYSTEM_ADMIN | スキーマ進化の分析ビュー |
| 運用 | グループ管理（`groups`） | 常時 | グループ CRUD・招待 |
| 運用 | 学生管理（`students`、動的） | TEACHER / SYSTEM_ADMIN | 学生アカウント作成 |
| 運用 | 教員管理（`teachers`、動的） | SYSTEM_ADMIN のみ | 教員アカウント作成 |
| 運用 | システム統計（`system-stats`） | SYSTEM_ADMIN のみ | 教材統計 |
| 運用 | LLM使用量（`llm-usage`） | SYSTEM_ADMIN のみ | U層 使用量メトリクス（`admin-llm-usage.js` が描画） |
| 運用 | エラー解析（`error-analysis`） | SYSTEM_ADMIN のみ | 5xx エラーログの絞り込み・一括コピー |

※ ロール別の挙動: **STUDENT** は管理画面へアクセスすると学習画面（`/`）へリダイレクト。
**SYSTEM_ADMIN** はコンテンツ系4タブ（教材管理/コース構築/コース管理/原稿スタジオ）が
非表示になり、初期タブはエラー解析になる（コンテンツ管理は教員の画面という整理）。

タブのほかに、ヘッダへ常設の「📋 次にやること」（G層バッジ、`admin-next-steps.js`）と
「🔔 通知」（V層インボックス、`versioning.js`）、および Admin Copilot（`admin-assistant.js`）が載る。

---

## 1. 教材管理

| 機能 | API |
|---|---|
| PDF / TeX アーカイブのアップロード（非同期, 202 + task_id） | `POST /api/admin/materials/upload` |
| 教材一覧（自分 + 公開 + グループ共有 + コース参照） | `GET /api/admin/materials` |
| 教材詳細（抽出された知識グラフ構造） | `GET /api/admin/materials/{id}` |
| PDF 取得 / 差し替え | `GET/PUT /api/admin/materials/{id}/pdf` |
| 開示範囲変更 | `PUT /api/admin/materials/{id}/visibility` |
| 削除（チャンク等をカスケード） | `DELETE /api/admin/materials/{id}` |
| パイプライン実行 / 状態 | `POST /materials/{id}/document-pipeline/run`, `GET .../status`, `POST /documents/{id}/reanalyze` |

アップロード後は **PDF 解析パイプライン**が非同期で走ります（[パイプライン概要](../pipeline/overview.md)）。
進捗は `GET /api/admin/tasks/{task_id}` でポーリング。ステージ: `uploaded → document-analysis → script-generation → audio-generation`。

---

## 2. AI 支援コースビルダー

LLM との対話形式でコース構造（章・トピック・前提知識・到達目標）を設計します。

| 機能 | API |
|---|---|
| セッション作成 / 一覧 / 取得 / 更新 | `POST/GET /course-builder/sessions`, `GET/PUT /course-builder/sessions/{id}` |
| コース構築 AI チャット | `POST /course-builder/chat`（`session_id` で履歴永続化） |
| ドラフト形式取得 | `GET /courses/{id}/draft-format` |

- 対話履歴と `course_draft` は `course_builder_sessions`（PostgreSQL）に永続化され、ページリロード後も継続可能。
- 承認時に `is_template = TRUE` でコースを登録。
- `_COURSE_BUILDER_SYSTEM_PROMPT` が `topics[].prerequisites` に適切な値を入れるよう誘導（前提知識チェックの土台）。

---

## 3. コース管理・公開

| 機能 | API |
|---|---|
| 管理用コース一覧 | `GET /api/admin/courses` |
| 学生への公開（`visibility='public'` に更新。`is_published` も自動追従） | `PUT /api/admin/courses/{id}/visibility` |
| グループへの権限付与/剥奪（viewer/editor） | `GET/POST /courses/{id}/groups`, `DELETE /courses/{id}/groups/{gid}` |

公開（`is_published = TRUE`）されたテンプレートは学生の「受講可能なコース」に並び、受講登録は複製せず `learning_states` に1行 INSERT するだけです（コース本体は不変のまま共有）。

---

## 4. Lecture Studio

管理画面で最も複雑な機能。教員がレクチャー原稿・音声を事前構築・編集します。3 パネル構成。

| パネル | 内容 |
|---|---|
| 左（ナビ） | コースタブ（章→トピック）/ Document Structure タブ（DSL ツリー）/ Components タブ（理論操作グラフのノード） |
| 中央（エディタ） | ソース PDF と display text の分割表示、display script / spoken text の編集 |
| 右（メタ/操作） | チャンク DSL、構造グラフ要素、claim/theory/graph 抽出ボタン、保存・AI アシスタント |

主な API:
| 機能 | API |
|---|---|
| 設定（ナレーション/応答ペルソナ） | `GET/PUT /courses/{id}/lecture-studio/settings` |
| バッチスクリプト生成（非同期） | `POST /courses/{id}/lecture-scripts/generate` |
| スクリプト取得 / 手動保存 | `GET/PUT /courses/{id}/lecture-scripts/{chunk_id}` |
| AI スクリプト書き換え（トーン/難易度/長さ） | `POST .../lecture-scripts/{chunk_id}/rewrite` |
| バッチ音声生成（非同期） | `POST /courses/{id}/lecture-audio/generate` |
| 理論コンポーネント CRUD | `GET/POST/DELETE /courses/{id}/lecture-studio/components` |
| コース/文書構造 | `GET .../course-structure`, `GET .../document-structure` |

ペルソナは `personas.py` の 4 種（一般⇄専門 × フレンドリー⇄フォーマル）。
理論操作グラフの可視化は `source_backing_status` / `graph_layer` で表示を切り替えます（[理論操作グラフ](../pipeline/theory-graph.md#ui-表示adminjs)）。

---

## 5. 動的スキーマ進化（スキーマ提案タブ）

固定の OntologyType/CorePredicate を超えてドメイン固有の概念・関係を拡張します。

1. 未回答クエリをメタ分析 → スキーマ提案生成（`POST /schema-proposals/analyze`）
2. Shadow Testing で Before/After をプレビュー
3. 承認 / スコープ指定承認 / 棄却（`PUT /schema-proposals/{id}/approve | approve-with-scope | reject`）
4. 再抽出ジョブで既存グラフを再構築（`GET/POST /reextraction-jobs`）

詳細フロー → [動的スキーマ進化](../pipeline/schema-evolution.md)。

---

## 6. グループ・ユーザー管理

| 機能 | API |
|---|---|
| グループ作成/一覧/更新/削除、招待コード再発行 | `/api/groups*` |
| メンバー招待/参加（招待コード）、招待の承諾/辞退 | `/api/groups/{id}/members`, `/api/groups/join-by-code`, `/api/me/invitations*` |
| 学生アカウント作成（TEACHER+） | `POST /api/admin/users/student` |
| 教師アカウント作成（SYSTEM_ADMIN のみ） | `POST /api/admin/users/teacher` |

→ ロール・開示範囲の詳細は [認証・権限・開示範囲](auth-visibility.md)。

---

## 7. つまずき分析・エラー分析

- つまずきデータ: `GET /api/admin/courses/{id}/unanswered-queries`（`student_stumble_events` / `unanswered_query_logs`）。
- エラー分析: キーワード/重大度/期間でログを絞り込み、複数形式で一括コピー。
- システム統計: `GET /api/admin/system/materials-stats`（SYSTEM_ADMIN）。

---

[← 学習機能](learning.md) ｜ 次へ: [認証・権限・開示範囲 →](auth-visibility.md)
