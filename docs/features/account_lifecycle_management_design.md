# アカウントライフサイクル管理 設計書 — 一覧・停止・削除・パスワードリセット・最終ログイン・利用実績照会

> **状態: 実装済み（正本・凍結）**（2026-08-23 実装。migration は **068**（users 状態列 + auth_events）と
> **069**（llm_usage_events ユーザー別インデックス）で採番済み。以後は §15 実装記録のみ追記する）

Administrator（SYSTEM_ADMIN）による教師・学生アカウントの管理機能を、現状の「作成のみ」から
**一覧 / 停止・再開 / パスワードリセット / 削除（移管→墓標化→選択的purge）/ 最終ログイン /
認証・LLM トークン利用実績の照会** まで拡張する層。認証はステートレス JWT のまま、
**トークン世代（token_generation）+ サーバ側照合** で停止・リセットの即時失効を実現する。

---

## §0 要旨

現状（2026-08-23 調査、4系統の全数調査に基づく）:

- アカウント管理 API は作成 2 本のみ（`POST /api/admin/users/student` = TEACHER 以上 /
  `POST /api/admin/users/teacher` = SYSTEM_ADMIN。`backend/api/routes/admin.py`）。
  一覧・停止・削除・パスワード更新の API / UI / DB 列はすべて存在しない。
- `users` は `id / email / display_name / role / password_hash / auth_provider / created_at /
  updated_at` のみ（`backend/db/init.sql:15-25`）。停止フラグ・最終ログイン列なし。
  `UPDATE users` / `DELETE FROM users` を発行するコードはゼロ。
- 認証はステートレス JWT（HS256・24h 固定）。`_get_current_user` はトークンをデコードするだけで
  users テーブルを照会しない（`backend/api/dependencies.py:63-80`）。`sessions` テーブルは
  DDL と ORM 定義のみで完全未使用。ログイン成功・失敗の記録は一切ない。
- **教員行の生 `DELETE FROM users` は必ず FK 違反で失敗する**: `ON DELETE` 句なし（NO ACTION）の
  FK が 17 列あり、うち `documents.uploaded_by` / `theory_claims.created_by` /
  `theory_review_events.changed_by` / `shared_versions.published_by` は users からの CASCADE
  経路を持たない。
- 仮に FK を外して消すと CASCADE 連鎖が破壊的: `learning_courses.user_id` CASCADE で
  **受講中学習者の learning_states が全消失**、`groups.created_by` CASCADE で
  **自作グループ→object_group_permissions が全滅し他教員のコース共有まで巻き添え**。

この事実から、本設計は「**users 行は物理削除しない（墓標化）**」を第一原理に置く。

---

## §1 不変条項（AL1〜AL10）

- **AL1 users 行を物理 DELETE しない。** 削除 = 状態遷移（`pending_deletion` → `deleted`）+
  行の匿名化（墓標化）+ 個人データの明示 purge。`DELETE FROM users` を発行するコードを
  本層に書かない（17 の NO ACTION FK と CASCADE 連鎖を構造的に無害化する唯一の方法）。
- **AL2 停止は認証の拒否のみ。** 停止時に所有権（`documents.uploaded_by` /
  `learning_courses.user_id`）・可視性・グループ・共有・受講状態を一切触らない。
  停止中も共有先教員の閲覧・学習者の受講は継続する（`user_can_view_document` 等の
  権限判定は所有者 UUID の一致だけを見るため、所有列を触ると全滅する）。
- **AL3 失効はトークン世代で即時化する。** JWT に `gen` クレームを追加し、
  `_get_current_user` がサーバ側の `users.token_generation` と `status` を照合する
  （プロセス内 TTL キャッシュ、既定 30 秒）。停止・パスワードリセットは最大キャッシュ TTL
  以内に全 API で効く。「24h TTL の間は停止が効かない」状態を残さない。
- **AL4 監査必須。** 停止・再開・リセット・削除予約/取消/purge・移管を
  `theory_review_events`（新カタログ定数 `AUDIT_ENTITY_USER_ACCOUNT`）に
  `services.record_review_event` 経由で記帳する。**平文パスワード・ハッシュ値を
  監査 metadata・アプリログ・auth_events に入れない。**
- **AL5 認証イベントは append-only。** `auth_events` に行単位の削除・改変 API を作らない
  （U6 / DO4 と同型）。
- **AL6 数値の開示範囲は U5 を継承。** ユーザー別 LLM トークン数値は SYSTEM_ADMIN のみ。
  学習者・教員本人向け UI に出さない。実測と推計は `usage_source` 別に分離して返す（U1）。
- **AL7 学生の行動監視にしない。** 学生アカウントの認証イベント・利用実績の**個票**は
  SYSTEM_ADMIN のみ（TEACHER に開示しない）。用途はアカウント運用（不正利用・休眠検知）に
  限定し、学習評価に使わない（P3 と同じ精神。マニュアルにも明記する）。
- **AL8 情報を落とさない。** 停止・削除予約は取消可能な状態遷移。auth_events・監査記録・
  U層イベントは墓標化後も保持する（`llm_usage_events` が FK を張らない設計意図
  「テレメトリ行が本体の削除を妨げない」`backend/db/043:6-8` を踏襲）。
- **AL9 purge は前提条件を構造的に強制する。** 教員の purge は「所有オブジェクト
  （documents / learning_courses / groups）がゼロ、または移管済み」でなければ実行しない
  （残存時は中止して事実文で通知。黙って巻き添え削除しない）。
