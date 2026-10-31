# 行動型（archetype）

行動型は「分野に依らない振る舞いの癖」だけを書くファイルです。1 型 = 1 ファイル。
ペルソナ（`uxsim/domains/<domain_key>/personas/`）は `archetype: students/<id>` のように行動型を参照し、
その上に分野の知識・誤解・目標を重ねます（正本: `docs/architecture/persona_enactment_testing_design.md` §6・§11）。

## 書いてはいけないこと

- **分野の語を一切書かない**（概念名・天体名・手法名・装置名）。分野の語は分野パックの
  `knowledge/concepts.yaml` にだけ置く。ガードレールは行動型ファイルに各分野の概念ラベルが
  部分文字列として現れないことを検査する。
- 数値の点数・割合・確率を書かない（癖は段階語で表す。PE7・PE10）。
- 製品の内部名（API パス・テーブル名・不変条項の番号）を書かない。ペルソナは画面に見えるものしか知らない（§6.3）。

## ファイルの形

```yaml
id: skeptic                     # ファイル名と一致
population: students            # students | teachers
summary: 出典と確かめられ方を毎回確かめる   # 1 行の要約（報告書の見出しに使う）
habits: { ... }                 # 下の語彙から選ぶ。欄を省いたら既定値
voice: |                        # 話し方・考え方（think-aloud と入力文の文体に使う）
  ...
gives_up_when: |                # 諦める条件を言葉で（patience を補う。runner は続行を強制しない）
  ...
asks_out_of_principle:          # 製品の原則に反する要求の例（要求自体は欠陥ではない。応じ方を見る — §6.3）
  - ...
```

## habits の語彙

**頻度語**（下の表で「頻度語」と書いた欄に共通）: `never`（しない）/ `rarely`（ほとんどしない）/
`sometimes`（ときどき）/ `often`（よくする）/ `always`（毎回する）。段階語であって回数や割合ではない。

`yes` / `no` を値に取る欄は **引用符で囲んで書く**（`uses_voice: "no"`）。YAML 1.1 では裸の `yes` / `no` が真偽値に
読まれてしまい、語彙の照合がずれるため。

### 共通（students / teachers）

| 欄 | 値 | 意味 |
|---|---|---|
| `reads_manual` | `never` / `when_stuck` / `first` | 画面の「使い方」（インスペクト）やマニュアルを引く時機。`never` は詰まっても読まない |
| `patience` | `low` / `medium` / `high` | 同じ操作での失敗にどれだけ耐えるか。`low` は 2 回うまくいかなければ諦める、`medium` は 3〜4 回、`high` は理由が分かるまで粘る |
| `device` | `desktop` / `narrow` | 画面幅。`narrow` は 375px 相当（browser runner のみ意味を持つ） |
| `session_length` | `short` / `medium` / `long` | 1 回の利用で使う時間の長さの感覚。`short` は数操作で切り上げる |
| `returns_later` | `no` / `yes` | 途中で離れて、別のセッションで戻ってくるか |
| `language_mix` | `ja_only` / `en_only` / `mixed` | 入力に使う言語。`mixed` は日本語に英語の術語を混ぜる |
| `demands_out_of_principle` | 頻度語 | 製品の原則に反する要求（点数・比較・進捗率など）をどれだけ口にするか |

### 学生（students）

| 欄 | 値 | 意味 |
|---|---|---|
| `asks_questions` | 頻度語 | チャットで質問する頻度 |
| `uses_voice` | `no` / `sometimes` / `mostly` | ハンズフリー音声会話・読み上げを使うか |
| `prefers_mode` | `sequential` / `discuss` / `lecture` / `mixed` | 好む学び方（順番に学ぶ・論文と議論・レクチャーを聴く・混在） |
| `opens_sources` | 頻度語 | 回答の出典・⚓（引用）を開くか |
| `checks_verification` | 頻度語 | 「確かめられているか」の一行（検証状態）を読むか |
| `pushes_back` | 頻度語 | AI の説明や判定に反論・異議を出すか |
| `writes_own_words` | 頻度語 | 着地や書き置きで自分の言葉を書くか |
| `uses_maps` | 頻度語 | 分野の地図・わたしの地図・論文の海を開くか |
| `rewrites_messages` | 頻度語 | 送ったメッセージを書き直すか |

