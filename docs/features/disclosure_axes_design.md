> **状態:** 実装済み（正本・凍結。2026-09-10。migration 不要）。以後は §7 実装記録の追記のみ。
> 告知カード（初回1回）はオーナー判断 D4 の未決事項で、本書のスコープ外。

# 可視性6軸の宣言（disclosure_axes）

`docs/vision.md` §5.4 は可視性を**一軸に畳まない**ことを要求する — 「閲覧できる audience /
名前を見られる audience / 引用・再利用できる条件 / 評価に利用できるか / 外部 index や
外部 AI に渡るか / いつ撤回・凍結できるか」を独立に扱う。現行実装はこのうち第1軸
（`private` / `group` / `public`）と第4軸（評価に使わない＝`IndicatorSpec` の非利用4項目）
しか宣言していなかった。とりわけ**第5軸（外部 AI に渡るか）は「常に渡る」で固定されたまま、
学習者向けドキュメントに記述が1件も無かった**（調査: [六つのレンズ レンズ4](../architecture/six_lenses_2026-09-10/04_community.md)
§1.1 の表・提案2、統合は [vision_ux_gap_six_lenses_2026-09-10.md](../architecture/vision_ux_gap_six_lenses_2026-09-10.md)
§4 第1波 #9）。

原則8（出所の正直さ）を外向き（AI の出所ラベル）には徹底し、内向き（自分自身の情報フロー）
には適用しないという非対称は、この製品で最も高くつく不正直である。本層はそれを塞ぐ:
**制度指標カタログ（`core/indicator_catalog.py` + `GET /api/indicators`）が「計器の定義」に
対して行ったことを、「可視性そのもの」に対して行う。**

## 1. 不変条項

| # | 条項 | 実装点 |
|---|---|---|
| DA1 | **宣言は現物から確認できることだけ** — 事実文は `basis` に挙げた実装を読んで書く。実装を変えたら本モジュール・設計書・（学習者に見えるものなら）マニュアルを同じ PR で直す | `DisclosureSpec.basis` 必須。ガードレールが `basis` のファイル実在を検査 |
| DA2 | **AI 通過点の宛先は provider だけ** — モデル名・トークン数・金額を書かない。provider 名はカタログに焼き込まず実行時の設定から解決する | `FORBIDDEN_DESTINATION_TERMS` を `__post_init__` で強制。`note` は `{provider}` プレースホルダのみ |
| DA3 | **宣言は全当事者が読める** — 送っている当事者（学習者）が読めない告知は告知ではない | `GET /api/disclosure` の依存は `_get_current_user`（`_require_teacher` を使わない） |
| DA4 | **学習者に内部名を出さない** | `LEARNER_TEXT_DENYLIST`（`core/help_kb/validator.py::STUDENT_DENYLIST` のミラー。包含関係はガードレールが固定）を `__post_init__` で強制 |
| DA5 | **同意を装わない** — 本層は**告知**であって同意取得ではない。同意ボタン・チェックボックス・確認モーダルを作らない | 公開 API に書き込みメソッドなし。宣言本文の「同意して」等を `__post_init__` が拒否。UI は閉じるボタンすら持たない常設の段落 |
| DA6 | **外部 AI を通らない経路も同じ表に書く** — 「送られる」だけを並べると、送られない経路まで送られているように読める | `DisclosureSpec.without_ai`（学習者向け spec では非空を `validate_catalog()` が強制） |

数値を見せない（原則4 改訂）・押し付けない（原則12: 警告色にしない・確認ダイアログを出さない）
は既存原則の継承。第5軸を書くことで学習者が萎縮しうるリスクへの回答が DA6 と「警告色にしない」
である。

## 2. 6軸（vision §5.4 が正。増やさない・畳まない）

