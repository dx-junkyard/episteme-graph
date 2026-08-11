# API とルーティング

[← ドキュメント目次](../README.md)

FastAPI バックエンドのエンドポイント構成、認証・RBAC、開示範囲の制御をまとめます。
実装は `backend/api/`。

> **役割分担（正本の所在）**: 本ページは**エンドポイントの正本**（メソッド / パス / 権限 / 一行説明）。
> 画面操作の**手順の正本**は [docs/admin_operations/](../admin_operations/) 各ページ
> （materials / course / lecture_studio / atlas / doubt / library / llm_usage / interest_dashboard /
> users / system）に委ねる。手順の詳細をここに書かない。

---

## 1. アプリ構成

| ファイル | 役割 |
|---|---|
| `backend/api/main.py` | FastAPI アプリ本体。lifespan で起動時マイグレーション・スキーマ seed・管理者初期化・各種 worker 起動（V層スイーパ / status watcher）、ルーター登録、CORS、エラーログ middleware |
| `backend/api/dependencies.py` | 認証・RBAC の依存関数（JWT 検証、ロール要求） |
| `backend/api/schemas.py` | API 固有の Pydantic リクエスト/レスポンスモデル |
| `backend/api/services.py` | 共通ビジネスロジック（バックグラウンドタスク CRUD、権限判定 `resolve_document_access` など） |
| `backend/api/routes/*.py` | 機能別ルーター（下記マウント一覧参照。`export_artifacts.py` はルーターではなく export のヘルパー） |

**ルーターのマウント（main.py、Tier 3-17c でフラット化）**: 全ルーターは `main.py` から直接
`app.include_router(...)` で登録される（admin.py 経由の二段ネストは廃止済み）。

- **自前 prefix で直接登録**: `auth`（/api/auth）/ `learning`（/api/learning）/ `admin`（/api/admin）/
  `figure_presentation`（/api/admin）/ `error_logs`（/api/admin/error-logs）/ `lecture`
  （/api/learning/lecture）/ `groups`（/api/groups・/api/me）/ `export`（/api/courses・/api/documents）/
  `atlas.learning_router`（/api/learning/atlas）/ `atlas.report_router`（/api/atlas）/ `atlas_view`
  （/api/atlas）/ `doubt.learning_router`（/api/learning）/ `reconstruction.learning_router`
  （/api/learning）/ `library`（/api/admin/library）/ `llm_usage`（/api/admin/llm-usage）/
  `personal_map.router`（/api/learning）/ `personal_map.me_router`（/api/me）
- **`prefix="/api/admin"` を付けて登録される admin 系子ルーター（14本）**: `lecture_studio`
  （パッケージ。`_shared`/`scripts`/`pipeline`/`topics` に分割、Tier 3-17a）/ `theory_components` /
  `cartridges` / `revisions` / `atlas.router`（/cartridges 配下）/ `atlas.admin_atlas_router`（/atlas）/
  `atlas.binding_router`（/courses）/ `doubt.admin_router`（/doubt）/ `admin_assistant`（/assistant）/
  `reconstruction.admin_router` / `versioning` / `status`（/status）/ `notifications`（/notifications）/
  `deliberation`（/deliberation）
- **例外（#496）**: `GET /api/admin/documents/{id}/figures` は admin.py にも定義が残るが、main.py が
  admin.router から当該 GET ルートを除去して `figure_presentation.py` 側のハンドラだけを配信する
  （admin.py 側は後方互換テスト用の残置）。

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
表中の「TEACHER」は `_require_teacher`（= TEACHER または SYSTEM_ADMIN）、
「SYSTEM_ADMIN」は `_require_system_admin` を指す。「本人のみ」は JWT の user_id で行レベルに絞ることを指す。

---

## 3. エンドポイント一覧

### 共通

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/healthz` | 公開 | ヘルスチェック |

### 認証 `/api/auth`（`routes/auth.py`）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/auth/login` | 公開 | ログイン（JWT 取得） |
| GET | `/api/auth/me` | 要ログイン | 現在のユーザー情報 |

### 学習 `/api/learning`（`routes/learning.py`）

全エンドポイントが要ログイン。ロールゲートは無く、権限は「所有 / 受講（`learning_states` 行）/
グループ共有 / 本人スコープ SQL」で決まる。RAG の中身は [RAG チャットフロー](rag-chat.md)、
学習 UI 側は [学習機能](../features/learning.md)。

#### コース CRUD・進捗・受講登録

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/learning/courses` | 要ログイン（`visibility='group'` は当該グループメンバーのみ） | コース作成（バックグラウンドでコース内容生成を起動） |
| GET | `/api/learning/courses` | 要ログイン | コース一覧（所有 + 受講中 + 公開テンプレ + グループ共有を重複排除） |
| GET | `/api/learning/courses/{cid}` | 所有者 or 受講者（純 viewer は V層発行版スナップショット） | コース詳細（マスター教材 + 個人レイヤーの分離形式） |
| GET | `/api/learning/courses/{cid}/version-notice` | 所有者 or 受講者（fail-open） | 削除予約など版ライフサイクルの一行通知（V層バナー用） |
| PUT | `/api/learning/courses/{cid}` | 所有者 or editor | コースの指定フィールドのみ部分更新 |
| DELETE | `/api/learning/courses/{cid}` | 所有者のみ | コース削除（object_group_permissions 孤児行も明示削除） |
| GET | `/api/learning/courses/{cid}/progress` | 所有者 or 受講者 | 学習進捗の計算 |
| POST | `/api/learning/courses/{cid}/enroll` | 公開テンプレ / group 可視 + メンバー / コース共有 viewer・editor | 受講登録（コースは複製せず `learning_states` に1行 INSERT。migration 011 でクローン方式は廃止済み） |

#### トピック教材・確認問題・チャット

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/topics/{tid}/material` | 所有者 or 受講者 | トピック教材（`student_material` 最優先 → content/summary → PDF チャンク） |
| POST | `/api/learning/courses/{cid}/topics/{tid}/check` | 所有者 or 受講者 | 確認問題の LLM 採点（不合格は誤解記録、合格はトピック完了を永続化） |
| POST | `/api/learning/courses/{cid}/topics/{tid}/chat` | 所有者 or 受講者 | RAG チャット（意図分類・casual モード・書き直し `replace_message_id`・tier/grounding 判定・tension/anchor 痕跡記録を内包） |
| GET | `/api/learning/courses/{cid}/topics/{tid}/chat` | 本人の履歴のみ | チャット履歴取得 |
| DELETE | `/api/learning/courses/{cid}/topics/{tid}/chat` | 所有者 or 受講者（本人の行のみ） | 本人のトピック別チャット履歴を全削除 |
| DELETE | `/api/learning/courses/{cid}/topics/{tid}/chat/messages/{mid}` | 所有者 or 受講者（本人の履歴のみ） | 指定メッセージ以降の往復を truncate（派生 interest_traces は `superseded` 化。機能3） |
| GET | `/api/learning/courses/{cid}/source-chunk/{chunk_id}` | 所有者 or 受講者 **かつ chunk の document が当該コースの source** | 出典ポップアップ用のチャンク本文・数式・出典名（音声会話の教材パネルにも使用）。スコープは `list_course_source_document_ids(course_data)` を `get_chunk_passage(..., allowed_document_ids=)` の SQL 内 `ANY(...)` で強制。コース非アクセス・コース source 外・不明 chunk はすべて同一 404 |