- **AL10 ロックアウトを構造的に防ぐ。** 自分自身と bootstrap Administrator
  （`display_name='Administrator'`、`main.py` lifespan が生成）は停止・削除・降格できない
  （422）。

---

## §2 現状の事実確認（偵察結果の要点）

設計判断の根拠となる確定事実。詳細な file:line は実装時に再検証すること。

1. **FK 全数**: users(id) への FK は約 58 列。CASCADE 26 列 / SET NULL 15 列 /
   NO ACTION 17 列。FK なしで user_id を保持する箇所が別途多数
   （`interest_traces.user_id`, `llm_usage_events.user_id`, `atlas_skeletons.created_by/updated_by`,
   `library_entries.created_by`(TEXT), `epistemic_ledger` JSONB 内 `recorded_by` 等）。
2. **孤児 UUID の実行時リスク**: `notification_recipients.atlas_skeleton_editor_ids` は
   FK なしの `atlas_skeletons.created_by/updated_by` を通知宛先として返し、
   `user_notifications.recipient_id`（NOT NULL + FK）へ INSERT する。ユーザー行が消えると
   地図の freeze/retire 通知が FK 違反で静かに落ちる。→ AL1（行を消さない）でこの類の
   事故を一括回避する。墓標ユーザー宛の通知は配送時に `status='deleted'` を除外する
   （§8.4）。
3. **削除の既存パターン**: V層に「予約（`pending_deletion` + `purge_after`、既定14日
   `DEFAULT_GRACE_DAYS`）→ スイーパ worker（1件失敗で止めない・権限消失前に宛先収集）→
   墓標（`lifecycle='purged'` 行を残す）」の三層構造が確立済み（`core/versioning/deletion.py` /
   `worker.py`）。本層はこれを userアカウントに転用する。
4. **U層**: `llm_usage_events.user_id` 列は既存（NULL 許容・FK なし・インデックスなし）。
   集計 API の group_by ホワイトリスト（`core/llm_usage/metrics.py::_GROUP_BY_SQL`）に
   `user_id` は無く、ユーザー別集計は未実装。バッチ/worker 系（doubt 各 worker・
   atlas 生成・standardization・help_kb embed）は恒常的に user_id NULL で記録される。
5. **UI の現在地**: 教員管理タブ `teachers`（SYSTEM_ADMIN のみ、`setupRoleBasedUI()` が
   動的生成）に作成フォームのみ。学生管理タブ `students`（TEACHER 以上）も同様。
   LLM 使用量タブ `llm-usage`（SYSTEM_ADMIN）は `admin-llm-usage.js` がキー汎用描画で、
   バックエンドが `user_id` キーを返せば軽微な改修で表示可能。
6. **既存の穴（本層 Phase 0 で是正）**: ①アカウント作成の重複チェックは `display_name`
   のみで、email 重複は DB UNIQUE 違反 → 500 になる（409 にすべき）。②ログイン成功・失敗の
   ログ・記録が皆無。③`auth.py` の `logger` は定義のみ未使用。
   ④`users.password_hash` は nullable（`init.sql`）だが、ログイン照合は NULL ガードなしで
   `_verify_password(body.password, record[2])` に渡すため、hash NULL 行への
   ログイン試行は TypeError → 500 になる潜在バグ（§4.1 で是正。墓標化 §8.4 の前提でもある）。

なお §2.1 の件数はいずれも 2026-08-23 時点の実測。正本は
`grep "REFERENCES users(id)" backend/db/*.sql` の再実行結果とする（§5-6 カウント記法）。

---

## §3 DB 変更（migration は次の空き番号で採番。フェーズごとに 1 ファイル）

### 3.1 users への列追加（Phase 1）

```sql
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'pending_deletion', 'deleted')),
    ADD COLUMN IF NOT EXISTS status_changed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS status_changed_by UUID,          -- FK なし（墓標参照許容）
    ADD COLUMN IF NOT EXISTS status_reason TEXT,
    ADD COLUMN IF NOT EXISTS token_generation INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS password_updated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS purge_after TIMESTAMPTZ;         -- pending_deletion 時のみ非NULL
```

- 状態語彙は V層 `shared_version_state.lifecycle` と対応（`active / pending_deletion / purged`
  ≒ `deleted`）+ 本層固有の `suspended`。
- `status_changed_by` に FK を張らない（管理者自身が後に墓標化されうるため。
  表示は `LEFT JOIN users` で NULL 安全に）。
- ORM `core/models.py::User` は実行時未使用（import 元ゼロ）のため列同期は必須ではないが、
  同期しない場合はその旨をコード側コメントに明記する（黙った乖離を残さない）。

### 3.2 auth_events 新設（Phase 0）

`discuss_metric_events`（migration 060）型 = **FK なし・append-only・語彙ホワイトリスト**を踏襲:

```sql
CREATE TABLE IF NOT EXISTS auth_events (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID,                    -- 失敗時に未特定なら NULL。FK は意図的に張らない
    username_attempted TEXT NOT NULL DEFAULT '',-- login_failed 時の入力名（存在照合前）
    event              TEXT NOT NULL,           -- 語彙はサーバ側ホワイトリスト（§3.2.1）
    ip_address         TEXT,
    user_agent         TEXT,
    payload            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_auth_events_user  ON auth_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_auth_events_event ON auth_events(event, created_at);
```

