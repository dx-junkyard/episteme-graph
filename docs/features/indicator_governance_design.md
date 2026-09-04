# 制度指標カタログ（Indicator Governance）

> **状態:** 実装済み（正本・凍結）— 2026-09-04

[← ドキュメント目次](../README.md)

制度を観察するための集約計器（SYSTEM_ADMIN / TEACHER が値を読む指標）の
**定義・目的・宛先・粒度・出所・保持・非利用・副作用レビュー**を、1つの機械可読カタログに
宣言し、**学習者を含む全当事者が読める**ようにする層。値の宛先（誰が数値を見られるか）は
一切変えず、**定義だけ**を公開する。

- カタログ本体（正本）: `backend/core/indicator_catalog.py`
- 公開 API: `backend/api/routes/indicators.py`（`GET /api/indicators`）
- フロント: `frontend/public/js/admin-indicators.js`
- ガードレール: `backend/tests/test_indicator_catalog_guardrails.py` /
  `test_indicator_catalog.py` / `test_indicators_api.py`

---

## 1. 背景 — 原則4の改訂「数値を見せない → 数値の用途と粒度を統治する」

本システムは長らく「数値を見せない」を横断原則として運用してきた（P7 / LS5 / SL4 / UC9 /
W8 …）。この原則は**個人を比較・順位付けする数値**に対しては正しく、いまも維持される。

一方で 2026-09-04 の
[研究調査による再審](../architecture/vision-research-evaluation-and-reframed-direction-2026-09-04.md)
§5.4 / §7 は、研究評価の失敗史（**Leiden Manifesto** / **DORA** / Goodhart の法則）が示す
教訓は「数値を隠すこと」ではなく **「指標の定義・用途・粒度を公開して統治すること」** だと
指摘した。指標を隠せば、隠れたところで代理指標が使われ、誰も再検査できなくなる。むしろ

- **何を数えているのか**（定義）
- **何のために数えているのか**（目的）
- **どの粒度で・誰が値を見るのか**（宛先と粒度）
- **どこに、どれだけ保持するのか**（出所と保持）
- **何には使わないのか**（非利用）
- **誰が副作用（目標化への漂流）を見張るのか**（レビュー）

を全当事者が読める形にしておくことが、権力の非対称を減らす。

これを受けて [`docs/vision.md`](../vision.md) §6 原則4 は
**「数値を見せない → 数値の用途と粒度を統治する」** に改訂され、§6.1「数値の宛先と用途」に
**全当事者** の行が加わった —
> 指標の**定義・収集法・保持期間・意思決定への使い方・副作用レビュー**を読める
> （機械可読の指標カタログ + マニュアル）

本設計書はその「機械可読の指標カタログ」の実装である。同時に §5.4「三領域を分離して
リンクする」の**制度監査**領域に対して、「指標の定義・用途を公開し、独立監査に再検査を
許す」という接続条件を具体化する。

---

## 2. 不変条項 IG1〜IG5

| # | 条項 | 実装上の固定点 |
|---|---|---|
| **IG1** | **値の宛先は変えない。定義は全当事者に公開する** | カタログは**値を1つも持たない**（`catalog_public_view()` は定義フィールドのみ）。`GET /api/indicators` の依存は `_get_current_user` であり `_require_teacher` **ではない** — 観察される側の学習者が定義を読めなければ「全当事者に公開」にならない。各計器の**値**は従来どおり各 API のロールゲートの内側にあり、本層はそれに触れない |
| **IG2** | **非利用4項目は全計器に必須** | `IndicatorSpec.__post_init__` が `not_used_for` に `ranking` / `grading` / `recommendation` / `auto_gate` の4つを要求する。「この計器だけは成績に使う」という spec は**書けない**（構造強制） |
| **IG3** | **個人ランキング・自動ゲートを作らない** | 個人を比較・順位付けする集約をカタログに登録しない。閾値到達で何かが自動的に切り替わる入力にもしない（discuss 観測基盤 **DO5**「参考目安は自動ゲートにしない」の一般化） |
| **IG4** | **カタログに無い集約 API を新設しない** | ガードレールが既知の集約経路（下表）の網羅を固定する。教員・管理者に集約を見せるエンドポイントを足したらカタログにも1件足す |
| **IG5** | **定義の変更は設計書とカタログの両方に記録する** | 数え方・粒度・宛先を変えるときは `design_doc` の設計書と `core/indicator_catalog.py` を同時に更新する。片方だけの変更は「同じ名前で別のものを数える」= 出所の不正直（原則8）になる |