#### 問いの軌跡（interest_traces）・地図導線

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/interest-traces` | 本人のみ | 問いの軌跡一覧（`map_excluded` フラグ付き。`topic_id` で絞り込み可） |
| POST | `/api/learning/courses/{cid}/interest-traces/{tid}/resolve` | 本人のみ | 痕跡を resolved にする |
| POST | `/api/learning/courses/{cid}/interest-traces/{tid}/internalize` | 本人のみ | 「なぜ自分に重要か」を payload に保存 |
| POST | `/api/learning/courses/{cid}/atlas/path-decision` | 要ログイン（本人名義で記録） | 学習パス提案カードの選択（proceed/edit/dismiss/connect）を痕跡に記録 |
| POST | `/api/learning/traces/{trace_id}/map-exclude` | 本人の行のみ | 個人知識ネットワーク表示から除外（`payload.map_excluded` のみ。status・行は触らない） |
| POST | `/api/learning/traces/{trace_id}/map-restore` | 本人の行のみ | 表示へ戻す |

#### 違和感（tension、B層）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/tension/digest` | 本人のみ | tension 候補（confidence≥0.55・最大3件・数値は返さない）+ 遅延マイニング起動 |
| POST | `/api/learning/tension/{trace_id}/confirm` | 本人の candidate 行のみ | 候補を本人が確定（→ open / `learner_text` 付きは articulated） |
| POST | `/api/learning/tension/{trace_id}/dismiss` | 本人の candidate 行のみ | 却下（dismissed 遷移。行は保持） |
| POST | `/api/learning/tension/{trace_id}/connect` | 本人の open/articulated 行のみ + 接続先 document の閲覧可否を fail-closed 検証 | 確定済み tension をグラフ node/edge に接続（`connected_refs` に記録） |

#### 構造帰属型の問い（structure_anchor）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/anchors/digest` | 本人のみ | llm_candidate の帰属候補（最大3件・数値なし）+ 遅延マイニング起動 |
| POST | `/api/learning/anchors/{trace_id}/confirm` | 本人の行のみ | 帰属の確定/訂正（未生成なら segment 縮退アンカーを新規作成） |
| POST | `/api/learning/anchors/{trace_id}/dismiss` | 本人の llm_candidate 帰属のみ | `structure_anchor.status='dismissed'`（問い自体は保持） |

#### ハンズフリー音声会話・C層学習者向け

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/learning/voice/transcribe` | 要ログイン | multipart 音声の文字起こし（Whisper 系。10MB 上限、openai プロバイダ以外 503） |
| POST | `/api/learning/voice/speak` | 要ログイン | テキスト整形（LaTeX/markdown/出典マーカー除去）→ TTS で MP3(base64) |
| GET | `/api/learning/courses/{cid}/components/{comp_id}/explanations` | コース閲覧権限 | 承認済み（teacher_approved）説明バージョン一覧（段階ラベルのみ・数値スコアなし） |

### レクチャーモード `/api/learning/lecture`（`routes/lecture.py`）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/lecture/courses/{cid}/topics/{tid}/sequence` | 受講者（コース可視性） | レクチャーシーケンス構築（トピック教材 `_lecture_uses_topic_material` 優先、無ければ PDF チャンク経路） |
| POST | `/api/learning/lecture/courses/{cid}/topics/{tid}/tts` | 受講者 | **キャッシュ済み** TTS 音声の配信のみ（未生成は 404。生成は管理側バッチ限定。`topic:{tid}` 形式はトピック音声キャッシュから） |
| GET | `/api/learning/lecture/courses/{cid}/topics/{tid}/audio-status` | 受講者 | 再生可能な音声の有無を軽量判定（レクチャーボタン活性用。生成は行わない） |
| POST | `/api/learning/lecture/courses/{cid}/topics/{tid}/interrupt` | 受講者 | レクチャー一時停止中の質問チャット（現在チャンクをコンテキストに回答） |

### 分野の地図 — 学習者向け（`routes/atlas.py` learning_router / report_router、`routes/atlas_view.py`）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/atlas/runtime-config` | 公開 | atlas-data.js のデータソース既定（api / fixture）を返す |
| GET | `/api/atlas` | 要ログイン | 地図データ一式（骨格+キャッシュ+個人層。`course`/`topic`/`focus` をサーバ側で解決。骨格なしは 404） |
| GET | `/api/atlas/node/{node_id}` | 要ログイン | 詳細パネル用のノード情報 |
| GET | `/api/learning/atlas/{cartridge_id}/skeleton` | 要ログイン | 学習者向け骨格（凍結・レビュー済み版のみ。draft のみの domain は 404） |
| GET | `/api/learning/atlas/cues/state` | 要ログイン | 導線の永続状態（first_login_seen。DB 不通時は seen=True の fail-closed） |
| POST | `/api/learning/atlas/cues/events` | 要ログイン | 導線イベント（shown/opened/dwell/learn_reached）の内部計測記録 |
| POST | `/api/atlas/report` | 要ログイン | 地図上からの修正報告を帰属つきで記録（教員レビューキューへ） |
| GET | `/api/atlas/reports/mine` | 本人のみ | 自分の報告一覧（`unacked=true` で未読の処理結果のみ） |
| POST | `/api/atlas/reports/{report_id}/ack` | 報告者本人 | 採用/見送り結果の既読化 |

### D層 — 学習者向け読み取り（`routes/doubt.py` learning_router、D3-6）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/ledger/{target_type}/{target_id}` | 要ログイン + コースアクセスゲート | 台帳の正直表示（検証状態ラベル・スコープ4軸・事実文のみ。記帳者 ID・疑義・生スコアは返さない。台帳なしは 404 の fail-closed） |
| GET | `/api/learning/courses/{cid}/open-assumptions` | 要ログイン + コースアクセスゲート | 未検証合意リストの閲覧（疑義者名を含めない読み取り専用版） |

