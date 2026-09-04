# フロントエンド構成

[← ドキュメント目次](../README.md)

フロントエンドは **フレームワーク不使用の Vanilla JS SPA**（2 つ）と、それを配信・プロキシする nginx で構成されます。
実装: `frontend/`。

---

## 1. ファイル構成

| ファイル | 用途 |
|---|---|
| `public/index.html` + `js/app.js` ほか | 学習 UI（学生向け、**ES6+**: const/let, async/await） |
| `public/admin.html` + `js/admin.js` ほか | 管理 UI（教員/管理者向け、**ES5 互換**で記述） |
| `public/css/styles.css` | 統一デザインシステム |
| `public/css/atlas.css` | 分野の地図（Field Atlas）オーバーレイ・ミニマップ用スタイル |
| `nginx.conf` | 静的配信 + `/api` リバースプロキシ |
| `Dockerfile` | nginx イメージのビルド |

> コーディング規約: `admin.js` と管理側モジュールは既存コードに合わせ ES5 互換、`app.js` は ES6+。フレームワークは使わない。

### JS モジュール一覧（`public/js/`、2026-09-03 時点の実ファイル突合で 38 本）

`element-vocab.js` / `element-card.js` の 2 本は学習 UI・管理 UI の**両方**が読み込む共用モジュール。
残り 36 本は片側専用（学習 UI 専用 16 本、管理 UI 専用 20 本）。

**共用（`index.html` と `admin.html` の両方が読み込む 2 本）**

| モジュール | グローバル | 役割 |
|---|---|---|
| `element-vocab.js` | `ElementVocab` | 要素種別・状態などの表示名の正本（admin_ux_issues_2026-08-01.md §3.3 Phase 0）。DOM 非操作・依存なしの純関数群 |
| `element-card.js` | `ElementCard` | 統一パーツカードの描画・イベントバインド（同 §3.2）。編集可能（`VARIANT_EDITABLE`）/ 読み取り専用（`VARIANT_READONLY`）の2バリアント |

**学習 UI 側（`index.html` が読み込む、共用込みで 18 本 / 専用 16 本。ほかに KaTeX（`katex.min.js` +
`auto-render.min.js`）を CDN から読む）**

| モジュール | グローバル | 役割 |
|---|---|---|
| `app.js` | — | 学習 SPA 本体（コース・チャット・レクチャー・音声会話・問いの軌跡） |
| `atlas-fixture.js` | — | 分野の地図の開発用フィクスチャデータ（`ATLAS_DATA_SOURCE=fixture` 明示時のみ使用） |
| `atlas-data.js` | `AtlasData` | 地図データ取得の fixture ⇄ API 切替。API 失敗・404 は null（fail-closed、フィクスチャへ退避しない） |
| `atlas-overlay.js` | `AtlasOverlay` | 分野の地図の全画面オーバーレイ描画 |
| `atlas-panel.js` | `AtlasPanel` | 地図ノードの詳細パネル |
| `atlas-report.js` | `AtlasReport` | 地図上からの修正報告（issue D） |
| `atlas-minimap.js` | `AtlasMinimap` | 左パネル下の常設ミニマップ（F-1。数値・ラベルを描かない） |
| `atlas-cues.js` | `AtlasCues` | 見晴らしの導線カード + 初回ログイン一度きり自動表示（F-2） |
| `personal-map.js` | `PersonalMap` | 個人知識ネットワークのコースビュー（旅カード・「地図には反映しない」訂正操作込み） |
| `personal-map-home.js` | `PersonalMapHome` | 最上位「わたしの地図」パネル（P-3、ヘッダ `#my-map-btn`）。「いまここの周り / いまの地図 / 問いからの旅」の3タブ |
| `my-records.js` | `MyRecords` | 主権台帳v1「わたしの記録」パネル（本人の全痕跡の一望 + 持ち出し。読み取り専用） |
| `landscape-layer.js` | `LandscapeLayer` | 知識ランドスケープ（migration 065）の「論文の位置」レイヤー。地図オーバーレイ + 出典タブへ配置データを供給 |
| `atlas-threads-layer.js` | `AtlasThreadsLayer` | RE層「推定の糸」レイヤー（migration 076）。L2 のみ・点線・既定オフ。`landscape-layer.js` と同じ3フック型（`mountControls` / `onLevelRendered` / `onOverlayClosed`） |
| `reconstruction.js` | `Reconstruction` | 再構成ループ（R層）の学習画面導線「再構成に挑戦」 |
| `discuss.js` | `Discuss` | 「論文と話す」discuss モードのフロント（二枚看板・スコープトグル・モードバー・開幕/着地画面・UCサイクルの動機入力/LEAVE 区画） |
| `corpus-sea.js` | `CorpusSea` | コーパス回遊層（migration 073）「🌊 論文の海」。サイドバー常設・簡易 SVG 地図（領域の塗り + アンカーごとの段階サイズ）・コース無し論文議論の入口 |

