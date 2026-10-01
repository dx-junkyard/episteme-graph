---
id: IK-0589
title: "教材に見えている主張 ⚓ の要素文脈が、表示中トピックの論文でも「このコースの教材からは特定できませんでした」になる"
status: resolved
recorded_at: 2026-10-01
resolved_at: 2026-10-01
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
  perspective: [representation_change, guardrail_fix]
  note: '主張 ⚓ の宛先を論文内で一意にした。UUID → block 修飾 → 素の番号の順で解決し、表示中トピックの論文でも曖昧なら推測せず事実文にする。'
  landed_in:
    - backend/core/element_context.py
    - backend/core/course_content_builder.py
    - backend/api/routes/learning.py
    - backend/tests/test_claim_address_resolution.py
    - docs/features/focus_document_design.md
    - cb301bb
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（次の周で確かめる）"
      - ブラウザでの画面確認
related: [IK-0516, IK-0553, IK-0580]
view_of: []
history:
  - date: '2026-10-01'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '第 18 波で主張 ⚓ の宛先を論文内で一意にした（ガードレールで確認・再演は次の周）。（persona_enactment_testing_design.md §18.10）'
---

## 課題

教材に出ている主張チップを開くと、文脈の代わりに「特定できませんでした」の事実文だけが出る。前周は同じ種類の主張文脈が開けていた。

## 発見の観点

第 17 周の頭脳メモ（前周は開けた主張文脈が開けない）と、要素文脈の解決関数を突き合わせた。

## 解決の観点

主張の宛先の表現を論文内で一意な形に変え、解決順を UUID → block 修飾 → 素の番号に固定した（cb301bb）。曖昧な宛先は推測で結ばず事実文に落とす。
原因は起票時の仮説のまま（classification.basis）で、是正後の再演で確かめる。

## 一般化

衝突を避けるための一意性の条件が、同じ範囲の中の別名どうしでも成り立たなくなる。