補足（IG1 の解釈）: レスポンスの `readable_by_me` は「あなたはその計器の**値**を読める
立場か」という**事実の投影**であって、この API の認可判断ではない。`readable_by_me: false`
の項目も定義は全文返る。

---

## 3. カタログ（v1・15件）

粒度語彙は5つ:

| 粒度 | 意味 |
|---|---|
| `aggregate_k_anonymous` | 学習者由来の信号を k-匿名（k の正本は `core/privacy.py::K_ANONYMITY` = 3）で集約したもの |
| `aggregate_system` | 個人ではなくシステム全体の状態・消費を集約したもの |
| `self_only` | 本人の記録だけを本人に返すもの |
| `per_item_no_person` | 教材・論文など**物**の単位で、人に紐づかないもの |
| `per_account_operational` | **1アカウント単位の運用データ**（学習データではない） |

`per_account_operational` は本層で新設した語彙である。アカウント個票
（`GET /api/admin/users/{id}/activity`）は集約でも匿名化でもなく1人単位の運用データであり、
`per_item_no_person`（人に紐づかない）と呼ぶのは**嘘になる**。三領域分離（vision §5.4）では
制度監査側に属し、学習記録とは分離され、学習評価には使わない（AL6 / AL7）。個人単位で
あることを丸めずに宣言するために専用語彙を置いた。

| id | route | 値の宛先 | 粒度 | k-匿名 |
|---|---|---|---|---|
| `llm-usage-metrics` | `/api/admin/llm-usage/metrics` | system_admin | aggregate_system | – |
| `llm-usage-forecast` | `/api/admin/llm-usage/forecast` | teacher | aggregate_system | – |
| `llm-usage-estimate` | `/api/admin/llm-usage/estimate/documents/{document_id}` | teacher | per_item_no_person | – |
| `doubt-metrics` | `/api/admin/doubt/metrics` | system_admin | aggregate_system | – |
| `discuss-observation-status` | `/api/admin/discuss/observation-status` | system_admin | aggregate_system | – |
| `interest-dashboard` | `/api/admin/interest-dashboard` | teacher | aggregate_k_anonymous | ○ |
| `bridge-insights` | `/api/admin/courses/{course_id}/bridge-insights` | teacher | aggregate_k_anonymous | ○ |
| `anchor-insights` | `/api/admin/courses/{course_id}/anchor-insights` | teacher | aggregate_k_anonymous | ○ |
| `naive-signals` | `/api/admin/doubt/courses/{course_id}/naive-signals` | teacher | aggregate_k_anonymous | ○ |
| `frontier-interest` | `/api/admin/discovery/frontier-interest` | teacher | aggregate_k_anonymous | ○ |
| `reconstruction-review-queue` | `/api/admin/reconstruction/items/review-queue` | teacher | aggregate_k_anonymous | ○ |
| `claims-stumble-summary` | `/api/admin/documents/{document_id}/claims/stumble-summary` | teacher | aggregate_k_anonymous | ○ |
| `help-gaps-pending` | `/api/admin/assistant/next-steps` | teacher | aggregate_k_anonymous | ○ |
| `account-activity` | `/api/admin/users/{user_id}/activity` | system_admin | per_account_operational | – |
| `my-records` | `/api/me/records` | learner_self | self_only | – |

各 spec の `definition` は**実装を読んで**書いてある（推測で書かない）。とくに次の2件は
実装の実態を丸めずに書いた:

