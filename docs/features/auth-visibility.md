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

ログイン/登録/自己情報は `/api/auth`（`/login`, `/register`, `/me`）。

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
| `group` | 指定グループのメンバーのみ | `course_group_permissions` の権限（viewer/editor）を持つグループ員 |
| `private` | 作成者のみ | オーナーのみ |

ルール（例: `learning.py`）:
- `visibility='group'` の場合は有効な `group_id`（ユーザーが所属するグループ）が必要。
- `visibility='public'` のコースは `is_template=true` かつ `is_published=true`。
- 更新はオーナーまたは `course_group_permissions.editor`、削除はオーナーのみ。

---

## 4. グループ

協調学習・共有のためのグループ機構（`routes/groups.py`）。

| テーブル | 役割 |
|---|---|
| `groups` | グループ（`invite_code` UNIQUE, `created_by`） |
| `group_members` | メンバーシップ（`role`: admin/member, `UNIQUE(group_id, user_id)`） |
| `group_invitations` | 招待（`status`: pending/accepted/declined/revoked） |
| `course_group_permissions` | コース×グループの権限（viewer/editor） |

参加経路は 2 つ:
1. **管理者による直接招待** — `POST /groups/{id}/members` → 招待者が `POST /me/invitations/{id}/accept`
2. **招待コード** — `POST /groups/join-by-code`（コードが有効なら自動参加）

グループ管理者は招待コードの再発行（`POST /groups/{id}/invite-code/rotate`）やメンバー削除が可能。

---

## 5. アクセス制御の組み合わせ

実際のアクセス可否は **ロール（RBAC）× 開示範囲（Visibility）× グループ権限**の組み合わせで決まります。

```
学生がコースを開く
  ├─ public        → 誰でも閲覧可（受講登録でクローン）
  ├─ group         → そのグループに所属 & course_group_permissions あり
  └─ private/owner  → 作成者のみ

教員が教材を操作
  ├─ TEACHER 以上が必須（_require_teacher）
  └─ さらに Visibility/グループでフィルタ

教師アカウント作成
  └─ SYSTEM_ADMIN のみ（_require_system_admin）
```

---

[← 管理機能](admin.md) ｜ 次へ: [フロントエンド構成 →](../frontend/overview.md)
