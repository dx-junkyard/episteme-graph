# API とルーティング

[← ドキュメント目次](../README.md)

FastAPI バックエンドのエンドポイント構成、認証・RBAC、開示範囲の制御をまとめます。
実装は `backend/api/`。

---

## 1. アプリ構成

| ファイル | 役割 |
|---|---|
| `backend/api/main.py` | FastAPI アプリ本体。lifespan で起動時マイグレーション・スキーマ seed・管理者初期化、ルーター登録、CORS、エラーログ middleware |
| `backend/api/dependencies.py` | 認証・RBAC の依存関数（JWT 検証、ロール要求） |
| `backend/api/schemas.py` | API 固有の Pydantic リクエスト/レスポンスモデル |
| `backend/api/services.py` | 共通ビジネスロジック（バックグラウンドタスク CRUD など） |
| `backend/api/routes/*.py` | 機能別ルーター（auth / learning / admin / lecture / lecture_studio / groups / theory_components / cartridges / revisions / error_logs / export）。lecture_studio・theory_components・cartridges・revisions は `admin.py` のルーターに include され `/api/admin` 配下で公開される（`export_artifacts.py` はルーターではなく export のヘルパー） |

> `core/` には FastAPI を import しない方針（テスタビリティ確保）。API 固有モデルは `api/` 側に置きます。

---

## 2. 認証と RBAC

### 認証方式（`dependencies.py`）
- **JWT (HS256)**, 有効期限 24 時間。`JWT_SECRET` で署名。
- パスワードは **bcrypt**（passlib）でハッシュ。
- トークン payload: `{sub, username, email, role, exp}`。

### ロールのマッピング
| DB 上のロール | アプリ定数 | 説明 |
|---|---|---|
| `learner` | `ROLE_STUDENT` | 学生 |
| `instructor` | `ROLE_TEACHER` | 教員 |
| `admin` | `ROLE_SYSTEM_ADMIN` | システム管理者（最高権限） |

### 依存関数
- `_get_current_user()` — Bearer トークンを検証し `{id, username, email, role}` を返す（全保護エンドポイントで使用）
- `_require_teacher()` — TEACHER 以上でなければ 403
- `_require_system_admin()` — SYSTEM_ADMIN でなければ 403

権限階層は **SYSTEM_ADMIN > TEACHER > STUDENT**。詳細・開示範囲の制御は [認証・権限・開示範囲](../features/auth-visibility.md) を参照。

---

## 3. エンドポイント一覧

### 認証 `/api/auth`（`routes/auth.py`）
| メソッド | パス | 説明 | 権限 |
|---|---|---|---|
| POST | `/register` | ユーザー登録（既定 STUDENT） | 公開 |
| POST | `/login` | ログイン（JWT 取得） | 公開 |
| GET | `/me` | 現在のユーザー情報 | 要ログイン |

### 学習 `/api/learning`（`routes/learning.py`）
| メソッド | パス | 説明 | 権限/可視性 |
|---|---|---|---|
| POST | `/courses` | コース作成 | 要ログイン（visibility 検証） |
| GET | `/courses` | コース一覧（自分 + 受講中 + 公開/グループ共有） | 要ログイン |
| GET | `/courses/{id}` | コース詳細（マスター + 個人レイヤー） | 可視性チェック or オーナー |
| PUT | `/courses/{id}` | コース更新 | オーナー or editor |
| DELETE | `/courses/{id}` | コース削除 | オーナーのみ |
| GET | `/courses/{id}/progress` | 学習進捗 | 受講者 |
| POST | `/courses/{id}/enroll` | 公開コースへ受講登録（クローン） | 要ログイン（可視性/グループ確認） |
| GET | `/courses/{cid}/topics/{tid}/chat` | チャット履歴取得 | 受講者 |
| POST | `/courses/{cid}/topics/{tid}/chat` | RAG チャット + 誤解検出 | 受講者 |
| POST | `/courses/{cid}/topics/{tid}/check-question` | 理解度チェック | 受講者 |
| DELETE | `/courses/{cid}/topics/{tid}/stumble-events` | つまずきログ消去 | 教員 |
| GET | `/courses/{cid}/source-chunk/{chunk_id}` | 根拠チャンク本文の取得（出典表示・音声会話の教材パネル用） | 受講者 |
| GET | `/courses/{cid}/interest-traces` | 問いの軌跡（関心痕跡）一覧。tension の candidate/dismissed は含めない | 本人のみ |
| POST | `/courses/{cid}/interest-traces/{trace_id}/resolve`（`/internalize`） | 痕跡の解決 / 内在化 | 本人のみ |
| GET | `/courses/{cid}/components/{component_id}/explanations` | 承認済み説明の閲覧（C層の学習者向け窓口） | 受講者 |

