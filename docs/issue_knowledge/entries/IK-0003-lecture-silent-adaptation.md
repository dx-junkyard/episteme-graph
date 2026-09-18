---
id: IK-0003
title: レクチャーが履歴から「習得済み」を推定し、告知なく内容を落とす
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-10
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §2 F3
  - docs/architecture/six_lenses_2026-09-10/01_learner.md §2 提案1
feature_context:
  realizing: 教材をレクチャーとして順に提示し、学習者が音声と表示で読み進められるようにする
  layers: [lecture_player, frontend_learning_ui]
classification:
  primary: governance
  facets: [governance.assignment, connection.information]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「何を読むかの決定が、本人ではなく推定に割り当てられている」こと。推定の実装は
    仕様どおり動いており、条件も伝わっている。割り当てを本人へ戻さないかぎり、推定の材料を
    差し替えても同じ構造が残る。確認手段は提案1 が引く省略・要約置換の分岐と、習得済みの
    第三の供給源が接触の痕跡であること、および学習者側に告知が無いこと。
generalization:
  level: general
  general_form: 提示する内容の取捨を推定に任せ、取捨が起きた事実を本人に告げない
pattern: ai-decides-instead-of-human
discovery:
  perspective: [invariant_audit, trace_walk]
  note: >-
    「沈黙適応をしない」「出所の正直さ」という不変条項と実装を照合した。習得済みという
    語の供給源を遡ると、そのうち一つが「そのトピックで質問した履歴がある」だった。
    質問するほど内容が消えるという逆向きの選択になる。表示側に告知が無いことも併せて確認した。
resolution:
  perspective: [responsibility_move, carry_through]
  note: >-
    推定の精度を上げず、内容を改変する分岐そのものを落とした。推定の結果は「前に触れた箇所」
    という注記だけに使い、畳むかどうかは本人のトグルに置く。消すのではなく畳み、畳んだ位置に
    開く行を残す。機能として告知していたマニュアルの記述も実挙動に合わせた。
  landed_in:
    - backend/core/lecture.py
    - frontend/public/js/app.js
    - backend/tests/test_lecture_no_silent_adaptation_guardrails.py
    - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 3
    - docs/manual/student/02-student.md
related: [IK-0001, IK-0004]
view_of: []
history: []
---

## 課題

症状は、同じトピックで一度質問すると、次にそのトピックのレクチャーを開いたときに一部の区画が
消えるか要約に置き換わることである。学習者側にはその事実が表示されない。

原因は推定の精度ではなく、**何を読むかという決定が本人から推定へ移っていた**ことにある。しかも
推定の材料の一つは「質問した」という接触の痕跡だったので、理解していない箇所ほど質問し、
その箇所ほど省略されるという逆向きの選択が起こりうる。見せられていないものを学習者は知らないので、
異議の対象にすらならない。

## 発見の観点

不変条項（推定で提示内容を暗黙に変えない・出所を正直に）と実装の照合。習得済みという語の
供給源を遡って三つ目に接触の痕跡が出てきたところで、害の向きが逆であることが確定した。
表示側に告知が無いことを併せて確認し、「本人が気づけない」という性質を押さえた。

## 解決の観点

推定を精緻化する案は採らなかった。精緻でも本人が知らないまま内容が変わる点は変わらないからで
ある。内容を改変する分岐を落とし、推定の結果は注記に留め、畳む操作を本人の側に置いた。
消さずに畳み、畳んだ位置に開く行を残すことで、情報を落とさないという条項とも両立させた。

## 一般化

「利用者に合わせて出し分ける」機能すべてで再発する。出し分けの根拠が接触の痕跡であるほど、
本人の意図と逆に働きやすい。辞書の `ai-decides-instead-of-human` に対応し、処方は
「出し分けの決定を本人の操作に移し、推定は注記に留める」。
