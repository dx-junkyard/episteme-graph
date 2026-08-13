# 理解サイクル（Understanding Cycle, UCサイクル）設計書

**作成日:** 2026-08-13
**状態:** 設計書 + **Phase 1・Phase 2 実装済み（2026-08-13, §14/§15 実装記録参照）**。本書が UCサイクルの**正本**。
**出典:**
- `vision_expansion_proposals_2026-08.md`（7分野専門家パネル討論。3つの独立収束と6統合提案）
- `vision_expansion_ux_proposal_revised_2026-08-13.md`（UX観点の再構成提案。理解サイクル・
  cold start・AI 4モードの追加仮説）

**親文書:** `knowledge_network_vision.md`（KN-1〜4）
**関係する既存正本:** `discussion_mode_design.md`（DM1〜8）/ `reconstruction_loop_design.md`
（R層）/ `structure-anchored-questions.md` / `personal_knowledge_network_design.md`（PN-1〜7）

---

## §0 要旨 — 何を作るか

AIチャットを「回答装置」として改善するのではなく、**学習者が予測し、差を見て、理解を更新し、
問いを持ち越し、時間をおいて再訪する理解サイクルを一級の体験として設計し、その中に AI を
補助レイヤーとして再配置する**。

> **OPEN → ELICIT → DIFF → REVEAL → UPDATE → ANCHOR → LEAVE → REVISIT**

新しい大機構は作らない。サイクルの各段は既存機構の薄い拡張で構成する（§3 マッピング表）。
Phase 1（最小閉ループ）は **migration 0 で実装可能**（interest_traces の kind 追加のみ。
kind は CHECK なしの TEXT — 020_interest_trace.sql 確認済み）。

---

## §1 添付提案書の検討結果 — 仕様化にあたっての裁定

UX提案書（revised_2026-08-13）の内容を既存システムの不変条項と突き合わせ、以下を裁定した。
本節が提案書と本仕様の差分の正本である。

| # | 提案書の記述 | 裁定 |
|---|---|---|
| 1 | 「AIが学習者の予想を要約する」「差分候補を作る」 | **candidate-only を明示**。AI の要約・差分候補は常に提案であり、学習者の記録の正本は本人の逐語（P1/W2 と同型）。AI 要約で本人の言葉を置換しない |
| 2 | 予測（自由記述）と本文の DIFF | **自由記述の構造照合は決定論でできない**。v1 の DIFF は**並置のみ**（あなたの予想 / 著者の構成を並べ、判定しない）+ 任意の一行「何が違いましたか」。LLM による差分候補の提示は Phase 2 の AI Diff モード（候補・仮説文体）。選択肢型予測（R層 predict）だけが非LLM・決定論の DIFF を持つ — この2系統を混ぜない |
| 3 | 初回の第一問い「なぜこの論文を開きましたか」の記録 | 採用。ただし逐語を積むため、**tension/anchor worker・digest・教員向け集約から構造的に除外**する（kind 分離。§5.2）。読む動機は監視対象にしない |
| 4 | 「気になる」等の軽量アンカー | 新機構にせず**既存 structure_anchor 経路A（learner_selected・同期・非LLM）への語彙マッピング**で実装（§5.4）。AI のリアルタイム分類をしない方針は既存 P1/P6 と一致 |
| 5 | AI 4モード（Elicit/Diff/Explain/Reflect） | 採用。ただし**新しいチャットモードを増やさない** — 既存 intent_mode / 意図分類の内部拡張とプロンプト契約で実現（§8）。Reflect/Carry は非LLM を既定とし、LLM は整形のみ |
| 6 | 単一論文の 15 分精読プロトタイプ | コース文脈のない単独論文読解は **discuss モードの `_discussion` 疑似トピック**に載せる（新しい会話コンテキストを発明しない） |
| 7 | 提示順を裏で並べ替えない・能力推定をしない | パネル討論の棄却判断（スキーマ距離レンズ・沈黙的並べ替え）と一致。不変条項に昇格（UC5/UC7） |
| 8 | KPI を正答率・滞在時間・連続日数に置かない | discuss 観測基盤（DO1〜6）と同型の内部計測のみ（§10）。学習者に数値を見せない |

---

## §2 不変条項（UC1〜UC10）

- **UC1 ELICIT-first は opt-in**: 予測を挟む読み方は「精読モード」の明示トグルでのみ有効。
  既定レンダリング・既定の読書体験を変えない。通覧（survey reading）に摩擦税をかけない。
- **UC2 DIFF は採点しない**: 正解/不正解・点数・正答率を出さない。差分は事実の並置と
  「食い違いの可能性」の仮説文体（R層の流儀）で提示する。権威は常に出典リビール。
