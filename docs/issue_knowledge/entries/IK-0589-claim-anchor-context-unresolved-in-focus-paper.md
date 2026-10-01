---
id: IK-0589
title: "教材に見えている主張 ⚓ の要素文脈が、表示中トピックの論文でも「このコースの教材からは特定できませんでした」になる"
status: open
recorded_at: 2026-10-01
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.9
feature_context:
  realizing: "ペルソナ通し受講 第 17 周（4 つの根の是正後の再演・c-astro-structure-30）の場面"
  layers: [learner_experience_b, knowledge_objects]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: low
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 第 17 周の再演（st-09 s-two-layer-explanation・t11）で主張 ⚓8（agent ID claim_span_007）の要素文脈が available:false だった。`element_context._resolve_element` は焦点の論文で先に解決し、`_resolve_claim` は集合内で曖昧なら None にする（FD4 の fail-closed）。同じ論文の中で親・atomic 子・legacy_ids が同じ agent ID に当たると、焦点の論文でも曖昧になって解決できない疑い（接続軸 target）。ハーネスが topic_id を送ったかは transcript に残っておらず、送っていなければハーネス側。両方を確かめれば確定する。
generalization:
  level: instance
  general_form: "衝突を避けるための一意性の条件が、同じ範囲の中の別名どうしでも成り立たなくなる"
pattern: id-unique-only-within-inner-scope
discovery:
  perspective: [reproduction, trace_walk]
  note: "第 17 周の頭脳メモ（前周は開けた主張文脈が開けない）と、要素文脈の解決関数を突き合わせた。"
resolution:
  perspective: [pending]
  note: "未解決。第 17 周の記録（§18.9）で起票した。"
  landed_in: []
related: [IK-0516, IK-0553, IK-0580]
view_of: []
history: []
---

## 課題

教材に出ている主張チップを開くと、文脈の代わりに「特定できませんでした」の事実文だけが出る。前周は同じ種類の主張文脈が開けていた。

## 発見の観点

第 17 周の頭脳メモ（前周は開けた主張文脈が開けない）と、要素文脈の解決関数を突き合わせた。

## 解決の観点

未解決。原因は仮説で、classification.basis に確かめる点を書いた。

## 一般化

衝突を避けるための一意性の条件が、同じ範囲の中の別名どうしでも成り立たなくなる。
