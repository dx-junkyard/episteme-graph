# 痕跡kind登録簿と主権台帳v1「わたしの記録」設計書（Phase 1）

**状態:** 実装済み（Phase 1 v1、2026-08-15。§4 実装記録）
**作成日:** 2026-08-15
**由来:** [AIアシスタント拡張の多専門家討論記録](../architecture/ai_assistant_personalization_debate_2026-08-15.md)
の提案6 v1（わたしの記録 — 学習痕跡の主権台帳）と、監査横断所見⑤・§9.6 が
「どの機能よりも先に実装すべき基盤」と特定した**痕跡kind登録簿＋除外許可リストの網羅ガードレール**。
実装順序の正本は [実装計画](../architecture/personalization_implementation_plan.md)。

migration: **不要**（新テーブルなし。既存 `interest_traces` の読みと Python 正本モジュールの新設のみ）。

---

## 1. 不変条項

- **TR1 登録簿が kind の単一の真実源** — `interest_traces.kind` の語彙・各 kind の露出宣言
  （問いの軌跡に出るか / 教員向け k-匿名集約の対象か / わたしの地図の導出対象か）は
  `backend/core/trace_registry.py` に一元宣言する。新しい kind は露出宣言なしには追加できない
  （dataclass の必須フィールドで構造強制）。
- **TR2 消費面はガードレールで登録簿に一致させる** — 主要消費者のソースが宣言と食い違えば
  テストが落ちる。「新しい kind を足したら登録簿と除外リストを更新しないと落ちる」状態を作る。
- **TR3 情報を落とさない（P4継承）** — 書き込み経路が現存しない語彙（kind `detour`、
  status `revisited` / `abstracted`）は削除せず `dead` マークで登録簿に保持する（既存行は存在しうる）。
- **TR4 台帳は読み取り専用・本人のみ** — わたしの記録の API は GET のみ。本人以外からのアクセス
  経路を作らない（PN-1 と同型）。行削除・封印 API は作らない（封印は P4 例外の専用設計書を経る v2）。
- **TR5 来歴は誠実に** — 現在記録されていない事実（集約への実際の包含来歴）を推定で表示しない。
  「集約への包含の来歴は現在記録されていません」と事実文で言う。偽のボタン・先取りの約束を出さない。
- **TR6 数値を見せない（UC9継承）** — 台帳は本人の行の列挙であり数値集計ではない。件数バッジ・
  進捗率・スコアを出さない。
- **TR7 台帳の表示はステアリングに使わない（UC5継承）** — 台帳 DTO を提示内容・提示順・対話方針の
  入力にしない。

## 2. Part A: 痕跡kind登録簿（`backend/core/trace_registry.py`）

### 2.1 背景（2026-08-15 時点の実態調査より）

- kind は 8 種（`raw` / `question` / `detour` / `misconception` / `tension` / `help_usage` /
  `intention` / `anchor_mark`）。語彙定数は `services._INTEREST_KINDS` にあるが、露出宣言は存在しない。
- 消費者は 33 箇所（2026-08-15 時点。正確な検査対象は `backend/tests/test_trace_registry_guardrails.py` が正）
  で、除外方式が4種に分裂している:
  (A) SQL kind 許可リスト（新 kind を自動除外 — 安全）
  (B) SQL kind 除外リスト（`get_interest_traces` / `aggregate_interest_dashboard` の2箇所のみ。
  新 kind のたびに手追記が必要 — **唯一の漏れ穴**）
  (C) payload フラグ方式（tension worker の `tension_hint` / atlas 個人層の `atlas.node_id` /
  discuss 観測の `entry_mode` — 偶然の除外であり構造保証ではない）
  (D) Python 定数分岐（`personal_graph/derive.py`）。
- 既存ガードレールの検査対象は6モジュール固定で、`naive_signal.py` / `stumble.py` /
  `next_steps.py` / `cycle/queries.py` / `atlas_view._personal_layer` / `discuss/observation.py` は
  どの網にもかかっていない。

### 2.2 登録簿の形

```python
@dataclass(frozen=True)
class TraceKindSpec:
    kind: str
    label: str                       # 日本語系統ラベル（台帳表示の正本）
    writers: tuple[str, ...]         # 書き込み経路の記述（ドキュメンテーション）
    statuses: frozenset[str]         # この kind で使われる status 語彙（_TRACE_STATUSES の部分集合）
    learner_trajectory: bool         # 問いの軌跡（get_interest_traces）に出るか
    teacher_dashboard: bool          # aggregate_interest_dashboard（k-匿名集約）の対象になり得るか
    personal_map: bool               # わたしの地図（personal_graph 導出）の対象か
    dead: bool = False               # 書き込み経路が現存しない語彙（TR3）

TRACE_KINDS: dict[str, TraceKindSpec]   # 8 kind すべて
# 導出 frozenset（消費者・ガードレールが参照する正本）:
ALL_TRACE_KINDS / TRAJECTORY_EXCLUDED_KINDS / DASHBOARD_EXCLUDED_KINDS / PERSONAL_MAP_KINDS
```

初期宣言（現状の挙動をそのまま写す — 本フェーズで露出の意味論は変えない）:

