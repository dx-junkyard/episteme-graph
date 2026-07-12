# 状態管理・通知基盤 設計書 — Status Projection + 遷移イベント + 統合通知インボックス

G層（`guidance_layer_design.md`）の土台となる基盤層。通知とエージェント（Admin Copilot）で
ユーザーサポートを行うために必要な「状態の読み取りモデル」「遷移の検知」「通知の届け先」を
整備する。migration 038 で実装（設計時点では 039 を想定していたが、039 は先行して
G層 `guidance_layer_design.md` が使用したため、実装では **038** が割り当てられた）。

## 0. 背景 — 通知・エージェント支援に必要な3要素

| 要素 | 問い | 用途 |
|---|---|---|
| **現在状態（stock）** | 「この教材はいまどの段階か」 | バッジ（G層 next_steps）、Copilot の状況回答、UI の状況列 |
| **遷移（flow）** | 「解析がいつ完了/失敗したか」 | 通知（ブラウザを閉じていても後から届く） |
| **届け先（inbox）** | 「誰に何を知らせ、既読をどう管理するか」 | 通知UI、将来のエージェント起動トリガー |

## 1. 現状調査 — 何があるか（2026-07-10 時点の ura-dev）

状態管理の**部品は揃っているが、分断されている**。

| 仕組み | 場所 | 内容 | 限界 |
|---|---|---|---|
| `documents.status` | init.sql | uploaded / processing / completed / failed | 粗い。パイプライン詳細を持たない |
| `document_analysis_runs` | migration 015 | run 単位の pending/running/completed/failed + `current_stage` + `stage_outputs` | 実行履歴であり「教材の現在状態」の正規形ではない |
| `background_tasks` | migration 005 | 汎用ジョブ状態（pending/processing/completed/failed, `task_type`, `result_data`） | **ジョブ単位・ポーリング前提**。`GET /api/admin/tasks/{id}` を admin.js が `setInterval` で見る |
| 機能ローカル状態 | 004/006/014/018/019 | `reextraction_jobs` / `lecture_audio_cache` / `section_assembly_status` / `course_builder_session_status` / `revision_runs` | 各機能が独自語彙。横断参照口が無い |
| コース状態 | `learning_courses` | `is_template` / `is_published` + `data` JSONB（`cartridge_id`, `topics[].atlas_node_id`, sources） | サーバ側の正規投影が無く、原稿/音声の生成状況は **admin.js がその場で合成**（draft/partial/generated/audio_generated/no_chunks） |
| `theory_review_events` | migration 013〜 | 全層横断の**人間操作の監査ログ**（append-only, entity_type 拡張式） | パイプラインの自動遷移は記録されない。通知への配線が無い |
| `share_notifications` | migration 037（V層） | **通知インボックスの実物**（recipient / kind / read_at / acted_at + 🔔 バッジ UI in versioning.js） | V層イベント（版発行・削除猶予）専用。kind が CHECK で固定 |
| G層設計 | guidance_layer_design.md | 状態から導出する未実施事項（next_steps） | 設計のみ・未実装。stock のみで flow を扱わない |

### 1.1 決定的なギャップ

1. **正規のライフサイクル語彙が無い** — 「教材の処理状況」を答えるには 3 テーブル
   （documents / document_analysis_runs / background_tasks）の突合が要り、その合成ロジックが
   UI（admin.js）にしか存在しない。Copilot・通知・G層がそれぞれ再実装する構造になっている。
2. **遷移がブラウザの生存に依存する** — 完了検知は画面を開いている間の `setInterval`
   ポーリングのみ。教員がアップロード後にブラウザを閉じると、解析完了・失敗は**誰にも
   届かない**。翌日ログインしても「何が終わったか」を知る口が無い。
3. **通知インボックスが V層専用** — 汎用の「解析が完了しました」「音声生成が失敗しました」を
   届ける器が無い。

## 2. 設計原則（既存層の文化を継承）

- **S1 導出できる状態は保存しない**: 現在状態は既存テーブルからの**決定論的投影**
  （G1 と同一）。状態の正本を複製するテーブルは作らない。
- **S2 遷移だけを記録する**: イベントは append-only の事実（from→to, 出所, 発生時刻）。
  冪等（UNIQUE 制約）で二重発火しない。
- **S3 非LLM・既存層非改変**: 投影・遷移検知・通知 fan-out に LLM を使わない。
  A〜V 層のテーブル・コードを変更しない（読み取り + 新規テーブルへの書き込みのみ）。