**管理 UI 側（`admin.html` が読み込む、共用込みで 22 本 / 専用 20 本 + vis-network / KaTeX CDN）**

| モジュール | グローバル | 役割 |
|---|---|---|
| `admin.js` | — | 管理 SPA 本体（タブ制御・教材/コース管理・スキーマ提案・グループ等） |
| `admin-lecture-studio.js` | `LectureStudio` | 原稿スタジオ（`ls` 接頭辞の関数群を admin.js から分離。Tier 3-17b） |
| `admin-assistant.js` | `AdminAssistant` | Admin Copilot（統合 AI アシスタント・道案内 `runLocatePlan`） |
| `admin-next-steps.js` | `AdminNextSteps` | G層「📋 次にやること」バッジ + パネル |
| `versioning.js` | `Versioning` | V層 共有版モーダル（発行・版履歴・削除予約）+ 通知インボックス🔔 |
| `deliberation.js` | `Deliberation` | W層 要素検討ワークスペース（「深く検討」パネル・要素インベントリ） |
| `doubt-atlas.js` | `DoubtAtlas` | D層「前提の地図」タブ（Field Atlas とは別機能） |
| `admin-indicators.js` | `AdminIndicators` | 制度指標カタログ（`GET /api/indicators`）の事実文1行を計器パネルへ差し込む。**カタログは値を持たないので数値を描く経路が無い**・取得失敗時は何も描かない fail-soft。計器パネル（`admin-llm-usage.js` / `admin-discuss-observation.js` / `admin.js` の関心集約）が `mount()` を呼ぶため**それらより前に読み込む** |
| `admin-llm-usage.js` | `AdminLlmUsage` | U層 LLM 使用量タブ（SYSTEM_ADMIN）+ 教材見積りポップオーバー（TEACHER） |
| `atlas-draft-preview.js` | `AtlasDraftPreview` | 分野の地図・骨格エディタのビジュアルプレビュー |
| `atlas-assist-panel.js` | `AtlasAssistPanel` | 骨格エディタの AI アシスト編集パネル |
| `admin-llm-models.js` | `AdminLlmModels` | M層 場面別 LLM モデル選択（教材管理の解析モデル1行サマリ + 運用タブ「AIモデル」+ 共通モデルチップ `createModelChip`） |
| `admin-figure-studio.js` | `FigureStudio` | 教材図スタジオ（AI 対話で SVG 説明図を生成し `![[figure:id]]` として教材へ採用） |
| `admin-release-review.js` | `AdminReleaseReview` | リリース前の確認ウィザード（学習マップ割当 → 論文の位置づけ → 公開の3ステップ） |
| `admin-manual-editor.js` | `ManualKbEditor` | 利用者マニュアル KB（help_kb, migration 058/059）の draft 編集 + 凍結配信 UI（SYSTEM_ADMIN のみ） |
| `admin-discuss-observation.js` | `AdminDiscussObservation` | discuss 観測基盤（Observation Layer）の状況ダッシュボード + ダンプ取得（SYSTEM_ADMIN のみ） |
| `admin-help-inspect.js` | `AdminHelpInspect` | 管理画面「❓ 使い方」インスペクト・モード（`[data-ui-anchor]` ホバーでマニュアル節ツールチップ） |
| `admin-paper-discovery.js` | `PaperDiscovery` | 論文ディスカバリー（migration 071/072）。教材管理タブ「arXivから探す」モーダル（購読条件・候補・取り込みキュー・地図の端への関心） |
| `admin-paper-radar.js` | `PaperRadar` | 論文レーダー。教材行「📡 近い論文を探す…」モーダル（距離3択・段階ラベル・比較分析・出所の後付け登録） |
| `admin-graph-review.js` | `GraphReview` | グラフ対話レビュー（migration 075）。教材行「🕸 グラフレビュー…」の全画面モーダル（層トグル・未レビュー導線・承認/却下・ノード対話 / グラフ全体対話・「表示: グラフ｜論文の順」の論文層ビュー）。**グラフ描画は `LectureStudio.graphView` へ委譲**（二重実装しない = GR8） |
| `admin-voice-chat.js` | `AdminVoiceChat` | 管理画面向けハンズフリー音声対話エンジン（**DOM 非依存**。MediaRecorder + WebAudio 無音検知）。`admin-graph-review.js` が 🎤 トグルの配線だけを持つ。学習側 `app.js` は非改変 |