#### 3.2.1 event 語彙（正本は `backend/core/auth_events.py` に定数で置く）

| event | 発生点 |
|---|---|
| `login_success` | `/api/auth/login` 成功 |
| `login_failed` | 同・失敗（ユーザー不在 / パスワード不一致を **payload で区別しない** — 列挙攻撃対策） |
| `login_rejected_suspended` | 停止中アカウントのログイン試行 |
| `token_rejected_suspended` | 有効期限内トークンが status 照合で拒否された |
| `token_rejected_stale` | token_generation 不一致（リセット後の旧トークン） |
| `password_reset` | 管理者によるリセット実行（payload に対象 user_id、**パスワードは入れない**） |

- `last_seen_at` の更新は**イベント化しない**（列のスロットル更新のみ。書き込み増幅防止）。
- `login_rejected_suspended` は**資格情報が一致した場合のみ**記録する。不一致は status に
  関わらず `login_failed` に落とす（§4.1 の判定順序と対）。
- IP は `X-Real-IP` を第一候補、無ければ `X-Forwarded-For` の**末尾要素**を採用する
  （`$proxy_add_x_forwarded_for` はクライアント送信値の後ろに実 IP を付けるため、
  先頭は攻撃者が偽装できる）。`frontend/nginx.conf` は既に全 `/api/*` ロケーションで
  `X-Real-IP` / `X-Forwarded-For` を付与済みのため nginx 側の追加作業はない。

### 3.3 llm_usage_events へのインデックス追加（Phase 2）

```sql
CREATE INDEX IF NOT EXISTS idx_llm_usage_events_user
    ON llm_usage_events(user_id, occurred_at) WHERE user_id IS NOT NULL;
```

migration 043 は既存正本のため編集せず、新番号ファイルで追加する（U6 は行削除 API の禁止で
あり DDL 追加は制約しない）。

---

## §4 認証フローの変更（Phase 0 + Phase 1）

### 4.1 ログイン（`routes/auth.py`）— 判定順序が重要（列挙攻撃対策）

1. **先に資格情報を検証する**。`password_hash IS NULL` の行は `_verify_password` を
   呼ばずに不一致として扱う（§2.6-④ の 500 潜在バグの是正。墓標行にも効く）。
   不一致・ユーザー不在は status に関わらず 401 固定文言 + `auth_events(login_failed)`
   （payload で不在/不一致を区別しない）。
2. **資格情報が一致した場合のみ** status を判定する。`suspended` / `pending_deletion` は
   403 + 事実文「このアカウントは停止されています。管理者に連絡してください。」+
   `auth_events(login_rejected_suspended)`。`deleted` は 401（存在を教えない）。
   ※ status 判定を資格情報検証より先に置くと、第三者がユーザー名だけで
   「存在し、かつ停止中」を判定できてしまう（列挙リーク）。この順序を逆にしない。
3. 成功時: `last_login_at = now()` を UPDATE + `auth_events(login_success)` を記録。
4. JWT payload に `gen`（発行時点の `token_generation`）を追加。

### 4.2 トークン検証（`dependencies.py::_get_current_user`）

1. JWT デコード後、`users` から `status / token_generation` を照合する。
   - `status <> 'active'` → 401 + `auth_events(token_rejected_suspended)`
   - `gen <> token_generation` → 401 + `auth_events(token_rejected_stale)`
   - **行が引けない（`fetchone()` が None）→ 401** + `auth_events(token_rejected_stale)`。
     AL1 により行は消えないため、行不在は偽造 sub か移行漏れを意味し fail-open と
     同一視しない。
   - `gen` クレームの無い旧トークンは `gen=0` とみなす（列の初期値 0 と一致するため
     後方互換。リセットを実行した時点から旧トークンが失効する）。
2. **プロセス内 TTL キャッシュ（既定 30 秒、`llm_policy_store` の 20 秒 TTL パターン踏襲）**で
   user_id → (status, token_generation) を持ち、リクエスト毎の DB 往復を避ける。
   停止・リセットの API 実行時に該当エントリを `invalidate()` する（同一プロセス内は即時、
   多プロセス構成でも最大 TTL 秒で反映）。
3. **fail-open は DB 例外（接続失敗等）のみ**（従来どおり JWT 検証のみで通す）。DB 断では
   他の全エンドポイントも動かないため実害はなく、認証層の二重障害を避ける。fail-open した
   事実は `logger.warning` に残す。行不在（項1）は fail-open の対象ではない。
4. `last_seen_at`: 照合で DB を引いた機会に、前回更新から 5 分以上経過していれば UPDATE
   （プロセス内スロットル。厳密さより書き込み量を優先する近似値であることを UI に注記）。

---

## §5 API 設計（`backend/api/routes/admin.py` の User Management 節を拡張）

権限の原則: **教員・管理者アカウントへの操作は SYSTEM_ADMIN のみ。学生アカウントへの
操作（一覧・停止・再開・リセット）は TEACHER 以上**（作成の権限分担と対称）。
個票 activity は AL7 により全ロール対象で SYSTEM_ADMIN のみ。

