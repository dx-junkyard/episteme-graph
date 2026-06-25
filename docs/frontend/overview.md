# フロントエンド構成

[← ドキュメント目次](../README.md)

フロントエンドは **フレームワーク不使用の Vanilla JS SPA**（2 つ）と、それを配信・プロキシする nginx で構成されます。
実装: `frontend/`。

---

## 1. ファイル構成

| ファイル | 用途 |
|---|---|
| `public/index.html` + `js/app.js` | 学習 UI（学生向け、**ES6+**: const/let, async/await） |
| `public/admin.html` + `js/admin.js` | 管理 UI（教員/管理者向け、**ES5 互換**で記述） |
| `public/css/styles.css` | 統一デザインシステム |
| `nginx.conf` | 静的配信 + `/api` リバースプロキシ |
| `Dockerfile` | nginx イメージのビルド |

> コーディング規約: `admin.js` は既存コードに合わせ ES5 互換、`app.js` は ES6+。フレームワークは使わない。

---

## 2. 学習 SPA（app.js）

- 3 パネルレイアウト（学習パス / チャット・レクチャー / コンテキスト）。
- 主要 state: `token / role / courseId / course / personalLayer / currentTopicId / chatMessages / topicMaterial / learningSupport`。
- 機能の詳細は [学習機能](../features/learning.md)。

代表的な関数 → API 対応:
| 関数 | エンドポイント |
|---|---|
| `loadCourses()` | `GET /api/learning/courses` |
| `loadCourse(id)` | `GET /api/learning/courses/{id}` |
| `loadProgress()` | `GET /api/learning/courses/{id}/progress` |
| `sendMessage()` | `POST /api/learning/courses/{id}/topics/{tid}/chat` |
| `loadLectureSequence()` | `GET /api/learning/lecture/courses/{id}/topics/{tid}/sequence` |

---

## 3. 管理 SPA（admin.js）

- タブベースのナビゲーション（教材管理 / コースビルダー / コース管理 / Lecture Studio / つまずき / スキーマ提案 / グループ / システム統計 / エラー分析）。
- 非同期処理は `POST → 202(task_id) → GET /tasks/{id} ポーリング`の形（教材解析・スクリプト/音声生成・再抽出）。
- 理論操作グラフの可視化: `source_backing_status`（通常/細線/点線枠/薄色⚠）と `graph_layer`（主グラフ/式の詳細/すべて）で描き分け。
- 機能の詳細は [管理機能](../features/admin.md)。

---

## 4. nginx リバースプロキシ（nginx.conf）

- ポート **3000** で静的 SPA を配信し、クライアントサイドルーティングのため `try_files $uri $uri/ /index.html`。
- `/api/*` を `api-server`（内部 8001）へプロキシ。プロキシ対象パス:
  `/api/learning/`, `/api/auth/`, `/api/admin/`, `/api/groups`(+ `/api/groups/`), `/api/me/`, `/api/courses/`, `/api/documents/`。
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
