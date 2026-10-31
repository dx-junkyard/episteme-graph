---
id: IK-1145
title: "意図分類に添える画面の文脈 1 行が「最後に開いた地図の場所」を最優先し、選んだ一節・わたしの地図の場所では「ここ」の手がかりが付かない"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/assistant_screen_adapter_design.md"
feature_context:
  realizing: "学習者が「ここ」と尋ねたとき、分類器が今の往復で選んでいるものを手がかりにする"
  layers: [rag_chat, screen_adapter_sa]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    _intent_screen_hint は view.atlas_node_label（最後に開いた場所・地図を閉じても残る）を先に返し、選択テキスト・わたしの地図の参照は見ていなかった。接続軸 condition。
generalization:
  level: repo_pattern
  general_form: "持続する画面状態が今の往復の明示の選択より優先される"
pattern: condition-not-propagated
discovery:
  perspective: [reproduction]
  note: >-
    製品頭脳 B 4308: 「ここ、一言で言うと何をしているの？」の分類要求に画面の文脈の 1 行が無い。
resolution:
  perspective: [carry_through]
  note: >-
    第 25 周 第 10 波（2026-10-31）: 順を 地図で今選んだ場所（selection.atlas_node_id）→ 選んだ要素 → 選んだ一節 → わたしの地図で見ている場所 → 最後に開いた地図の場所 にした（本文は渡さない = SA1）。FD-note 4 と同じ区別。
    第 25 周 第 11 波: 製品頭脳 B 4330（「この部品」の分類要求に画面の行なし）。フロントは明示の要素が開いている部品と同じならその名前を申告し、サーバは名前が届かなくても element_id があれば「教材の要素を 1 つ選んでいます」を渡す（内部 ID は渡さない）。
  landed_in:
    - "frontend/public/js/app.js"
    - "backend/tests/test_round25_wave11_doubt_studio_chat.py"
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave10_chat.py"
    - "docs/features/focus_document_design.md"
  verification:
    methods: [guardrail]
    unverified:
      - "分類の結果が変わる往復の再演"
related: [IK-1064, IK-1112]
view_of: []
history: []
---

## 課題

意図分類に添える画面の文脈 1 行が「最後に開いた地図の場所」を最優先し、選んだ一節・わたしの地図の場所では「ここ」の手がかりが付かない。

## 発見の観点

製品頭脳 B 4308: 「ここ、一言で言うと何をしているの？」の分類要求に画面の文脈の 1 行が無い。

## 解決の観点

順を 地図で今選んだ場所（selection.atlas_node_id）→ 選んだ要素 → 選んだ一節 → わたしの地図で見ている場所 → 最後に開いた地図の場所 にした（本文は渡さない = SA1）。FD-note 4 と同じ区別。

## 一般化

持続する画面状態が今の往復の明示の選択より優先される。型 condition-not-propagated。
