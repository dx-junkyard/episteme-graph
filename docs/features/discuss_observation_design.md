# discuss 観測基盤（Observation Layer for Phase 3 Gate）設計書

- 対象: episteme-graph（ura-dev）
- ステータス: 設計確定（2026-07-25）・同日実装
- 親文書: `docs/features/discussion_mode_design.md`（§6.4 Phase 3 ゲート・§7 観察ポイント・裁定 #9）
- 目的: discuss モードの Phase 3（v2）着手判断を「実測ゲート」で行えるようにする。
  ①必要データの蓄積 ②蓄積状況のシステム管理者向け可視化 ③分析用ダンプ（tar.gz / zip）の3点。

---

## 0. なぜ必要か（現段階の課題）

discuss 設計書は「Phase 1/2 の U層実測でモードの価値を確認してから Phase 3 に着手する」
（§6.4）と定めたが、2026-07-25 時点の実装では **観察ポイント（§7）の大半に計器がない**:

| # | 課題 | 現状 | 本設計での対応 |
|---|---|---|---|
| 1 | LLM 利用量は蓄積されるが「十分溜まったか」が見えない | `llm_usage_events`（migration 043）に `learning:chat_discuss` タグで自動蓄積。ただし SYSTEM_ADMIN が見られるのは合計値のみで、分析開始の判断基準がない | 観測状況 API + 参考目安（§4） |
| 2 | content_grounding 分布が蓄積されない | 応答ごとに計算してレスポンスで返すだけ。interest_traces payload の `cited_chunk_ids` から事後近似できるが不正確 | `_trace_payload` に `content_grounding` / `discuss_scope` を追記（§2-1） |
| 3 | 着地画面・開幕画面・分岐チップの利用が一切計測されない | フロント完結・計測 API なし。「着地率」（§7 リスク5）は測定不能 | `discuss_metric_events`（migration 060、§2-2） |
| 4 | 生成プロンプト応答率（§7 リスク2）は直接測定できない | 「学習者が生成プロンプトに応じたか」は LLM 判定なしに判別不能 | **直接測定はしない（誠実に断念）**。近似指標 = 応答後の継続送信（daily_summary の turn 系列から導出）。ダンプ README に近似である旨を明記 |
| 5 | 429（コスト上限超過）の発生が観測できない | CostGate は in-memory・プロセスローカルで、拒否された呼び出しは `llm_usage_events` に載らない | v1 非対応の**既知の限界**として記録（対応するなら別途。専用上限判断には成立呼び出し量で足りる） |
| 6 | 着地画面経由の confirm と digest 経由の confirm が区別できない | `theory_review_events` の監査は入口を区別しない | UI イベント側（`landing_confirmed`）で区別可能に |
| 7 | 外部分析への持ち出し経路がない | ダンプ機能なし。DB 直接アクセスが必要 | 観測ダンプ API（§5。tar.gz / zip、本文非含有・仮名化） |
| 8 | 観察の前提となる実運用が始まっていない | Phase 0〜2 は未コミット・docker E2E 未実施 | 本設計のスコープ外（残作業として継続） |

## 1. 不変条項（DO1〜DO6）

discuss の DM1〜DM8、B層 P1〜P7、U層 U1〜U8 を継承したうえで:

- **DO1 本文を持ち出さない**: 観測イベント・ダンプに学習者の発話本文・問いの逐語・paraphrase・
  evidence_quote を**一切含めない**（量的分析に本文は不要。質的分析は本人可視の既存 UI の領分）。
- **DO2 仮名化**: ダンプ内の user_id は HMAC-SHA256（鍵は `settings.jwt_secret` 由来・
  ドメイン分離文字列付き）による安定仮名。ダンプ間で同一ユーザーを追跡できるが復元はできない。
- **DO3 学習者に数値を見せない**: 計測は内部専用。学習者向けレスポンス・UI に件数・率を出さない
  （atlas_cue_events と同じ規律）。可視化は SYSTEM_ADMIN のみ（U5 と同じ）。
- **DO4 削除 API を作らない**: `discuss_metric_events` は append-only（U6 と同じ）。
- **DO5 参考目安は自動ゲートにしない**: 「分析に十分か」の目安は表示のみ。閾値到達で何かが
  自動的に起きることはない（判断は常にオーナー）。
- **DO6 計測失敗で UX を止めない**: フロントのイベント送信は fire-and-forget（失敗は無視）。
  記録側の例外はチャット・画面遷移に漏らさない。

## 2. データ蓄積

### 2-1. 会話ターンの grounding / scope（migration 不要）

`learning_chat` の `_trace_payload` に2キー追記（全モード共通で `content_grounding`、
discuss のみ `discuss_scope`）。interest_traces は既存テーブルのまま。
記録開始日以前の痕跡には無いキーなので、集計側は「記録済み件数」を分母として明示する（U1 と同じ誠実さ）。

### 2-2. UI イベント（migration 060 `discuss_metric_events`）