| Method / Path | 権限 | 概要 |
|---|---|---|
| `GET /api/admin/users?role=&status=&q=&limit=&offset=` | TEACHER 以上 | 一覧。**TEACHER は `role=learner` に強制固定**（fail-closed）。SYSTEM_ADMIN は全ロール。返却: id / username / email / role / status / created_at / last_login_at / last_seen_at |
| `POST /api/admin/users/{user_id}/suspend` body `{reason}` | 学生=TEACHER / 教員・admin=SYSTEM_ADMIN | 停止。reason 必須（監査に残す）。自分自身・Administrator は 422（AL10） |
| `POST /api/admin/users/{user_id}/restore` | 同上 | 再開（`suspended` → `active`）。`pending_deletion` の解除は削除取消 API のみ |
| `POST /api/admin/users/{user_id}/password-reset` body `{new_password}` | **SYSTEM_ADMIN のみ（対象が学生でも。§14-1 裁定）** | ハッシュ更新 + `token_generation++` + `password_updated_at` + auth_events + 監査。最低 8 文字（サーバ検証） |
| `GET /api/admin/users/{user_id}/activity?limit=&before=` | SYSTEM_ADMIN | 個票照会: last_login / last_seen + auth_events 直近（上限 100・ページング）+ LLM 利用サマリ（§7.3） |
| `POST /api/admin/users/{user_id}/deletion` body `{grace_days?}` | SYSTEM_ADMIN | 削除予約（§8）。前提: `suspended` であること（422） |
| `DELETE /api/admin/users/{user_id}/deletion` | SYSTEM_ADMIN | 削除予約の取消（`pending_deletion` → `suspended`） |
| `POST /api/admin/users/{user_id}/transfer-ownership` body `{to_user_id}` | SYSTEM_ADMIN | 所有物の移管（§8.2）。移管先は `active` な TEACHER 以上（422） |

- 既存の作成 2 エンドポイントに **email 重複の事前チェック（409）** を追加する（Phase 0）。
- **対象ユーザーのロールはリクエストではなく DB から読んで権限判定する**（fail-closed。
  TEACHER が role パラメータを偽って教員に操作することを構造的に防ぐ）。
- SYSTEM_ADMIN が自分自身のパスワードを再設定した場合も `token_generation++` は無条件に
  行う（自分のトークンも即失効する）。レスポンスで再ログインが必要な旨を返し、UI は
  ログイン画面へ誘導する。
- 停止・再開・リセット・削除予約/取消・移管はすべて `record_review_event` で監査
  （`AUDIT_ENTITY_USER_ACCOUNT`、entity_id = 対象 user_id、old/new は status 遷移。
  metadata に reason・移管先を入れ、**資格情報は入れない**）。
- エラー方針: 対象不在は 404 統一。権限不足は 403。前提未達（自分自身・Administrator・
  非 suspended からの削除予約・非 active への移管）は 422 + 事実文。

---

## §6 停止のセマンティクス（Phase 1）

- 効果は **認証の拒否のみ**（AL2）: 新規ログイン拒否 + 発行済みトークンの照合拒否
  （§4.2、最大 30 秒で全 API に波及）。
- 触らないもの: 所有 documents / courses の可視性・共有・受講状態・グループ・V層ピン・
  C層承認・下書き類。停止中も共有先教員は教材を閲覧でき、学習者は受講を継続できる。
- 停止中アカウントが「宛先」になる通知（状態通知・V層通知）は配送対象のまま
  （再開時に読める。インボックスは本人しか見ないため実害なし）。
- **起動済みの背景処理は完走する**: 解析パイプライン・音声一括生成・tension / anchor /
  doubt 等の worker は `threading.Thread` で認証を通らないため、停止後もキュー済み処理は
  走り、LLM 消費は停止ユーザーに帰属して `llm_usage_events` に記録される（停止 = 認証の
  拒否であり、進行中ジョブの中断ではない。マニュアルに明記する）。停止中は新規チャットが
  401 になるため、新たな痕跡・ジョブは積まれない。
- 一覧・詳細 UI では状態チップ（`停止中` / `削除予定` / `削除済み`）で表示。
  学習者向け UI には教員の停止状態を一切出さない（運用情報）。

---

## §7 利用実績照会（Phase 2）

### 7.1 認証トークン利用実績

- `GET /api/admin/users/{id}/activity` が auth_events を新しい順に返す
  （event / created_at / ip_address / user_agent。`login_failed` の `username_attempted` は
  本人行に user_id が立った行のみ対象）。
- 一覧 API の `last_login_at` / `last_seen_at` で「休眠アカウント」を一覧上で判別できる。
- 集計値（ログイン回数等）は v1 では出さない（個票の時系列で足りる。数値ダッシュボード化は
  要求が出てから）。

### 7.2 ユーザー別 LLM トークン利用実績

- `core/llm_usage/metrics.py::_GROUP_BY_SQL` ホワイトリストに `user_id` を追加し、
  `collect_metrics` に任意の `user_id` フィルタ引数を追加する（既存 API 互換は不変）。
- `GET /api/admin/llm-usage/metrics?group_by=user_id,...` が使えるようになる
  （権限は既存どおり `_require_system_admin` = U5 継承で追加作業なし）。
- **表示名解決は API 層で二段引き**（`llm_usage_events` は FK なしのため）:
  レスポンス組み立て時に users を一括 SELECT し、`user_id → display_name` を付与。
  解決不能（墓標・孤児）と NULL は `未帰属 / 削除済みユーザー` として正直に表示（U1 精神。
  バッチ/worker 系は恒常的に NULL である事実を UI 注記に書く）。