#### 違和感（tension）ダイジェスト（B層, マイグレーション 022）
| メソッド | パス | 説明 | 権限 |
|---|---|---|---|
| GET | `/courses/{cid}/tension/digest` | 本人の tension 候補（confidence≥0.55、最大3件。confidence 数値は返さない） | 本人のみ |
| POST | `/tension/{trace_id}/confirm` | 候補を本人が確定（candidate → open / `learner_text` 付きなら articulated） | 本人のみ |
| POST | `/tension/{trace_id}/dismiss` | 「違う」と判定（candidate → dismissed。行は削除しない） | 本人のみ |
| POST | `/tension/{trace_id}/connect` | 確定済み tension をグラフ上の node/edge に接続 | 本人のみ |

#### ハンズフリー音声会話
| メソッド | パス | 説明 |
|---|---|---|
| POST | `/voice/transcribe` | multipart 音声 → `core.llm.transcribe_audio()` で文字起こし（Whisper 系。openai プロバイダのみ） |
| POST | `/voice/speak` | `{text}` → LaTeX/markdown 記号を除去して `core.tts.generate_tts_audio()` で MP3(base64) |

→ RAG の中身は [RAG チャットフロー](rag-chat.md)。学習 UI 側は [学習機能](../features/learning.md)。

### レクチャーモード `/api/learning/lecture`（`routes/lecture.py`）
| メソッド | パス | 説明 |
|---|---|---|
| GET | `/courses/{cid}/topics/{tid}/sequence` | 適応的レクチャーシーケンス構築（習得済みはスキップ/要約） |
| POST | `/courses/{cid}/topics/{tid}/tts` | spoken_text から TTS 音声生成（キャッシュ付き） |
| POST | `/courses/{cid}/topics/{tid}/interrupt` | 講義中の中断チャット（再開位置を保持） |

### 管理 `/api/admin`（`routes/admin.py`）
教材・コース・スキーマ・ユーザー管理。基本的に **TEACHER 以上**、ユーザー作成の一部は **SYSTEM_ADMIN**。

| 分類 | 代表的なエンドポイント |
|---|---|
| 教材管理 | `POST /materials/upload`（非同期, 202）, `GET /materials`, `GET /materials/{id}`, `GET/PUT /materials/{id}/pdf`, `PUT /materials/{id}/visibility`, `DELETE /materials/{id}`, `POST /documents/{id}/reanalyze` |
| コースビルダー | `POST/GET /course-builder/sessions`, `GET/PUT /course-builder/sessions/{id}`, `POST /course-builder/chat` |
| コース公開/権限 | `GET /courses`, `PUT /courses/{id}/publish`, `GET/POST /courses/{id}/groups`, `DELETE /courses/{id}/groups/{gid}` |
| ユーザー管理 | `POST /users/student`（TEACHER+）, `POST /users/teacher`（SYSTEM_ADMIN のみ） |
| スキーマ進化 | `GET/POST /schema/types`, `GET/POST /schema/predicates`, `GET /schema-proposals`, `POST /schema-proposals/analyze`, `PUT /schema-proposals/{id}/approve`（`/approve-with-scope`, `/reject`）, `GET/POST /reextraction-jobs` |
| タスク/分析 | `GET /tasks/{task_id}`, `GET /courses/{id}/unanswered-queries`, `GET /system/materials-stats`（SYSTEM_ADMIN）, `GET /interest-dashboard`（関心・tension の k-匿名化集計） |
| カートリッジ | `GET /cartridges`, `GET /cartridges/{id}`（`/ontology`, `/component-types`, `/relation-types`, `/maturity-levels`, `/support-statuses`）（`routes/cartridges.py`） |
| リビジョン | `POST/GET /documents/{id}/revisions`, `POST .../revisions/{rid}/run`, `GET .../run-status`, `GET .../report`, `POST .../accept`（`/reject`, `/revise`）（`routes/revisions.py`, マイグレーション 019） |
| ドキュメント構造/グラフ閲覧 | `GET /documents/{id}/structure`, `GET /documents/{id}/component-graph`, `GET /documents/{id}/chunks/{chunk_id}/claims`, `PATCH /claims/{claim_id}`（`routes/theory_components.py`） |
| エラーログ | `GET /api/admin/error-logs`（`routes/error_logs.py`。5xx を middleware が記録） |

