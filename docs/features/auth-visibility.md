# 認証・権限・開示範囲

[← ドキュメント目次](../README.md)

JWT 認証、RBAC（ロールベースアクセス制御）、グループ、Visibility（開示範囲）をまとめます。
実装: `backend/api/dependencies.py`, `backend/api/routes/groups.py`, 各ルートの権限チェック。

---

## 1. 認証（JWT + bcrypt）

- **JWT (HS256)**、有効期限 24 時間。`JWT_SECRET` で署名。
- パスワードは **bcrypt**（passlib）でハッシュ化。
- トークン payload: `{sub, username, email, role, exp}`。
- フロントは `localStorage["eg_token"]` に保持し、`Authorization: Bearer {token}` を付与。

ログイン/自己情報は `/api/auth`（`/login`, `/me`）。

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
| `group` | 指定グループのメンバーのみ | `object_group_permissions`（`object_type='course'`。旧 `course_group_permissions`、マイグレーション044で統合）の権限（viewer/editor）を持つグループ員 |
| `private` | 作成者のみ | オーナーのみ |

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
| `object_group_permissions`（旧 `course_group_permissions`。マイグレーション044で `document_group_permissions` と統合） | コース×グループの権限（viewer/editor）。`object_type='course'` 行が対応。詳細は `docs/architecture/data-model.md`「グループ権限テーブルの統合（マイグレーション 044）」参照 |

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