### 読み込み順と DI 注入の関係

`index.html` の読み込み順: `element-vocab.js` → `element-card.js` → `app.js` → `atlas-fixture.js` →
`atlas-data.js` → `atlas-overlay.js` → `atlas-panel.js` → `atlas-report.js` → `atlas-minimap.js` →
`atlas-cues.js` → `personal-map.js` → `personal-map-home.js` → `my-records.js` → `landscape-layer.js`
（`atlas-overlay.js` のフックから呼ばれるため `personal-map.js` の後）→ `atlas-threads-layer.js` →
`reconstruction.js` → `discuss.js` → `corpus-sea.js`。
`element-vocab.js` / `element-card.js` はこれらを参照する全スクリプトより前に読み込む必要があるため
先頭固定。`app.js` が各グローバルの存在チェック付きで呼び出し、
`PersonalMap.init({openTrajectory})` / `PersonalMapHome.init({openTrajectory})` /
`CorpusSea.init({})` / `MyRecords.init({})` のように依存を渡す。

`admin.html` の読み込み順: `element-vocab.js` → `element-card.js` → `doubt-atlas.js` →
`atlas-draft-preview.js` → `atlas-assist-panel.js` → `admin-help-inspect.js` → `admin-assistant.js` →
`admin-next-steps.js` → `versioning.js` → `admin-figure-studio.js` → `admin-lecture-studio.js` →
`deliberation.js` → `admin-indicators.js` → `admin-llm-usage.js` → `admin-manual-editor.js` → `admin-discuss-observation.js` →
`admin-llm-models.js` → `admin-release-review.js` → `admin-paper-discovery.js` →
`admin-paper-radar.js` → `admin-voice-chat.js` → `admin-graph-review.js` → **最後に `admin.js`**。
`element-vocab.js` / `element-card.js` はここでも先頭固定（`admin-lecture-studio.js` /
`deliberation.js` / `admin.js` が参照するため）。各分離モジュールは `window.*` グローバルを
公開するだけで、初期化は `admin.js` の起動処理が DI 注入で行う:

- `AdminLlmModels.init({apiFetch, escHtml, onTabActivate, state, activateTabView})` — `initUpload()` /
  `initMaterialsPanel()` より**前**に注入する（教材管理の初期化がこの DI に依存するため）。直後に
  `AdminLlmModels.initMaterialsPanel()` を呼ぶ
- `AdminReleaseReview.init({apiFetch, escHtml, atlasBindingPropose, refreshNextSteps, onPublished})` —
  コース管理初期化と同じブロックで起動（SYSTEM_ADMIN では初期化しない）
- `PaperDiscovery.init({apiFetch, escHtml, onUploadAccepted})` / `PaperRadar.init({apiFetch, escHtml,
  onUploadAccepted})` — いずれも受理後の合流点 `handleUploadAccepted` を注入し、URL 取得・通常
  アップロードと**同一経路**へ乗せる（新しいポーリングを作らない）
- `GraphReview.init({apiFetch, escHtml, getToken})` — `Deliberation` と同じブロックで起動
  （`getToken` は音声 API のリクエスト用）
- `LectureStudio.init({apiFetch, apiFetchRaw, escHtml, onTabActivate, state})` — 公開 API は
  `init` / `openExportModal` / `getScreenContext`。**`FigureStudio.init({apiFetch, apiFetchRaw,
  escHtml})` はこの内部から呼ばれる**（admin.js から見た起動点を増やさないため。admin.js は
  `FigureStudio` を直接 init しない）
