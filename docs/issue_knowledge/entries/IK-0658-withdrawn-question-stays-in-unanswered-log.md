---
id: IK-0658
title: "学習者が取り消した質問が、担当教員の「教材から答えられなかった質問」一覧に表示名つきで残る"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/disclosure_axes_design.md"
  - "docs/features/trace_registry_sovereignty_ledger_design.md"
feature_context:
  realizing: "学習者が学習チャットの往復を取り消し、その質問が教員の一覧からも外れることを期待する"
  layers: [rag_chat, teacher_triage, disclosure_axes]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [condition]
    governance: [assignment]
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
    確認: 答えられなかった質問は unanswered_query_logs に user_id と質問文で記録され（services.py の INSERT）、表に message_id が無い（003_unanswered_queries.sql）。取り消しの経路はこの表に触れない。構造軸 representation = 記録が元の往復を指す列を持たない。接続軸 condition = 取り消しという本人の意思が派生記録へ伝わらない。統制軸 assignment = 本人が取り消した質問を教員に残すかの判断の担い手が決まっていない。処理軸 none。
generalization:
  level: general
  general_form: "派生した記録が元の発話を指していないため、本人が元を取り消しても派生記録だけが他者の画面に残る"
pattern: condition-not-propagated
discovery:
  perspective: [invariant_audit, trace_walk]
  note: "第 23 周の机上レビューで、取り消しの経路と、そこから作られる記録の全ての置き場を辿って見えた。"
resolution:
  perspective: [pending, deferred_decision]
  note: "未解決・オーナー判断候補（人の権利の変更）。推奨は「外す」: 表に元の往復への参照を足し、取り消しで一覧から外す（行は状態遷移で残す）。宣言は現物どおり「外れません」と直してある。"
related: [IK-0653, IK-0654]
view_of: []
history: []
---

## 課題

症状: 🗑 で取り消した質問が、教員の「答えられなかった質問」一覧に表示名つきで残る。原因: その記録が元の往復を指す列を持たず、取り消しの経路が触れない。

## 発見の観点

取り消しの経路 × 派生記録の置き場の読み合わせ（invariant_audit）と、往復から作られる記録の追跡（trace_walk）。

## 解決の観点

未解決。本人が取り消した質問を教員の一覧から外すかは人の権利に関わるためオーナー判断候補（推奨: 外す）。外すなら、元の往復への参照を足して取り消しの経路から状態遷移で外せば解ける。

## 一般化

派生記録に元への参照が無いと、元の取り消しは派生側へ届かない。確かめ方は、取り消しの後に元の往復から作られた全ての記録を列挙し、それぞれの見え方が宣言どおりであることを見ること。