- **UC3 予測・想起・意図の痕跡は本人のみ**: すべて本人のみ可視（PN-1）・評価利用禁止（P3）・
  教員向けには既存の k-匿名集約（core/privacy.py 正本）以外の経路を作らない。
- **UC4 セッション間は何もしない**: 督促・プッシュ通知・連続日数・未消化バッジ・忘却曲線の
  提示を作らない。「間」を埋めないこと自体が設計。
- **UC5 沈黙適応をしない**: 学習者モデルの推定（能力・理解度・スキーマ距離）で提示内容・
  提示順・対話方針を暗黙に変えない。個人化はすべて「本人の産出物を本人に見せる」形か、
  本人が選ぶ提案型でのみ行う。
- **UC6 情報を落とさない（P4 継承）**: 持ち越し問い・予測・アンカー・想起回答は削除せず
  状態遷移（active / superseded / dismissed）で保持する。行削除 API を作らない。
- **UC7 cold start で能力推定をしない**: 初回はその場で本人が生成した情報（動機・予測・
  引っかかり）だけを初期状態とする。過去データからの推定入口分岐を作らない。
- **UC8 サイクルの骨格は非LLM・同期**: OPEN の再提示・DIFF の並置・ANCHOR・LEAVE・REVISIT の
  骨格に LLM を置かない（P6 継承）。AI モードは補助レイヤーであり、失敗時は骨格だけで
  サイクルが完結する（degraded 縮退）。
- **UC9 数値を見せない**: 予測の的中数・アンカー数・サイクル完了数などの数値・進捗表現を
  学習者にも教員にも出さない。
- **UC10 既存層は非改変**: A層・R層・B層・discuss の既存コードは読む側として使う。拡張は
  kind/語彙の追加・API の optional フィールド追加・新モジュールに限る。

---

## §3 理解サイクル 8段階 × 既存機構マッピング

| 段階 | 内容 | 実装基盤（既存） | 新規 |
|---|---|---|---|
| OPEN | 初回:「なぜ今開いたか」/ 再訪: 持ち越し問いの再提示 | discuss opening（`build_opening`・LLM 0回） | intention 痕跡 + opening への一枠 |
| ELICIT | 予測・予想図の産出 | R層 reconstruction（predict/restate）・discuss 対話 | 論文骨格予測（Phase 2）・precision mode トグル |
| DIFF | 予想と実物の並置 | R層 DIFF（選択肢型のみ決定論） | 自由記述は並置ビュー（判定なし） |
| REVEAL | 本文・出典の開示 | 既存レンダリング・出典リビール（R層） | なし |
| UPDATE | 「何が変わったか」の一行 | discuss reflection API（`record_learner_articulated_tension`） | 呼び出し文脈の拡張 |
| ANCHOR | 引っかかりを教材上の位置に残す | structure_anchor 経路A（learner_selected・同期・非LLM） | 軽量ラベル4種のマッピング |
| LEAVE | 持ち越す問いを1つ選ぶ | discuss 着地画面（landing） | intention(carryover) 痕跡 + 選択 UI |
| REVISIT | 問い→再回答→前回差分 | discuss opening + interest_traces タイムスタンプ | 再回答記録 + 差分事実文（Phase 2 で個人地図差分） |

---

## §4 データモデル

### 4.1 intention 痕跡（migration 不要）

`interest_traces` に **kind='intention'** を追加する（kind は CHECK なし TEXT・新テーブル不要）。

```
kind:    'intention'
payload: {
  "role":       "opening_motive" | "carryover_question" | "revisit_answer",
  "text":       <本人の逐語>,
  "source_trace_id": <carryover の元になった問い/アンカーの trace id | null>,
  "prediction": <opening_motive のみ任意 {"text": ...}>,
  "structure_anchor": <あれば既存 payload.structure_anchor と同形式 | null>,
  "session_ref": <遠征/セッションの論理キー | null>
}
status:  'open'（active の意味） | 'superseded' | 'dismissed'   -- 行削除しない（UC6）
```

**実装裁定（2026-08-13）**: status に `'active'` は新設せず、既存 `_TRACE_STATUSES` の
`'open'` を active の意味で使う（`_TRACE_STATUSES` 外の値は黙って `'open'` に丸められる
ため語彙追加を避けた）。また「予想してから開く」（§5.3）のため、`role='opening_motive'`
に限り **text 空 + `prediction.text` あり**の記録を許容する。

- **carryover は常に active 最大1件/コース**: 新しい持ち越しを書いたら旧行を `superseded` に
  遷移（置き換え。催促しないため「未消化」概念を持たない）。
