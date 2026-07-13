# 共有物のバージョン管理 + 更新通知 + 削除猶予（V層）設計書

パイプライン生成物（教材の解析成果）とコースを「発行版（Release）」として不変スナップショット化し、
共有先の教員が**所有者の一方的な更新・削除から保護される**第五の運用機構（仮称 **V層 = Versioning layer**）。
更新は通知され、共有先は**同意するまで自分の版に留まり**、削除は**猶予表示のうえ期限後に全ユーザーから物理削除**される。

- ステータス: 実装済み（`learning-ux`。Phase 0–5。migration 037 / `backend/core/versioning/` /
  `backend/api/routes/versioning.py` / `frontend/public/js/versioning.js` / `admin.js`・`app.js` フック /
  `backend/tests/test_shared_versioning_{migration,api,guardrails,logic}.py`）。**DB 適用・pytest・E2E は docker 復帰後に検証予定**（本セッションは `py_compile` + 静的検査まで）。
- 前提ブランチ: `learning-ux`
- マイグレーション: `backend/db/037_shared_versioning.sql`（新規・参照専用。実適用は `main.py` の `_run_migrations()`）
- 関連: パイプラインの版管理（migration 019 の `document_analysis_runs` / `active_analysis_run_id`）/
  グループ共有（migration 010 `course_group_permissions` / migration 035 `document_group_permissions`）/
  承認・共有レイヤー C層（`component_citations`）/ 監査（`theory_review_events`）

> **2026-07 更新（アーキテクチャ整理 Tier 3）**: `share_notifications` は migration 045 で
> `user_notifications`（状態管理・通知基盤, migration 038）に統合され（`source='shared'` で区別）、
> `course_group_permissions`（010）/ `document_group_permissions`（035）は migration 044 で
> `object_group_permissions` に統合された。マイグレーションの実行方式自体も一本化され、
> `main.py::_run_migrations()` は廃止済みで `backend/core/migrations.py` のランナーが
> `backend/db/*.sql` を毎起動・冪等再実行する。以下の本文はこれらの統合が行われる**前**の
> 設計当時の記述であり、テーブル名・実行方式の記載は歴史的経緯として残している
> （API パス・挙動は統合後も不変）。詳細は `docs/architecture/data-model.md` と
> `docs/architecture/consolidation_survey_2026-07.md` 第4部（Tier 3-13/14/15）を参照。

---

## 1. 背景 — なぜ必要か

パイプライン生成物（`theory_components` / `theory_claims` / `theory_component_graphs` + `document_analysis_runs`）と
コース（`learning_courses`）は、グループ権限（viewer|editor）で**他の教員と共有**される。しかし現状:

- コースは migration 011 で「不変マスター1本 + `learning_states`（学習状態差分）」に刷新済みで、
  マスター更新は**全消費者に即反映**され、削除は `ON DELETE CASCADE` で学習状態ごと即消滅する。
- パイプライン成果は投影テーブルが `document_id` 単位で毎回上書きされ、再解析(reanalyze)や revision accept で
  **共有先が見ている内容が一方的に変わる**。旧版は `document_analysis_runs.stage_outputs`（不変）にしか残らない。
- 共有先教員が引用/再利用（`component_citations`）した内容も、元が更新・削除されると破綻する。

→ 「共有物を使っている最中の一方的な更新・削除」で影響が大きい。本機構はその上に
**版発行 → 更新通知 → 同意して取り込み → 削除は猶予表示のうえ期限後に全ユーザー物理削除** の閉ループを積む。

### 確定した仕様（要件ヒアリング）

1. **版（Release）は所有者の明示発行のみ**（「共有版として発行」）。下書き編集は発行するまで共有先に見えない。
2. **削除猶予は所有者が削除予約時に指定（既定14日）**。期限（`purge_after`）後に全ユーザーから物理削除。
3. **消費側は fork せず、発行版にピン留めして読む**（＝同意するまで内容が変わらない）。
   利用形態は「閲覧＋自コースへ引用/再利用」と「editor での共同編集」。