- `admin-llm-usage.js` の group_by セレクタに「ユーザー別」を追加（キー汎用描画のため
  select option + ラベル整形のみ）。

### 7.3 アカウント個票への合流

`GET /api/admin/users/{id}/activity` のレスポンスに LLM 利用サマリを同梱する:
直近 30 日の `usage_source` 別（reported / estimated 分離、U1）トークン合計と feature 上位
5 件。実装は既存 `collect_metrics` の再利用のみで新集計 SQL を書かない
（実シグネチャは `collect_metrics(session, *, date_from, date_to, group_by)` —
`session` 第1位置引数・期間必須。ここに任意の `user_id` フィルタ引数を追加する）。

---

## §8 削除のセマンティクス（Phase 3）

### 8.1 三段構造（V層の予約→スイーパ→墓標パターンを転用）

1. **予約**: `POST .../deletion`（前提: `suspended`）→ `status='pending_deletion'` +
   `purge_after = now() + grace_days`（既定は V層 `DEFAULT_GRACE_DAYS` = 14 日を流用）。
   取消可能（AL8）。
2. **スイーパ**: `core/versioning/worker.py` の `sweep_once` が、既存 `_due_objects()` と
   **並列に** 新設 `_due_users()`（`users WHERE status='pending_deletion' AND
   purge_after <= now()`）を回す形で相乗りする（「1 件の失敗が全体を止めない」
   「権限が消える前に宛先を収集」構造を継承。独立 worker を増やさない）。
   **`shared_version_state.object_type` に 'user' を追加しない** — V層 3 テーブルの
   CHECK は `('course','document')` 固定のままとし、user の削除予約状態は §3.1 の
   users 列（`status` / `purge_after`）だけで管理する（V層の意味論を汚さない）。
3. **purge（`purge_user`）**: §8.3 の前提チェック → §8.4 の墓標化 + 個人データ purge。

### 8.2 所有物の移管（transfer-ownership）

- 対象を後任へ UPDATE: `documents.uploaded_by` / `learning_courses.user_id` **+
  `learning_courses.owner_id`**（認可判定には未使用だが INSERT 時に user_id と同値が
  書かれている実列。黙った不整合を残さないため同時更新する。§13 の DROP までの整合維持）/
  `groups.created_by`（グループは合わせて移管先を `group_members` の admin として保証）。
- 受講者の `learning_states`・共有・V層版はそのまま生きる（所有者 UUID の付け替えだけで
  権限判定・通知宛先が新所有者に切り替わる）。
- 監査 metadata に移管前後の所有者と対象件数を記録。V層の版（`shared_versions.published_by`）
  は**発行時点の事実**なので付け替えない（不変 Release の帰属を偽らない）。

### 8.3 purge の前提チェック（AL9）

`documents WHERE uploaded_by = :id` / `learning_courses WHERE user_id = :id` /
`groups WHERE created_by = :id` のいずれかが 1 件でも残っていれば purge を中止し、
`user_notifications` で SYSTEM_ADMIN 宛に事実文通知（「所有教材が N 件残っています。
移管または削除してから再実行されます」）。スイーパは次周期に再試行する（完了フラグを
持たず状態から毎回導出、G1 と同型）。学生アカウントは所有物を持たないため通常そのまま通る。

### 8.4 墓標化 + 個人データの明示 purge

users 行は残して匿名化する:

```
email         → 'deleted+{uuid}@invalid.local'   -- UNIQUE 制約対応
display_name  → 'deleted-{uuidの先頭8桁}'         -- ログイン名衝突回避
password_hash → '!'（検証不能な非NULL センチネル）  -- NULL にしない（§4.1 の NULL ガードと
                                                  --  二重防御。既存の呼び出し規約も壊さない）
token_generation++ / status='deleted'
```

個人データの扱いは「**DELETE する / 残す」の二分で全テーブルを網羅する**（CASCADE に
頼らず明示 DELETE。この表が正本で、`REFERENCES users(id)` を持つ全テーブルが
どちらかに現れることをガードレールで固定する — §12-11）:

**DELETE する（本人由来の学習痕跡・会話・個人設定）**:

| テーブル | 根拠 |
|---|---|
| `learner_profiles`（+ 子 3 表は FK CASCADE） | 本人の学習者プロファイル |
| `learning_states` / `learning_chat_history` / `chat_sessions`（→ `chat_messages`） | 本人の受講状態・会話 |
| `course_builder_sessions` / `unanswered_query_logs` | 本人の作業履歴・質問ログ |
| `interest_traces` / `learner_reconstructions` / `student_stumble_events`（student_id 行） | 本人の学習痕跡 |
| `atlas_cue_events` / `assistant_step_dismissals` / `assistant_actions` | 本人の導線履歴・操作スナップショット |
| `counterfactual_sessions`（owner_id 行） | 本人所有のセッション |
| `user_notifications`（受信箱） / `shared_version_subscriptions`（ピン） | 本人宛・本人設定 |
| `group_members` / `group_invitations`（invitee / inviter とも） | 本人のメンバーシップ |
| `llm_model_policies`（`scope='user'` 行） | 本人設定 |
| `sessions` | 未使用テーブルだが網羅性のため明示 |

**残す（監査・テレメトリ・共同体の記録。帰属表示は墓標名になる）**:

