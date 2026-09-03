# 管理機能（教員/管理者UI）

[← ドキュメント目次](../README.md)

教員・管理者向けの管理 UI を機能別に解説します。
実装: `frontend/public/admin.html` + `frontend/public/js/admin.js`（ES5 互換 SPA）+ 分離モジュール
（`admin-lecture-studio.js` / `admin-figure-studio.js` / `admin-assistant.js` /
`admin-help-inspect.js` / `admin-next-steps.js` / `versioning.js` / `deliberation.js` /
`admin-graph-review.js` / `admin-voice-chat.js` / `doubt-atlas.js` / `admin-llm-usage.js` /
`admin-llm-models.js` / `admin-manual-editor.js` / `admin-discuss-observation.js` /
`admin-release-review.js` / `admin-paper-discovery.js` / `admin-paper-radar.js` /
`atlas-draft-preview.js` / `atlas-assist-panel.js`。**読み込み順の正は `admin.html` 末尾の
`<script>` 群**。モジュール一覧と DI 注入は [フロントエンド構成](../frontend/overview.md)）。
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
| 運用 | マニュアル編集（`manual-editor`） | SYSTEM_ADMIN のみ | 利用者マニュアル KB の draft / 凍結（`admin-manual-editor.js`。配信既定は files のまま、freeze 実行後に DB 配信へ） |
| 運用 | discuss 観測（`discuss-observation`） | SYSTEM_ADMIN のみ | discuss 観測基盤の蓄積状況・分析ダンプ（`admin-discuss-observation.js`） |
| 運用 | AIモデル（`llm-models`） | SYSTEM_ADMIN のみ | M層 場面別モデルのシステム既定（`admin-llm-models.js`）＋ **URL取得の許可ドメイン**区画（教材管理タブが SYSTEM_ADMIN では非表示のため、この末尾に動的生成） |

※ ロール別の挙動: **STUDENT** は管理画面へアクセスすると学習画面（`/`）へリダイレクト。
**SYSTEM_ADMIN** はコンテンツ系4タブ（教材管理/コース構築/コース管理/原稿スタジオ）が
非表示になり、初期タブはエラー解析になる（コンテンツ管理は教員の画面という整理）。
「学生管理」「教員管理」の各パネルには**アカウントライフサイクル**の区画（一覧・停止/再開・
パスワードリセット・削除予約・移管）が同居する（§6）。

タブのほかに、ヘッダへ常設の「📋 次にやること」（G層バッジ、`admin-next-steps.js`）と
「🔔 通知」（V層インボックス、`versioning.js`）、「❓ 使い方」（管理画面のインスペクト・モード。
ON の間だけ `[data-ui-anchor]` を持つ部品のツールチップにマニュアル節を出す。
`admin-help-inspect.js` + `GET /api/admin/assistant/help/ui-anchors`）、および
Admin Copilot（`admin-assistant.js`）が載る。

---

## 1. 教材管理

| 機能 | API |
|---|---|
| PDF / TeX アーカイブのアップロード（非同期, 202 + task_id） | `POST /api/admin/materials/upload` |
| **URL から取得**（許可ドメインのみ。取得後は通常アップロードと同一経路へ合流） | `POST /api/admin/materials/upload-from-url` |
| 許可ドメインの参照 / 追加・削除（参照は TEACHER 以上、変更は SYSTEM_ADMIN） | `GET/POST/DELETE /api/admin/url-fetch-domains` |
| 教材一覧（自分 + 公開 + グループ共有 + コース参照） | `GET /api/admin/materials` |
| 教材詳細（抽出された知識グラフ構造） | `GET /api/admin/materials/{id}` |
| PDF 取得 / 差し替え | `GET/PUT /api/admin/materials/{id}/pdf` |
| 開示範囲変更 | `PUT /api/admin/materials/{id}/visibility` |
| 削除（チャンク等をカスケード） | `DELETE /api/admin/materials/{id}` |
| パイプライン実行 / 状態 | `POST /materials/{id}/document-pipeline/run`, `GET .../status`, `POST /documents/{id}/reanalyze` |

アップロード後は **PDF 解析パイプライン**が非同期で走ります（[パイプライン概要](../pipeline/overview.md)）。
進捗は `GET /api/admin/tasks/{task_id}` でポーリング。ステージ: `uploaded → document-analysis → script-generation → audio-generation`。

### 教材行の操作（`⋯` メニュー）

一覧の各行に、権限に応じて次の入口が並ぶ（UI アンカーの正本は
`backend/core/help_kb/admin_ui_anchors.py`。件数は `backend/tests/test_admin_help_ui_anchors.py` が正）。