→ スキーマ進化の流れは [動的スキーマ進化](../pipeline/schema-evolution.md)。管理 UI 側は [管理機能](../features/admin.md)。

### エクスポート（`routes/export.py`）
| メソッド | パス | 説明 |
|---|---|---|
| POST | `/api/courses/{course_id}/export-bundle` | コース一式のエクスポートバンドル生成 |
| POST | `/api/documents/{document_id}/export-bundle` | ドキュメント一式のエクスポートバンドル生成 |

### Lecture Studio `/api/admin`（`routes/lecture_studio/` パッケージ）
教員向けの講義原稿・音声の事前構築、理論コンポーネント、ドキュメントパイプライン操作。

| 分類 | 代表的なエンドポイント |
|---|---|
| 設定 | `GET/PUT /courses/{id}/lecture-studio/settings`（ナレーション/応答ペルソナ） |
| スクリプト | `POST /courses/{id}/lecture-scripts/generate`（非同期）, `GET/PUT /courses/{id}/lecture-scripts/{chunk_id}`, `POST .../rewrite`（AI 書き換え） |
| 音声 | `POST /courses/{id}/lecture-audio/generate`（非同期）, `GET /courses/{id}/tasks/active` |
| ドキュメントパイプライン | `POST /documents/{id}/reanalyze`, `POST /materials/{id}/document-pipeline/run`, `GET /materials/{id}/document-pipeline/status` |
| 理論コンポーネント | `GET/POST/DELETE /courses/{id}/lecture-studio/components`, `GET .../components/{cid}`, `POST .../claims` |
| コース構造 | `GET .../course-structure`, `GET .../document-structure`, `PUT .../course-topics/{tid}`, `POST .../course-topics/{tid}/draft/rewrite`, `POST /courses/{id}/course-content/generate`, `POST /courses/{id}/structure/reanalyze` |

### グループ `/api/groups`・`/api/me`（`routes/groups.py`）
| 分類 | 代表的なエンドポイント |
|---|---|
| グループ CRUD | `POST/GET /groups`, `GET/PUT/DELETE /groups/{id}`, `POST /groups/{id}/invite-code/rotate` |
| メンバー/招待 | `POST/DELETE /groups/{id}/members[/{uid}]`, `POST /groups/join-by-code`, `GET /groups/{id}/invitations` |
| 自分の招待 | `GET /me/invitations`, `POST /me/invitations/{id}/accept`（`/decline`） |

### 承認・共有レイヤー `/api/admin`（`routes/theory_components.py`, C層）
教員による査読承認・独自解釈の並存・教員間共有。詳細は [承認・共有レイヤー](../features/endorsement-sharing.md)。
承認は説明バージョン（explanation）単位。状態変更は `theory_review_events` に監査記録。

| 分類 | 代表的なエンドポイント | 権限 |
|---|---|---|
| 説明バージョン | `GET/POST /theory-components/{id}/explanations`, `PATCH /explanations/{id}`（編集・shared切替・review_status） | teacher（PATCH は作者/admin） |
| 承認 | `POST/DELETE /explanations/{id}/endorse`, `GET /explanations/{id}/endorsements`（集計＋段階ラベル） | teacher |
| 引用・可視化 | `POST /explanations/{id}/cite`, `GET /courses/{id}/sharing-dashboard`（集団集計） | teacher |
| 候補生成 | `POST /theory-components/candidates/from-query`（AI候補→教員確定, claim紐づけは confirmed=false） | teacher |
| 学習者向け | `GET /api/learning/courses/{id}/components/{cid}/explanations`（承認済みのみ） | 受講者 |

---

## 4. 非同期処理パターン

重い処理（教材アップロード解析・スクリプト/音声バッチ生成・再抽出）は次の形を取ります。

```
クライアント         api-server                  background_tasks
   │  POST .../upload  │                              │
   │ ───────────────▶ │ 202 { task_id } を返す       │ INSERT (pending)
   │ ◀─────────────── │                              │
   │                  │  別スレッドで処理 ──────────▶ │ processing → completed/failed
   │  GET /tasks/{id} │                              │
   │ ───────────────▶ │  状態を返す                  │
```

`services.py` の `create_background_task` / `update_background_task` / `get_background_task` が状態を管理し、
`document_analysis_runs` の進捗も合わせて返します。

---

[← データモデル](../architecture/data-model.md) ｜ 次へ: [コアエンジン →](core-engine.md)
