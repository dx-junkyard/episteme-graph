---
id: IK-0686
title: "問いの軌跡で、学習者の質問に〔誤答〕の種別名が付き、わたしの記録では同じ行が「誤解の記録［回答で訂正の指摘あり］」と画面ごとに違う語で出る"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-10
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/trace_registry_sovereignty_ledger_design.md"
feature_context:
  realizing: "学習者が自分の質問を問いの軌跡とわたしの記録で見返す"
  layers: [trace_registry, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [aggregation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（砂場の版）: app.js の問いの軌跡は種別の表示語を JS の表（misconception → 誤答）で持ち、わたしの記録はサーバの登録簿の語（誤解の記録）を出していた。処理軸 wording = 採点語。構造軸 aggregation = 種別の表示語が 2 箇所。作業ツリーの第 3 波で表示語の正本をサーバの kind_label に寄せた（未再演）。
generalization:
  level: repo_pattern
  general_form: "同じ記録の種別名を画面ごとの表で持ち、片方の表に評価語が残る"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、懐疑的な学生が「質問なのに誤答と言われるのは引っかかる」「軌跡と記録で言い方が違う」と書いた。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "種別の表示語の正本をサーバ（label_vocab.TRACE_TRAJECTORY_KIND_LABELS）に置き、問いの軌跡の DTO が kind_label を返し、app.js はそれを描く（JS の表は旧サーバ向けの縮退表として「訂正あり」に揃えた）。質問の行に「誤答」という評価語を付けない。"
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/core/learning_experience.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_round23_server_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（問いの軌跡まで辿る場面が第 23 周の再演でまだ回っていない）"
      - "わたしの記録の系統見出し（誤解の記録）と問いの軌跡の「訂正あり」の語の距離（同じ行を 2 画面で別の語で呼ぶこと自体は残る）"
related: [IK-0666, IK-0656]
view_of: []
history: []
---

## 課題

症状: 質問に〔誤答〕。原因: 種別の表示語が画面ごとに別の表。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

種別の表示語をサーバの label_vocab に一本化し、DTO の kind_label を画面が描く形にした（TestTrajectoryKindLabels が「誤答」の不在を固定）。わたしの記録側の系統見出しとの語の距離は再演で確かめる。

## 一般化

表示語の表が 2 つあると、片方だけに古い評価語が残る。確かめ方は、同じ行を出す全画面の語を並べて一致を見ること。