- **revisit_answer** は carryover への再回答。`source_trace_id` で連鎖し、理解の縦断記録になる
  （提案4「時間レンズ」発展形の素材）。
- **worker・集約からの除外（§1-3）**: tension worker は `payload.tension_hint` 起点、
  anchor worker は `kind='question'` 明示フィルタ（worker.py 確認済み）のため構造的に対象外
  だが、ガードレールテストで **kind='intention' が tension/anchor worker・digest・
  問いの軌跡・個人知識ネットワーク導出・教員向け集約のいずれにも入らない**ことを固定する
  （help_usage の除外と同型。ただし将来「わたしの地図」の時間層に本人向け表示として載せる
  余地は残す — その場合も PN-1 のまま）。

### 4.2 軽量アンカーの語彙マッピング（migration 不要）

既存 `payload.structure_anchor`（`attribution_source='learner_selected'`・即確定）の
**payload 形式**に相乗りするが、行の kind は **`'anchor_mark'`（新設）**とする
（実装裁定 2026-08-13: `kind='question'` に載せると空テキストの行が「問いの軌跡」を
汚染し、personal_graph の question ノード導出とも衝突するため。`quick_label` / `revisit`
は `build_anchor_payload` がホワイトリスト構築のため structure_anchor の**中ではなく
兄弟キー**として置く）。語彙・マッピングの正本は `core/cycle/schema.py::QUICK_LABELS`。

| ボタン | doubt_type | 追加 payload |
|---|---|---|
| 気になる | `unclassified` | `quick_label: "curious"` |
| まだ分からない | `justification_gap` | `quick_label: "not_yet"` |
| あとで戻る | `unclassified` | `quick_label: "return_later"`, `revisit: true` |
| 何かとつながりそう | `connection` | `quick_label: "connects"` |

- 1タップ = 経路A の即確定（同期・非LLM・LLM 候補を経ない）。テキスト選択があれば既存の
  `selection_text` / `selection_segment_id` を使い、なければ表示中チャンク/スライドに縮退
  （既存の縮退順 claim → concept → chunk → segment を踏襲）。
- `revisit: true` の痕跡は LEAVE の選択リストに優先掲載される（それ以外の用途を持たない —
  リマインダー・バッジ化しない）。

### 4.3 精読モード（migration 不要）

コース・学習者単位のクライアント設定（localStorage）+ `LearningChatRequest.screen_mode` と
同様のリクエストフラグ `precision_reading: true`。**サーバ側に学習者設定テーブルを作らない**
（v1。オフにすれば痕跡だけが残る）。

### 4.4 Phase 2 で追加する語彙（migration 不要・正本は core 側）

- R層 `elicit_mode` に `regime`（極限・支配項当て）/ `next_step`（次の操作予測）を追加。
  DB 列は CHECK なし（036 確認済み）— 語彙の正本は `core/reconstruction/schema.py` に追加し、
  validator・伏せフィールド規約は既存のまま継承する。答えキーは derivation_chain の
  近似 operation（linearize_*/approximate_*/eliminate_*）から非LLM 生成。

---

## §5 Phase 1 仕様 — 最小閉ループ（最優先）

> 範囲: OPEN（初回・再訪）/ 軽量 ELICIT（論文スケールのみ）/ DIFF（並置）/ UPDATE /
> ANCHOR（軽量4ボタン）/ LEAVE / REVISIT。**すべて非LLM**（AI モードは Phase 2）。

### 5.1 OPEN — 初回

単独論文の読解は discuss モード（`_discussion` 疑似トピック）に載せる（§1-6）。
discuss 開幕画面（`GET /api/learning/courses/{course_id}/discuss/opening`）に一枠追加:

- 初回（当該コースに intention 痕跡なし）: 「この論文を、なぜ今開きましたか？」の任意一文
  入力。送信で `kind='intention'` / `role='opening_motive'` を記録（非LLM・逐語保存）。
  **書かなくても何も起きない**。二問目以降（タイトル予想・気になる点）は Phase 1 では
  出さない（初回から多くの質問を並べない — 提案書 §3.2 の抑制を仕様化）。
- opening DTO に `intention` フィールド（optional）を追加。承認済み discussion_seeds 等の
  既存キーは不変（OA 系の互換を壊さない）。

### 5.2 OPEN — 再訪（REVISIT）

開幕時に active な carryover があれば、**他の何よりも先に問いだけを表示**する:

> 「前回、この問いを残しました: 『…』 — いまならどう考えますか？」

- 任意の一文回答 → `role='revisit_answer'`（`source_trace_id` = carryover 行）。
  スキップ可。回答後（またはスキップ後）に初めて通常の開幕情報を表示する。
