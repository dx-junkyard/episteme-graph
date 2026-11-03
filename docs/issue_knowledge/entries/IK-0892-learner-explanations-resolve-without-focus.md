---
id: IK-0892
title: "学生が受講中のコースで部品の説明を開くと「この部品は、このコースの教材の中では見つかりませんでした。」が返る — 同じ部品の文脈は焦点論文（表示中トピック）で agent ID を解決して開けるのに、説明の一覧の route は焦点を受け取らず、論文ごとに採番される agent ID（comp_001）がコースの複数論文に当たると解決を諦め、在る部品を「無い」と言う"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生が教材の部品の ⚓ から、その部品の承認済みの説明を開く"
  layers: [endorsement_c, learner_experience_b, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition, target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 接続軸 condition: backend/api/routes/learning.py:8597 の get_component_explanations_for_learner は resolve_course_component_id(component_id, course_document_ids) を preferred_document_ids なしで呼ぶ。解決規則（backend/core/component_context.py:106 _resolve_component_row → focus_document.resolve_in_focus）は焦点が空なら「スコープ内でちょうど 1 論文に当たるとき」だけ解決する（FD4）。部品の文脈の route（learning.py:8812-8818）は topic_id / document_id のクエリから _learner_focus で焦点を作って渡すが、説明の route にはクエリ引数が無く、app.js:2083-2084 も focusQuerySuffix を付けない（:8335 の文脈呼び出しは付ける）。接続軸 target: agent ID comp_001 は論文ごとに採番されるので、4 本の論文を束ねるコースでは複数の論文に当たり、解決が None → 404 + 「このコースの教材の中では見つかりませんでした」（事実に反する文）。確定には砂場の theory_components_live で、コース 1bad7d9f の sources の document 群に legacy_ids comp_001 が複数あることを確かめればよい（同じ周の文脈 API は同じ comp_001 を dfe1d5e4… に解決している）。構造軸は none（解決関数は正本 1 つで、引数が渡っていない）。
generalization:
  level: repo_pattern
  general_form: "同じ要素を開く複数の経路のうち、ある経路だけが曖昧さを解く文脈（焦点）を受け取らず、解けない曖昧さを「存在しない」と報告する"
pattern: condition-not-propagated
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    学生 st-06・st-09 が両方のコースで部品の説明を開き、全部「説明を取得できませんでした」（ハーネスは detail を描かない — 画面は 404 の detail を描く）。応答は 404「この部品は、このコースの教材の中では見つかりませんでした。」で、同じ周の部品の文脈は同じ ID で開けていた。文脈と説明の route の解決の呼び方を突き合わせた。run: uxsim/runs/c-astro-r25-share-r2/20261022T220149Z-75546 seq18・20261022T220528Z-78210 seq9。
resolution:
  perspective: [carry_through]
  note: >-
    学生の説明の route（get_component_explanations_for_learner）に optional の topic_id / document_id を足し、部品の文脈と同じ焦点の解決（_learner_focus = services.learner_focus_document）を作って resolve_course_component_id(..., preferred_document_ids=) に渡す。「どの論文か」の条件式は書かず、焦点の外は FD4（resolve_in_focus）に委ねる。解決できないとき、コースの複数の論文に当たる（component_matches_multiple_documents）なら「どの論文の部品か特定できませんでした」と言い分け、不在の文は使わない。app.js は説明の呼び出しにも focusQuerySuffix を付ける。
  landed_in:
    - "backend/api/routes/learning.py"
    - "backend/core/component_context.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_share_round25_r2_fixes.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "説明の解決を部品の文脈と同じ焦点論文の正本（focus_document）に通した。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB で 4 論文を束ねたコースの comp_001 の説明が開けること（第 3 周の st-06 (d)）"
related: [IK-0861, IK-0554]
view_of: []
history: []
---

## 課題

症状: 受講中のコースの部品の説明を開くと、在る部品について「このコースの教材の中では見つかりませんでした」と言う。原因（仮説）:
説明の一覧の route が焦点論文を受け取らず、論文ごとに採番される agent ID がコースの複数の論文に当たると解決を諦める。

## 発見の観点

学生ペルソナの通し受講（reproduction）。文脈と説明の route の解決の引数を突き合わせた（trace_walk）。

## 解決の観点

第 2 波（2026-10-23）で是正。部品の文脈と同じ焦点の段順を説明の route にも渡す（焦点論文の正本 core/focus_document.py を通す）。

## 一般化

複数論文を束ねる面で、ID の曖昧さを解く文脈を経路ごとに渡し忘れる型。確かめ方は、4 本の論文を束ねるコースの各論文の
comp_001 で、文脈・説明・instance.explanation の 3 経路を開き、同じ部品に解決されることを見ること。