---

## 2. 設計原則（不変条項）

- **A層非改変**: `src/episteme_graph/agents/` の生成パイプラインは読むだけ。既存 API/関数を呼ぶ側として実装する。
- **core は FastAPI を import しない**（project rule 2。テスタビリティ確保）。`backend/core/versioning/` は
  `theory_review_events` への監査も含めて独立して完結する。
- **発行・削除予約は所有者のみ**。editor は working copy を編集できるが発行/削除はできず、**常に HEAD を読む（ピンしない）**。
- **監査必須**: 発行・削除予約/取消/purge・取り込みを `theory_review_events`（entity_type は open vocab）に記録。
- **fail-closed**（権限判定はサーバ側）。**物理削除は冪等な purge 経路に一本化**する。
- **既存資産の再利用**: document の版は migration 019 の不変 run をピン（成果物を複製しない）。監査は既存表。
  グループ権限 join・`component_citations` を流用する。

---

## 3. モデル

- **版 = Release（不変スナップショット）** — `shared_versions`。
  - course: `snapshot = {title, description, data}`（発行時点の working copy 全体）。
  - document: `snapshot = {analysis_run_id, cartridge_id, manifest, source_path}`。成果物本体は複製せず、
    既に不変な `document_analysis_runs.stage_outputs` を指す `analysis_run_id` をピンする。
- **オブジェクト状態** — `shared_version_state`（`(object_type, object_id)` 主キー）。
  `active_release_id`（新規/未ピン消費者が見る版）・`latest_version_no`・`lifecycle`（active / pending_deletion / purged）・
  削除予約情報。
- **消費者ピン** — `shared_version_subscriptions`。`pinned_release_id` が消費者の見る版。所有者が新版を発行しても
  既存ピンは動かない。**未ピンの viewer・学習者は `active_release_id` に追従**。
- **通知** — `share_notifications`（インボックス）。
- **ポリモーフィック**（course=TEXT id, document=UUID）のため `object_id TEXT`・**FK なし** →
  削除は `purge_object` の明示クリーンアップに一本化。document は必ず `_resolve_document`（`services.py`）で
  `documents.id::text` に正規化してから版キーに使う。

---

## 4. DB スキーマ（migration 037）

`backend/db/037_shared_versioning.sql` が正本リファレンス。実適用は `backend/api/main.py` の `_run_migrations()`
（Migration 037 ブロック、全文 `IF NOT EXISTS` で冪等）。成功ログは `Migrations (002-037) applied successfully.`。

| テーブル | 役割・主な列 |
|---|---|
| `shared_versions` | 不変 Release。`object_type CHECK('course','document')` / `object_id TEXT` / `version_no INT` / `snapshot JSONB` / `note` / `published_by` / `UNIQUE(object_type,object_id,version_no)` |
| `shared_version_state` | `PRIMARY KEY(object_type,object_id)` / `active_release_id` / `latest_version_no` / `lifecycle CHECK('active','pending_deletion','purged')` / `delete_scheduled_at`・`delete_purge_after`・`delete_scheduled_by`・`delete_reason`。部分 index `WHERE lifecycle='pending_deletion'` |
| `shared_version_subscriptions` | `UNIQUE(object_type,object_id,subscriber_id)` / `pinned_release_id` / `status('active','unsubscribed')` / `subscriber_id → users ON DELETE CASCADE` |
| `share_notifications` | `recipient_id → users ON DELETE CASCADE` / `kind CHECK('version_published','deletion_scheduled','deletion_cancelled','deleted')` / `release_id` / `payload JSONB` / `read_at` / `acted_at` |
| `component_citations`（既存, migration 021 を ALTER） | 引用の版固定列 `source_object_type` / `source_object_id` / `source_release_id` / `source_version_no` を追加 |

---

## 5. コアモジュール `backend/core/versioning/`（FastAPI 非依存）