### 再構成ループ — 学習者向け（`routes/reconstruction.py` learning_router、R層）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/topics/{tid}/reconstruction/next` | 受講者本人 | 次の出題を返す（expected 等の伏せフィールドは返さない） |
| POST | `/api/learning/reconstruction/{item_id}/submit` | 本人 | 応答保存 → 非LLM DIFF → verdict + 出典リビール |
| POST | `/api/learning/reconstruction/{item_id}/revise` | 本人 | 再挑戦（改訂履歴 `revision_of` でつなぐ） |
| POST | `/api/learning/reconstruction/{recon_id}/self-check` | 本人 | 自己確認（agreed / disagreed / verdict_wrong） |
| POST | `/api/learning/reconstruction/{recon_id}/descend` | 本人 | 記号葉（SymbolRegistry）への降下プローブ |

### 個人知識ネットワーク（`routes/personal_map.py`、読み取り専用）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/personal-network` | 本人のみ | コースビュー（互換 API。`course_id` は provenance + フィルター） |
| GET | `/api/learning/courses/{cid}/personal-network/journey?node_id=` | 本人のみ | コーススコープの旅（当該コース sources 内限定 + cross_course_hint） |
| GET | `/api/me/personal-network` | 本人のみ | 正本 API（本人所有の全痕跡由来。`include_candidate_links=true` は 422 の fail-closed） |
| GET | `/api/me/personal-network/journey?node_id=` | 本人のみ | コース横断の旅（hop ごとに can_view_document で fail-closed フィルタ） |

> このルーターは**読み取り専用**（書き込み API を作らないことをガードレールで固定）。
> 訂正操作（map-exclude / map-restore）は `routes/learning.py` 側にある。

### グループ `/api/groups`・`/api/me`（`routes/groups.py`）

グループ内ロールは `admin`（作成者ほか）/ `member`。下表の「グループ admin」はグループ内ロールを指し、アプリロールとは別。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/groups` | 要ログイン | グループ作成（作成者がグループ admin になる） |
| GET | `/api/groups` | 要ログイン | 自分が所属するグループ一覧 |
| GET | `/api/groups/{gid}` | グループ member | グループ詳細（招待コードは admin のみ表示） |
| PUT | `/api/groups/{gid}` | グループ admin | 名前・説明の更新 |
| DELETE | `/api/groups/{gid}` | グループ admin | グループ削除（members / invitations は CASCADE） |
| POST | `/api/groups/{gid}/invite-code/rotate` | グループ admin | 招待コードの再発行 |
| POST | `/api/groups/{gid}/members` | グループ admin | ユーザーを直接招待（username / email 指定） |
| DELETE | `/api/groups/{gid}/members/{uid}` | admin または本人 | 退会/除名（非 admin は自分自身のみ） |
| POST | `/api/groups/join-by-code` | 要ログイン | 招待コードで参加 |
| GET | `/api/groups/{gid}/invitations` | グループ admin | グループの招待一覧 |
| GET | `/api/me/invitations` | 本人 | 自分宛ての pending 招待一覧 |
| POST | `/api/me/invitations/{inv}/accept` | 本人 | 招待を承諾 |
| POST | `/api/me/invitations/{inv}/decline` | 本人 | 招待を辞退 |

---

### 管理 `/api/admin`（`routes/admin.py`）

教材・コース・スキーマ・ユーザー管理の中核ルーター（43 エンドポイント）。
手順の正本: [admin_operations/materials.md](../admin_operations/materials.md) /
[admin_operations/course.md](../admin_operations/course.md) /
[admin_operations/users.md](../admin_operations/users.md)（グループ管理） /
[admin_operations/students.md](../admin_operations/students.md) /
[admin_operations/teachers.md](../admin_operations/teachers.md) /
[admin_operations/system.md](../admin_operations/system.md)。

#### 教材管理・タスク

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/materials/upload` | TEACHER | PDF/TeX を MinIO へ保存しバックグラウンドで解析パイプライン起動、task_id を即時返却（`analyze_images` で装置図解析をオプトイン） |
| POST | `/api/admin/documents/{id}/reanalyze` | TEACHER + document 編集権（所有者 / document editor / SYSTEM_ADMIN） | 保存済み PDF を Agent パイプラインで再解析（`analyze_images` 未指定時は前回 run の options を継承）。認可は MinIO 取得・background task 起動より前。閲覧のみ（public / viewer / コース経由）と不明 ID は同一 404 |
| GET | `/api/admin/materials` | TEACHER（自分 + public + group/document 共有 + コース参照） | 教材一覧（status は projector 正本。`?include=summary` でサマリ付加） |
| GET | `/api/admin/materials/{id}` | TEACHER（一覧と同じポリシー） | 教材詳細（ナレッジグラフ含む） |
| GET | `/api/admin/materials/{id}/pdf` | TEACHER（閲覧権） | 教材 PDF を MinIO からプロキシ配信 |
| PUT | `/api/admin/materials/{id}/pdf` | TEACHER + document 編集権（所有者 / document editor / SYSTEM_ADMIN） | PDF の再登録（テキスト類似度 <0.3 は 409。欠落ページ情報を推定補完）。認可はファイル読取・PDF パース・MinIO upload より前。閲覧のみ（public / viewer / コース経由）と不明 material は同一 404 |
| PUT | `/api/admin/materials/{id}/visibility` | 教材所有者のみ | 開示範囲（public/group/private）を更新 |
| DELETE | `/api/admin/materials/{id}` | 教材所有者 + `confirm_name` 一致必須 | 教材削除（参照コース・チャンク・W層孤児行も削除、V層 teardown 通知） |
| GET | `/api/admin/tasks/{task_id}` | TEACHER | バックグラウンドタスクのステータス（ポーリング用） |

#### コースビルダー

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/course-builder/chat` | TEACHER | 選択教材の theory_components 等を注入して AI とコース設計対話、course_draft JSON を抽出 |
| POST | `/api/admin/course-builder/sessions` | TEACHER | セッション新規作成 |
| GET | `/api/admin/course-builder/sessions` | TEACHER（本人のみ） | セッション一覧（更新日時降順） |
| GET | `/api/admin/course-builder/sessions/{sid}` | TEACHER（本人のみ） | セッション詳細（チャット履歴・course_draft） |
| PUT | `/api/admin/course-builder/sessions/{sid}` | TEACHER（本人のみ） | タイトル・履歴・draft・status の更新 |

#### コース管理・公開・グループ共有

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/courses` | TEACHER（所有 + group 共有 editor/viewer） | 管理可能なコース一覧（role・公開状態・atlas binding 状況付き） |
| GET | `/api/admin/courses/{cid}/draft-format` | 所有者 or editor | 登録済みコースを course_draft 形式へ変換（ビルダー再インポート用） |
| PUT | `/api/admin/courses/{cid}/visibility` | コース所有者のみ | 開示範囲更新（`is_published` を実態と同期） |
| DELETE | `/api/admin/courses/{cid}` | 所有者 or editor + `confirm_name` 一致必須 | コース削除（チャット履歴・group 権限行も明示削除、V層 teardown 通知） |
| GET | `/api/admin/courses/{cid}/unanswered-queries` | TEACHER + コース編集権（所有者 / course editor / SYSTEM_ADMIN） | コースの RAG 未回答クエリ（最大200件・学生表示名を含む）。認可は SQL 実行より前。権限なし・不明コースは空配列ではなく同一 404 |
| GET | `/api/admin/courses/{cid}/groups` | 閲覧可能な者 | コースのグループ権限一覧 |
| POST | `/api/admin/courses/{cid}/groups` | コース所有者のみ | グループ権限（viewer/editor）の付与・更新 |
| DELETE | `/api/admin/courses/{cid}/groups/{gid}` | コース所有者のみ | グループ権限の削除 |