| kind | label | trajectory | dashboard | personal_map |
|---|---|---|---|---|
| question | 問い | ✓ | ✓ | ✓ |
| misconception | 誤解の記録 | ✓ | ✓ | — |
| tension | 引っかかり | ✓ | ✓ | ✓ |
| raw | その他の記録 | ✓ | ✓ | — |
| detour（dead） | 寄り道 | ✓ | ✓ | — |
| help_usage | 使い方の質問 | — | — | — |
| intention | 学習の意図 | — | — | — |
| anchor_mark | 軽量アンカー | — | — | — |

あわせて `CONSUMERS`（消費者名 → モジュールパス・方式・対象 kind 集合の宣言表）を登録簿に持ち、
ガードレールの検査対象を列挙可能にする（ドキュメンテーション兼テスト駆動データ）。

### 2.3 ガードレール（`backend/tests/test_trace_registry_guardrails.py`）

1. `services._INTEREST_KINDS` == 登録簿の kind 集合（語彙の二重管理を同値で固定）。
2. `core/cycle/schema.py` の `KIND_INTENTION` / `KIND_ANCHOR_MARK` が登録簿に存在。
3. B方式の2消費者（`get_interest_traces` / `aggregate_interest_dashboard`）のソースに、
   `TRAJECTORY_EXCLUDED_KINDS` / `DASHBOARD_EXCLUDED_KINDS` の**全要素**が除外式として現れる
   （`extract_function_source` + リテラル検査。既存 `test_understanding_cycle_guardrails.py` /
   `test_help_kb_guardrails.py` の方式を登録簿駆動に一般化）。
4. A方式・D方式の主要消費者（personal_graph queries/derive・tension/anchor worker・digest 2種・
   `naive_signal` ・`stumble`・`next_steps` help_gaps・`bridges`・`cycle/queries`）のソースに現れる
   kind リテラル集合が `CONSUMERS` の宣言と一致する。
5. すべての kind が露出3宣言を持つ（dataclass 必須フィールドなので構文的にも強制されるが、
   dead 語彙も含めて登録簿に8件存在することを固定）。
6. `trace_registry.py` は FastAPI / LLM / sqlalchemy を import しない（純宣言モジュール）。
7. statuses は `services._TRACE_STATUSES` の部分集合。

### 2.4 既知の不整合の是正（本フェーズで実施）

- `structure_anchor/worker.py::_fetch_pending_questions` と `routes/learning.py` の anchor 遅延起動
  クエリに `status <> 'superseded'` を追加する（同じ kind を読む `get_anchor_digest` には既にあり、
  書き直しで消したはずの問いが帰属解析される既存バグの是正。P4 の supersede 意味論に整合）。
- tension worker の直 INSERT（`record_interest_trace` 非経由）は**変更しない**が、登録簿の
  `writers` に第2の書き込み経路として明記する（`analyzed_at` の同時セットが必要な意図的経路）。
- `record_interest_trace` の未知 kind → `raw` 縮退は変更しない（best-effort 記録の意図的挙動）。
  登録簿ガードレール(1)が「コードに現れる kind リテラルは必ず登録簿にある」状態を作るため、
  縮退経路が踏まれるのは実行時の異常系のみになる。

## 3. Part B: 主権台帳v1「わたしの記録」

### 3.1 体験（討論記録 提案6 の ux_narrative v1 範囲）

学習画面ヘッダーの「わたしの地図」の隣に「わたしの記録」。開くと、確定した tension・
dismiss したまま保持されている候補・書き直しで superseded になった問い・学習の意図・
軽量アンカー——系統ごとに全てが並ぶ。各行には公表状態の事実文（「あなた以外には表示されません」/
「教員向けには3人以上の匿名集計にのみ含まれることがあります」/「AIの候補です。あなたが確定するまで
あなたの痕跡になりません」）。「持ち出す」で自分の痕跡一式が JSON で手元に落ちる。
封印ボタンは無い——代わりに正直な事実文「封印の仕組みは、封印したという事実を残したまま内容を
読めなくする形で設計中です」。

### 3.2 core（`backend/core/trace_ledger.py`、FastAPI 非 import）

- `fetch_ledger_rows(session, user_id, limit)` — 本人の全行（kind 条件なし・全 status。
  dismissed / superseded / candidate も含む — P4 の一望）。新しい降順・上限 500 行
  （超過は `truncated: true` で正直に返す。持ち出しは常に全件）。
- `build_ledger_overview(rows, *, course_labels)` — 純関数。登録簿の `label` で系統グルーピングし、
  各行を {id, kind, kind_label, status, status_label, text, created_at, course_label,
  flags(map_excluded / superseded / candidate), publicity(事実文)} に射影する。
  publicity は登録簿の露出宣言 + 行の状態から決定論導出（TR5: 実際の包含来歴は表示しない）。
  confidence 等の生数値キーは payload から再帰除去（W8 同型）。
- `build_ledger_export(rows)` — `{schema_version: 1, exported_at, records: [...]}`。
  records は id / kind / status / text / payload（全文）/ course_id / topic_id / created_at。
  スキーマ安定性: `schema_version` を持ち、将来の kind 追加は records に新 kind の行が
  増えるだけで既存キーは変えない。