| テーブル / 列 | 根拠 |
|---|---|
| `theory_review_events` | 監査台帳（AL8） |
| `auth_events` / `llm_usage_events` / `discuss_metric_events` | テレメトリ。060 の設計意図「計測はユーザー削除と独立」を踏襲 |
| `component_endorsements` / `component_citations` | 共同体の合意記録。消すと他教員の承認数が黙って減る |
| `challenges` / `verification_proposals` / `atlas_correction_reports` | D層・地図の共同体記録（疑義・提案・修正報告は個人の痕跡ではなく知の記録） |
| `component_explanations`（`kind='personal'` 含む） | 教員の説明資産は承認・引用の対象になった共同財。author_id は SET NULL 済みの列で墓標名表示 |
| SET NULL 系の帰属列全般 / FK なしの帰属列（`atlas_skeletons.created_by` 等） | 表示は `LEFT JOIN users` で墓標名 or NULL 安全 |

- 通知配送系の宛先から `status='deleted'` を除外する。ただし
  `notification_recipients.py` の各関数は現状 users を JOIN していない（group_members /
  learning_courses / atlas_skeletons から user_id を返すだけ）ため、**全宛先解決関数への
  `JOIN users` 追加 + `user_notifications` INSERT 直前の除外（二重化）**として実装する。
  同ファイルを通らない直接配信元（atlas_lifecycle / library / reconstruction の通知）も
  同じ除外を通す。
- FK なし孤児 UUID による配送 FK 違反（§2.2）は、**本層が墓標化した user については**
  行が残るため起きなくなる。データ移行や手作業由来の「users に存在しない UUID」への
  防御は上記の宛先解決側 `JOIN users` が担う（本層で完全には閉じない事実を明記しておく）。

### 8.5 学習者への予告 — v1 では出さない

AL9 により、所有コースは移管または個別削除を経ない限り purge されない = 「アカウント削除
予約によってコースが黙って消える」事態は構造的に起こらない。したがって受講者向けの
削除予告バナーは v1 では追加しない（起こらない喪失を予告して煽らない）。
コース自体の削除予約は既存の `course_deletion_notice`（V層）が従来どおり告知する。
将来「所有コースの巻き添え削除」を運用として解禁する場合に、§14-2 と併せて再設計する。

---

## §9 UI（admin.js — ES5。3点セット必須）

### 9.1 教員管理タブ `teachers`（SYSTEM_ADMIN）

既存の作成フォームの下に一覧テーブルを追加:
`ユーザー名 / メール / 状態チップ / 最終ログイン / 最終アクセス / 操作`。
操作 = `停止…`（reason 入力モーダル）/ `再開` / `パスワード再設定…` / `利用状況…`
（activity モーダル: 認証イベント時系列 + LLM 利用サマリ）/ `削除予約…`（`suspended` 時のみ
活性。confirm はコース削除と同型の**対象名入力**方式）/ `移管…`（移管先選択）。

### 9.2 学生管理タブ `students`（TEACHER 以上）

学生一覧 + `停止…` / `再開`。`パスワード再設定…` / `利用状況` / `削除予約` は
SYSTEM_ADMIN のみ表示（§14-1 裁定・AL7）。

### 9.3 LLM 使用量タブ `llm-usage`（SYSTEM_ADMIN）

group_by セレクタに「ユーザー別」を追加。未帰属行の注記を表示。

### 9.4 登録物（実装時に漏れると網羅テストが落ちる）

- `ADMIN_UI_ANCHORS` / `KNOWN_ADMIN_UI_ANCHOR_IDS` への追加（想定:
  `teachers.user-list` / `teachers.user-suspend` / `teachers.user-reset` /
  `teachers.user-activity` / `teachers.user-delete` / `teachers.user-transfer` /
  `students.user-list` / `students.user-suspend` /
  **`students.user-reset` / `students.user-activity` / `students.user-delete`**
  （§9.2 の SYSTEM_ADMIN 限定ボタンにも担体が要る。値は `system_admin/` 配下の節にする —
  `resolve_admin_ui_anchors(TEACHER)` は `teacher/` のみを返す fail-closed が
  AL7 をそのまま担保する）。件数の正はテスト定数（現在 266 —
  `test_admin_help_ui_anchors.py` 参照。実装時に更新）。
  **`llm-usage.group-by-user` は作らない** — group_by セレクタには既存アンカー
  `llm-usage.groupby` が付いており、1属性1ID 規約に反する。既存節
  `docs/manual/system_admin/13-admin-llm-usage.md` に「ユーザー別」の説明を追記する。
- マニュアル節: `docs/manual/system_admin/10-admin-teachers.md` を拡張（操作要素 1 つ = 1 節、
  「ボタンが無効になっている場合」節を停止・削除・移管に必ず付ける）+
  `docs/manual/teacher/` の学生管理節に停止・リセットを追記
- `data-ui-anchor` 付与 + `AA.registerUiAnchors` の DOM 解決関数登録
- Capability 登録（`core/admin_assistant/capabilities.py`）— **`required_role` は 1 値
  なので、対象ロール別に capability を分割する**（`users.suspend` 1 本に
  `required_role=TEACHER` とすると Copilot が TEACHER に教員停止の手順まで案内して
  しまい P1 違反）:
  `users.list`（guidance, TEACHER）/
  `users.suspend_student`・`users.restore_student`（TEACHER）/
  `users.suspend_teacher`・`users.restore_teacher`（SYSTEM_ADMIN）/
  `users.password_reset`（SYSTEM_ADMIN。対象ロールを問わず一本。
  **reversible=false → confirm=true 必須**）/
  `users.schedule_deletion`（SYSTEM_ADMIN。予約自体は取消可能だが outcome が破壊的の
  ため reversible=false 扱い + confirm=true）/
  `users.transfer_ownership`（SYSTEM_ADMIN, reversible=false, confirm=true）。
  各 capability の `howto_doc` 先として **`docs/admin_operations/users.md` に新節
  （`{#anchor}`）を追加する**（capability KB が手順の正本。未整備のままだと Copilot が
  「未整備」を返し G層 `assistant_kb.undocumented` が恒久点灯する）。
