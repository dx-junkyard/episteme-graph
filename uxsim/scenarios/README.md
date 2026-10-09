# 経路（scenario）

経路は「ペルソナが何を目指して、どの行為をどの順で試みるか」を書くファイルです。**分野の語を書かず**、
分野固有の値は `{{param}}` の穴にして、分野パックの `domains/<domain_key>/scenario_params/<scenario-id>.yaml`
が埋めます（正本: `docs/architecture/persona_enactment_testing_design.md` §8・§11.3）。

- `goal/student/` `goal/teacher/` — 目標志向の経路（ペルソナ LLM が各ステップで think-aloud と行為選択を行う）。
- `regression/` — 直した課題ごとの回帰経路。ファイル名は `IK-NNNN-<slug>.yaml`（課題ナレッジの ID と一致）。

## ファイルの形

```yaml
id: s-ask-until-confused          # ファイル名と一致
population: students              # students | teachers
runner: [api, browser]            # 走らせてよい runner。api だけの経路もある
goal: "{{goal_text}} のために …"   # ペルソナ LLM に渡す目標文（穴を含んでよい）
params_required: [goal_text, ...] # scenario_params が必ず埋める穴の一覧
preconditions: [...]              # 任意。始める前に成り立っているべき状態（事実文。下の「前提」）
seed_scenarios: [...]             # 任意。機械で確かめた前提が欠けていたら、先に歩いて種をまく経路の id
session: 1                        # 任意。何回目のセッションとして走らせるか（既定 1。2 以上は再ログインで近似 — §7.4）
steps: [...]                      # 下の記法
stop_when: [gave_up, budget_exhausted]
expects:                          # 審判 D（文書）の期待の引き先。manual は docs/manual/ からの相対パス + #anchor
  - manual: student/02-student.md#ai-chat
watch_for:                        # 審判が見るべきこと（平叙の事実文。点数・閾値を書かない）
  - 429 のときに上限に達したことが本人に伝わる文が出る
```

## 前提（preconditions / seed_scenarios）

`preconditions` の要素は事実文の文字列か、機械で確かめる dict です。

```yaml
preconditions:
  - text: 前のセッションでこのペルソナが書き置きと持ち越しの問いを残している
    check: return_door                 # 語彙は scenario.py の PRECONDITION_CHECKS（return_door / owns_course）
    requires: [leave_note, carryover]  # return_door の部品（leave_note / carryover / last_tension）。空なら「扉が空でない」
seed_scenarios: [s-discuss-one-paper]
```

runner は経路を始める前に `check` 付きの前提を学習者の GET（`return_door` は `GET /cycle/return-door`）で確かめ、
その GET を `runner_driven:` の transcript 行に残します。満たされていなければ、

1. `seed_scenarios` があればそれを同じペルソナ・同じセッションで先に歩き（最初の行に `seeded_precondition:`）、
   もう一度確かめる。種まきの前に runner は通常の経路と同じコースの文脈を用意する（受講していなければ
   `learning.course.enroll` → `learning.course.open`。`runner_driven:` の transcript 行・第 18 周 st-07 は
   これが無く、受講可能なだけのコースを開いて 404 → 以降が `course_id` 無しで空振りした）。種まき中のペルソナの
   目標には「議論を終える前に（部品）を必ず残してから終える」を足し、ペルソナが途中でやめても（persona_stop /
   goal_reached / blocked）まだ歩いていない部品を残す手順（`learning.cycle.intention` の role=leave_note /
   carryover_question、last_tension は `learning.tension.confirm`）へ進める（`seed_landing:`）。記録は
   `meta.json` の `seeded_precondition`（bootstrap・止まり方・着地した部品・満たされたか）。
2. 種まき経路が無い、または歩いても足りなければ、経路を**歩かずに飛ばし**、`meta.json` の `blocked_precondition`
   と notes（`blocked_precondition:`）・runner_note に残す。空のセッションを歩くと「扉・持ち越しが空」を製品の欠陥と
   読む偽所見になる（第 17 周 st-07）。

`check: owns_course`（第 23 周）は教員の `GET /api/admin/courses` に自分の所有するコースがあるか（`requires: [unpublished]`
なら未公開のもの）を確かめ、あればそのコースを「確認して公開」の対象にする。教員の種まき経路はコースの受講を要しない。

文字列だけの前提は機械では確かめません（ペルソナと審判が読む事実文のまま）。

## steps の記法

