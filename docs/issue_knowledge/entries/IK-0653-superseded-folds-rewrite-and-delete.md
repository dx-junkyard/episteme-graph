---
id: IK-0653
title: "学習チャットで往復を取り消した後に画面が無言で、わたしの記録では「書き直しで差し替え」と出る"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-09
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/trace_registry_sovereignty_ledger_design.md"
  - "docs/features/disclosure_axes_design.md"
feature_context:
  realizing: "学習者が学習チャットの往復を取り消し、そこから作られた記録がどうなったかを確かめる"
  layers: [trace_registry, rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 書き直しと取り消しの両方が派生痕跡を superseded の 1 語にし、理由を残していなかった（services.truncate_chat_and_supersede）。台帳の状態名は superseded を「書き直しで差し替え」と訳していた（label_vocab）。削除 API は件数だけ返し、画面は何も出さなかった。構造軸 representation = 状態語彙に原因の区別を表す場所が無い。接続軸 information = 操作の種類が痕跡へ届かない。処理軸 wording = 理由を持たない行にも書き直しと断言する訳。統制軸 none。
generalization:
  level: general
  general_form: "異なる原因の遷移を 1 つの状態語に畳み、利用者が自分のした操作と違う名前で記録を見る"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [invariant_audit, trace_walk]
  note: "第 23 周の机上レビューで、取り消しの経路と、わたしの記録の状態名・取り消し直後の画面を読み合わせて見えた。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: "痕跡の payload に理由（書き直し / 取り消し）を残し、台帳は理由ごとの状態名で読む。理由の無い旧行は書き直しと断言しない名前にした。取り消しの直後にサーバの事実文を会話欄へ出す。"
  landed_in:
    - "backend/api/services.py"
    - "backend/api/routes/learning.py"
    - "backend/core/label_vocab.py"
    - "backend/core/trace_ledger.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_round23_learner_cycle.py"
    - "backend/tests/test_trace_ledger_core.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場・ブラウザでの取り消し直後の事実文とわたしの記録の表示"
      - "理由を持たない旧行の表示"
related: [IK-0654, IK-0658]
view_of: []
history: []
---

## 課題

症状: 🗑 で往復を取り消しても画面は何も言わず、わたしの記録には「書き直しで差し替え」と出る。原因: 書き直しと取り消しを superseded の 1 語に畳み、理由を記録していなかった。

## 発見の観点

取り消しの経路 × 台帳の状態名の読み合わせ（invariant_audit）と、操作から台帳表示までの値の流れ（trace_walk）。

## 解決の観点

状態語は増やさず、理由を payload の 1 キーにして読み時に訳し分けた（representation_change）。未知の理由は書き直しに倒さず拒否する。

## 一般化

原因の違う遷移を同じ状態にすると、記録は残っていても利用者は自分の操作を記録の中に見つけられない。確かめ方は、両方の操作で作った痕跡を台帳で並べ、別の名前で読めることを見ること。