- 回答直後に**前回との差分の事実文**を最大3件表示する（非LLM・読み時導出）:
  「前回の遠征以降、あなたはこのコースで問いを2件確定しています」ではなく（数値・UC9）、
  「前回残した問いの近くに、あなたが確定した引っかかりがあります: 『…』」のような
  **列挙型の事実文**（k や件数を出さない。個人地図の構造差分レンダリングは Phase 2）。

### 5.3 ELICIT / DIFF — 論文スケールの並置（v1）

discuss 開幕の任意ステップとして「予想してから開く」を置く（精読モード on のときのみ既定表示、
off でもリンクからは入れる）:

1. タイトル（+著者・年）だけを表示 → 「この論文は何を示すと思いますか？」任意一文
2. アブストラクト表示
3. **並置 DIFF**: 左に本人の予想（逐語）、右に paper_skeleton / thesis の骨格
   （`central_question` / `central_thesis` — build_opening が既に投影済みのフィールドを再利用）。
   判定・採点・一致度は出さない（UC2）。
4. 任意の一行「予想と何が違いましたか？」→ UPDATE として記録（§5.5）

予想の逐語は `kind='intention'` / `role='opening_motive'` の payload に
`prediction: {...}` として同居させる（行を増やさない）。

### 5.4 ANCHOR — 軽量4ボタン

教材区画・discuss 応答バブルの近傍に「気になる / まだ分からない / あとで戻る /
何かとつながりそう」を付箋様 UI で常設（§4.2 のマッピング）。既存のテキスト選択→
「ここについて質問」導線は不変。1タップで確定し、確認ダイアログを出さない（軽さが本体）。
取り消しは既存 dismiss（`structure_anchor.status='dismissed'` 保持）。

### 5.5 UPDATE / LEAVE — 着地

既存 discuss 着地画面に統合する（着地画面を帳票化しない — 三案統合の設計判断を継承）:

- UPDATE: 既存 reflection（「今日の理解を自分の言葉で」= `record_learner_articulated_tension`）
  をそのまま使う。新 API を作らない。
- LEAVE: 「次に持ち越すなら、どの問いにしますか？」— **新規入力欄ではなく選択リスト**。
  候補 = 当日セッションの本人痕跡（articulated tension・learner_selected/confirmed anchor・
  `revisit: true` のアンカー）+ 最後に自由入力1枠。選択で `role='carryover_question'` を記録し
  旧 carryover を superseded に。選ばなければ何も起きない。
- トリガーは既存の着地トリガー（明示終了 / トピック切替 / 無活動15分・ポーリング禁止）を共有。

### 5.6 API（Phase 1）

```
GET  /api/learning/courses/{course_id}/discuss/opening      -- 既存。optional キー intention を同梱
POST /api/learning/courses/{course_id}/cycle/intention      -- {role, text, source_trace_id?, prediction?}
                                                            -- 201 {ok, trace_id, facts[]}（facts は revisit_answer のみ）
POST /api/learning/cycle/intention/{trace_id}/dismiss       -- status 遷移のみ（UC6）
POST /api/learning/courses/{course_id}/cycle/anchor         -- {quick_label, topic_id?, selection_*?, chunk_id?, element_*?}
                                                            -- 軽量アンカーの1タップ確定（chat 非経由の専用経路。
                                                            -- 既存 anchors API に作成経路が無いため新設 — 実装裁定）
GET  /api/learning/courses/{course_id}/cycle/landing-candidates -- LEAVE 選択リスト {candidates[]}
POST /api/learning/courses/{course_id}/discuss/reflection   -- 既存。変更なし
```

- すべて本人のみ（受講ゲートは `get_accessible_course_data`）。教員・管理者は個別行に
  アクセス不可（tension と同型）。
- migration: **0**。新テーブル・新列なし。

### 5.7 フロント（Phase 1）

- `discuss.js` / `app.js`: 開幕一枠（motive / carryover 再回答）・着地の LEAVE 選択・
  並置 DIFF ビュー・軽量アンカー4ボタン。ES6（app.js 系）。
- 1画面レイアウト規約（`.mn` overflow: clip・下段 flex 0 0 auto）を守る。ポーリング禁止。
- 学習側 UI 追加につきマニュアル節 + インスペクト係留の既存運用（ui_anchors）に追随する。

---

## §6 Phase 2 仕様 — 深い読解（実装済み 2026-08-13。裁定は各項・記録は §15）