| 軸 id | ラベル | 読み手にとっての問い |
|---|---|---|
| `audience` | 閲覧できる相手 | この記録は誰が読めますか。 |
| `name_disclosure` | 名前が見える相手 | 読める相手に、書いた人の名前まで見えますか。 |
| `reuse` | 引用・再利用の条件 | 他の人が引用・再利用できますか。できるとき、何が付きますか。 |
| `evaluation_use` | 評価への利用 | 成績評価・順位付け・自動的な判定に使われますか。 |
| `external_transfer` | 外部の AI・外部の索引への送信 | 外部の AI プロバイダや外部の検索索引へ送られますか。何が送られますか。 |
| `withdrawal` | 撤回・凍結できるとき | 後から取り消し・非表示・凍結ができますか。 |

軸そのものは6つに固定し、6軸の事実を読むために必要な文脈として**保持・削除**（`retention`）/
**集約への算入**（`aggregation`）/ **持ち出し**（`portability`）の3項目を各 spec に添える
（軸を8つに増やすと vision §5.4 との対応が崩れるため、軸には昇格させない）。

## 3. データ種別（`data_kind`）と宣言の出所

宣言順は公開ビュー・マニュアルの並び順の正本。

| data_kind | ラベル | 宣言の出所（`basis`） |
|---|---|---|
| `learning_chat` | 学習チャット・議論・音声で送った本文 | `routes/learning.py::learning_chat` / `routes/admin.py::list_unanswered_queries` / `core/llm_policy.py` |
| `learner_traces` | 学習の痕跡（問い・引っかかり・意図・印） | `core/trace_registry.py` / `core/privacy.py::K_ANONYMITY` / `services.py::aggregate_interest_dashboard` / `core/tension/input_builder.py` / `core/account_lifecycle.py::PURGE_TABLES` |
| `check_answers` | 確認問題・再構成の回答 | `routes/learning.py::check_topic_understanding` / `core/reconstruction/diff.py` / `core/reconstruction/health.py` |
| `course_materials` | 教材・論文と、その解析結果 | `services.py::resolve_document_access` / `core/document_pipeline/orchestrator.py` / `core/llm_policy.py` / `routes/export.py` |
| `teacher_reviews` | 教員の承認・却下・疑義 | `core/schema.py::AUDIT_ENTITY_TYPES` / `services.py::record_review_event` / `core/account_lifecycle.py::RETAIN_TABLES` / `core/decision_context.py` |
| `account` | アカウント（表示名・ログイン記録） | `core/account_lifecycle.py` / `core/auth_events.py` / `core/account_status.py` |

AI 通過点（`ai_touchpoints`）は「どの操作で・何が送られるか・U層の帰属キー（`feature`）」の
3つだけを持ち、宛先は持たない（DA2 — 宛先は実行時に1つ解決して添える）。`feature` が
`core/llm_usage/schema.py::KNOWN_FEATURES` の要素であることをガードレールが固定する
（計測されていない通過点を宣言できない・宣言されていない通過点に気づける）。

## 4. 実装

- **core**: `backend/core/disclosure_axes.py`（FastAPI / sqlalchemy / LLM 非 import の純宣言。
  `core/indicator_catalog.py` と同じ作法。**値を1つも持たない**）。
- **API**: `backend/api/routes/disclosure.py`（`main.py` 直接登録）。
  `GET /api/disclosure`（6軸 + 全データ種別 + 実行時の provider + 固定事実文 + k の正本値）/
  `GET /api/disclosure/{data_kind}`（1件・未知は 404）。書き込みメソッドなし。
- **nginx**: `frontend/nginx.conf` に `/api/disclosure/` と `= /api/disclosure` の2 location
  （`/api/indicators` と同じ事故形 — 欠けると SPA フォールバックが index.html を 200 で
  返し、JSON パースが失敗して事実文が黙って消える）。
- **フロント**: `frontend/public/js/disclosure-note.js`（ES5・`window.DisclosureNote`）。
  静的な担体 `<div data-disclosure-note="...">` は `init()` が埋め、動的パネルは
  `mount(container, data_kind)` を呼ぶ。**文言をフロントに焼き込まない**（サーバの
  `note` をそのまま描く）。取得失敗時は何も描かない（fail-soft）・ポーリングしない。

### 事実文の配置（常設・1行）