- 開発ドキュメントの同時更新（§5-1 / `test_docs_registry_guardrails.py` が要求）:
  migration 追加ごとに `docs/architecture/data-model.md` の表 +
  `docs/architecture/layer_registry.md` §3（+「次の空き番号」案内の更新）。
  新設ルーターは無し（admin.py / auth.py / llm_usage.py の拡張のみ）のため
  `docs/backend/api.md` は該当なしと確認して済ませる。学習者向け挙動の変更は
  v1 では無い（§8.5 でバナーを見送ったため `docs/features/learning.md` 更新は不要）。
  CLAUDE.md の監査語彙カウント等の時点付き記述も実装時に追随させる。

---

## §10 コスト・計測

- 本層は LLM を一切呼ばない（全機能が非 LLM・同期）。
- 認証照合の DB 往復は TTL キャッシュで抑制（§4.2）。auth_events の書き込みは
  ログイン時 + 拒否時のみ（正常リクエスト毎には書かない）。

---

## §11 実装フェーズ分割

| Phase | 内容 | migration |
|---|---|---|
| **0** | auth_events 新設 + ログイン成功/失敗の記録 + last_login_at（users 列は Phase 1 とまとめても可）+ email 重複 409 是正 + nginx X-Forwarded-For 確認 | 次の空き番号 |
| **1** | users 状態列 + 一覧 API/UI + 停止/再開 + パスワードリセット + token_generation 照合（即時失効） | 同上（Phase 0 と同一ファイル可） |
| **2** | 利用実績照会: activity API + U層 user_id 集計軸 + llm_usage_events インデックス + UI | 次の空き番号 |
| **3** | 削除: 移管 API + 削除予約/取消 + スイーパ拡張 + purge_user（墓標化 + 明示 purge リスト）+ 受講者バナー + 宛先除外 | 次の空き番号 |

Phase 0+1 が最小リリース単位（ここまでで「作れるが止められない」状態が解消される）。
Phase 3 は影響範囲が最大のため、Phase 1 の停止運用で実需を観察してから着手してよい。

---

## §12 ガードレール（`backend/tests/test_account_lifecycle_guardrails.py` ほか）

1. 本層のコードに `DELETE FROM users` が存在しない（AL1。`guardrail_helpers.assert_source_forbids`。
   ORM 経由の削除も禁止語彙に含める: `session.delete(` / `delete(User` / `query(User).delete`
   — `core/models.py::User` は `cascade="all, delete-orphan"` を持つため文字列 SQL 検査
   だけでは AL1 を守れない）
2. suspend/restore の実装が `uploaded_by` / `learning_courses.user_id` / `groups` /
   `object_group_permissions` に触れない（AL2）
3. auth_events への削除・改変 API が存在しない（AL5）
4. `password` / `password_hash` が監査 metadata・auth_events payload・logger 呼び出しに
   渡されない（AL4。ast 走査）
5. 一覧 API が TEACHER に対して `role=learner` へ fail-closed する / activity API が
   `_require_system_admin` である（AL7）
6. 自分自身・Administrator への suspend/deletion が 422（AL10）
7. `purge_user` が所有物残存時に users 行を変更しない（AL9）
8. 監査 entity_type がカタログ定数 `AUDIT_ENTITY_USER_ACCOUNT` を使う（生文字列禁止）
9. token_generation 照合: gen 不一致トークンが 401 になる / キャッシュ invalidate が
   suspend・reset 経路に存在する（AL3）
10. U層: group_by=user_id が SYSTEM_ADMIN 以外に露出しない / reported・estimated の
    分離が維持される（AL6、既存 test_llm_usage_guardrails.py への追記）
11. **purge 網羅性**: `grep "REFERENCES users(id)" backend/db/*.sql` で得た全テーブルが
    §8.4 の「DELETE する / 残す」いずれかの表（実装ではコード内の 2 つの明示リスト）に
    現れる（将来の migration 追加での取りこぼしを構造的に検出。G1 / LS7 と同型）
12. ログインの判定順序: 資格情報検証が status 判定より先である /
    `password_hash IS NULL` 行で `_verify_password` が呼ばれない（C1/C2 回帰防止）

ほか通常テスト: `test_account_lifecycle_{api,auth,purge}.py` /
`test_account_lifecycle_ui_static.py`（アンカー 3 点セット・状態チップ・confirm 文言）。

---

## §13 非スコープ（v1）

- 本人によるパスワード変更・「パスワードを忘れた」フロー（メール送信基盤が無い。
  管理者リセットで代替）
- 初回ログイン時のパスワード変更強制（`must_change_password`。フロント改修が大きく、
  リセット運用の実測後に判断）
- OAuth / 外部 IdP（`auth_provider` 列は 'local' のまま温存）
- `sessions` テーブルの復活・セッション単位の失効管理（token_generation で代替。
  「特定端末だけログアウト」の要求が出てから）