- `llm-usage-forecast` — 日次カウンタは**システム全体で共有**されており、教員ごとの残数
  ではない（`core/llm_usage/forecast.py::_gate_remainings` は 4 ステージのプロセスローカル
  CostGate と当日 run の DB 集計を読む）。したがって粒度は `self_only` ではなく
  `aggregate_system`。返すのは `{show, message}` だけで数値は出さず、導出失敗時は何も
  表示しない（TT2 / TT4）。
- `interest-dashboard` — ヒートマップのセルは関与人数の k-匿名ゲートを通るが、トピック
  単位のホットスポット件数はコース全体の粗い集計である（個人行・本文・user_id は返さない）。
  この非対称を `definition` に書いた。

`side_effect_review` は全件「オーナーの定期レビュー（自動判定なし）」。**指標の副作用
（目標化・KPI 化への漂流）を検出する自動判定は置かない**（IG3）。

---

## 4. API

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/indicators` | **認証済みなら誰でも**（`_get_current_user`） | 全件の定義 + `note`（固定事実文 `CATALOG_NOTE`）+ `k_anonymity`（`core/privacy.py` の正本値）。各項目に `readable_by_me` |
| GET | `/api/indicators/{indicator_id}` | 同上 | 1件。未知の id は 404 |

書き込みメソッドは作らない（カタログはコードが正本 — API から変えられない）。
レスポンスに値・件数・レンジは一切含まれない。

加えて、既存の計器レスポンスのうち**キー集合をテストで固定していないもの**に、
トップレベルの `indicator_id`（文字列）を1つ足した — 応答が「自分がどの計器か」を名乗り、
そこから定義へ辿れるようにするためである。追加先は
`llm-usage-metrics` / `doubt-metrics` / `discuss-observation-status` /
`interest-dashboard` / `bridge-insights` / `claims-stumble-summary` の6経路で、いずれも
**ルート層**で足している（core の集計関数の戻り値契約は変えない）。
`anchor-insights` / `frontier-interest` / `review-queue` / リリース前確認の GET は
キー集合がテストで固定されているため**足していない**（既存の契約を壊さない）。

---

## 5. UI

`frontend/public/js/admin-indicators.js`（ES5・`window.AdminIndicators`・DI 注入）。

- ログイン後に `GET /api/indicators` を1回だけ取得（ポーリングしない）。
- `factLine(id)` → 「計器: 〈label〉 — 〈purpose〉個人の比較・成績・自動判定には使いません。」
- `mount(containerEl, id)` が `<p class="indicator-fact">` を差し込む。
- **fail-soft**: カタログを取得できないときは**何も描かない**（推測で書かない・計器本体の
  表示は妨げない）。**カタログの数値フィールドは存在しないので、UI が数値を描く経路もない。**

配置は制度指標が実際に描画される3箇所:

1. 運用タブ「LLM使用量」（`admin-llm-usage.js`）
2. 運用タブ「discuss 観測状況」（`admin-discuss-observation.js`）
3. 「関心集約ダッシュボード」（`admin.js` + `admin.html`）

事実の段落であって操作要素ではないため `data-ui-anchor` は付けない（ボタン・リンクを
足すときはアンカー3点セットを揃えること）。

学習者側の UI 変更は**ない**。学習者に対する言明はマニュアル（§6）に置く — 画面に新しい
計器の話を差し込むのは「押し付けない」（原則12）に反するため。

---

## 6. マニュアル

| ファイル | 節 | 内容 |
|---|---|---|
| `docs/manual/student/01-specification.md` | `## あなたについて集計される計器 {#institutional-indicators}` | 学習者の活動が入りうる計器の一覧（計器名は `label` と逐語一致）。何が集計されるか / k-匿名であること / 成績・ランキング・自動判定に使わないこと / 定義は誰でも読めること |
| `docs/manual/system_admin/13-admin-llm-usage.md` | `## 計器の定義と用途（指標カタログ） {#indicator-catalog}` | カタログの位置づけ・非利用4項目・副作用レビュー・**指標の意味を変えるときはカタログと設計書の両方を直す**（IG5） |
| `docs/manual/teacher/21-admin-interest-dashboard.md` | `### この計器の定義 {#indicator-fact}` | 画面上の事実文と、定義の全文がどこにあるか |