| 形 | 意味 |
|---|---|
| `- do: <action_id>` | 行為レジストリ（`uxsim/actions/registry.py`）の id を 1 つ実行する。**レジストリに無い id は書けない**（PE9） |
| `  with: {k: v}` | 行為の引数。引数を取る行為にだけ書く（例: `learning.chat.ask` の `message`） |
| `  note: "…"` | ペルソナ LLM への補足（どういうつもりでこの行為をするか）。行為の引数ではない。runner は API に送らない |
| `- repeat: {until: "<条件>", max: N, do: <action_id>, with: {…}}` | 条件が成り立つか N 回に達するまで繰り返す。`until` は審判・runner が読む平叙の条件文 |
| `- repeat: {until: "…", max: N, steps: [ … ]}` | 複数ステップの塊を繰り返す |
| `- choose: [ {do: …}, {do: …} ]` | 並べた候補からペルソナ LLM が 1 つ選ぶ（行動型の癖に従う）。選ばない理由も think-aloud に残す |
| `- choose: [ … ]` + `  optional: true` | 何も選ばずに先へ進んでもよい |

`until` の条件文で使ってよい語: `friction in [confused, misread, blocked, gave_up]`（ペルソナの think-aloud の friction）/
`response is 429`（製品が上限を返した）/ `steps > N` / `goal_reached`（ペルソナが目標達成と判断した）/
`persona_decides_to_stop`。

### 穴（placeholder）

| 書き方 | 意味 |
|---|---|
| `{{name}}` | `scenario_params` の値をそのまま入れる |
| `{{name \| next}}` | 値がリストのとき、この経路の中で次の要素を取り出す（尽きたらペルソナ LLM が同じ調子で作る） |
| `{{name \| current}}` | 直前に `next` で取り出した要素をもう一度使う |
| `{{persona.<欄>}}` | ペルソナファイルの欄（例: `{{persona.course_brief}}` は `corpus/course_briefs/<名>.md` の本文、`{{persona.teaching.course_subject}}`） |

`scenario_params` の `per_archetype:` に行動型 id ごとの上書きを書ける（例: `non_native` の質問の種は英語）。

### stop_when の語彙

`gave_up`（ペルソナが諦めた）/ `blocked`（前に進む行為が無い）/ `goal_reached` / `budget_exhausted`（campaign の予算）/
`quota_exhausted`（製品が上限を返し、ペルソナが続けられないと判断した）。

## 一覧

