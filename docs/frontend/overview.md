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

### JS モジュール一覧（`public/js/`、2026-07 時点で 22 本）

**学習 UI 側（`index.html` が読み込む 11 本）**

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
| `personal-map-home.js` | `PersonalMapHome` | 最上位「わたしの地図」パネル（P-3、ヘッダ `#my-map-btn`） |
| `reconstruction.js` | `Reconstruction` | 再構成ループ（R層）の学習画面導線「再構成に挑戦」 |

**管理 UI 側（`admin.html` が読み込む 10 本 + 共用 vis-network / KaTeX CDN）**

| モジュール | グローバル | 役割 |
|---|---|---|
| `admin.js` | — | 管理 SPA 本体（タブ制御・教材/コース管理・スキーマ提案・グループ等） |
| `admin-lecture-studio.js` | `LectureStudio` | 原稿スタジオ（`ls` 接頭辞の関数群を admin.js から分離。Tier 3-17b） |
| `admin-assistant.js` | `AdminAssistant` | Admin Copilot（統合 AI アシスタント・道案内 `runLocatePlan`） |
| `admin-next-steps.js` | `AdminNextSteps` | G層「📋 次にやること」バッジ + パネル |
| `versioning.js` | `Versioning` | V層 共有版モーダル（発行・版履歴・削除予約）+ 通知インボックス🔔 |
| `deliberation.js` | `Deliberation` | W層 要素検討ワークスペース（「深く検討」パネル・要素インベントリ） |
| `doubt-atlas.js` | `DoubtAtlas` | D層「前提の地図」タブ（Field Atlas とは別機能） |
| `admin-llm-usage.js` | `AdminLlmUsage` | U層 LLM 使用量タブ（SYSTEM_ADMIN）+ 教材見積りポップオーバー（TEACHER） |
| `atlas-draft-preview.js` | `AtlasDraftPreview` | 分野の地図・骨格エディタのビジュアルプレビュー |
| `atlas-assist-panel.js` | `AtlasAssistPanel` | 骨格エディタの AI アシスト編集パネル |

### 読み込み順と DI 注入の関係

`index.html` の読み込み順: `app.js` → `atlas-fixture.js` → `atlas-data.js` → `atlas-overlay.js` →
`atlas-panel.js` → `atlas-report.js` → `atlas-minimap.js` → `atlas-cues.js` → `personal-map.js` →
`personal-map-home.js` → `reconstruction.js`。`app.js` が各グローバルの存在チェック付きで呼び出し、
`PersonalMap.init({openTrajectory})` / `PersonalMapHome.init({openTrajectory})` のように依存を渡す。

`admin.html` の読み込み順: `doubt-atlas.js` → `atlas-draft-preview.js` → `atlas-assist-panel.js` →
`admin-assistant.js` → `admin-next-steps.js` → `versioning.js` → `admin-lecture-studio.js` →
`deliberation.js` → `admin-llm-usage.js` → **最後に `admin.js`**。各分離モジュールは
`window.*` グローバルを公開するだけで、初期化は `admin.js` の起動処理が DI 注入で行う:

- `LectureStudio.init({apiFetch, apiFetchRaw, escHtml, onTabActivate, state})` — 公開 API は
  `init` / `openExportModal` / `getScreenContext`
- `Deliberation.init({apiFetch, apiFetchRaw, escHtml})`（SYSTEM_ADMIN では LectureStudio とともに初期化しない）
- `AdminLlmUsage.init({apiFetch, escHtml, onTabActivate, state})`
- `AdminAssistant.init({apiFetch, state, activateTabView})` → 直後に `registerAssistantHooks()` で
  各画面の screen context / UI アンカー（道案内の点灯先）を登録
- `AdminNextSteps.init({apiFetch, state})` — AdminAssistant の**後**に起動する
  （パネルの `[案内する]` が `AdminAssistant.runLocatePlan` を呼ぶため）
- `Versioning.init({apiFetch, state})` + `Versioning.initInbox()`（ヘッダの 🔔 通知）
- `DoubtAtlas` はタブ活性化フック `onTabActivate("doubt-atlas", ...)` 経由で遅延初期化

---

## 2. 学習 SPA（app.js）

- 3 パネルレイアウト（学習パス / チャット・レクチャー / コンテキスト）。中央には教材区画・チャット区画・
  レクチャープレイヤーに加えて**ハンズフリー音声会話パネル**（🤖 ボタンで起動、「いま話している題材」を表示）。
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
- 原稿スタジオの UI は `admin-lecture-studio.js` に分離済み（開発ルール: 原稿スタジオの変更はこちらに書く）。
- 機能の詳細は [管理機能](../features/admin.md)。

---

## 4. nginx リバースプロキシ（nginx.conf）

- ポート **3000** で静的 SPA を配信し、クライアントサイドルーティングのため `try_files $uri $uri/ /index.html`。
- `/api/*` を `api-server`（内部 8001）へプロキシ。プロキシ対象パス:
  `/api/learning/`, `/api/auth/`, `/api/admin/`, `/api/groups`(+ `/api/groups/`), `/api/me/`,
  `/api/atlas`(+ `/api/atlas/`), `/api/courses/`, `/api/documents/`。
- **運用注意: `/api/atlas` は明示 proxy が必須**。`location /api/atlas/` と末尾スラッシュなしの
  `location = /api/atlas`（`GET /api/atlas?course=...` 用）の両方が nginx.conf に定義されている。
  この location が欠けると SPA フォールバック（`location /`）が index.html を 200 で返し、
  フロント（atlas-data.js）の JSON パースが失敗する形で事故る。
- タイムアウト 120s、アップロード上限 150MB。`Host` / `X-Real-IP` / `X-Forwarded-For` / `X-Forwarded-Proto` を引き継ぎ。
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