| 入口 | UI アンカー | 内容・正本 |
|---|---|---|
| 共有 | `materials.row-share` | グループへの viewer / editor 付与（解析成果はドキュメント権限を継承） |
| 共有版 | `materials.row-version` | V層の版発行・履歴・削除予約（[shared_versioning_design.md](shared_versioning_design.md)） |
| 検出要素の一覧 | `materials.row-inventory` | 要素インベントリ → 「深く検討」（[element_inventory_design.md](element_inventory_design.md)） |
| 図・画像 | `materials.row-figures` | 図モーダル（bbox オーバーレイ・パーツ・提示モード・ライブラリ昇格） |
| 🕸 グラフレビュー… | `materials.row-graph-review` | 理論操作グラフの確認・承認 + AI との対話（§9） |
| 位置づけ（分野マップ）… | `materials.row-landscape` | 知識ランドスケープの配置レビュー（[knowledge_landscape_design.md](knowledge_landscape_design.md)） |
| ゼミ前ブリーフ… | `materials.row-seminar-brief` | 下記 |
| 📡 近い論文を探す… | `materials.row-radar` | 論文レーダー（[paper_radar_design.md](paper_radar_design.md)） |
| 解析の見積り | `materials.row-estimate` | U層の事前見積り（レンジのみ・金額なし） |
| 再解析 / 再開 / ステージ再試行 / PDF 差し替え / 削除 | `materials.row-pipeline-run` ほか | パイプラインの再実行系 |

アップロードゾーンには「**URLから取得（arXiv など）**」（`materials.url-upload`）と
「**arXivから探す**」（`materials.arxiv-discovery`）のリンクが並ぶ。前者は許可ドメイン
（SYSTEM_ADMIN が管理・**初期状態は空＝機能無効**）だけを取得し、SSRF ガードは
`backend/core/url_fetch.py` が正本（正本: [url_material_upload_design.md](url_material_upload_design.md)）。
後者は分野ごとの購読条件で
arXiv を検索して**教員が選んだ候補だけ**を既存の URL 取得 → 解析パイプラインへ流す
（`POST /api/admin/discovery/{search,ingest,ingest-batch}` ほか。自動クロール・自動取り込みの
経路は無い。正本: [paper_discovery_design.md](paper_discovery_design.md) PD1〜PD8）。

### ゼミ前ブリーフ

輪講の前に対象論文の「賭け金」を10分で把握する read-only 合成ビュー（正本:
[seminar_brief_mirroring_design.md](seminar_brief_mirroring_design.md) §1、SB1〜SB4）。
`GET /api/admin/documents/{ref}/seminar-brief`（`_require_teacher` +
`_ensure_document_viewable`。実体は `core/doubt/seminar_brief.py::build_seminar_brief` —
新テーブル・新 LLM ゼロの読み時合成, SB1）。教材管理の行 ⋯メニュー「ゼミ前ブリーフ…」→
モーダル（admin.js `openSeminarBriefModal`）が4区画を順に描画する:
**脆い前提**（D層 open-assumptions の document 絞り込み投影・段階ラベルのみ + claim には
k-匿名通過分のつまづき段階ラベル）/ **一点吊りの支持線**（SL層 support_paths
`level=single` の事実文）/ **晴れ間**（「このコーパスの中では検証記録が見つかりません。」の
閉世界固定文, SL1）/ **学習者からの問い**（v1 は空欄で予約, SB3 — 警告色・催促文にしない）。
件数・人数の生値は教員にも出さない（SB2）。空の区画は静かに省略し、第4区画のみ常設。
アンカー2件（`materials.row-seminar-brief` / `materials.seminar-brief-modal`）+
teacher マニュアル節（`11-admin-materials.md#seminar-brief`）の3点セット登録済み。

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

### アカウントライフサイクル

「学生管理」「教員管理」パネルには、作成フォームの下にアカウント一覧の区画が同居する
（正本: [account_lifecycle_management_design.md](account_lifecycle_management_design.md)
AL1〜AL10、migration 068/069）。

| 操作 | API | 権限 |
|---|---|---|
| 一覧（状態・最終ログイン等） | `GET /api/admin/users` | TEACHER 以上（TEACHER は learner 固定に fail-closed） |
| 停止 / 再開 | `POST /api/admin/users/{id}/suspend` / `.../restore` | 学生は TEACHER 以上、教員への操作は SYSTEM_ADMIN |
| パスワードリセット | `POST /api/admin/users/{id}/password-reset` | SYSTEM_ADMIN |
| 利用実績の照会 | `GET /api/admin/users/{id}/activity` | SYSTEM_ADMIN |
| 削除予約 / 取消 | `POST` / `DELETE /api/admin/users/{id}/deletion` | SYSTEM_ADMIN |
| 所有物の移管 | `POST /api/admin/users/{id}/transfer-ownership` | SYSTEM_ADMIN |

- **`users` 行を物理 DELETE しない**（AL1）。削除は状態遷移（`active` → `suspended` →
  `pending_deletion` → 匿名化墓標）＋明示 purge で表現し、猶予期間（既定14日）経過後に
  V層スイーパが相乗りで実行する。所有物（教材・コース・グループ）が残っていれば purge は
  中止して通知する。