- **式スケール ELICIT（支配項の直感道場）**: R層に `elicit_mode='regime'` / `'next_step'` を
  追加（§4.4）。出題は derivation_chain の**近似 operation の地点のみ**（恒等変形に出さない）。
  精読モード on のとき、導出表示に薄い霧 + ワンタップ予測 → 霧が晴れる連続的
  インタラクション（モーダル禁止）。判定は既存 DIFF（非LLM・決定論）・リビールは出典
  evidence_quote。対比ペア出題（同じ式・違う領域）は out-of-scope にせず optional で対応。
  間隔制御: 同一トピック内で predict 系提示は N 回に1回に間引く（機械的クリック化の防止）。
- **論文骨格予測**: §5.3 の並置を、thesis の support_structure との対応付き提示に拡張
  （対応付けは LLM 候補・仮説文体・candidate-only）。
- **AI 4モードの実装**（§8）。
- **帰り道の景色（再訪差分レンダリング）**: REVISIT の差分事実文を、personal graph の
  過去時点導出との構造差分（「前回ここを訪れたとき、この橋はまだ架かっていませんでした」）に
  昇格。毎回導出（PN-2）のままタイムスタンプでフィルタする読み時計算。数値なし。
  差分提示の直前に任意の自由想起「何が変わったと思いますか？」を挟める（スキップ可）。

## §7 Phase 3 / Phase 4 — 参照仕様

本書では枠のみ規定し、着手時に**専用設計書を切る**（本書の子にしない）:

- **Phase 3 賭け金の台帳**: 反証条件レジストリ（D層拡張・candidate→教員確定）→ 観測の反実仮想
  → 独立支持経路。必須条件は panel 文書 §3 提案3 のとおり。特に**閉世界語彙の固定**
  （「このコーパスの中では検証記録がありません」以外の不在言明を構造的に禁止）と
  verification_proposal 昇格前のコーパス外文献確認を、設計書の不変条項に昇格させること。
  → **専用設計書 `stakes_ledger_design.md` を 2026-08-13 に執筆済み**（SL1〜SL10 で上記
  必須条件を不変条項化。着手条件は充足 — 実装はガードレールテストの作成から始める）。
- **Phase 4 集合知（橋の生態系・静かな開通・欲望の小径）**: ガードレール5条件
  （①本人の自力探索後にのみ表示 ②減衰 ③独立コホート再現のみ ④提示順の沈黙的並べ替え禁止
  ⑤未踏を劣位として描かない）+ つながりの弁の安全装置3点（キュー順序のみに使用・確定者への
  事前非開示・高統合効果リンクは教員2名確定）を**先に**ガードレールテストとして書けることを
  着手条件とする。

---

## §8 AI 4モード仕様（Phase 2）

新しいチャットモード・新エンドポイントを作らず、既存学習チャットの内部で実現する（UC10）。

| モード | 挙動 | 実装 |
|---|---|---|
| **Elicit** | 答えを言わず、本人の予測を引き出す短い問いを1つ返す | 精読モード中の ELICIT 局面でシステムプロンプト契約（「解を提示しない」をテスト契約フレーズ化）。1 LLM コール |
| **Diff** | 本人の予想（逐語）と骨格・出典の差分**候補**を仮説文体で提示 | 並置ビューからの明示ボタン「AIに差分の観点を出してもらう」。candidate-only・出典 verbatim 検査 |
| **Explain** | 本人が要求した部分だけ説明 | 既存 RAG チャットそのもの（変更なし） |
| **Reflect / Carry** | 当日の痕跡の整理・持ち越し候補の列挙 | **非LLM を既定**（§5.5 の選択リスト）。LLM は使わない（v1）。将来使う場合も整形のみ・本人の逐語を置換しない |

- モードはユーザーに「モード選択」として露出しない。局面（ELICIT 中か・並置ビューか・
  通常チャットか）が自然に決める。discuss の歩調合わせ（DA1〜6）・casual・usage_help の
  既存判定順は変更しない。
- U層 feature タグ: `learning:cycle_elicit` / `learning:cycle_diff` を追加（分離計測）。
- コスト: 既存 `LEARNING_CHAT_MAX_CALLS_PER_DAY` に相乗り（専用上限は U層実測後に判断 —
  discuss 裁定 #9 と同型）。LLM 失敗時は骨格のみで続行（degraded 事実文 + 200、UC8）。

---

## §9 画面構成の原則

```
教材本文（中心）
  └ 付箋: [気になる] [まだ分からない] [あとで戻る] [何かとつながりそう]
     ↓ 精読モード on のときだけ、判断分岐点に薄い霧 → ワンタップ予測 → 晴れる
並置 DIFF（必要時のみ・判定なし）
AIアシスタント（必要時のみ展開・常時ウィンドウにしない）
```

- チャットは常設の主役にしない。ただし**既存のチャット UI・二枚看板・discuss バーは撤去しない**
  （UC1: 既定体験を変えない。理解サイクルは opt-in の層として上に載る）。
