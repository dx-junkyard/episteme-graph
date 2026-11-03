---
id: IK-0650
title: "持ち越しの問いを候補から選ぶと、切り詰めて印の種類を前置きした表示用の文が本文として保存される"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-09
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/return_door_design.md"
  - "docs/features/understanding_cycle_design.md"
feature_context:
  realizing: "学習者が論文との議論の着地で、自分の書いた問い・引っかかりから次回へ持ち越す問いを選ぶ"
  layers: [understanding_cycle, discuss]
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
    確認: 着地の候補 DTO は表示用の label（上限で切り詰め・印の種類の接頭辞付き）しか持たず、画面はその label を data 属性に入れて本文として保存していた（core/cycle/derive.py の候補組み立てと discuss.js の保存）。構造軸 representation = 表示と本文を 1 つの欄が兼ねる。接続軸 information = 本文が候補 DTO を通って保存まで届かない。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "表示のために短くした文と本人の原文を 1 つの欄で運び、保存の段で表示用の文が原文の代わりに記録される"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [invariant_audit, trace_walk]
  note: "第 23 周の机上レビューで、RD1（扉と開幕には本人の言葉の逐語だけを出す）と、候補の選択から保存までの値の流れを読み合わせて見えた。"
resolution:
  perspective: [carry_through, guardrail_fix]
  note: "候補 DTO に切り詰めない本文 text を足し、保存は text（本文の無い印だけは label）にした。表示は従来どおり label で、全文は title で読める。"
  landed_in:
    - "backend/core/cycle/derive.py"
    - "frontend/public/js/discuss.js"
    - "backend/tests/test_round23_learner_cycle.py"
  verification:
    methods: [guardrail]
    unverified:
      - "既に切り詰めた文で保存された持ち越しの問い（原文へ戻せない）"
      - "砂場・ブラウザでの扉と開幕の表示"
related: [IK-0651]
view_of: []
history: []
---

## 課題

症状: 扉と開幕に出る持ち越しの問いが末尾「…」で切れ、印の種類（「気になる: 」等）が前に付く。原因: 候補 DTO に原文が無く、表示用の label が本文として保存された。

## 発見の観点

RD1 × 候補 DTO と保存の読み合わせ（invariant_audit）と、選択から保存までの経路（trace_walk）。

## 解決の観点

原文を後段まで運ぶ（carry_through）。表示と保存の欄を分けたうえで、保存に label が戻らないことを静的検査で固定した。

## 一般化

表示用に加工した文を保存に流すと、本人の言葉が加工後の形で残り、後から復元できない。確かめ方は、上限を超える長さの問いと本文の無い印を候補から選び、保存された本文がそれぞれ原文と印の語であることを見ること。
