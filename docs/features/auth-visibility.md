# 認証・権限・開示範囲

[← ドキュメント目次](../README.md)

JWT 認証、RBAC（ロールベースアクセス制御）、グループ、Visibility（開示範囲）をまとめます。
実装: `backend/api/dependencies.py`, `backend/api/routes/groups.py`, 各ルートの権限チェック。

---

## 1. 認証（JWT + bcrypt）

- **JWT (HS256)**、有効期限 24 時間。`JWT_SECRET` で署名。
- パスワードは **bcrypt**（passlib）でハッシュ化。
- トークン payload: `{sub, username, email, role, gen, exp}`。`gen` は発行時点の
  `users.token_generation`（欠落は 0 とみなすので旧トークンと後方互換）。
- フロントは `localStorage["eg_token"]` に保持し、`Authorization: Bearer {token}` を付与。

ログイン/自己情報は `/api/auth`（`/login`, `/me`）。

### 1.1 トークン検証はアカウント状態も見る（AL3）

`_get_current_user()`（`backend/api/dependencies.py`）は署名・期限の検証に加えて、
`backend/core/account_status.py`（30 秒 TTL キャッシュ）経由で `users` の状態を照合する。

| 条件 | 結果 |
|---|---|
| `status <> 'active'` | 401（`token_rejected_suspended`） |
| `gen`（欠落は 0）≠ `users.token_generation` | 401（`token_rejected_stale`） |
| 行が存在しない | 401 |
| DB 例外 | **そのときだけ fail-open**（payload だけで通し warning ログ） |

停止・パスワードリセットの API は `account_status.invalidate()` を必ず呼ぶため、
期限内のトークンでも即座に無効化される。通過時は `last_seen_at` を 5 分スロットルで
列更新する（イベント化しない）。

### 1.2 ログインの判定順序と `auth_events`

- **資格情報 → status の順**で判定する（先に status を見ると停止中アカウントの列挙リークに
  なるため）。`password_hash IS NULL` は照合せず 401。
- 認証の出来事は `auth_events`（migration 068、**FK なし・append-only・削除 API なし**）に
  記録する。語彙の正本は `backend/core/auth_events.py`
  （`login_success` / `login_failed` / `login_rejected_suspended` /
  `token_rejected_suspended` / `token_rejected_stale` / `password_reset`）。
  IP は `X-Real-IP` → `X-Forwarded-For` 末尾。平文パスワード・ハッシュは入れない。

### 1.3 アカウント状態（migration 068）