#### ドキュメント × グループ共有（パイプライン成果共有、migration 044）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/documents/{id}/groups` | 閲覧可能な者 | ドキュメントのグループ共有設定一覧 |
| POST | `/api/admin/documents/{id}/groups` | ドキュメント所有者のみ | 解析成果のグループ共有（viewer/editor。`document_share` 監査記録） |
| DELETE | `/api/admin/documents/{id}/groups/{gid}` | ドキュメント所有者のみ | グループ共有の解除（監査記録あり） |

#### 図画像配信（L層、migration 041）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/documents/{id}/figures/{fid}/image` | TEACHER + document 閲覧権 | 図画像本体（PNG）を MinIO `figure-images` から配信 |

※ 図の**一覧** `GET /documents/{id}/figures` は admin.py にも関数定義が残るが非配信
（main.py がルート除去。配信される正本は下記 figure_presentation 節）。

#### ユーザー管理・システム統計

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/users/student` | TEACHER | 学生（learner）アカウント作成（重複 409） |
| POST | `/api/admin/users/teacher` | SYSTEM_ADMIN | 教員（instructor）アカウント作成 |
| GET | `/api/admin/system/materials-stats` | SYSTEM_ADMIN | 全コースのパイプライン進捗・受講者数・チャット数 |

#### スキーマ進化（[動的スキーマ進化](../pipeline/schema-evolution.md)）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/schema/types` | TEACHER | 登録済み OntologyType 一覧 |
| POST | `/api/admin/schema/types` | TEACHER | OntologyType 追加 |
| GET | `/api/admin/schema/predicates` | TEACHER | 登録済み CorePredicate 一覧 |
| POST | `/api/admin/schema/predicates` | TEACHER | CorePredicate 追加 |
| GET | `/api/admin/schema-proposals` | TEACHER | スキーマ拡張提案一覧（`?status=` フィルタ可） |
| POST | `/api/admin/schema-proposals/analyze` | TEACHER | 未回答クエリの LLM 分析 → 提案生成 |
| POST | `/api/admin/schema-proposals/{pid}/simulate` | TEACHER | Shadow Testing（Target/Similar/Control への試験適用と差分） |
| PUT | `/api/admin/schema-proposals/{pid}/approve` | TEACHER | 提案承認 → Type/Predicate 登録 + 再抽出ジョブのエンキュー |
| PUT | `/api/admin/schema-proposals/{pid}/approve-with-scope` | TEACHER | スコープ付き承認（full / canary=指定コースのみ） |
| PUT | `/api/admin/schema-proposals/{pid}/reject` | TEACHER | 提案却下 |
| GET | `/api/admin/reextraction-jobs` | TEACHER | 再抽出ジョブ一覧 |

#### 教員向け集約ダッシュボード（B層 / 個人知識ネットワーク Phase B）

手順の正本: [admin_operations/interest_dashboard.md](../admin_operations/interest_dashboard.md)。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/interest-dashboard` | TEACHER | interest_traces の集団集計（件数・比率・関与人数のみ。個人特定情報なし） |
| GET | `/api/admin/courses/{cid}/bridge-insights` | TEACHER + コース編集権（所有者 / course editor / SYSTEM_ADMIN） | 学習者が connect した橋候補の k-匿名集約（k=3・人数レンジ表示）。認可は `aggregate_bridge_candidates()` より前（権限のない教員へ集約の存在・空非空を開示しない）。権限なし・不明コースは同一 404 |

### 図の表示分類 `/api/admin`（`routes/figure_presentation.py`、#496）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/documents/{id}/figures` | TEACHER + document 閲覧権 | 図一覧 + 分類（機能構成図/データグラフ/解説画像）・装置候補・bbox・`viewer_is_owner` を返す（admin.py の旧ハンドラを置換） |
| PATCH | `/api/admin/documents/{id}/figures/{fid}/presentation-mode` | TEACHER + document 編集権 | 教員による表示モード上書き（`null` で解除し提案値に復帰。監査記録あり） |
| POST | `/api/admin/documents/{id}/figures/{fid}/reanalyze` | TEACHER + document 編集権 | 教員指示付き図再解析（`hint_text` / `focus_bbox`。AI 候補生成のみで自動確定しない） |

### エラーログ（`routes/error_logs.py`）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/error-logs` | SYSTEM_ADMIN | 直近ログ一覧（keyword / minutes / limit / include_info で絞り込み。5xx は middleware が記録） |

### 原稿スタジオ（`routes/lecture_studio/` パッケージ、Tier 3-17a）

`scripts.py` / `pipeline.py` / `topics.py` の3ルーター（いずれも自前 prefix なし）を `__init__.py` が束ね、
`/api/admin` にマウント。全エンドポイント TEACHER。コース単位の追加ゲートは
`get_viewable_course_data`（閲覧）/ `get_editable_course_data`（所有者 + editor グループ）。
手順の正本: [admin_operations/lecture_studio.md](../admin_operations/lecture_studio.md)。

