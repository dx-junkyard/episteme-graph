---
id: IK-0666
title: "AI が訂正を返した往復の質問が「誤解の記録」としてだけ残り、問いの軌跡とわたしの地図から消える（地図は「問いを残すと現れます」と言う）"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/trace_registry_sovereignty_ledger_design.md"
  - "docs/features/personal_knowledge_network_design.md"
feature_context:
  realizing: "学習者が教材の一文を選んで反論を含む質問をし、その問いを自分の地図・問いの軌跡で見返す"
  layers: [learner_experience_b, trace_registry, personal_network]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 学習チャットは往復 1 つにつき痕跡の kind を 1 つだけ選び、誤解検出（course_update）が立つと question ではなく misconception で記録する（routes/learning.py の _trace_kind）。登録簿で misconception は personal_map=False。砂場 DB でも st-02 の選択質問は misconception 1 行だけで question 行が無く、GET /api/me/personal-network は nodes 空。構造軸 representation = 1 往復が「問い」と「誤解の指摘」の両方である事実を 1 つの kind で表せない。接続軸 information = 問いとしての情報が地図・軌跡へ届かない。
generalization:
  level: repo_pattern
  general_form: "1 つの出来事が 2 つの種別に当たるのに種別を 1 つしか持てず、片方を選んだ瞬間にもう片方の面から出来事が消える"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 23 周の砂場で、選択質問の直後に開いたわたしの地図が空で「地図から外す」を試せず（blocked）、痕跡の行を読んで種別の取り違えと確かめた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core）: 往復は常に question として記録し、誤解の指摘は別の痕跡（または同じ行の印）にする。表示語は既存の「訂正あり」に揃える。"
related: [IK-0653]
view_of: []
history: []
---

## 課題

症状: 選んだ一文への反論つきの質問をした直後、わたしの地図に何も出ず「まだ痕跡がありません。問いを残すと、ここに現れます」と出る。問いの軌跡にも問いとして残らず、わたしの記録では「誤解の記録」に入る。原因: 往復の痕跡の種別を 1 つしか持てず、訂正が付いた往復を誤解の記録として記帳する。

## 発見の観点

ペルソナの通し受講（reproduction）と痕跡の行の読み取り（data_inspection）。

## 解決の観点

未解決。問いとしての記録を常に残し、訂正の事実は別の印に分ければ、地図・軌跡・記録の 3 面で同じ問いが見える。

## 一般化

種別を排他にした記録は、複数の面がそれぞれ別の種別だけを読むと、出来事がどの面からも半分しか見えない。確かめ方は、訂正が付く往復を作り、問いを読む全面にその問いが出ることを見ること。