| 画面 | 担体 | data_kind |
|---|---|---|
| 学習チャット・discuss の入力欄（composer 直上） | `index.html` の `data-disclosure-note` | `learning_chat` |
| ハンズフリー音声パネル | `index.html` の `data-disclosure-note` | `learning_chat` |
| 予想を書く枠 / 違いの観点（AI に問い・観点を求めるボタンの近傍） | `discuss.js` の `mount()` | `learning_chat` |
| 操作アシスタント（Copilot）の入力欄 | `admin-assistant.js` の `mount()` | `course_materials` |
| W層「深く検討」の対話 | `deliberation.js` の担体 + `mount()` | `course_materials` |
| グラフ対話レビューのチャット | `admin-graph-review.js` の担体 + `mount()` | `course_materials` |
| コース構築チャットの入力欄 | `admin.html` の `data-disclosure-note` | `course_materials` |

事実の段落であって操作要素ではないため `data-ui-anchor` は付けない（`admin-indicators.js`
の `mount()` と同じ規律）。学習画面は1画面レイアウトの規律に従い `flex: 0 0 auto` の下段に
1行で置く（`.disclosure-note` / `.disclosure-note-slot`）。

## 5. マニュアル

- 学習者: `docs/manual/student/02-student.md` §18「あなたの入力はどこへ送られるか」
  （`{#disclosure}`）。student denylist を通す。
- 教員・管理者: `docs/manual/teacher/10-admin-common.md`「AI との対話で外部に送られるもの」
  （`{#disclosure}`）。

ガードレールが「全 `label` がマニュアルに逐語で現れること」を固定する（`indicator_catalog`
の同種検査の転用）。

## 6. 非スコープ

- **初回1回の告知カード**（オーナー判断 D4。同意ではなく告知として置くかの判断が未決）。
  本層は常設の事実文までで、カード・モーダル・チェックボックスを作らない（DA5）。
- **本人向けの AI 通過点履歴**（提案 B2 の `GET /api/me/ai-touchpoints`。既存
  `llm_usage_events` を読むだけで実装できるが、「わたしの記録」への区画追加を含むため別件）。
- **opt-out**（外部 AI を通さない学習モードの選択）。現行は LLM を通らない経路（使い方の
  質問・楽屋・再構成の構造照合）が既に存在することを同じ表で示すに留める。
- 第2軸の実装（帰属記帳と開示の分離 = 提案3「保護された異議」）・第1軸の学習者側
  （提案1「引き受けの段」）・第3軸の条件付き再利用。本層は**宣言**であって、可視性の
  選択肢を増やす層ではない。
- 外部 index（検索エンジン）への登録。現行は行っておらず、宣言でその事実を述べるのみ。

## 7. 実装記録

**2026-09-10（第1波 #9）**: 上記すべてを実装（migration 不要・新テーブルなし・LLM 0回）。

- `backend/core/disclosure_axes.py`（新設）/ `backend/api/routes/disclosure.py`（新設）/
  `backend/api/main.py`（登録2行）/ `frontend/nginx.conf`（2 location）。
- `frontend/public/js/disclosure-note.js`（新設）+ 担体の差し込み（`index.html` 2件 /
  `admin.html` 1件 / `discuss.js` 2件 / `admin-assistant.js` / `deliberation.js` /
  `admin-graph-review.js`）+ `css/styles.css`（`.disclosure-note` / `.disclosure-note-slot`）。
- マニュアル2節（student §18 / teacher 共通操作）。
- テスト: `backend/tests/test_disclosure_axes.py` / `test_disclosure_axes_guardrails.py` /
  `test_disclosure_api.py`。
- 判断: ①軸は vision §5.4 の6つに固定し、`retention` / `aggregation` / `portability` は
  軸に昇格させず補足3項目として持つ。②`STUDENT_DENYLIST` は import せずミラー + ガードレールで
  固定（core の推移的純粋性を崩さないため。`label_vocab` と同じ作法）。③音声も
  `learning_chat` と同じデータ種別として扱い、事実文1行に「音声のときは録音した音声」を
  含める（種別を増やすと同じ会話が2つの宣言に割れる）。