- 来歴欄: 系統ごとの露出事実文（登録簿由来・静的）+ 全体注記
  「集約への包含の来歴は現在記録されていません。手渡しの仕組み（実装予定）と同時に、
  どの集約に含まれたかを記録する仕組みを追加します」。
  ※ 包含来歴の記録基盤の仕様は提案1 v2（手渡しチャネル）の専用設計書で確定する（先取りしない）。

### 3.3 API（`backend/api/routes/my_records.py`、`me_router` prefix `/api/me`）

- `GET /api/me/records` — overview DTO（本人のみ。認証ユーザーの user_id 固定）。
- `GET /api/me/records/export` — JSON ダウンロード（`Content-Disposition: attachment`）。
- **読み取り専用** — 本ルーターに書き込みメソッドを作らない（`personal_map.py` と同じ
  ガードレール固定）。map-exclude 等の既存訂正操作は `routes/learning.py` 側のまま。

### 3.4 フロント（`frontend/public/js/my-records.js`）

- ヘッダーの `#my-map-btn` の隣に `#my-records-btn`「わたしの記録」。`personal-map-home.js` と
  同型のオーバーレイパネル（ポーリング禁止・開いたときのみフェッチ）。
- 系統別セクション（登録簿 label 順）・行ごとの status ラベルと publicity 事実文・
  「持ち出す」ボタン（Blob ダウンロード）・封印準備中の正直表示。
- 常設注記「この記録はあなたにだけ表示されます。成績評価には使用されません。」
  （わたしの地図と同文）。件数バッジ・数値なし（TR6）。
- UI アンカー4点セット: `KNOWN_UI_ANCHOR_IDS` + `UI_ANCHORS` + `docs/manual/student/02-student.md`
  の明示アンカー付き節 + `data-ui-anchor` 属性（網羅は専用テストで固定）。

### 3.5 テスト

- `test_trace_registry_guardrails.py`（§2.3 の7項）
- `test_trace_ledger_core.py`（fake rows での純関数テスト: 系統グルーピング・publicity 導出・
  candidate/superseded の保持・数値キー除去・export のスキーマ）
- `test_my_records_api.py`（本人のみ・読み取り専用・export のヘッダ）
- `test_my_records_ui_static.py`（常設注記・封印の正直表示の逐語・ポーリング禁止・
  アンカー4点セット網羅・数値バッジ禁止語彙）

### 3.6 ドキュメント同時更新（development_checklist §5-1）

`docs/features/learning.md`（学習者向け機能解説）・`docs/backend/api.md`
（`routes/my_records.py` の節 + マウント一覧）・`docs/README.md` 索引・
`docs/architecture/layer_registry.md` §1（migration 不要の層として）・CLAUDE.md
（横断基盤に `trace_registry.py` を登録 — 「新しい kind・消費者はコピペせずこれを使う」）。

## 4. 実装記録（2026-08-15）

- 実装: `backend/core/trace_registry.py`（8 kind・消費者13宣言・導出 frozenset 4種）/
  `backend/core/trace_ledger.py` / `backend/api/routes/my_records.py`（`me_router`、GET 2本のみ）/
  `frontend/public/js/my-records.js` / `core/label_vocab.py` に `TRACE_STATUS_LABELS`（9 status）/
  UIアンカー `topbar.my-records`（`docs/manual/student/02-student.md#my-records`）。
- `services._INTEREST_KINDS` は登録簿からの導出に一本化（挙動不変）。§2.4 の superseded
  フィルタ是正2箇所（`structure_anchor/worker.py::_fetch_pending_questions` /
  `routes/learning.py` の anchor 遅延起動クエリ）実施。
- 実装判断: ①export ルートの読みは `_EXPORT_ROW_LIMIT`（100,000 行）— LIMIT なし SQL を
  増やさないため（「持ち出しは全件」の実務上の上限）②未登録 kind の行は overview で落とさず
  末尾系統として保持（P4 の防御実装）③**学習者本人の持ち出しは監査記帳しない**
  （本人行動の記録は観察面の拡大になるため意図的に記帳しない — TR5 と同根の判断）
  ④「3人以上」文言は `core.privacy.K_ANONYMITY` から合成（リテラル再定義禁止）。
- テスト: `test_trace_registry_guardrails.py`（superseded 是正の回帰含む）/
  `test_trace_ledger_core.py` / `test_my_records_api.py` / `test_my_records_ui_static.py`。
  バックエンド全スイート 9,531 passed / 0 failed（2026-08-15）。

## 5. 非スコープ（v2 以降・専用設計書を切る）

- 封印（暗号消去。P4/UC6 の正面例外 — vision §10 手続き + per-trace 鍵基盤）
- 集約への包含来歴の記録基盤（提案1 v2 手渡しチャネルと同時に設計）
- 公表状態の三段敷居（未寄贈/手渡し済み/引用済み — 手渡しチャネル実装後）
- 台帳からの直接操作（confirm / dismiss / map-exclude は既存画面のまま）
