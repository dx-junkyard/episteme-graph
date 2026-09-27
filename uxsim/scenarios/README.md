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
preconditions: [...]              # 任意。始める前に成り立っているべき状態（事実文）
session: 1                        # 任意。何回目のセッションとして走らせるか（既定 1。2 以上は再ログインで近似 — §7.4）
steps: [...]                      # 下の記法
stop_when: [gave_up, budget_exhausted]
expects:                          # 審判 D（文書）の期待の引き先。manual は docs/manual/ からの相対パス + #anchor
  - manual: student/02-student.md#ai-chat
watch_for:                        # 審判が見るべきこと（平叙の事実文。点数・閾値を書かない）
  - 429 のときに上限に達したことが本人に伝わる文が出る
```

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
| teachers | `t-build-course-from-corpus` | 次にやること → 教材 → コースビルダー → 登録 → リリース前の確認 → 公開 |
| teachers | `t-review-graph-then-publish` | グラフレビューで承認・却下 → コース登録 → リリース前の確認 → 公開 |
| teachers | `t-onboard-student-and-share` | 学生アカウント作成 → グループ作成・招待 → 共有 |

経路を足すときは分野の語を書かず `params_required` で穴にする（§11.5）。経路を改名・削除しない
（使わなくなったら `status: retired` を付けて残す — 課題エントリから参照されるため）。
