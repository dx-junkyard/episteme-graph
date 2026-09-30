---
id: IK-0579
title: "内部 ID・数値・英語生成文の遮断が画面ごとの遮断器に分散し、語彙が系統ごとに食い違い、遮断器の無い面から漏れ続けていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/root_cause_consolidation_2026-09-30.md §2
  - docs/features/display_projection_design.md
feature_context:
  realizing: "解析層の成果を学習者向けの画面・応答に投影し、内部の番地や生の数値を見せずに読める語で出す"
  layers: [learner_experience_b, shared_infra, tests_guardrails]
classification:
  axes:
    processing: [none]
    structure: [aggregation, responsibility]
    connection: [contract]
    governance: [review]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造=aggregation: 内部 ID の語彙・禁止キー・一般ラベル表の正本が無く、遮断器ごとに 4 系統の語彙が並び互いに
    食い違っていた（表示投影 §1 で確認）。構造=responsibility: 遮断の判断を消費面の各モジュールが持ち、
    学習者向けルートの出口という 1 つの責務の置き場所が無かった。接続=contract: 解析層の出力（内部 ID を含む）と
    学習者向け DTO の契約（内部 ID を出さない）の間に、全経路で検査される変換が無かった。統制=review: 遮断器の
    無い面を見つける横断検査が無く、面を足すたびに漏れが静かに入った。処理=none: 個々の遮断器の判定は
    その系統の語彙の中では正しく、単一処理の不良ではない。
generalization:
  level: general
  general_form: "同じ出力の遮断規則を消費面ごとに書き足し、規則を持たない面や語彙のずれた面から内部の値が漏れ続ける"
pattern: rule-enforced-per-surface
discovery:
  perspective: [inventory, symptom_report]
  note: "第 14〜16 波の個別是正（要素文脈・出典本文・記号 lookup・再構成・ゼミ前ブリーフ・支持線・グラフ対話 grounding・理論モジュール・論文の順）を並べたところ、同じ漏れが画面を変えて出続けていることが見えた。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "語彙の正本を 1 モジュールに集め、学習者向けルーターの route class で全ルートの戻り値が必ず 1 回射影を通る構造にした。旧遮断器は同じ語彙オブジェクトを参照する委譲だけにし、全学習者ルートの route class と語彙の同一性を横断検査で固定した。"
  landed_in:
    - backend/core/display_projection.py
    - backend/api/display_route.py
    - backend/tests/test_display_projection_guardrails.py
    - docs/features/display_projection_design.md §6
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "出力の遮断の派生形（直列化の直前に射影を挟むルートクラス）。番地のキーは残し、系統ごとに意図的に違う判定（論文の式番号の例外）は正本の中に系統別の定数として並べた。"
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（ペルソナ通し受講の次の周）
      - ブラウザでの画面確認
      - 教員向けルート（project_for_teacher は未配線）
related: [IK-0338, IK-0411, IK-0428, IK-0517, IK-0520, IK-0524, IK-0546, IK-0563, IK-0564, IK-0565, IK-0566, IK-0571, IK-0578]
view_of: []
history: []
---

## 課題

学習者向けの応答に内部 ID（`eq_blk_*` / `claim_span_*` / `k1:` 等）・生の数値・英語の生成ラベル文が漏れる。
面ごとに遮断器を足して塞いできたが、遮断器は画面ごとに別々で語彙が 4 系統に分かれ、遮断器の無い面も
残っていたため、次の画面から同型の漏れが出続けた。原因は症状の画面ではなく、遮断規則の置き場所が無いこと。

## 発見の観点

個別是正を並べる棚卸しで、同じ漏れが別の画面から繰り返し報告されていることを束ねた。

## 解決の観点

正本の一本化（語彙を 1 箇所に）と全ルートの結線（route class）を主に、横断ガードレールを従にした。
画面ごとにマスクを足し続ける案は、次の面で同じ漏れが出るため採らなかった。

## 一般化

出力の変換規則を消費面ごとに持つ構造なら、どの機能でも新しい面から同じ欠陥が出る（`rule-enforced-per-surface`）。