- **S4 情報を落とさない**: 通知は既読・却下で状態遷移し、行削除しない（P4）。
- **S5 fail-closed**: 状態が投影できないエンティティは `unknown` を返す（捏造しない）。
  通知の宛先はサーバ側で権限判定（所有者・共有 editor のみ）。
- **S6 学習者を監視しない**: 本基盤は教員向け運用状態のみ扱う。学習者の進捗を教員に
  push しない（P3 は既存の k-匿名集約に委ねる）。

## 3. アーキテクチャ

```
既存テーブル（正本・非改変）                     新規（migration 038）
┌────────────────────────┐
│ documents / document_analysis_runs │──┐
│ background_tasks / lecture_audio_… │  │ ①投影（保存しない）
│ learning_courses / chunks          │  ├──▶ core/status/projector.py ──▶ GET /api/admin/status/…
│ theory_review_events               │  │         │                          ├─ admin UI 状況列
└────────────────────────┘  │         │                          ├─ Copilot 状況回答
                                        │         ▼                          └─ G層 next_steps（同じ語彙）
                                        │ ②遷移検知（watermark scan）
                                        └──▶ core/status/watcher.py
                                                  │ 冪等 emit
                                                  ▼
                                            status_events ──③fan-out──▶ user_notifications
                                                                              │
                                                              ④統合インボックス API（V層と読み取り併合）
                                                                              ▼
                                                                    ヘッダー 🔔（既読/却下）
```

### 3.1 ① Status Projection（`backend/core/status/`、FastAPI 非 import）

`schema.py` にライフサイクル語彙の**正本**を定義し、`projector.py` が既存テーブルから投影する。

**教材（MaterialStatus）**:
`uploaded → chunking → analyzing(stage) → analyzed | analysis_failed(stage, reason)`
- 出所: `documents.status` + 最新 `document_analysis_runs`（status / current_stage /
  error_message）+ 進行中 `background_tasks`。
- `analyzed` の判定は run completed かつ成果物（theory_components 等）が存在すること。

**コース（CourseStatus）**: 単線ではなく**チェックポイント集合**として投影する
（順序が強制でないため）:
`{registered, script_status(draft/partial/generated), audio_status(none/partial/generated),
atlas_bound(bool), published(bool), shared(bool)}`
- 出所: `learning_courses` 列 + `data` JSONB + `lecture_audio_cache` + chunks の原稿有無。
- admin.js 内のその場合成（statusLabel 等）はこの API の結果表示に置き換えていく（段階移行）。

API（`routes/status.py`、実パス `/api/admin/status/...`、TEACHER 以上・自分の所有物のみ）:
- `GET /api/admin/status/overview` — 自分の教材・コースの状態一覧（G層バッジと同じ更新規律:
  ログイン時・タブ切替時・操作完了後。ポーリングしない）
- `GET /api/admin/status/materials/{material_id}` / `GET /api/admin/status/courses/{course_id}`

**G層との統合**: `next_steps.py` のルール条件はこの projector を呼ぶ形に実装する
（未実施事項 = 状態投影の否定形。語彙を二重化しない）。

**Copilot との統合**: `intent.py` に `status_query`（「解析どうなってる？」）を追加し、
guidance モードで projection API の結果を事実文で回答する（DB 非変更・根拠併記 P4）。
フロントの `registerScreenContext`（DOM 由来）とは独立に、サーバ側で正確な状態を参照できる。

### 3.2 ② 遷移検知（`core/status/watcher.py` — snapshot-diff 不要の watermark 方式）

書き込みパス計装（outbox）は A層・パイプラインコードの改変を要するため採らない（S3）。
代わりに **既存テーブル自体が遷移の証跡を持っている**ことを利用する:

| 監視対象 | 遷移の証跡 | 発火するイベント |
|---|---|---|
| `document_analysis_runs` | status ∈ {completed, failed} + `completed_at`/`updated_at` | `material.analysis_completed` / `material.analysis_failed` |
| `background_tasks` | status ∈ {completed, failed} + `updated_at` + `task_type` | `course.script_generated` / `course.audio_generated` / 各 failed |
| `theory_review_events` | append-only（人間操作は既に全部ここにある） | `course.published` / `course.atlas_bound` / `document.shared` など（entity_type で選別） |

- worker は `threading.Thread`（tension / anchor / reconstruction worker と同型）。
  周期スキャン（既定 60 秒、`STATUS_WATCH_INTERVAL` で調整）+ 自コードが持つ完了パス
  （タスク完了ハンドラ等）からの任意 flush。
- **watermark**: `status_events` の `MAX(occurred_at) per source_table` を再開点にする
  （専用 watermark テーブル不要）。
