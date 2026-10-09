---
id: IK-0687
title: "書き直し・取り消しをした往復から生まれた誤解の候補が、わたしの記録の「誤解の記録」に生きたまま残る"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-10
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/trace_registry_sovereignty_ledger_design.md"
feature_context:
  realizing: "学習者が質問を書き直し、取り下げた言い方から作られた記録が外れたことを確かめる"
  layers: [learner_experience_b, trace_registry]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（第 3 波の調査）: 誤解の候補と永続履歴が同じ user メッセージ id を持たず、truncate_chat_and_supersede が候補を supersede できなかった。構造軸 representation = 候補が元の往復を指す鍵を持たない。接続軸 information = 取り下げが候補へ届かない。作業ツリーの第 3 波で id を揃えた（未再演）。
generalization:
  level: repo_pattern
  general_form: "派生した記録が元の発話と同じ鍵を持たず、元を取り下げても派生記録だけが生き残る"
pattern: condition-not-propagated
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、懐疑的な学生が書き直し前の言い方が「誤解の記録」に入っていることに気付いた。"
resolution:
  perspective: [carry_through, state_transition]
  note: "取り下げ（書き直し・以降の削除）が誤解の候補まで届くよう、truncate_chat_and_supersede の同じセッションで、取り除いた user メッセージ id を持つ誤解の候補を superseded（理由付き）に遷移させる。教員・本人が判断済みの行と旧形式の行は触らない。"
  landed_in:
    - "backend/core/personal_graph/graph_data.py"
    - "backend/api/services.py"
    - "backend/tests/test_round23_server_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（書き直し後にわたしの記録の誤解の記録区画を開く場面）"
      - "鍵を揃える前に作られた旧い誤解の候補（message id を持たない行）は対象外のまま"
related: [IK-0658, IK-0653]
view_of: []
history: []
---

## 課題

症状: 取り下げた往復の誤解候補が残る。原因: 鍵が揃っていない。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

誤解の候補に元の往復の id を持たせ、取り下げの同じトランザクションで supersede する（supersede_misconceptions_for_messages / _supersede_misconceptions_in_session。TestSupersedeMisconceptions / TestSupersedeInSession）。

## 一般化

派生記録が元と同じ鍵を持たないと、取り下げが届かない。確かめ方は、書き直しの後に派生記録の状態を読むこと。
