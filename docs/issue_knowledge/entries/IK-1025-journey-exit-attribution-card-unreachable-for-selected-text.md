---
id: IK-1025
title: "教材のテキストを選んで質問した痕跡（区画に帰属）から「問いからの旅」を開くと、出口の案内が「帰属カードで主張や式を選んで確定すると 1 点に絞り込まれ、道がたどれます」と言うが、選択質問の痕跡は本人選択で直に記録され帰属の候補にならないので、帰属カードは「確認できる問いの候補はまだありません」で空 — 案内された出口に入れない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/personal_map_nearby_design.md"
feature_context:
  realizing: "学生が自分の問いの記録から、論文の構造や別の論文への道をたどる"
  layers: [personal_network, learner_experience_b, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 contract: core/personal_graph/nearby.py FACT_SELECTED_SHARPEN（segment / chunk 帰属の出口案内）は「帰属カードで主張や式を選んで確定する」を勧めるが、帰属カード（GET /anchors/digest）は llm_candidate の帰属しか出さず、テキスト選択の問いは attribution_source=learner_selected で直に確定する（構造帰属の経路 A）。区画帰属の痕跡を主張・式へ絞り直す入口は学習画面に無い。実測: st-08 が選択質問 → 帰属の候補「確認できる問いの候補はまだありません」→ 旅「この記録からたどれる道は、まだ記録されていません。…帰属カードで主張や式を選んで確定すると…」（uxsim/runs/c-astro-r25-share-r6/20261027T122930Z-7994 seq11-13）。案内の文と帰属カードの供給条件の契約が両立しない。処理軸・構造軸・統制軸 none: 各部は設計どおり。
generalization:
  level: repo_pattern
  general_form: "縮退時の出口案内が、その記録の出所では実行できない操作を勧め、利用者が案内どおりに進めない"
pattern: last-mile-missing
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    st-08 が選択質問 → 帰属の候補 → わたしの地図の旅の順に開き、旅の出口案内と直前の帰属カードの空を並べて読んだ（uxsim/runs/c-astro-r25-share-r6/20261027T122930Z-7994）。nearby.py の案内の選び方と帰属カードの供給条件を突き合わせた。
resolution:
  perspective: [single_point_fix]
  note: >-
    テキスト選択で帰属した痕跡（segment / chunk）の旅の出口は、帰属カード（AI の帰属候補だけを並べ、選択質問の痕跡には出ない）を勧めず、式・主張の文だけを選び直して質問する手だけを言う（personal_graph.nearby.FACT_SELECTED_SHARPEN を sharpen_fact_for が選ぶ）。案内を実行できる操作に限った。
  landed_in:
    - "backend/core/personal_graph/nearby.py"
    - "backend/tests/test_share_round25_r6_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "区画に埋め込まれた主張・式を選んで帰属を確定し直す入口は作っていない"
      - "砂場の旅で出口の文が変わったことの再演"
related: [IK-0988, IK-0896]
view_of: []
history: []
---

## 課題

症状: 選択質問の直後、帰属カードは空で、わたしの地図の旅は「帰属カードで主張や式を選んで確定すると、1点に絞り込まれ、道がたどれます。」と言う。学生は案内された帰属カードに何も出ていないので次の手が無い。

原因: 旅の出口案内（IK-0896 で帰属の種類ごとに分けた）は区画帰属に「帰属カードでの確定」を勧めるが、帰属カードは AI の帰属候補しか並べず、テキスト選択の問いは候補を経ずに確定する。区画から主張・式へ絞り直す画面の入口が無い。

## 発見の観点

再現（reproduction）: (d) の場面の後半 3 手。経路の追跡（trace_walk）: nearby.sharpen_fact_for → FACT_SELECTED_SHARPEN、anchors/digest の供給条件。

## 解決の観点

是正済み（第 25 周・面 A 第 6 波・2026-10-27）。

旧案: 区画帰属の痕跡には、区画に埋め込まれた主張・式（⚓）を選んで帰属を確定し直す入口を旅のカードか記録の行に置く（API は anchors/{trace_id}/confirm の訂正 anchor_type / anchor_id が既にある）。それまでは案内を「この記録は選んだ箇所に帰属しています。主張や式に絞る操作はまだありません」と事実どおりにする。

## 一般化

縮退の事実文に「次はこうすれば進める」を添えるとき、その操作がその記録の出所から実行できるかを確かめないと、案内が袋小路を指す。型 last-mile-missing。

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: GONE。選択質問の痕跡の旅の出口が「この記録は教材の選んだ箇所に帰属しています。1点に絞り込むには、教材の中の式か主張の文だけを選び直して質問してください。帰属カードには AI の帰属の候補だけが並ぶため、この記録はそこには出ません。」と実在の手を指した（uxsim/runs/c-astro-r25-share-r7/20261027T171757Z-53326 seq13）。