#### scripts.py — 設定・原稿生成・音声生成

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/courses/{cid}/lecture-studio/settings` | TEACHER + コース閲覧 | コース単位設定（口調ペルソナ + 読み上げ言語）の取得 |
| PUT | `/api/admin/courses/{cid}/lecture-studio/settings` | TEACHER + コース編集 | 設定保存（変更時は原稿再生成フラグ。language 省略は変更しない） |
| POST | `/api/admin/courses/{cid}/lecture-scripts/generate` | TEACHER + コース編集 | 全チャンクの display/spoken_text・数式を非同期一括生成（202。`auto_audio` で音声チェーン可。チャンク0件は 422） |
| GET | `/api/admin/courses/{cid}/lecture-scripts` | TEACHER + コース閲覧 | チャンク原稿一覧（スライド数・音声準備状況付き） |
| POST | `/api/admin/lecture-studio/preview-split` | TEACHER | スライド分割プレビュー（配信側と同一の `core/lecture.py::split_slides`。DB 非変更） |
| PUT | `/api/admin/chunks/{chunk_id}/lecture-script` | TEACHER | 教員編集の原稿・数式を保存し当該チャンクの音声キャッシュを無効化 |
| POST | `/api/admin/chunks/{chunk_id}/lecture-script/rewrite` | TEACHER | 教員指示に基づく LLM 原稿書き換え |
| POST | `/api/admin/courses/{cid}/lecture-audio/generate` | TEACHER + コース編集 | スライド単位 TTS 音声の非同期一括生成（202。空 spoken_text ありは 422、言語切替チェーン時は免除） |
| GET | `/api/admin/chunks/{chunk_id}/lecture-audio` | TEACHER | スライド単位音声の試聴配信（キャッシュのみ・未生成 404） |

#### pipeline.py — Agent Pipeline・再解析・コース内容生成

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/courses/{cid}/structure/reanalyze` | TEACHER + コース編集 | 既存チャンク維持のまま構造 DSL/変数を非同期再解析（進行中 409） |
| POST | `/api/admin/courses/{cid}/document-pipeline/run` | TEACHER + コース編集 | コース配下全教材の Agent Pipeline 起動（target_stage/start_stage 指定可） |
| GET | `/api/admin/materials/{mid}/document-pipeline/status` | TEACHER + アップロード者本人 or SYSTEM_ADMIN | 教材単位のパイプライン実行状態（stage 別・縮退情報・進行中タスク） |
| POST | `/api/admin/materials/{mid}/document-pipeline/run` | TEACHER + アップロード者本人 or SYSTEM_ADMIN | 教材単位の Agent Pipeline 起動（stage 単独再実行可。進行中 409） |
| POST | `/api/admin/courses/{cid}/course-content/generate` | TEACHER + コース編集 | パイプライン成果物からコース内容（章・トピック）を非同期再生成 |
| GET | `/api/admin/courses/{cid}/tasks/active` | TEACHER + コース閲覧 | 進行中バックグラウンドタスク最新1件（ポーリング再開用） |

#### topics.py — 章・トピック構造・授業用ドラフト

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/courses/{cid}/lecture-studio/course-structure` | TEACHER + コース閲覧 | 章・トピック構造（原稿/音声生成ステータス集計付き） |
| PUT | `/api/admin/courses/{cid}/lecture-studio/course-topics/{tid}` | TEACHER + コース所有者本人 or SYSTEM_ADMIN | トピックの授業用ドラフト（教材・読み上げ原稿・確認問題）保存 + トピック音声キャッシュ無効化 |
| POST | `/api/admin/courses/{cid}/lecture-studio/course-topics/{tid}/draft/rewrite` | TEACHER + コース閲覧 | LLM によるドラフト案生成（保存は別 PUT。DB 非変更） |
| GET | `/api/admin/courses/{cid}/lecture-studio/document-structure` | TEACHER + コース閲覧 | コース教材の文書構造（Agent 復元構造優先） |
| GET | `/api/admin/courses/{cid}/lecture-studio/components` | TEACHER + コース閲覧 | 理論コンポーネント一覧・依存グラフ・解析ステータスの集約 |

### 理論コンポーネント + 承認・共有レイヤー（`routes/theory_components.py`、C層）

全エンドポイント TEACHER。document 単位は `_ensure_document_viewable/editable`
（所有者 / public / group 共有 / document 共有 / 閲覧可コース経由。SYSTEM_ADMIN は無条件）、
コース単位は course の閲覧/編集ゲート。詳細は [承認・共有レイヤー](../features/endorsement-sharing.md)。

#### A層成果の閲覧（structure / claims / component-graph）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/documents/{id}/structure` | TEACHER + document 閲覧権 | チャンクからセクション構造を構築して返す |
| GET | `/api/admin/documents/{id}/chunks/{chunk_id}/claims` | TEACHER + document 閲覧権 | チャンク単位の theory_claims（0件ならドキュメント全体にフォールバック） |
| GET | `/api/admin/documents/{id}/sections/{sid}/components` | TEACHER + document 閲覧権 | セクション単位の theory_components（0件ならドキュメント全体にフォールバック） |
| GET | `/api/admin/documents/{id}/component-graph` | TEACHER + document 閲覧権 | 保存済み TheoryOperationGraph の正規化返却（無ければ決定論的に構築） |
| PATCH | `/api/admin/claims/{claim_id}` | TEACHER + document 編集権 | claim の全項目更新（review_status 遷移は監査、rejected は伝播、承認時は R層 item オーサリングを非同期起動） |

#### 理論コンポーネント CRUD（コース単位）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/courses/{cid}/theory-components` | TEACHER + コース閲覧 | コンポーネント一覧（`?chunk_id` 絞込可。0件時は chunk→document→sources の順にフォールバック） |
| POST | `/api/admin/chunks/{chunk_id}/theory-components/extract` | TEACHER + 当該教材を含む編集可能コース | SMILES DSL からの構造抽出（`use_llm` で LLM 補強）を candidate として upsert |
| POST | `/api/admin/courses/{cid}/theory-components` | TEACHER + コース編集 | コンポーネント手動作成（重複候補を自動付与） |
| PUT | `/api/admin/theory-components/{comp_id}` | TEACHER + コース編集 | 全項目更新（review_status 遷移は監査・rejected 伝播） |
| POST | `/api/admin/theory-components/{comp_id}/reject` | TEACHER + コース編集 | rejected 遷移（行削除しない）+ グラフエッジ無効化 |
| POST | `/api/admin/courses/{cid}/theory-components/validate-connection` | TEACHER + コース閲覧 | 2コンポーネント間の接続妥当性を非LLM検証（warnings を返す） |

#### C層: 説明バージョン・承認・引用

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/theory-components/{comp_id}/explanations` | TEACHER + コース閲覧 | 説明バージョン一覧（standard 無ければ summary から遅延生成） |
| POST | `/api/admin/theory-components/{comp_id}/explanations` | TEACHER + コース閲覧 | `kind='personal'` の独自解釈を作成（監査記録） |
| PATCH | `/api/admin/explanations/{eid}` | 作者本人 or SYSTEM_ADMIN | title/body/backing_claims/shared/review_status の部分更新（backing_claims の確定/却下も監査） |
| POST | `/api/admin/explanations/{eid}/endorse` | TEACHER + コース閲覧 | 承認を upsert（level=provisional/endorsed/strong。再承認で revoked 解除） |
| DELETE | `/api/admin/explanations/{eid}/endorse` | TEACHER（自分の承認行のみ） | 承認取り消し（`revoked=TRUE`。行削除せず履歴保持） |
| GET | `/api/admin/explanations/{eid}/endorsements` | TEACHER + コース閲覧 | 有効な承認一覧 + 集計 + 段階ラベル |
| POST | `/api/admin/explanations/{eid}/cite` | TEACHER + shared or 自分の説明 + 引用先コース編集権 | 説明を自コースへ帰属付き引用（V層の版固定 + auto-pin を best-effort 実行） |
| GET | `/api/admin/courses/{cid}/sharing-dashboard` | TEACHER + コース閲覧 | 承認・共有・引用の集団集計（個人追跡ではない） |
| POST | `/api/admin/theory-components/candidates/from-query` | TEACHER + コース編集 | 質問 + AI 回答からコンポーネント候補を生成（candidate + `confirmed=False` の backing_claims。確定は教員のみ） |

### カートリッジ `/api/admin/cartridges`（`routes/cartridges.py`）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/cartridges` | TEACHER | 利用可能カートリッジのサマリ一覧 |
| GET | `/api/admin/cartridges/{id}` | TEACHER | カートリッジ全体（manifest + ontology + 各種定義） |
| GET | `/api/admin/cartridges/{id}/ontology` | TEACHER | ontology 部分のみ |
| GET | `/api/admin/cartridges/{id}/component-types` | TEACHER | component type 語彙 |
| GET | `/api/admin/cartridges/{id}/relation-types` | TEACHER | relation type 語彙 |
| GET | `/api/admin/cartridges/{id}/maturity-levels` | TEACHER | 成熟度レベル定義 |
| GET | `/api/admin/cartridges/{id}/support-statuses` | TEACHER | サポートステータス定義 |

