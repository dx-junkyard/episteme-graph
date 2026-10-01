---
id: IK-0587
title: "出所の正直さのプロンプト規則が定型句（「…一般的な学術知識からの補足ですが」）を例示しているため、句が文中に機械的に差し込まれて文が壊れる"
status: open
recorded_at: 2026-10-01
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.9
feature_context:
  realizing: "ペルソナ通し受講 第 17 周（4 つの根の是正後の再演・c-astro-structure-30）の場面"
  layers: [discuss, rag_chat]
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
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: `routes/learning.py` の discuss プロンプトのルール 7 が逐語の句を示しており、第 17 周の応答で「抜粋はここで途切れていますが、これはこの論文に書かれている内容ではなく一般的な学術知識からの補足ですが、最小結合の」のように句が文の途中に挿入された（2 回観察）。製品頭脳は Claude Code の子エージェントによる代行で、実運用の LLM で同じ挿入が起きるかは未確認（処理軸 wording）。
generalization:
  level: general
  general_form: "生成への指示に例文を逐語で示し、例文がそのまま文中に貼り付けられる"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, trace_walk]
  note: "第 17 周 s-discuss-backbone の頭脳メモ（文中の割り込み）とプロンプト本文のルール 7 を突き合わせた。"
resolution:
  perspective: [pending]
  note: "未解決。第 17 周の記録（§18.9）で起票した。"
  landed_in: []
related: [IK-0582]
view_of: []
history: []
---

## 課題

一般知識の補足であることの断りが、文の途中に定型句のまま差し込まれ、文が読めなくなる。

## 発見の観点

第 17 周 s-discuss-backbone の頭脳メモ（文中の割り込み）とプロンプト本文のルール 7 を突き合わせた。

## 解決の観点

未解決。原因は仮説で、classification.basis に確かめる点を書いた。

## 一般化

生成への指示に例文を逐語で示し、例文がそのまま文中に貼り付けられる。