| モジュール | 役割 |
|---|---|
| `schema.py` | 語彙・定数の正本（object_type / lifecycle / notification kind / 監査 entity_type / `DEFAULT_GRACE_DAYS=14`）+ 例外（`VersioningError` / `PurgedError` / `PendingDeletionError` / `AdoptConflictError`） |
| `audit.py` | `theory_review_events` への best-effort 監査追記（core 内で完結） |
| `releases.py` | `build_course_snapshot` / `build_document_snapshot`（`persistence.get_active_analysis_run_id` 再利用）/ `publish_release`（`shared_version_state` を `FOR UPDATE` して `version_no=latest+1` 採番→INSERT→active 更新。既存ピンは不動）/ `list_releases` / `get_release` / `get_state` |
| `subscriptions.py` | `ensure_pin`（初回引用時の UPSERT）/ `adopt_latest`（`pinned_release_id IS NOT DISTINCT FROM :expected` の楽観ロック→不一致は 409）/ `resolve_effective_release_id`（ピン有→pinned / 無→active）/ `subscriber_ids` |
| `notifications.py` | `fan_out`（viewer\|editor グループメンバー − 所有者へ配信）/ `recipients_for` / `notify_users`（purge 後の明示宛先配信）/ `list_inbox` / `unread_count` / `mark_read` / `mark_all_read` |
| `deletion.py` | `default_purge_after(days=14)` / `schedule_deletion` / `cancel_deletion` / `purge_object`（全ユーザー物理削除・冪等） |
| `resolver.py` | `resolve_course_data`（ピン viewer に版スナップショットを返す）/ `resolve_document_run_id` / `view_badges`（更新あり/削除予定バッジ用状態） |
| `worker.py` | 削除猶予の定期スイーパ（`sweep_once` / `run_forever` / `start_background_sweeper`。`threading.Thread` daemon、`VERSION_SWEEPER_ENABLED` で制御） |

### 発行（publish_release）
スナップショットをロック外で構築 → `shared_version_state` を `SELECT ... FOR UPDATE` → `version_no=latest+1` を採番 →
`shared_versions` INSERT → `state.latest_version_no`/`active_release_id` を更新。`purged` は 410、`pending_deletion` は 409。

### 取り込み（adopt_latest）
ピンを `active_release_id` へ前進。既存ピンは `expected_pinned_release_id` が現在値と一致しなければ **409**（楽観ロック、
`accept_revision` の active-run ガードと同型）。関連 `version_published` 通知に `acted_at` を刻む。

### 物理削除（purge_object）— 全ユーザーから削除 + orphan gap 解消
1オブジェクトを独立トランザクションで冪等削除（`purged` 再実行は no-op）。
- **course**: `learning_chat_history` → `learning_courses`（CASCADE で `learning_states` / `course_group_permissions` / course スコープ `theory_*`）。
- **document**: 所有者の該当コース（`data.sources[].material_id` 一致）→ **`document_analysis_runs`** と document スコープ `theory_claims`/`theory_components`/`theory_component_graphs`/`theory_component_links`（＝従来消し残していた **orphan gap** を解消）→ **D層 FK-less 孤児**（`epistemic_ledger`・`counterfactual_sessions` を `document_id`、`challenges`（→ `verification_proposals` は `ON DELETE CASCADE`）と `epistemic_ledger` を削除対象 claim/component の `target_id` で削除。theory_* 削除の**前**に対象 id を集める。`assumption_nodes` は複数論文横断のため対象外）→ `document_group_permissions` → `chunks` → `documents` → runs（`documents.active_analysis_run_id` FK のため runs は最後）。
- 版テーブル（`share_notifications`→`shared_version_subscriptions`→`shared_versions` の FK 順）は `_cleanup_version_tables()` で明示削除し、state を `purged` 墓標として残す。
- **手動削除の合流（既存 `delete_material`/`delete_course`）**: 物理削除自体は既存コード（非改変）だが、削除後に `deletion.teardown_versioning()` を best-effort で呼び、版・ピン・通知を掃除して state を `purged` 墓標にし購読者へ `deleted` を配信する（幽霊ピン・陳腐化通知・active のままの state を残さない）。宛先はグループ権限が消える前に `_versioning_collect_recipients()` で収集する。