| 集団 | id | 骨（`six_lenses_2026-09-10/01_learner.md` の歩いた経路に対応） |
|---|---|---|
| students | `s-enroll-and-first-topic` | 受講登録 → 最初のトピック → 教材を読む → ⚓ を開く |
| students | `s-ask-until-confused` | 質問を続けて詰まる → 書き直し → 記号 → 降下路 → 楽屋 |
| students | `s-check-and-object` | 確認問題 → 自己確認 → 再構成 → 「判定がおかしい」 |
| students | `s-discuss-one-paper` | 論文と議論 → 開幕画面 → 議論 → 着地 → 持ち越し・書き置き |
| students | `s-lecture` | レクチャー → 中断して質問 → 短く聴く |
| students | `s-return-next-day` | 再ログイン → 扉 → 持ち越しの問いに答える → わたしの地図 → 旅 |
| students | `s-maps-and-records` | 分野の地図 → 論文の位置づけ → 論文の海 → わたしの記録 |
| students | `s-quota-edge` | 上限に達するまで質問を続ける |
| students | `s-free-wander` | 目標だけ与えて自由に歩く |
| students | `s-voice-casual` | 音声・気軽な調子で話しかける |
| students | `s-read-backbone` | ⚓ を順に開く → 要素文脈（中心命題での役割）→ 出典（§18 #1・2） |
| students | `s-discuss-backbone` | 議論の開幕（中心命題・支持構造・確かめていないこと）→ 支持構造の一つを議論（#3） |
| students | `s-symbols-chain` | 記号を順にタップ → 式の文脈 → 前提の説明（#7・8） |
| students | `s-lecture-then-trace` | レクチャー → 止めて記号 → ⚓ → 質問 → 続き（#9） |
| students | `s-two-layer-explanation` | 同じ要素の一般説明とこの論文での意味を読み比べる（#10・11） |
| students | `s-check-then-reconstruct` | 確認問題 → 自己確認 → 再構成の出題と照合（#12） |
| students | `s-trace-one-hop` | ⚓ → 要素文脈 → 隣の要素へ 1 hop ずつ → 旅（#13・14） |
| students | `s-source-roundtrip` | 質問 → 出典チップ → 原文 → 主張の参照 → 教材へ戻る（#15・16） |
| students | `s-descend-and-nearby` | 降下路 → 楽屋 → いまここの周り（#17・18） |
| students | `s-map-to-sea-to-discuss` | 分野の地図 → 推定の糸 → 位置づけ → 論文の海 → コース外の論文と議論（#19・20） |
| students | `s-return-and-journey` | （session 2）扉 → 持ち越し → わたしの地図 → 霧 → 旅 → この場所の周り（#21） |
| students | `s-ask-where-from` | 式や回答の出所を構造として問い続ける（#22・23） |
| students | `s-select-and-ask` | 教材の一文を選択して質問 → 帰属カード（#24） |
| students | `s-misconception-mirror` | 誤解を前提に質問し訂正を受ける（#25） |
| students | `s-stuck-scaffold` | 「わからない」と短く言い続ける（#26） |
| students | `s-elicit-diff` | 予想を書く → 出典と並置（#27） |
| students | `s-v5-leave-nothing` / `s-v5-empty-door` | 第 23 周 F1: 何も残さずに終える → 翌日、空の扉に何が出るか |
| students | `s-v5-leave-and-carry` / `s-v5-return-door` | F1: 書き置き・持ち越しを残す → 翌日、扉から持ち越しに答える |
| students | `s-v5-revisit-rewrite` | F1: 持ち越しに答えてから書き直す → 扉と記録の変化 |
| students | `s-v5-rewrite-delete` | F2: 書き直し → 🗑 取り消し → 会話・問いの軌跡・わたしの記録 |
| students | `s-v5-map-exclude-restore` | F2: 「地図には反映しない」→「地図に戻す」→ 地図・旅・近傍・記録 |
| students | `s-v5-recon-revise` | F2: 再構成 → 判定がおかしい → もう一度挑戦 → 記録 |
| students | `s-v5-anchor-dismiss` | F2: 帰属・引っかかりの候補を外す → 記録 |
| students | `s-v5-precision-elicit` | F3: 精読モードの入口 → 予想 → 並置 → 軽い印 → 持ち越しを選ぶ |
| students | `s-v5-predict-pause` / `s-v5-predict-resume` | F3: 予想だけ書いてやめる → 翌日、続きを辿る |
| students | `s-v5-predict-no-grading` | F3: 自由記述の予想に正誤・点数が出ない・並置が出典を示す |
| students | `s-v5-records-export` / `s-v5-records-after-retraction` | F4: わたしの記録と持ち出し・取り消し後の状態ラベル |
| students | `s-v5-ask-disclosure` | F4: 入力欄の下の開示の一行とチャットの答えの一致 |
| students | `s-v5-landscape-check` | F5 の学習者側: 位置づけ表示（教員が確認を飛ばして公開した前後で比べる） |
| teachers | `t-v5-register-own-course` | F5 の種まき: 砂場の教材から自分のコースを登録する（公開しない） |
| teachers | `t-v5-release-skip-step2` | F5: 「確認して公開」でステップ 2 を「あとで」→ 公開 |
| teachers | `t-v5-release-click-through` | F5: 読まずに「次へ」→ 公開（編集権限の無い教員） |
| teachers | `t-v5-release-meticulous` | F5: 根拠を読み、却下・再検討 → 次へ → 公開（editor あり） |
| teachers | `t-build-course-from-corpus` | 次にやること → 教材 → コースビルダー → 登録 → リリース前の確認 → 公開 |
| teachers | `t-review-graph-then-publish` | グラフレビューで承認・却下 → コース登録 → リリース前の確認 → 公開 |
| teachers | `t-onboard-student-and-share` | 学生アカウント作成 → グループ作成・招待 → 共有 |
| teachers | `t-structure-layers` | グラフ画面で理論モジュール → 論文の順 → 対話（教材を読むだけ。§18 #4・5） |
| teachers | `t-paper-order-coverage` | 論文の順で章 → ノード、掛かっていない章を見つける（#6） |
| teachers | `t-graph-dialogue-to-approve` | グラフ全体対話 → ノード対話 → 根拠 claim の承認（#28） |
| teachers | `t-deliberation-figure` | 図を深く検討 → 本文での言及 → AI アシスタントに手順を聞く（#29） |
| teachers | `t-seminar-brief` | ゼミ前ブリーフを読む（#30） |

経路を足すときは分野の語を書かず `params_required` で穴にする（§11.5）。経路を改名・削除しない
（使わなくなったら `status: retired` を付けて残す — 課題エントリから参照されるため）。