- 霧・旗・付箋の視覚言語は atlas の霧表現と衝突しないこと（atlas は分野地図の未踏、
  UC は開示前の伏せ。色・テクスチャを分ける）。

---

## §10 計測（内部のみ）

discuss 観測基盤（`discuss_metric_events`, DO1〜6）に**相乗り**する（新テーブルなし）:
イベント語彙に `cycle_motive_saved` / `cycle_prediction_saved` / `cycle_diff_viewed` /
`cycle_carryover_saved` / `cycle_revisit_answered` / `cycle_anchor_quick` を追加。
本文非含有・仮名化・学習者に数値非表示・削除 API なし・計測失敗で UX を止めない（DO 条項継承）。
成功の観察対象は「予測 → 並置 → 更新 → 持ち越し → 再訪」の閉ループ通過であり、
正答率・滞在時間・連続日数を KPI にしない。参考目安は自動ゲートにしない（DO5）。

---

## §11 ガードレールテスト

`backend/tests/test_understanding_cycle_guardrails.py`（`guardrail_helpers.py` を使用）:

1. `kind='intention'` が tension/anchor worker の SQL・digest・問いの軌跡・personal_graph
   導出・教員向け集約のいずれにも現れない（構造的除外）
2. intention / 軽量アンカーに行削除 API が存在しない（status 遷移のみ・UC6）
3. cycle 系 API が本人以外（教員・管理者）からアクセスできない（fail-closed）
4. DIFF 並置ビューに正誤・点数・一致度の語彙が現れない（UI static・UC2）
5. 精読モードの既定が off であり、既定レンダリングを変更しない（UI static・UC1）
6. 差分事実文・着地・開幕に数値（件数・率）が現れない（UC9）
7. 督促・通知・連続日数に相当する語彙/経路が存在しない（UC4）
8. Elicit モードのプロンプトが「解を提示しない」契約フレーズを保持（Phase 2・DA テストと同型）
9. R層新 elicit_mode が伏せフィールド非漏洩・出題対象制限（source_backed + 承認済み）を継承
10. core 追加モジュールが FastAPI を import しない

---

## §12 非スコープ（v1）

- 学習者モデル・習熟度推定・スキーマ距離による適応（UC5/UC7 で恒久的に排除）
- 白地図スケッチ（地図スケール ELICIT）— Phase 2 以降、atlas 側と合同で別途設計
- 時間レンズ（Chronicle Lens）— 別設計書（W層第5レンズ）
- Phase 3 / Phase 4 の実装詳細（§7 の着手条件のみ本書が規定）
- intention の「わたしの地図」時間層への表示（余地は残すが v1 では出さない）
- 音声・casual 経路への精読モード適用
- 教員向けのサイクル痕跡の可視化（一切作らない。既存 k-匿名集約のみ）

## §13 未決事項（実装時に確認）

1. discuss opening DTO への intention 同梱が、opening の互換テスト
   （`test_discuss_opening_projection.py`）と衝突しないか — optional キー追加で通る想定
2. 着地画面の LEAVE 選択リストの上限（3〜5件想定）と、当日痕跡の「当日」境界
   （session_ref の粒度 = 着地トリガー単位）
3. `_discussion` 疑似トピック以外（通常トピック学習）への carryover 拡張の要否
   — v1 はコース単位1件で開始し、トピック単位化は実測後
4. 軽量アンカー4ボタンと既存「ここについて質問」導線の視覚的優先順位
5. Phase 2 の霧 UI と学習画面1画面レイアウト規約の両立（スライドステージ内での予測挿入位置）

---

## §14 Phase 1 実装記録（2026-08-13）

Fable 5 指揮・Sonnet サブエージェント2体（backend / frontend）+ 偵察2体で実装。
**migration 0**（interest_traces.kind は CHECK なし TEXT — 020 確認済み）。
バックエンドフルスイート **8,958 pass**（新規テスト 105+21 件を含む・リグレッションなし）。

- **バックエンド**: `backend/core/cycle/`（schema=INTENTION_ROLES・QUICK_LABELS の正本 /
  queries=SQL 読み取り（personal_graph 流儀・遅延 import）/ derive=純関数。FastAPI 非 import）
  + `backend/api/routes/cycle.py`（learning_router、main.py 直接登録）+ services.py に
  `record_cycle_intention` / `dismiss_cycle_intention` / `record_cycle_anchor_mark` /
  `_supersede_active_carryover`（書き込みは `record_interest_trace` 唯一入口を維持）。
  opening への同梱は `get_discussion_opening` の route 層マージ（fail-open・core 非改変・
  ゲート `get_course_data` 不変）。