- ログイン失敗のレートリミット・アカウント自動ロック（auth_events で材料は残る。
  検知→自動制御は運用実測後）
- 学生本人・教員本人への自分の利用実績表示（U5 / AL6）
- ロール変更 API（learner ⇄ instructor ⇄ admin。AL10 の「降格できない」は将来
  ロール変更を作る際の先行制約として置いてある）
- 受講者向けの「所有教員がアカウント削除予約中」バナー（§8.5。AL9 により v1 では
  構造的に不要）
- 一括操作（CSV インポート・一括停止）・アカウント有効期限
- NO ACTION FK 17 列の整理（`SET NULL` + 表示名非正規化への移行）と
  `learning_courses.owner_id` 死列の DROP、`groups.created_by` CASCADE の是正 —
  **AL1（行を消さない）により v1 では実害が無くなる**ため、別 issue の負債返済として切り出す

---

## §14 未決事項 — **オーナー裁定済み（2026-08-23）**

1. **TEACHER に学生のパスワードリセットを許すか** → **裁定: 今は実装しない。**
   パスワードリセットは対象ロールを問わず SYSTEM_ADMIN のみ（なりすまし経路を
   v1 で開けない）。学生の一覧・停止・再開は設計どおり TEACHER 以上・全学生対象。
2. **停止中教員の所有コースの学習者向け告知** → **裁定: 不要**（何も出さない）。
3. **削除猶予の既定日数** → **裁定: 移管機能（§8.2）を v1 に含める前提で 14 日**
   （V層 `DEFAULT_GRACE_DAYS` を流用。環境変数上書きは任意）。
4. **auth_events の保持期間** — v1 は無期限（append-only）。肥大化が観測されたら
   ローテーション方針を別途設計（裁定不要のまま維持）。
5. **bootstrap Administrator の同定方法** → **裁定: 現状と同じ**
   （`display_name='Administrator'` の固定名一致。`is_bootstrap` 列は追加しない）。

---

## §15 実装記録（2026-08-23、Phase 0〜3 一括実装）

Fable 5 指揮 + Opus 5 / Sonnet 5 の4並列実装。バックエンド全テスト **10,644 passed / 0 failed**。

- **migration**: `068_account_lifecycle.sql`（users 状態列9本 + `auth_events`）/
  `069_llm_usage_user_index.sql`（部分インデックス）。
- **新設モジュール**: `core/auth_events.py`（語彙・記録・IP抽出・credential キー除去
  `sanitize_payload`）/ `core/account_status.py`（30秒 TTL キャッシュ + `invalidate` +
  `touch_last_seen` 5分スロットル）/ `core/account_lifecycle.py`
  （`PURGE_TABLES` 20件・`RETAIN_TABLES` 34件・`due_users` / `purge_user` /
  `transfer_ownership`。import 時 assert で users の purge 混入を自己検査）。
- **編集**: `routes/auth.py`（§4.1 の判定順序）/ `dependencies.py`（`gen` クレーム照合）/
  `routes/admin.py`（API 8本 + email 重複 409）/ `schemas.py`（11 モデル）/
  `core/versioning/worker.py`（`_due_users` 並列追加。`object_type` に 'user' は不追加）/
  `core/notification_recipients.py`（全7関数に `JOIN users … status <> 'deleted'`）/
  `core/schema.py`（`AUDIT_ENTITY_USER_ACCOUNT` — カタログ36語彙に）/
  `capabilities.py`（8 capability）/ `admin-llm-usage.js` + `core/llm_usage/metrics.py`
  （`user_id` 集計軸・任意フィルタ・route 層で表示名解決）/ `admin.js`
  （teachers / students タブの一覧・操作 UI 約630行、ES5）。
- **3点セット**: アンカー 11件追加（266 → 277）/ マニュアル
  `system_admin/10-admin-teachers.md` 9節 + `teacher/16-admin-students.md` 3節 /
  `docs/admin_operations/users.md` 9節。
- **テスト**: `test_account_lifecycle_{auth,api,guardrails,purge,ui_static}.py` 計 370件超 +
  U層追記。`test_admin_help_ui_anchors.py` の件数は 277 に更新。
- **設計からの実装上の確定事項（意図的差分）**:
  1. Copilot `locate_steps` の anchor_id は `user_list` / `user_suspend_button` 等
     （`registerUiAnchors` キーとの一致をテストが強制するため。ヘルプの
     `data-ui-anchor`（`teachers.user-list` 等）とは別名前空間）。
  2. purge 中止通知は同一 kind/entity_id が24時間以内にあれば通知・監査ともスキップ
     （スイーパの毎周期再試行で同じ事実を積まないため）。
  3. AL1 ガードレールは `_code_only()`（コメント・docstring を除去し SQL リテラルは残す
     tokenize/ast 前処理)で検査（規約を説明する docstring が自爆しないため）。
  4. `backend/tests/conftest.py` に autouse fixture を追加（認証照合の既定 =
     active・世代0 + TTL キャッシュのテスト間クリア。既存 API テスト147件の
     「users 行なし→401」化を防ぐ）。
  5. `services.py` は変更不要だった（`record_review_event` をそのまま利用）。
  6. `docs/backend/api.md` は新設ルーター無しのため更新対象外（admin.py / auth.py /
     llm_usage.py の拡張のみ）。