### 教員（teachers）

| 欄 | 値 | 意味 |
|---|---|---|
| `uses_copilot` | `never` / `when_stuck` / `always` | 操作アシスタントに聞いてから操作するか |
| `follows_next_steps` | `no` / `sometimes` / `always` | 「次にやること」の道案内に従うか |
| `reviews_before_publish` | `clicks_through` / `skims` / `reads_all` | リリース前の確認で画面を読むか、「次へ」を続けて押すか |
| `edits_explanations` | 頻度語 | AI の説明・承認候補を自分で直すか |
| `rejects_candidates` | 頻度語 | 承認候補を却下するか（却下するときは理由を書く） |
| `reads_confirmations` | `skips` / `skims` / `reads` | 確認ダイアログの文を読むか |
| `manages_accounts` | 頻度語 | 学生アカウント・グループ・共有設定の操作をするか |

## 一覧

| 集団 | id | 要約 |
|---|---|---|
| students | `newcomer` | 入門者。質問が多く、マニュアルは読まない |
| students | `confident_theorist` | 自信があり反論する。議論を好む |
| students | `adjacent_field` | 隣の分野から来た。言葉の呼び方の違いで躓く |
| students | `non_native` | 英語で入力する |
| students | `night_parttime` | 夜に短時間だけ使い、途中で離れて翌日戻る |
| students | `skeptic` | 出典と確かめられ方を毎回確かめる |
| students | `voice_first` | 手が塞がっていることが多く、音声で話しかける |
| students | `lecture_listener` | 読むより、レクチャーを再生して聴くのを好む |
| teachers | `rookie_follows_todo` | 初めて使う。「次にやること」に素直に従う |
| teachers | `veteran_clicks_through` | 画面を読まず勘で押す。確認は続けて押す |
| teachers | `meticulous_reviewer` | 全部読んでから公開する。承認・却下・説明の手直しまで行う |
| teachers | `copilot_dependent` | 操作のたびに操作アシスタントに聞いてから動く |
| teachers | `delegator` | アカウント・グループ・共有の段取りは熱心だが、内容の確認は最小限 |

行動型を足すのは、2 分野以上で同じ癖のペルソナが要るときだけ（§11.5）。1 分野だけならペルソナの
`overrides:` で済ませる。行動型を改名・削除しない（使わなくなったら `status: retired` を付けて残す）。

## 参考: ペルソナ側の欄（分野パックに置く）

行動型そのものではないが、行動型を参照するペルソナファイル（`domains/<key>/personas/`）が持つ欄の語彙を
ここに控える。

| 欄 | 対象 | 値 |
|---|---|---|
| `archetype` | 両方 | `students/<id>` / `teachers/<id>`（このディレクトリのファイル） |
| `language` | 両方 | `ja` / `en`（ペルソナが入力に使う言語） |
| `knowledge.{knows,vague,unknown}` | 学生 | 分野の `knowledge/concepts.yaml` の `concepts[].label` か `sub_concepts[].label` のみ |
| `misconceptions` / `goals` | 学生 | 分野の `knowledge/misconceptions.yaml` / `goals.yaml` の id |
| `teaching.review_depth` | 教員 | `shallow`（ほとんど確かめない）/ `moderate`（気になった所だけ確かめる）/ `thorough`（全部確かめる） |
| `course_brief` | 教員 | 分野の `corpus/course_briefs/<名>.md` の拡張子を除いた名前 |
| `overrides.habits` | 両方 | 行動型の habits を局所的に上書きする（上の語彙の範囲で） |