`users.status ∈ {active, suspended, pending_deletion, deleted}`。**行を物理 DELETE しない**
（AL1）— 削除は状態遷移 + 匿名化墓標 + 明示 purge で表現する。停止は認証を拒否するだけで、
所有権・共有・受講の関係は変わらない（AL2）。運用手順は
[管理機能 §6](admin.md#6-グループユーザー管理) と
[account_lifecycle_management_design.md](account_lifecycle_management_design.md) を参照。

---

## 2. RBAC（ロール）

| DB 上 | アプリ定数 | 権限 |
|---|---|---|
| `learner` | `ROLE_STUDENT` | 学習 UI、チャット、コース受講登録 |
| `instructor` | `ROLE_TEACHER` | 上記 + 教材アップロード、コース作成・公開、学生アカウント作成 |
| `admin` | `ROLE_SYSTEM_ADMIN` | 全権限（教師アカウント作成を含む） |

階層: **SYSTEM_ADMIN > TEACHER > STUDENT**。

依存関数（`dependencies.py`）:
- `_get_current_user()` — トークン検証、`{id, username, email, role}` を返す
- `_require_teacher()` — TEACHER 未満は 403
- `_require_system_admin()` — SYSTEM_ADMIN 以外は 403

初期管理者は起動時に `ADMIN_PASSWORD` で作成され、以降のアカウントは管理 UI から作成します。

---

## 3. Visibility（開示範囲）

教材（`documents`）・コース（`learning_courses`）は次の開示範囲を持ちます。

| 範囲 | 意味 | アクセス可否 |
|---|---|---|
| `public` | システム全体に公開 | 全ユーザー（コースは `is_published=true`） |
| `group` | 指定グループのメンバーのみ | `object_group_permissions`（`object_type ∈ {course, document}`。旧 `course_group_permissions` / `document_group_permissions` をマイグレーション044で統合）の権限（viewer/editor）を持つグループ員 |
| `private` | 作成者のみ | オーナーのみ |

教材（document）側の共有は**コースを作らずに解析成果を教員間で共有する**ための層でもある。
権限はドキュメント単位に集約し、`theory_components` / `theory_claims` /
`theory_component_graphs` / `document_analysis_runs` はそれを継承する（成果テーブルに
権限列を足さない）。判定の入口は `services.resolve_document_access()` /
`user_can_view_document()` / `user_can_edit_document()` /
`user_owns_document()`（共有設定の変更は所有者のみ）。`object_id` に FK は張らない
（ポリモーフィックのため）ので、document / course の削除経路が明示 DELETE で孤児行を防ぐ。

ルール（例: `learning.py`）:
- `visibility='group'` の場合は有効な `group_id`（ユーザーが所属するグループ）が必要。
- `visibility='public'` のコースは `is_template=true` かつ `is_published=true`。
- 更新はオーナーまたは `object_group_permissions.editor`、削除はオーナーのみ。

---

## 4. グループ

協調学習・共有のためのグループ機構（`routes/groups.py`）。

| テーブル | 役割 |
|---|---|
| `groups` | グループ（`invite_code` UNIQUE, `created_by`） |
| `group_members` | メンバーシップ（`role`: admin/member, `UNIQUE(group_id, user_id)`） |
| `group_invitations` | 招待（`status`: pending/accepted/declined/revoked） |
| `object_group_permissions`（旧 `course_group_permissions`。マイグレーション044で `document_group_permissions` と統合） | オブジェクト×グループの権限（viewer/editor）。`PRIMARY KEY(object_type, object_id, group_id)` で、`object_type='course'` 行がコース共有、`'document'` 行が教材（解析成果）共有に対応。詳細は `docs/architecture/data-model.md`「グループ権限テーブルの統合（マイグレーション 044）」参照 |

参加経路は 2 つ:
1. **管理者による直接招待** — `POST /groups/{id}/members` → 招待者が `POST /me/invitations/{id}/accept`
2. **招待コード** — `POST /groups/join-by-code`（コードが有効なら自動参加）

グループ管理者は招待コードの再発行（`POST /groups/{id}/invite-code/rotate`）やメンバー削除が可能。

---

## 4.5 オブジェクトスコープの権限（ID 直指定エンドポイント）

`_require_teacher()` が保証するのは「TEACHER 以上であること」だけで、URL の
`{course_id}` / `{document_id}` / `{material_id}` が**誰のものか**は何も見ていません。
ID を直指定するエンドポイントは、対象オブジェクトへの権限をサーバ側で確認します
（UI のボタン非表示・disabled は認可根拠にしません）。

共通ゲート（`backend/api/routes/admin.py`）:

| ヘルパー | 許可 | 委譲先（権限の正本） |
|---|---|---|
| `_require_editable_document_or_404(document_ref, current_user)` | document 所有者 / `object_group_permissions('document', …, 'editor')` / SYSTEM_ADMIN | `services.resolve_document_access()`（UUID・`source_path` 両対応） |
| `_require_editable_course_or_404(course_id, current_user)` | コース所有者 / `object_group_permissions('course', …, 'editor')` / SYSTEM_ADMIN | `services.get_editable_course_data()` |

ルール:

- **不在と権限なしを同じ 404・同じ detail に畳む**（"Document not found" /
  "Course not found"）。レスポンス本文から対象の存在を判別させません。
  ロール不足（STUDENT が管理 API を叩く等）は従来どおり `_require_teacher` の 403 です。
- **副作用より先に認可する**。DB 集計・学生名取得・MinIO 読み書き・background task 起動・
  LLM 呼び出しはすべてゲート通過後に行います。
- **閲覧ゲートを変更系の認可に使わない**。`get_material()` や
  `routes/theory_components.py::_ensure_document_viewable` は public / viewer /
  コース経由の閲覧者も通すため、再解析・PDF 差し替え・学習痕跡の開示には使えません。
- SYSTEM_ADMIN の bypass はロール定数を明示比較して行い、TEACHER 全体へ広げません。
  document 側は SYSTEM_ADMIN でも `resolve_document_access()` を呼び、
  canonical な `document_id` / `source_path` を得ます（解決できなければ 404）。

適用済みエンドポイント:

| エンドポイント | 境界 |
|---|---|
| `GET /api/admin/courses/{cid}/unanswered-queries` | コース owner / editor（学生表示名・質問本文を返すため） |
| `GET /api/admin/courses/{cid}/bridge-insights` | コース owner / editor（k-匿名集約でも権限外へは存在ごと隠す） |
| `POST /api/admin/documents/{id}/reanalyze` | document owner / editor |
| `PUT /api/admin/materials/{id}/pdf` | document owner / editor |
| `GET /api/learning/courses/{cid}/source-chunk/{chunk_id}` | コースにアクセス可能、**かつ** chunk の document がそのコースの source |

`source-chunk` のスコープは `services.get_accessible_course_data()` →
`services.list_course_source_document_ids()` →
`services.get_chunk_passage(..., allowed_document_ids=…)` の SQL 内 `ANY(...)` で強制します
（取得後の Python 判定にしない。sources が空なら SQL を発行せず 404）。
本人の全域可視集合（`list_visible_document_ids`）は使いません — 使うと URL のコースに
紐づかない別コース・public 文書のチャンクまで読めてしまいます。逆に積集合も取りません
（コースへの正規アクセスが source 文書の開示根拠、という現行設計を保つため）。

---

## 4.6 RAG 検索とチャンク直読みの可視性（fail-closed）

全域ベクトル検索とチャンクの直読みは、**必須キーワード引数 `allowed_document_ids`** で
SQL 内 `ANY(:doc_ids)` として可視性を強制する。空集合なら SQL を発行せず空結果
（`None` はテスト・本番未接続コード専用の抜け道であり、通常経路では渡さない）。

| 関数 | 用途 | 可視集合の作り方 |
|---|---|---|
| `services.search_chunks_with_metadata(...)` | RAG 検索 | 全域: `services.list_visible_document_ids(user_id)` |
| `services.get_chunk_passage(...)` | 出典ポップアップ | **URL のコースの sources のみ**（§4.5） |
| `services.get_chunk_claim_refs(..., user_id=)` | 出典タブの claim 併記 | コース sources ∪ 本人可視 document |

`list_visible_document_ids(user_id)` は 1 SQL で次の和集合を返す（チャンクごとに
権限判定を呼ぶ N+1 は禁止）:

1. document へ直接アクセスできるもの（所有 / public / 単一グループ共有 /
   `object_group_permissions('document', …)`）
2. **アクセス可能なコース**（所有 / 公開テンプレート / グループ / 受講中）の
   `sources[]` が指す document

2 を含めるのは、受講コースのソース論文（教員 private が多い）を RAG できないと既存の学習
体験が壊れるため。コース sources → document の解決は
`services.list_course_source_document_ids(course_data)` が正本。

`search_chunks_with_metadata` は `material_id` を SELECT し続けること — 回答の出所判定
（`content_grounding`）がこの値に依存している（[RAG チャットフロー](../backend/rag-chat.md)）。

---

## 5. アクセス制御の組み合わせ

実際のアクセス可否は **ロール（RBAC）× 開示範囲（Visibility）× グループ権限**の組み合わせで決まります。

```
学生がコースを開く
  ├─ public        → 誰でも閲覧可（受講登録は `learning_states` に1行 INSERT。クローンしない）
  ├─ group         → そのグループに所属 & object_group_permissions(course) あり
  └─ private/owner  → 作成者のみ

教員が教材を操作
  ├─ TEACHER 以上が必須（_require_teacher）
  └─ さらに Visibility/グループでフィルタ

教師アカウント作成
  └─ SYSTEM_ADMIN のみ（_require_system_admin）
```

---

[← 管理機能](admin.md) ｜ 次へ: [フロントエンド構成 →](../frontend/overview.md)