### リビジョン（`routes/revisions.py`、マイグレーション 019）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/documents/{id}/revisions` | TEACHER | ドキュメントのリビジョン作成 |
| POST | `/api/admin/documents/{id}/revisions/{rid}/run` | TEACHER | リビジョンの再解析実行（非同期） |
| GET | `/api/admin/documents/{id}/revisions/{rid}/run-status` | TEACHER | 実行状態の取得 |
| GET | `/api/admin/documents/{id}/revisions` | TEACHER | リビジョン一覧 |
| GET | `/api/admin/documents/{id}/revisions/{rid}` | TEACHER | 単一リビジョン取得 |
| GET | `/api/admin/documents/{id}/revisions/{rid}/report` | TEACHER | 差分レポート取得 |
| POST | `/api/admin/documents/{id}/revisions/{rid}/accept` | TEACHER | 受理 |
| POST | `/api/admin/documents/{id}/revisions/{rid}/reject` | TEACHER | 棄却 |
| POST | `/api/admin/documents/{id}/revisions/{rid}/revise` | TEACHER | 差し戻し（再修正） |

### 分野の地図 — 管理（`routes/atlas.py` router / admin_atlas_router / binding_router）

手順の正本: [admin_operations/atlas.md](../admin_operations/atlas.md)。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/atlas/domains` | TEACHER | 骨格を持つ（または draft 中の）domain 一覧（migration 027） |
| GET | `/api/admin/cartridges/{cid}/atlas/skeleton` | TEACHER | 骨格のレビュー状態（draft / 凍結済み。DB が正本） |
| POST | `/api/admin/cartridges/{cid}/atlas/skeleton/generate` | TEACHER | 骨格 draft の LLM バッチ生成（再実行は `force` 明示。カートリッジ無し新分野は `body.domain` で可） |
| PUT | `/api/admin/cartridges/{cid}/atlas/skeleton/draft` | TEACHER | draft 保存（`revision` 楽観ロック、衝突 409） |
| POST | `/api/admin/cartridges/{cid}/atlas/skeleton/freeze` | TEACHER | draft の凍結・版付与 |
| POST | `/api/admin/cartridges/{cid}/atlas/skeleton/assist/interpret` | TEACHER | AI アシスト: 教員の発言を対象・要望に解釈（まだ編集しない） |
| POST | `/api/admin/cartridges/{cid}/atlas/skeleton/assist/propose` | TEACHER | AI アシスト: 確定済み解釈から編集案（JSON Patch）生成（draft は書き換えない） |
| GET | `/api/admin/cartridges/{cid}/atlas/reports` | TEACHER | 修正報告のレビューキュー |
| POST | `/api/admin/cartridges/{cid}/atlas/reports/{rid}/resolve` | TEACHER | 報告の採用/見送り確定（報告者へ結果通知） |
| POST | `/api/admin/cartridges/{cid}/atlas/reports/{rid}/incorporate` | TEACHER | 報告内容の骨格 draft への取り込み |
| POST | `/api/admin/cartridges/{cid}/atlas/overlay/refresh` | TEACHER | `atlas_overlay_cache` の状態導出バッチを明示実行 |
| POST | `/api/admin/courses/{cid}/atlas-binding/propose` | TEACHER | コース→地図配置の決定論的提案（LLM 不使用。教員が保存するまで確定しない） |
| PUT | `/api/admin/courses/{cid}/atlas-binding` | TEACHER | 承認済みバインディング保存（`cartridge_id` + `topics[].atlas_node_id`。監査記録あり） |

### D層 — 管理 `/api/admin/doubt`（`routes/doubt.py` admin_router）

全27本が TEACHER（metrics のみ SYSTEM_ADMIN）。withdraw（疑義者本人）と反実仮想 PATCH（作成者本人）を除き、
course_id / target_id への所有・共有チェックは行わない（ロールゲートのみ）。
手順の正本: [admin_operations/doubt.md](../admin_operations/doubt.md)。

#### 台帳（Epistemic Ledger）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/doubt/ledger/{ttype}/{tid}` | TEACHER | 台帳行 + 検証スコープ + LLM 候補 + 合意2軸 + 負荷段階 + 疑義一覧（無ければ 404） |
| POST | `/api/admin/doubt/ledger/{ttype}/{tid}/scopes` | TEACHER | 検証スコープの記帳（4軸のうち1つ以上 + 根拠 + reason 必須。監査記録） |
| PATCH | `/api/admin/doubt/ledger/{ttype}/{tid}/scopes/{sid}` | TEACHER | 既存スコープの訂正（旧値は監査イベントに保持） |
| PUT | `/api/admin/doubt/ledger/{ttype}/{tid}/verification-status` | TEACHER | 検証状態の変更（`directly_verified` 昇格はスコープ1件以上必須 = 全称検証の構造的禁止） |
| POST | `/api/admin/doubt/ledger/{ttype}/{tid}/scope-candidates/{cid}/confirm` | TEACHER | LLM スコープ候補の確定（教員の帰属で本体へ転記。候補行は保持） |
| POST | `/api/admin/doubt/ledger/{ttype}/{tid}/scope-candidates/{cid}/dismiss` | TEACHER | LLM スコープ候補の却下（`dismissed` で保持） |
| GET | `/api/admin/doubt/courses/{cid}/ledger-summary` | TEACHER | コース単位の台帳サマリ（スコープあり/空欄の件数を事実として返す） |
| POST | `/api/admin/doubt/courses/{cid}/scope-candidates/refresh` | TEACHER | スコープ候補生成の非同期スケジュール（同期パスに LLM を入れない） |