### スイーパ（worker）
`main.py _lifespan` で `_run_migrations()` 成功後に daemon 起動（`VERSION_SWEEPER_ENABLED` 既定 on、
`VERSION_SWEEP_INTERVAL_SECONDS` 既定 3600）。`lifecycle='pending_deletion' AND delete_purge_after<=now()` を検出し、
**権限が消える前に宛先を収集** → `purge_object` → `deleted` 通知配信 → 監査。1件失敗は skip。

---

## 6. API — `backend/api/routes/versioning.py`（admin 配下・実パス `/api/admin/shared/...`）

全 endpoint `Depends(_require_teacher)`。所有者限定は `services.user_owns_course`/`user_owns_document`、
閲覧は `user_can_view_*`。document は `_resolve_document` で正規化。非所有者は既存慣習に合わせ **404**。

| Method + Path | ガード | 内容 |
|---|---|---|
| `POST /shared/{object_type}/{object_id}/releases` | 所有者 | 発行 → `version_published` 通知 |
| `GET /shared/{object_type}/{object_id}/releases` | viewer | 版一覧 |
| `GET /shared/releases/{release_id}` | viewer | 単一 Release（スナップショット含む） |
| `GET /shared/{object_type}/{object_id}/version-state` | viewer | 版状態 + 呼び出し元バッジ（更新あり/削除予定） |
| `POST /shared/{object_type}/{object_id}/deletion` | 所有者 | 削除予約（`grace_days`/`purge_after`, 既定14日）→ `deletion_scheduled` 通知 |
| `DELETE /shared/{object_type}/{object_id}/deletion` | 所有者 | 予約取消 → `deletion_cancelled` 通知 |
| `POST /shared/{object_type}/{object_id}/subscription/adopt` | viewer | 取り込み（`expected_pinned_release_id` で楽観ロック、不一致 409） |
| `GET /shared/subscription/me` | teacher | 本人のピン一覧（更新有無つき） |
| `GET /shared/notifications`（`?unread_only=`）| 本人 | インボックス（未読数つき） |
| `POST /shared/notifications/{id}/read` / `POST /shared/notifications/read-all` | 本人 | 既読化 |

**エラーマッピング**: `PurgedError`→410 / `PendingDeletionError`・`AdoptConflictError`→409 / `VersioningError`（過去日・無効 ISO 等）→422。

**学習者向け**（`backend/api/routes/learning.py`）:
`GET /api/learning/courses/{course_id}/version-notice` — 受講可能なコースの削除予定を一行返す（fail-open。バナー表示用）。

**監査 entity_type**: `shared_release`（発行）/ `shared_deletion`（schedule|cancel|purge）/ `shared_subscription`（adopt|auto_pin）。

---

## 7. 読み取りの版解決

- **コース（完全対応・全読み取り経路で統一）**: 版解決は `services._apply_course_version_view(course_id, viewer_id, live_data)`
  に**一元化**し、学習者の主経路 `get_course_data`（GET コース・チャット・lecture・atlas_view が使用）と
  `get_viewable_course_data` / `get_accessible_course_data`（public/group 両分岐）の**全経路が必ず通す**。
  これにより同一学習者が経路ごとに違う版を見る不整合（主画面は HEAD、reconstruction/doubt は旧版）を解消する。
  所有者・editor は HEAD（live working copy）、**純 viewer・学習者は有効な版（ピン or active）のスナップショット**を見る。
  版未発行のコースは live にフォールバック（既存挙動・後方互換）。解決失敗時も live へフォールバック（学習を止めない）。
- **削除猶予バナー（学習者）**: `GET /api/learning/courses/{id}/version-notice` は `services.course_deletion_notice()` を使い、
  コース自体の削除予約に加え、**元教材の削除予約**も検出する（教材 purge は所有者のコースを巻き添え削除するため、
  コースの `sources[].material_id` が指す document が pending_deletion かつ所有者一致なら学習者へ警告する）。