student/ の節は `backend/core/help_kb/validator.py::STUDENT_DENYLIST` に触れない書き方に
する（`/api/admin` などの内部名を学生向けページに書かない）。このため学生向けマニュアルには
API パスを書かず、計器名と扱いだけを書く。

---

## 7. ガードレール

`backend/tests/test_indicator_catalog.py`

- `validate_catalog()` が通る / 全 spec に非利用4項目がある / `k_anonymity` ⇔ 粒度
- 公開ビューに**値らしいキー**（`count` / `value` / `total` / `score` / `weight` /
  `confidence` …）が再帰的に1つも無い
- `readable_by_me` のロール判定

`backend/tests/test_indicator_catalog_guardrails.py`

- core の純粋性（`indicator_catalog.py` が fastapi / sqlalchemy / core.llm を import しない）
- 全 spec の `route` が実際に `backend/api/routes/*.py` の**登録済みパス**として存在する
  （ルーター prefix + デコレータ引数の合成で厳密照合）
- **IG4 の網羅**: 既知の集約経路（14本）が漏れなく指標に対応する
- `trace_registry.CONSUMERS` のうち教員露出を持つ消費者がカタログに現れる
- `/api/indicators` が `_get_current_user` を使い `_require_teacher` を使わない
- 全 `label` が student マニュアル節 **または** system_admin マニュアル節に逐語で現れる
- JS がカタログ項目から数値を読まない（`.count` / `.total` へのアクセスが無い）

`backend/tests/test_indicators_api.py`: STUDENT / TEACHER / SYSTEM_ADMIN での
`readable_by_me`・未認証 401・未知 id 404・値の非漏洩。

---

## 8. 非スコープ（v1）

- **値の履歴**（指標値の時系列保存・スナップショット）— カタログは定義だけを持つ。
- **独立監査者ロール**の新設 — vision §6.1 の「独立監査者」行は権限分離された全体視点を
  求めるが、v1 は既存の SYSTEM_ADMIN をその位置に置き、ロールを増やさない。
- **副作用レビューの自動化** — 目標化への漂流の検出を自動判定にしない（IG3）。
  `side_effect_review` は「誰が見張るか」を書くだけ。
- **学習者向けのカタログ閲覧 UI** — API は全認証ユーザーに開いているが、学習画面に新しい
  導線は作らない（原則12。学習者向けの言明はマニュアルに置く）。
- **既存レスポンスへの `indicator_id` 全面付与** — キー集合が固定されている経路には
  足さない（§4）。

---

## 9. 実装記録（2026-09-04）

- 新規: `backend/core/indicator_catalog.py` / `backend/api/routes/indicators.py` /
  `frontend/public/js/admin-indicators.js` / 本設計書 /
  `backend/tests/test_indicator_catalog{,_guardrails}.py` / `test_indicators_api.py`
- 変更: `backend/api/main.py`（ルーター登録のみ）/ `routes/llm_usage.py` / `routes/doubt.py` /
  `routes/discuss_observation.py` / `routes/reconstruction.py` / `routes/admin.py`
  （いずれも `indicator_id` の1キー追加）/ `frontend/public/admin.html`（script タグ +
  事実文コンテナ）/ `frontend/public/js/admin.js` / `admin-llm-usage.js` /
  `admin-discuss-observation.js` / `frontend/public/css/styles.css`（`.indicator-fact`）/
  `docs/backend/api.md` / マニュアル3ファイル /
  `backend/tests/test_admin_help_inspect_ui_static.py`（`_ADMIN_FRONTEND_SOURCES` への登録）
- migration なし・新テーブルなし・LLM 呼び出しなし。
- ブリーフからの逸脱と理由:
  1. `llm-usage-forecast` の粒度を `self_only` ではなく `aggregate_system` にした
     （実装のカウンタはシステム全体で共有され、本人の残数ではない。§3）。
  2. 事実文の配線先は `admin.js` の3箇所ではなく、LLM 使用量と discuss 観測状況が
     それぞれ `admin-llm-usage.js` / `admin-discuss-observation.js` に分離済みのため
     そちらへ入れた（`admin.js` は関心集約のみ）。