- **除外の実装**: `_INTEREST_KINDS` に intention/anchor_mark 追加（丸め事故防止）、
  `get_interest_traces`（問いの軌跡）と `aggregate_interest_dashboard`（cohort/hotspots）に
  明示除外。後者は **help_usage の既存混入穴も同時に修正**。worker / personal_graph /
  digest は許可リスト方式のため非改変で構造的に除外。
- **観測**: `METRIC_EVENT_VOCAB` に cycle_* 6語彙を追加（payload は常に空 `{}` —
  sanitize ホワイトリストは拡張しない）。`test_discuss_observation.py` の語彙固定
  アサーションを 14→20 に更新。
- **フロントエンド**: discuss.js（`renderCycleOpeningSection`＝buildOpeningHtml 先頭・
  carryover 再回答 + facts 表示・初回動機・精読モード時の予想→並置 DIFF・landing の
  LEAVE 区画 + landing-candidates 取得・`invalidateOpeningCache` を全 intention POST 後に
  実施）+ app.js（選択ポップオーバー `#quick-anchor-popover` + 常設ストリップ
  `#quick-anchor-strip`・精読モードトグル `eg_precision_reading:<courseId>`・
  `isPrecisionReading` は Discuss.init への DI）+ styles.css（cycle-*/quick-anchor-* —
  discuss.js の `%` 全域禁止のためレイアウトは CSS 側のみ）。
- **3点セット**: `material.quick-anchor` を KNOWN_UI_ANCHOR_IDS + UI_ANCHORS に登録し、
  `docs/manual/student/02-student.md` に `{#quick-anchor}` / `{#understanding-cycle}` 節を
  追加（目次追随・改変履歴表現なし）。discuss.js 描画内には data-ui-anchor を置かない
  （学習側スキャンは index.html + app.js のみで扱いが割れるため）。
- **テスト**: `test_understanding_cycle_{core,api,guardrails,ui_static}.py`
  （23+41+23+21）。§11 のうち Phase 2 項目（8・9）は未実装分として保留。
- **設計書からの逸脱（§4.1/§4.2/§5.6 に反映済み）**: status='open' 採用 /
  kind='anchor_mark' 新設 / cycle/anchor・landing-candidates の専用エンドポイント新設 /
  opening_motive の text 空 + prediction.text 許容 / intention・anchor_mark の監査記帳なし
  （本人専用メモ）。
- **既知のトレードオフ**: carryover の supersede と新規 INSERT が別トランザクション
  （INSERT 失敗時に旧 carryover が superseded のまま残る稀ケース。行は保持されるため
  P4 違反ではない。v1 許容）。
- **残作業**: docker 実機 E2E（開幕→記録→着地→再訪の実データ確認）。

## §15 Phase 2 実装記録（2026-08-13）

Fable 5 指揮・偵察2体（R層/チャット側）+ Sonnet 実装2体（impl-regime / impl-aimodes、
ファイル所有権分離・並列）。**migration 0**。バックエンドフルスイート **9,036 pass**
（Phase 1 完了時点から +78・リグレッションなし）。

### 着手前4論点の裁定（§13-5 含む）

1. **霧 UI の載せ場所（§13-5）**: 学習者 UI に導出ビューアが存在しないため、式スケール
   ELICIT は **R層 reconstruction カード内で完結**させる（教材本文への霧の織り込みは v2）。
   モーダル禁止要件はカードのインライン展開（`#reconstruction-region`）が既に満たす。
2. **regime 出題の生成タイミング**: 非LLM・document 単位バッチ。
   `worker.run_item_authoring_for_document` の末尾に相乗りし、既存トリガー2本
   （claim 承認時フック・admin バッチ API）の両方から自動で走る。CostGate 非経由
   （symbol probe と同じ扱い）。
3. **間隔制御**: 「N回に1回」ではなく**連続回避**方式 — 本人の直近回答が導出系
   （regime/next_step）なら next 選定の ORDER BY で導出系を後回し（除外しない・
   サーバ側決定論・migration 不要・クライアントに間隔ロジックを置かない）。
4. **AI モードの実装形（方式A）**: cycle の LLM コールは既存 learning_chat の
   1コール地点に相乗り（`LearningChatRequest.cycle_mode ∈ {elicit, diff}`、不正値 422）。
   CostGate（`LEARNING_CHAT_MAX_CALLS_PER_DAY`）・degraded 縮退・window_history・
   コース単位モデル上書きを全て自動継承。新エンドポイントなし。

### 実装内容