#### 素朴な問い・負荷度・暗黙前提

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/doubt/courses/{cid}/naive-signals` | TEACHER | anchor 単位の k-匿名集計（k=3・n<3 非表示・件数レンジのみ） |
| POST | `/api/admin/doubt/courses/{cid}/load/recompute` | TEACHER | 負荷度の決定論的バッチ再計算（生スコアは返さない） |
| GET | `/api/admin/doubt/courses/{cid}/assumptions` | TEACHER | 前提一覧（status フィルタ + 学習者シグナル有無を合成） |
| POST | `/api/admin/doubt/assumptions` | TEACHER | 前提の手動登録（即 confirmed、台帳行を `untested`・スコープ空欄で自動生成） |
| POST | `/api/admin/doubt/assumptions/{aid}/confirm` | TEACHER | candidate 前提の確定（reason 必須。台帳行を自動生成） |
| POST | `/api/admin/doubt/assumptions/{aid}/dismiss` | TEACHER | 前提候補の却下（`dismissed` 遷移で保持） |
| POST | `/api/admin/doubt/courses/{cid}/assumption-mining/run` | TEACHER | 経路A（導出の隙間の反復）マイニングの非同期スケジュール |
| POST | `/api/admin/doubt/courses/{cid}/corpus-audit/run` | TEACHER | 経路B（コーパス横断監査）の同期実行（非LLM） |
| GET | `/api/admin/doubt/courses/{cid}/assumption-atlas` | TEACHER | 前提の地図（負荷度×検証度の散布データ。生スコア・評価語なし） |

#### 疑義・検証提案・反実仮想・KPI

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/doubt/targets/{ttype}/{tid}/challenges` | TEACHER | 疑義の作成（assumption/claim のみ。challenge_type + reason 必須・匿名不可・監査記録） |
| GET | `/api/admin/doubt/targets/{ttype}/{tid}/challenges` | TEACHER | 対象への疑義一覧（数値スコア化しない） |
| POST | `/api/admin/doubt/challenges/{chid}/withdraw` | 疑義者本人のみ | 疑義の取り下げ（`withdrawn` 遷移で履歴保持） |
| POST | `/api/admin/doubt/challenges/{chid}/proposals` | TEACHER | 疑義 → 検証提案への昇格（元 challenge を `led_to_verification` に遷移） |
| GET | `/api/admin/doubt/courses/{cid}/open-assumptions` | TEACHER | 未検証合意リスト（台帳の自動編纂・編集不可。教員版=疑義者名あり） |
| POST | `/api/admin/doubt/counterfactual/compute` | TEACHER | 保存なしの反実仮想試算（collapsed/surviving/indeterminate の決定論的伝播） |
| POST | `/api/admin/doubt/counterfactual/sessions` | TEACHER | 反実仮想セッションの保存（shared_scope 既定 private） |
| GET | `/api/admin/doubt/counterfactual/sessions` | TEACHER（自分所有 + public + 所属グループ共有のみ） | セッション一覧（`course_id` 絞り込み可） |
| PATCH | `/api/admin/doubt/counterfactual/sessions/{sid}` | 作成者本人のみ | notes・共有範囲の変更（scope 変更時のみ監査記録） |
| GET | `/api/admin/doubt/metrics` | SYSTEM_ADMIN | 運用判断用の内部 KPI（ダッシュボード UI は作らない前提） |

### W層 — 要素検討ワークスペース `/api/admin/deliberation`（`routes/deliberation.py`）

全15本が TEACHER。document-scoped 要素は `_ensure_document_viewable/editable`（404 fail-closed）、
domain-scoped 共通部品（shared_part = L層 `library_entries`）は TEACHER + 由来 document 権限での
route 層フィルタ。詳細は `docs/features/element_deliberation_workspace_design.md`。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/deliberation/elements/{etype}/{eid}/overview` | TEACHER + document 閲覧権（equation は `document_id` クエリ必須） | 面①内訳 + 面②位置づけ4レンズの集約（非LLM・DB 非変更） |
| POST | `/api/admin/deliberation/identity-links` | TEACHER + インスタンス側 document 編集権 | 同一性リンク候補の作成（常に `candidate`。監査記録） |
| POST | `/api/admin/deliberation/identity-links/{lid}/confirm` | TEACHER + document 編集権（決定済みは 409） | 同一性リンクの人間確定 |
| POST | `/api/admin/deliberation/identity-links/{lid}/reject` | TEACHER + document 編集権 | 同一性リンクの却下（status 遷移で保持） |
| GET | `/api/admin/deliberation/elements/{etype}/{eid}/identity-links` | TEACHER + document 閲覧権 | インスタンス要素の同一性リンク一覧（shared_part 指定は 422） |
| GET | `/api/admin/deliberation/shared-parts/{spid}/identity-links` | TEACHER（閲覧不可 document 由来は除外し `hidden_count` で正直に返す） | 共通部品側の同一性リンク一覧 |
| POST | `/api/admin/deliberation/sessions` | TEACHER + document 閲覧権 | 対話的検討セッションの開始 |
| GET | `/api/admin/deliberation/sessions/{sid}` | 作成者本人のみ（他人は 404） | セッションのメッセージ履歴込み取得 |
| POST | `/api/admin/deliberation/sessions/{sid}/messages` | 作成者本人 + document 閲覧権 + コスト上限（超過 429） | 1ターン送信 → LLM 応答 + 候補注釈（1応答=1 LLM コール。figure は vision 添付） |
| GET | `/api/admin/deliberation/elements/{etype}/{eid}/annotations` | TEACHER + document 閲覧権 | 候補/確定/却下注釈の一覧（confidence は段階ラベルのみ） |
| POST | `/api/admin/deliberation/annotations/{aid}/commit` | TEACHER + document 編集権（未対応 kind は 422） | 候補注釈の確定 → 既存構造（C層 explanation / component / identity / standardization 等）へルーティング |
| POST | `/api/admin/deliberation/annotations/{aid}/dismiss` | TEACHER + document 編集権（candidate 以外 409） | 候補注釈の却下（status 遷移で保持） |
| POST | `/api/admin/deliberation/shared-parts/{spid}/standardization/assess` | TEACHER | 共通部品1件の標準化判定（三角測量）を非同期開始（`force` なしで既評価はスキップ） |
| POST | `/api/admin/deliberation/domains/{dkey}/standardization/assess` | TEACHER | domain 内 active 共通部品すべての標準化判定を非同期開始 |
| GET | `/api/admin/deliberation/documents/{id}/elements` | TEACHER + document 閲覧権 | 要素インベントリ: 教材1件分の全検出要素（component/claim/equation/figure）を統一カード形式で返す |

### Admin Copilot + G層 `/api/admin/assistant`（`routes/admin_assistant.py`）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/assistant/chat` | TEACHER | 統合 AI アシスタントチャット（guidance / locate / action / clarify を自動振り分け。1 LLM コール上限） |
| POST | `/api/admin/assistant/actions` | TEACHER | 操作代行の実行（capability registry + ロールで fail-closed。不可逆操作は確認ゲート） |
| POST | `/api/admin/assistant/actions/{action_id}/revert` | TEACHER | 代行操作の取り消し（before スナップショットから復元。`reversible=false` は 409） |
| GET | `/api/admin/assistant/actions` | TEACHER | 代行操作の履歴一覧 |
| GET | `/api/admin/assistant/next-steps` | TEACHER | 状態導出型 To-Do（G層。`{steps, hidden, truncated, assistant_cue_pending}`） |
| POST | `/api/admin/assistant/next-steps/{step_key}/dismiss` | TEACHER | 却下を upsert（行削除しない） |
| POST | `/api/admin/assistant/next-steps/{step_key}/restore` | TEACHER | 却下の取り消し（`revoked` 遷移） |