- **冪等**: `UNIQUE(source_table, source_id, event_kind)` — 再スキャンしても二重 emit しない。

### 3.3 ③ 通知 fan-out + ④ 統合インボックス

- `notification_rules.py`（決定論的・非LLM）: イベント種別 → 宛先（教材所有者 /
  コース所有者 / 共有 editor）+ 重要度。v1 の配信対象は **完了・失敗の 6 種**
  （analysis completed/failed, script completed/failed, audio completed/failed）に限定
  （段階登録。通知過多は通知が無いのと同じ）。
- `user_notifications`（migration 038）: `share_notifications` と同形
  （recipient_id / kind / entity_type / entity_id / payload / created_at / read_at /
  dismissed_at）。**V層のテーブルは変更しない**。
- 統合 API `GET /api/admin/notifications` が `user_notifications` と
  `share_notifications` を**読み取りで併合**して返す（unread_count も合算）。
  既存の versioning.js 🔔 ボタンをこの統合 API に差し替え、ヘッダーの通知口を一本化する
  （届く種類が増えるだけで V層の挙動は不変）。
- 既読 = `read_at`、却下 = `dismissed_at`（S4: 行削除しない）。
- **G層バッジとの役割分担**: 🔔 = flow（何が起きたか。既読で消える）/
  📋 = stock（いま何が未実施か。やれば消える）。両方が同じ status 語彙を使う。

## 4. DB（migration 038）

```sql
-- 遷移イベント（append-only の事実ストリーム）
CREATE TABLE IF NOT EXISTS status_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_kind   TEXT NOT NULL,             -- 'material.analysis_completed' 等
    entity_type  TEXT NOT NULL,             -- 'material' | 'course' | 'document'
    entity_id    TEXT NOT NULL,
    from_state   TEXT NOT NULL DEFAULT '',
    to_state     TEXT NOT NULL,
    source_table TEXT NOT NULL,             -- 遷移の証跡テーブル
    source_id    TEXT NOT NULL,             -- 証跡行の ID
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at  TIMESTAMPTZ NOT NULL,      -- 証跡側のタイムスタンプ
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_table, source_id, event_kind)   -- 冪等（S2）
);
CREATE INDEX IF NOT EXISTS idx_status_events_entity ON status_events(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_status_events_watermark ON status_events(source_table, occurred_at DESC);

-- 汎用通知インボックス（share_notifications と同形・V層非改変）
CREATE TABLE IF NOT EXISTS user_notifications (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    event_id     UUID REFERENCES status_events(id),
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at      TIMESTAMPTZ,
    dismissed_at TIMESTAMPTZ                -- 却下も保持（S4）
);
CREATE INDEX IF NOT EXISTS idx_user_notif_recipient
    ON user_notifications(recipient_id, read_at, created_at DESC);
```

現在状態のスナップショットテーブルは**作らない**（S1: 状態は投影、保存するのは遷移のみ）。

## 5. ガードレール（`backend/tests/test_status_guardrails.py`）

- `core/status/` が FastAPI / LLM クライアントを import しない（S3）。
- watcher の再実行で `status_events` が増えない（冪等性）。
- 通知の宛先が所有者・共有 editor 以外に広がらない（S5 fail-closed）。
- 既読・却下が行削除しない（S4）。
- 投影不能エンティティが `unknown` を返す（例外で落ちない）。
- v1 の通知 kind が 6 種を超えない（段階登録の構造的強制）。

## 6. 段階導入

| Phase | 内容 | 依存 |
|---|---|---|
| 1 | `core/status/schema.py` + `projector.py` + status API。admin.js の状況列とG層 next_steps をこれに載せる | なし（G層 Phase 1 と同時が理想） |
| 2 | migration 038 + watcher + fan-out + 統合インボックス API + 🔔 一本化 | Phase 1 |
| 3 | Copilot `status_query` intent / エージェント向けイベント購読（status_events を将来の自動支援のトリガーに使う） | Phase 2 |

## 7. 非スコープ / 決定事項

- **メール・プッシュ等の外部配信はしない** — インボックスはログイン時の受動表示のみ
  （G層と同じ判断。将来やるなら fan-out の先に足すだけの構造にしてある）。
- **学習者向け通知は扱わない**（S6）。
- **既存の機能ローカル状態テーブルは廃止しない** — 正本はそのまま、投影で束ねる。
  語彙の付け替え・移行マイグレーションはしない。
- **status_events は通知専用ではない** — 将来のエージェント自動支援（例: 解析失敗を
  Copilot が能動的に説明する）・KPI 集計の共通事実ストリームとして設計してある。
  ただし v1 の消費者は通知 fan-out のみ。