```sql
CREATE TABLE IF NOT EXISTS discuss_metric_events (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL,
    course_id   TEXT NOT NULL DEFAULT '',
    event       TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

（FK は張らない — 計測はユーザー削除と独立に残す。atlas_cue_events と同じ思想。索引は
`(event, created_at)` と `(user_id)`。）

**イベント語彙（サーバ側ホワイトリスト。未知イベントは 422）**:
`discussion_entered` / `opening_shown` / `opening_starter_clicked` / `opening_backbone_clicked` /
`branch_deep_clicked` / `branch_wide_clicked` / `scope_switched` / `landing_shown` /
`landing_confirmed` / `landing_dismissed` / `landing_skipped` / `landing_probe_clicked` /
`landing_continue_clicked`

**payload ホワイトリスト**: `scope`（course_sources|all_visible）/ `reason`
（explicit|topic_switch|timeout）/ `kind`（tension|anchor）。**それ以外のキーはサーバ側で捨てる**
（本文混入の構造的防止 — DO1）。

**取込 API**: `POST /api/learning/discuss/metric-events`（認証必須・本人記録のみ）
body `{"events": [{"event": str, "course_id": str, "payload": {...}}]}`（1リクエスト最大20件）。
レスポンスは `{"recorded": n}` のみ（UI はこれを表示しない — DO3）。

## 3. 送信ポイント（フロント）

- `app.js`: `discussion_entered`（enterDiscussMode）/ `scope_switched`（トグル変更）/
  `branch_*_clicked`（分岐チップ）
- `discuss.js`: `opening_shown` / `opening_starter_clicked` / `opening_backbone_clicked` /
  `landing_shown`（payload.reason）/ `landing_confirmed`（payload.kind）/ `landing_dismissed` /
  `landing_skipped` / `landing_probe_clicked` / `landing_continue_clicked`
- すべて fire-and-forget（navigator.sendBeacon 級の気軽さ。await しない・失敗は握りつぶす — DO6）。

## 4. 蓄積状況の可視化（SYSTEM_ADMIN）

`GET /api/admin/discuss/observation-status` →
`usage`（learning:chat_discuss / chat_casual / chat の3タグ別: 件数・distinct users・期間）+
`traces`（discuss 痕跡総数・grounding 記録済み件数と分布・記録開始日）+
`ui_events`（イベント別件数・期間）+ `criteria`（下記）+ `ready_for_analysis`。

**分析開始の参考目安（コード内名前付き定数。自動ゲートではない — DO5）**:

| key | 目安 | 根拠 |
|---|---|---|
| distinct_discuss_users | ≥ 5 | 1〜2人の癖ではなく傾向として読める最低人数 |
| discuss_turns | ≥ 200 | grounding 分布・モード間比較が%で意味を持つ量 |
| observation_span_days | ≥ 14 | 曜日・課題周期のバイアス緩和 |
| landing_shown | ≥ 30 | 着地率（confirm/skip）を率として読める量 |
| grounding_recorded_turns | ≥ 100 | §2-1 の計器設置後データの十分量 |

**UI**: 管理画面・運用タブに「discuss 観測状況」セクション（SYSTEM_ADMIN のみ表示、
`admin-discuss-observation.js`、ES5・`window.AdminDiscussObservation`）。
状況テーブル + 目安の達成状況 + ダンプのダウンロードボタン（tar.gz / zip）。簡素でよい。

## 5. 観測ダンプ

`GET /api/admin/discuss/observation-dump?format=tar.gz|zip`（SYSTEM_ADMIN、既定 tar.gz）
→ `discuss-observation-<UTC時刻>.tar.gz` を返す。**取得を `theory_review_events` に監査**
（`entity_type='discuss_observation'` — 監査カタログに新定数を追加）。

**同梱ファイル**（すべて本文非含有・user は仮名 — DO1/DO2）:

| ファイル | 内容 |
|---|---|
| `manifest.json` | 生成時刻・期間・各ファイル件数・truncated フラグ・仮名化方式・スキーマ版 |
| `README.md` | 各ファイルの列定義・分析観点の例・近似指標の注意（課題#4） |
| `llm_usage_chat.jsonl` | `llm_usage_events` の3チャットタグ分（created_at/feature/operation/model/usage_source/トークン列/user_pseudonym/course_id） |
| `discuss_traces.jsonl` | `entry_mode='discuss'` の interest_traces（created_at/user_pseudonym/course_id/kind/status/overall_tier/content_grounding/discuss_scope/tension_hint/structure_anchor 有無の bool/map_excluded） |
| `discuss_ui_events.jsonl` | `discuss_metric_events` 全列（user は仮名化） |
| `daily_summary.jsonl` | 日別集約（discuss ターン数・distinct users・grounding 分布・landing イベント数・継続送信近似） |

各ファイル 200,000 行で打ち切り、`manifest.json` に `truncated: true` を正直に記録（沈黙の切り捨て禁止）。

## 6. 実装配置

- `backend/db/060_discuss_metric_events.sql` — migration（冪等）
- `backend/core/discuss/observation.py` — 集計・目安判定・ダンプ構築・仮名化（FastAPI 非 import。
  純粋関数と DB 読みを分離）
- `backend/api/routes/discuss_observation.py` — `admin_router`（/api/admin 配下・SYSTEM_ADMIN）+
  `learning_router`（/api/learning 配下・イベント取込）。main.py にフラット登録（Tier 3-17c）
- `backend/api/routes/learning.py` — `_trace_payload` への2キー追記のみ
- `frontend/public/js/discuss.js` / `app.js` — イベント送信
- `frontend/public/js/admin-discuss-observation.js` — 運用タブのセクション
- テスト: `backend/tests/test_discuss_observation.py` / `test_discuss_observation_ui_static.py`

## 7. 非スコープ

- 429（CostGate 拒否）の計測（課題#5 — 既知の限界として残す)
- 学習者・教員向けの数値表示（DO3。教員向け k-匿名集約は Phase 3 の領分）
- 自動アラート・自動ゲート（DO5）
- 対話本文のエクスポート（DO1 — 恒久的に非スコープ）

## 8. Phase 3 との関係

- 本基盤が migration **060** を消費するため、**Phase 3 の migration は 061〜**
  （discussion_mode_design.md §9 の記載を更新済み）。
- 判断フロー: 実運用 → 観測状況 UI で目安到達を確認 → ダンプを取得して分析
  （この場に持ち込む等）→ 価値を確認 → Phase 3 専用設計文書 → 実装。