- `Deliberation.init({apiFetch, apiFetchRaw, escHtml})`（SYSTEM_ADMIN では LectureStudio とともに初期化しない）
- `AdminLlmUsage.init({apiFetch, escHtml, onTabActivate, state})`
- `ManualKbEditor.init({apiFetch, escHtml, onTabActivate, state})` — `admin-llm-usage.js` と同型の DI
- `AdminDiscussObservation.init({apiFetch, escHtml, onTabActivate, state})` — `admin-manual-editor.js`
  と同型の DI
- `AdminAssistant.init({apiFetch, state, activateTabView})` → 直後に `registerAssistantHooks()` で
  各画面の screen context / UI アンカー（道案内の点灯先）を登録
- `AdminHelpInspect.init({apiFetch})` — `AdminAssistant` の usage_help 連携より前に読み込む
- `AdminNextSteps.init({apiFetch, state})` — AdminAssistant の**後**に起動する
  （パネルの `[案内する]` が `AdminAssistant.runLocatePlan` を呼ぶため）
- `Versioning.init({apiFetch, state})` + `Versioning.initInbox()`（ヘッダの 🔔 通知）
- `DoubtAtlas` はタブ活性化フック `onTabActivate("doubt-atlas", ...)` 経由で遅延初期化

---

## 2. 学習 SPA（app.js）

- 3 パネルレイアウト（学習パス / チャット・レクチャー / コンテキスト）。中央には教材区画・チャット区画・
  レクチャープレイヤーに加えて**ハンズフリー音声会話パネル**（🤖 ボタンで起動、「いま話している題材」を表示）。
- **1 画面に収めるレイアウト**（開発ルール5）: ページと主カラム `.mn` は `overflow: clip`
  （`hidden` にしない — `scrollIntoView()` / `focus()` で画面全体がずれるため）。縦が足りないときに
  縮むのは上段（`.material-region`）と会話（`.ca`、floor 120px）で、下段（`.mode-bar` /
  `.discuss-bar` / `.ia` / `.lecture-player`）は `flex: 0 0 auto` で潰さない。ガードレールは
  `backend/tests/test_learning_layout_static.py`。
- ヘッダには「わたしの地図」（`#my-map-btn`）と「わたしの記録」（`#my-records-btn`）、サイドバーには
  二枚看板（順番に学ぶ / この論文と議論する）と「🌊 論文の海」を置く。
- 教材区画の軽量アンカー（UCサイクル、精読モード時）は `#quick-anchor-strip`。精読モードの
  オンオフは localStorage `eg_precision_reading:<courseId>`（**サーバに学習者設定テーブルを作らない**）。
- 学習画面の「？」インスペクト・モードは `[data-ui-anchor]` 属性を担体にする
  （表の正本は `backend/core/help_kb/ui_anchors.py`。表と DOM の整合は
  `backend/tests/test_help_ui_anchors.py` / `test_help_usage_ui_static.py` が固定）。
- 右パネルの進捗タブに**違和感（tension）ダイジェストカード**（`renderTensionDigestCard()`）と問いの軌跡を表示。
- 回答バブル・出典タブには**出所バッジ**（`GROUNDING_META` / `groundingBadge()`: 教材から回答 / 別の資料から回答 / AI の一般知識）。
- 主要 state: `token / role / courseId / course / personalLayer / currentTopicId / chatMessages / topicMaterial / learningSupport` + `lastGrounding / lastSources / lastOverallTier / interestTraces / tensionDigest / tensionDeferred / topicHasAudio`。
- 機能の詳細は [学習機能](../features/learning.md)。

代表的な関数 → API 対応:
| 関数 | エンドポイント |
|---|---|
| `loadCourses()` | `GET /api/learning/courses` |
| `loadCourse(id)` | `GET /api/learning/courses/{id}` |
| `loadProgress()` | `GET /api/learning/courses/{id}/progress` |
| `sendMessage()` | `POST /api/learning/courses/{id}/topics/{tid}/chat` |
| `loadLectureSequence()` | `GET /api/learning/lecture/courses/{id}/topics/{tid}/sequence` |
| 音声会話ループ | `POST /api/learning/voice/transcribe` → chat（`intent_mode='casual'`）→ `POST /api/learning/voice/speak` |
| `confirmTension()` / `dismissTension()` | `POST /api/learning/tension/{trace_id}/confirm`（`/dismiss`） |

---

## 3. 管理 SPA（admin.js + 分離モジュール）

