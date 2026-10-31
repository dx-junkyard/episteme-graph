---
id: IK-1039
title: "学習チャットの「検索で当たった箇所の構造」ブロックで、理論の骨格の段階に添える括弧の中が解析層の英語の説明文の途中切り（「（One neural network represents … conti）」）になる"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22"
  - "docs/features/focus_document_design.md"
  - "docs/features/assistant_screen_adapter_design.md §11"
feature_context:
  realizing: "学生が学習チャットで尋ね、回答に採用した出典の構造の手がかり（理論の骨格の段階）を事実として受け取る"
  layers: [rag_chat, screen_adapter_sa]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 wording: _retrieved_node_fact は main ノードの display_label の「: 」以降を理論対象として括弧で添え、上限で切る。理論対象が
    解析層の生成した英語の文（短い対象名ではない）のとき、途中で切れた英語が学習者向けの事実文に入る。mailbox req-04248 で確認した。
    IK-0571（A層の英語の生成文）と同じ根の、構造ブロックの面。
generalization:
  level: repo_pattern
  general_form: "生成された外国語の説明文を短い名前として扱い、上限で切ったまま利用者向けの文に添える"
pattern: rule-enforced-per-surface
discovery:
  perspective: [data_inspection]
  note: >-
    製品頭脳 A 4240 / 4248 / 4250・B 4241 / 4245 / 4251 が、構造ブロックの括弧の中の英語の途中切りを繰り返し記した。
resolution:
  perspective: [single_point_fix]
  note: >-
    理論対象が日本語を含まず英単語が並ぶ文（語が一定以上）のときは括弧ごと添えない。短い対象名（「TOV equations」）と数式は残す。
    学習者向けの射影（display_projection）の英語の生成ラベルの判定は目印（定型）を要るため使わず、resolver の 1 箇所で文の形を判定した。
  landed_in:
    - "backend/core/assistant_context/resolvers/learning.py"
    - "backend/tests/test_round25_wave6_chat_rag.py"
  verification:
    methods: [guardrail]
    unverified:
      - "日本語の理論対象を持つ教材（訳語のある display_label）で括弧が残ること"
related: [IK-0571]
view_of: []
history: []
---

## 課題

症状: 構造ブロックが「この主張は、理論の骨格では『整合関係』の段階に置かれています（One neural network represents the neutron-star equation of state P(ε) as a conti）」と英語の途中切りを渡し、AI がそれを学生に引用し得る。

## 発見の観点

製品頭脳に渡ったプロンプトの点検。

## 解決の観点

英語の文の形の理論対象は添えない（段階名だけで止める — 推測で埋めない既存の規律と同じ側）。

## 一般化

生成文を名前として添える型。確かめ方: 英語の文と短い対象名の両方の display_label で、文だけが落ちることを見る。

第 7 周（面 B の審判が製品頭脳の所見から写す・2026-10-28）: 製品頭脳 A 4266・B 4265（casual・PINN・論理要素を選んだ往復）で、画面文脈の要素の役割・前提が英語の生成文のまま（「Directly supports the central thesis…」「Profile outputs are differentiable in radius.」）学習者向けの事実文として LLM に渡った。この往復は面 B の 5 run には無く（選択要素のある往復は他の面のレーン）、transcript での確認は他の面の審判に委ねる。