- 停止・パスワードリセットは**トークン世代**（`users.token_generation`）を進めるため、
  期限内の JWT も即座に無効化される。
- 自分自身と bootstrap の `Administrator` は停止・削除できない（422）。
- 平文パスワード・ハッシュは監査・ログ・イベントに入れない。

→ ロール・開示範囲の詳細は [認証・権限・開示範囲](auth-visibility.md)。

---

## 7. つまずき分析・エラー分析

- つまずきデータ: `GET /api/admin/courses/{id}/unanswered-queries`（`student_stumble_events` / `unanswered_query_logs`）。
- エラー分析: キーワード/重大度/期間でログを絞り込み、複数形式で一括コピー。
- システム統計: `GET /api/admin/system/materials-stats`（SYSTEM_ADMIN）。

---

## 8. 宣言された弁と静かな計器（教員支援 v1）

正本は [宣言された弁と静かな計器 設計書](teacher_triage_instruments_design.md)（TT1〜TT6。migration 不要）。

- **負荷順トリアージ**: 説明レビューキュー（`deliberation.js`）と R層「再構成の確認」
  （`admin-lecture-studio.js`）に「並び順: 負荷の高い順」トグル。既定は従来順・明示トグル・
  適用中は「基盤への影響が大きい順に並んでいます」の宣言一行を常に表示（TT1。localStorage
  等へ保存せず毎回既定に戻る）。段階ラベル（低/中/高/最高位）はサーバの `load_level_label`
  をそのまま表示し、JS 側に語彙表を持たない（TT2）。導出不能候補は末尾 +
  「影響度を導出できない候補」の正直ラベル。確定操作（approve/dismiss/bulk・R層 PATCH）は
  `sort_order` を body で送り、どの並び順の下で確定したかを監査に残す（TT3）。
- **コスト見通しの一行**: 教材アップロードゾーンのモデルサマリ直下（`#llm-model-cost-note`）と
  再解析モーダルに、`GET /api/admin/llm-usage/forecast[/documents/{id}]` の `show=true` の
  ときのみ事実文を表示。収まる見込みなら行ごと出さず、ボタンの無効化・処理の中止はしない
  （TT4・fail-open）。
- **WMレンズ**: 原稿スタジオのスライドプレビュー（`preview-split` 応答の optional `wm`）で、
  高負荷スライドにのみ段階ラベル + 事実文（degraded 時は「記号の照合は表記の一致による
  近似です」を併記）。`===` を挿すのは教員の手のみ（TT6 — 自動挿入・分割候補の提示はしない）。

---

## 9. グラフ対話レビュー

教材行の `⋯` メニュー「🕸 グラフレビュー…」（`materials.row-graph-review`）から開く
フルスクリーンモーダル（`admin-graph-review.js` / `window.GraphReview`。正本:
[graph_dialogue_review_design.md](graph_dialogue_review_design.md) GR1〜GR8、migration 075）。
理論操作グラフを見取り図に、①構造を見る ②AI と確かめる ③その場で確定する を1画面で行う。

- **描画は原稿スタジオの実装に委譲**（`window.LectureStudio.graphView`）。グラフ描画を二重に
  実装しない（GR8）。層トグル・「次の未レビューへ」ナビ・未レビューの強調を持つ。
- **確定は人間のみ**（GR1）: ノード詳細から `POST /api/admin/theory-components/{id}/approve`
  （遷移専用。承認可能性はサーバーが強制し、満たさなければ 422 の事実文）と、根拠 claim 行の
  `POST /api/admin/claims/{id}/review`。AI 応答から承認 API を呼ぶ経路は作らない。
- **対話は2タブ**（ノード単位＝W層セッションの再利用 / グラフ全体＝
  `POST /api/admin/deliberation/documents/{id}/graph-sessions[/{sid}/messages]`）。グラフ全体対話は
  1コールで、CostGate は W層の上限に相乗りする。グラフ未構築は 422。
- **音声対話**: チャットの 🎤 トグル（`graph-review.voice`）で
  `POST /api/admin/deliberation/voice/{transcribe,speak}`。音声から承認 API は呼ばない。
- **論文の順**: ツールバーの「表示: グラフ | 論文の順」（`graph-review.paper-view`）で、
  `GET /api/admin/documents/{document_id}/paper-layer` の読み時射影（章立て・式番号・図番号に
  ノードを吊るす + 被覆）へ切り替える（正本:
  [graph_paper_layer_design.md](graph_paper_layer_design.md)。フレームは書き換えない・LLM 0 回）。
- 権限は閲覧・対話 = document viewable / 承認・却下 = document editable（GR6）。

---

[← 学習機能](learning.md) ｜ 次へ: [認証・権限・開示範囲 →](auth-visibility.md)