- タブはグループ別プルダウン（コンテンツ / 学習インサイト / ナレッジ基盤 / 運用）に束ねられている。
  タブ一覧・ロール別の出し分けは [管理機能](../features/admin.md#タブ構成) を正本とする。
- 非同期処理は `POST → 202(task_id) → GET /tasks/{id} ポーリング`の形（教材解析・スクリプト/音声生成・再抽出）。
- 理論操作グラフの可視化: `source_backing_status`（通常/細線/点線枠/薄色⚠）と `graph_layer`（主グラフ/式の詳細/すべて）で描き分け。
  描画の正本は `admin-lecture-studio.js` の `ls` グラフ関数群で、`window.LectureStudio.graphView` として
  公開されている。**グラフレビュー画面はこれに委譲し、描画ロジックを二重実装しない**（GR8）。
- 原稿スタジオの UI は `admin-lecture-studio.js` に分離済み（開発ルール: 原稿スタジオの変更はこちらに書く）。
- 管理画面の「❓ 使い方」インスペクト・モードは `[data-ui-anchor]` 属性を担体にする。**新しい管理 UI を
  追加したら「マニュアル節 + `ADMIN_UI_ANCHORS`（`backend/core/help_kb/admin_ui_anchors.py`）+
  `data-ui-anchor`」の3点を揃えること**（`backend/tests/test_admin_help_ui_anchors.py` /
  `test_admin_help_inspect_ui_static.py` が双方向網羅を固定しており、欠けるとテストが落ちる）。
- 機能の詳細は [管理機能](../features/admin.md)。

---

## 4. nginx リバースプロキシ（nginx.conf）

- ポート **3000** で静的 SPA を配信し、クライアントサイドルーティングのため `try_files $uri $uri/ /index.html`。
- `/api/*` を `api-server`（内部 8001）へプロキシ。プロキシ対象パス（**正は `nginx.conf` の
  `location` 行**）: `/api/learning/`, `/api/auth/`, `/api/admin/`, `/api/groups`(+ `/api/groups/`),
  `/api/me/`, `/api/indicators`(+ `/api/indicators/`), `/api/atlas`(+ `/api/atlas/`),
  `/api/courses/`, `/api/documents/`。
- **運用注意: `/api/atlas` と `/api/indicators` は明示 proxy が必須**。それぞれ
  `location /api/atlas/`・`location /api/indicators/` と末尾スラッシュなしの
  `location = /api/atlas`（`GET /api/atlas?course=...` 用）・`location = /api/indicators` の
  両方が nginx.conf に定義されている。
  この location が欠けると SPA フォールバック（`location /`）が index.html を 200 で返し、
  フロント（atlas-data.js / admin-indicators.js）の JSON パースが失敗する形で事故る。
- 全 proxy location でタイムアウト 120s。アップロード上限 `client_max_body_size 150m` は
  `location /api/admin/` にのみ設定（教材アップロードの経路）。`Host` / `X-Real-IP` /
  `X-Forwarded-For` / `X-Forwarded-Proto` を引き継ぐ（`X-Real-IP` は `auth_events` の
  IP 記録が参照する）。
- **外部公開はこの 3000 番のみ**。api-server は直接公開しない（[デプロイ構成](../architecture/deployment.md#ネットワーク設計セキュリティ)）。

---

## 5. デザインシステム（styles.css）

- CSS 変数でカラー/タイポグラフィを定義（背景 3 段階、テキスト 3 段階 + info/success/warning/danger、Apple 系 system font）。
- 学習ビューは CSS Grid（topbar / sidebar 260px / main 1fr / right 300px）。
- チャットバブル（`.mg.usr` 右寄せ青 / `.mg.ai` 左寄せ）、ステータスバッジ、レクチャーセグメントのフェードイン、タイピングドット等のアニメーション。

---

## 6. 認証フロー（フロント側）

```
ログイン → POST /api/auth/login → JWT
   → localStorage["eg_token"] に保存
   → 以降のリクエストに Authorization: Bearer {token}
```

→ [認証・権限・開示範囲](../features/auth-visibility.md)。

---

[← 認証・権限・開示範囲](../features/auth-visibility.md) ｜ [ドキュメント目次に戻る →](../README.md)

---

最終更新 2026-09-03（実ファイル突合: `frontend/public/js/` 38 本・`index.html` / `admin.html` の
script タグ順・`nginx.conf` の location）