- **引用の版固定**: `theory_components.cite_explanation`（`POST /explanations/{id}/cite`）で、引用元 document の
  有効な版へ **auto-pin** し、`component_citations` に `source_release_id`/`source_version_no` を刻む。
  引用元が更新されても、取り込むまで引用時点の版に留まる（best-effort）。
- **バッジ**: `resolver.view_badges` が `update_available`（ピンが active より前）と `lifecycle`/`delete_purge_after` を返す。

### 既知の限界（follow-up）
document 成果物の **4読み取りエンドポイント自体のピン凍結ブラウズは未実装**。`document_analysis_runs.stage_outputs`
からの `ClaimOut`/`TheoryComponentOut`/`ComponentGraphResponse` 完全再構築はリスク大のため保留し、消費側は現行(active)版を
見つつ「更新あり」バッジ＋通知＋取り込み、および**引用の版固定**で保護される。**UI はこの未対応を正直に反映する**
（`versioning.js`: document のモーダル説明・通知文言・取り込みラベルは course と分岐し、「同意するまで自分の版に留まる」
という凍結の約束を document では出さない）。既存の即時削除 `delete_material`/`delete_course` の**物理削除ロジックは非改変**
のまま、削除後に `teardown_versioning()` を best-effort で呼んで V層状態（版・ピン・通知・state）を掃除する形で合流させた
（猶予付き削除は引き続き新 API 経由）。

---

## 8. フロントエンド

- **`frontend/public/js/versioning.js`（ES5 / `window.Versioning`）**: `init({apiFetch, state})` で起動。
  - `openModal(objectType, objectId, title)`: 発行（メモ付き）/ 版履歴 / 削除予約（猶予日数・理由）/ 予約取消。削除予定バナー。
  - `initInbox()`: 右下の通知ベル🔔（未読バッジ）+ パネル。`version_published` に「取り込む」（`version-state` で現在ピンを取得し
    `expected_pinned_release_id` を載せて adopt、409 は再読込）、`deletion_scheduled` は猶予期限を表示。
- **`admin.js`**: 教材管理行に「共有版」ボタン（document）、コース管理（所有者行）に「共有版」ボタン（course）。
  `initApp()` で `Versioning.init(...)` + `Versioning.initInbox()`。
- **`app.js`（学習者）**: 受講コースが削除予約中なら猶予バナーを表示（ピン UI なし＝active 追従）。`admin.html` に script 追加。

---

## 9. テスト（`backend/tests/`）

実 DB を使わない規約（静的検査 + TestClient/monkeypatch + 純ロジック）に準拠。

- `test_shared_versioning_migration.py` — 参照 SQL と `main.py` インライン適用の整合（4表・CHECK・UNIQUE・引用列・成功ログ）。
- `test_shared_versioning_api.py` — TestClient。認証/RBAC（学生 403）・所有者ガード（非所有者 404）・
  発行 201・エラーマッピング（409/410/422）・通知インボックス・学習者 version-notice（404/200/fail-open）。
- `test_shared_versioning_guardrails.py` — core が FastAPI 非 import / 所有者ガード / purge の orphan gap 解消 /
  スイーパの thread+env / 監査語彙 / ルータ登録。
- `test_shared_versioning_logic.py` — `default_purge_after` の日数、`view_badges` の `update_available` 導出（DB 関数は monkeypatch）。

---

## 10. 検証手順（docker 復帰後）

1. `docker compose up -d --build api-server` → 起動ログ `Migrations (002-037) applied successfully.` と4表生成を確認。
2. `cd backend && pytest backend/tests/test_shared_versioning_*.py -v`。
3. E2E: 教員A が教材/コースを発行 → 教員B（同グループ viewer）が通知受信・旧版表示 → A 再発行 → B に「更新あり」→
   B が取り込み → 最新表示。A が削除予約（猶予短め）→ B・学習者に猶予バナー → 期限後にスイーパが全削除・`deleted` 通知
   （テストでは `core.versioning.worker.sweep_once()` を直接呼ぶ）。