- **式スケール ELICIT（支配項の直感道場）**: `ELICIT_MODES` += regime/next_step、
  新設 `CHOICE_MODES = (predict, regime, next_step)`（選択式 DIFF の集合）。
  `core/reconstruction/derivation_source.py`（新規・非LLM・FastAPI 非 import）が
  derivation_chain artifact から3段ゲート（①`REGIME_OPERATIONS`＝近似・削減系のみ
  ②step/chain の `source_evidence_ids` 非空 ③claim UUID が legacy_ids で解決可能）で
  probe を決定論生成。next_step = 操作当て（選択肢は同 chain の他 step → 固定順
  フォールバック、`operation_label` 訳・重複/空ラベルスキップ）、regime = 消える記号
  当て（正解 = eliminated_symbols 先頭1つのみ・ディストラクタは retained_symbols —
  正解の一意性を構造的に保証）。`author='system'` / `status='auto'` /
  `author_confidence = step.confidence`（既存の事後監査ループ＝health/レビューキューが
  無改造で故障検出器として働く）。**設計書 §4.4 の `linearize_*` 型ワイルドカードは
  実在せず**、実際は統制語彙 + `operation_subtype`（偵察で確定・§4.4 の記述を本記録で
  上書き）。worker の冪等 SQL・上限 SQL は `NOT IN ('symbol','regime','next_step')` 化
  （regime item が predict オーサリングをブロックしない）。
- **AI 4モード（Elicit/Diff）**: `_get_cycle_elicit_system_prompt`（契約フレーズ:
  「解を提示しないでください」「問いを一つだけ」「学生の直前の発話」）/
  `_get_cycle_diff_system_prompt`（「食い違いの可能性」「断定しないでください」
  「採点や点数評価をしないでください」「候補」）。U層 feature
  `learning:cycle_elicit` / `learning:cycle_diff`（KNOWN_FEATURES +
  `llm_policy.scene_for_feature` + `_FEATURE_ENV_SETTINGS` の3点セット登録）。
  Explain = 既存 RAG（変更なし）・Reflect/Carry = 非LLM（Phase 1 の LEAVE のまま）。
  フロントは discuss.js の predict エリアに「AIから問いをもらう」・並置 DIFF に
  「AIに違いの観点を出してもらう」（`window.sendPrompt` + `cycle_mode` payload）。
- **帰り道の景色**: `core/cycle/map_diff.py`（新規）— `build_network_as_of`
  （personal_graph の fetch 結果を created_at ISO 文字列で Python 側フィルタ →
  `build_network` 純関数を再利用。**personal_graph パッケージ非改変** — ガードレールが
  同パッケージへの intention/anchor_mark 文字列出現を禁止）+ `build_map_diff_facts`
  （新規ノード「あなたの地図に『X』が加わっています」/ 新規橋「自分でつないだ橋が
  増えています」、最大2件）。**否定形の断言はしない** — interest_traces は in-place
  更新されるため過去時点の不在は保証できない（肯定形のみ）。revisit facts は
  構造差分を先頭に合計3件上限（`build_revisit_facts` の後方互換な第3引数）。
- **テスト**: `test_understanding_cycle_regime.py`（52）+ `test_understanding_cycle_phase2.py`
  + `test_reconstruction_loop.py` 追記 + guardrails §9 節追記（CHOICE_MODES 語彙・
  derivation_source の非 FastAPI/非 LLM/DELETE 不在・REGIME_OPERATIONS が恒等変形を
  含まない・worker SQL 形・配信 SQL リテラル維持）。§11-8/§11-9 はこれで実装済み。
- **実装上の注意（将来の改修者へ）**: learning_chat には逐語固定のテスト窓がある
  （プロンプト分岐は「# 5. 回答の生成」後 600 字・feature 分岐は messages.append 後
  400 字・足場分岐はインデント込み逐語）。cycle 分岐は先頭にコンパクトに挿入する形で
  共存している。intent 分類・前提ゲートのバイパス条件に `_cycle_mode` は加えていない
  （テストが完全一致を要求。フロントが常に intent_mode=discuss を併送するため
  `_is_discuss` で実質バイパス済み）。
- **残作業**: docker 実機 E2E。Phase 3/4 は §7 の着手条件（専用設計書 + ガードレール
  先行）のとおり未着手。対比ペア出題（contrasting cases）と論文骨格予測の
  support_structure 対応付き LLM 提示は Phase 2 の optional として未実装
  （AI Diff モードが部分的に代替）。

---

*本書は panel 討論（vision_expansion_proposals_2026-08.md）と UX 再構成提案
（vision_expansion_ux_proposal_revised_2026-08-13.md）を仕様として統合したもの。
両文書は経緯記録として残し、以後の仕様変更は本書を更新する。*