### 再構成ループ — 管理（`routes/reconstruction.py` admin_router、R層）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/reconstruction/items/review-queue` | TEACHER | 疑わしさランク順の item レビューキュー（document_id / course_id で絞り込み） |
| PATCH | `/api/admin/reconstruction/items/{item_id}` | TEACHER | item の status 遷移（flagged / retired / confirmed）・prompt / expected 修正（削除 API は無い） |
| POST | `/api/admin/reconstruction/documents/{id}/author` | TEACHER | 手動オーサリング（claim → item の LLM 生成バッチ） |
| GET | `/api/admin/documents/{id}/claims/stumble-summary` | TEACHER | claim 単位のつまづきサマリー（k-匿名・レンジ表示） |

### 共有物のバージョン管理 `/api/admin/shared`（`routes/versioning.py`、V層）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/shared/{otype}/{oid}/releases` | TEACHER + 所有者 | 共有版の発行（共有先へ version_published 通知） |
| GET | `/api/admin/shared/{otype}/{oid}/releases` | TEACHER | 版一覧 |
| GET | `/api/admin/shared/releases/{release_id}` | TEACHER | 単一版の取得 |
| GET | `/api/admin/shared/{otype}/{oid}/version-state` | TEACHER | 版状態（更新あり / 削除予定バッジ用） |
| POST | `/api/admin/shared/{otype}/{oid}/deletion` | TEACHER + 所有者 | 削除予約（既定14日猶予。期限後スイーパが物理削除） |
| DELETE | `/api/admin/shared/{otype}/{oid}/deletion` | TEACHER + 所有者 | 削除予約の取り消し |
| POST | `/api/admin/shared/{otype}/{oid}/subscription/adopt` | TEACHER | 版の取り込み（`expected_pinned_release_id` 楽観ロック・不一致 409） |
| GET | `/api/admin/shared/subscription/me` | TEACHER | 本人のピン一覧（更新有無つき） |
| GET | `/api/admin/shared/notifications` | TEACHER | 共有通知インボックス（`unread_only` 可） |
| POST | `/api/admin/shared/notifications/{nid}/read` | TEACHER | 既読化 |
| POST | `/api/admin/shared/notifications/read-all` | TEACHER | 全件既読化 |

`object_type ∈ {course, document}`。エラーは `PurgedError`→410 / `PendingDeletionError`・`AdoptConflictError`→409 / `VersioningError`→422。
学習者向けの削除予定バナーは `GET /api/learning/courses/{cid}/version-notice`（learning 側）。

### 状態・通知（`routes/status.py` / `routes/notifications.py`、migration 038）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/status/overview` | TEACHER | 自分の教材・コースの導出状態一覧（`core/status/projector.py` が正本） |
| GET | `/api/admin/status/materials/{id}` | TEACHER + 所有者 | 教材単体の状態 |
| GET | `/api/admin/status/courses/{id}` | TEACHER + 所有者 | コース単体の状態 |
| GET | `/api/admin/status/events` | TEACHER | status_events の読み取り（自分の所有分に限定。entity_id 指定時は entity_type 必須） |
| GET | `/api/admin/notifications` | TEACHER | 統合インボックス一覧（status / shared 由来を統合。`unread_only` 可） |
| POST | `/api/admin/notifications/{nid}/read` | TEACHER | 既読化（source 不問） |
| POST | `/api/admin/notifications/read-all` | TEACHER | 全件既読化 |
| POST | `/api/admin/notifications/{nid}/dismiss` | TEACHER | 却下（行削除しない。v1 は status 由来のみ対象） |

### ナレッジライブラリ `/api/admin/library`（`routes/library.py`、L層）

手順の正本: [admin_operations/library.md](../admin_operations/library.md)。削除 API は無く `retire` 遷移のみ。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/library/entries` | TEACHER | エントリ一覧（domain_key / entry_type / q / include_retired で絞り込み） |
| POST | `/api/admin/library/entries` | TEACHER | draft エントリ新規作成（昇格 / 手動作成の共通経路。例示画像は元 document 所有者のみ） |
| GET | `/api/admin/library/entries/{eid}` | TEACHER | 単一エントリ取得 |
| GET | `/api/admin/library/entries/{eid}/versions` | TEACHER | 凍結版履歴 |
| PUT | `/api/admin/library/entries/{eid}` | TEACHER | draft 更新（`revision` 楽観ロック・衝突 409。retired は 409） |
| POST | `/api/admin/library/entries/{eid}/freeze` | TEACHER | 凍結版の発行（パイプラインが読むのは凍結版のみ） |
| POST | `/api/admin/library/entries/{eid}/retire` | TEACHER | retire 遷移（retrieval から除外） |
| POST | `/api/admin/library/entries/{eid}/restore` | TEACHER | retire からの復帰 |
| GET | `/api/admin/library/domains` | TEACHER | domain 別サマリ |
| POST | `/api/admin/library/entries/similar` | TEACHER | 類似エントリ検索（昇格モーダルの統合候補提示用） |

### LLM 使用量 `/api/admin/llm-usage`（`routes/llm_usage.py`、U層）

手順の正本: [admin_operations/llm_usage.md](../admin_operations/llm_usage.md)。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/llm-usage/metrics` | SYSTEM_ADMIN | 使用量集計（reported / estimated 分離 + dropped_events + cost_usd） |
| GET | `/api/admin/llm-usage/estimate/documents/{id}` | TEACHER + document 閲覧権 | 解析実行前のトークン見積り（レンジのみ・金額なし） |

### エクスポート（`routes/export.py`）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/courses/{course_id}/export-bundle` | TEACHER | コース一式のエクスポート ZIP 生成・ダウンロード |
| POST | `/api/documents/{document_id}/export-bundle` | TEACHER | ドキュメント一式のエクスポート ZIP 生成・ダウンロード |

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
