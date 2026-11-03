---
id: IK-1026
title: "同一性リンクの［確認］の問いが「部品「TOV residual loss at collocation points」と共通部品「TOV residual loss at collocation points」は、同じものですか」になる — 候補づくりがハブのエントリに片方の部品の名前をそのまま付けるため、問いが同じ名前どうしの比較になり、本当に比べるべき相手（もう一方の版の部品「TOV residual constraint」）が問いに出ない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "教員が同じ論文の 2 版（または 2 本の論文）の部品が同じものかを確かめて同一性リンクを確定する"
  layers: [concept_registry, deliberation_w, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [review]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 representation: 同一性の候補は「部品 ⇄ ハブのエントリ」のリンクで表され、core/library/identity_candidates.py は候補エントリの名前を対の一方の部品名から作る。確認の問い（routes/library.py の confirm_question = deliberation identity_links.confirm_question）はリンク 1 本の両端（部品と共通部品）を名指すので、エントリが部品名を名乗るとき両端が同じ文字列になる。接続軸 meaning: 問いは「一方が他方を使う・満たすだけではありませんか」と意味の弁を張るが、比べる相手の意味（対のもう一方の部品）が問いに渡らず、弁が働かない。統制軸 review: 確定の判断材料が問いに足りない。実測: te-03 seq3 の確認の問い（uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503）。処理軸 none。
generalization:
  level: repo_pattern
  general_form: "2 つの対象の対の判断を、片方の名前を付けた中継の記録と各対象の 1 本ずつの判断に分解すると、確認の問いが同じ名前どうしの比較になり、比べるべき相手が問いに出ない"
pattern: same-name-different-referents
discovery:
  perspective: [reproduction, invariant_audit]
  note: >-
    te-03 がリンク 1 本を確定したとき、確認の問いの両端が同じ名前だった（uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503 seq3）。頭脳は「どの版の部品を何に結ぶのかが問いから読めない」と書き、エントリ詳細を開いて初めて対の相手（PDF 版の TOV residual constraint）が見えた。
resolution:
  perspective: [explicit_contract]
  note: >-
    候補の行の確認の問いは、同じエントリの他の行を対の相手として「部品「A」（TeX 版『題名』）と部品「B」（PDF 版『題名』）は、同じものですか（…）」と名指しする（identity_links.pair_confirm_question・instance_side_label。版は document_format_label を常に運ぶ。相手が無ければ従来の問い）。ライブラリの一覧と W層の共通部品の一覧が同じ関数を使い、内部のキー（_ で始まる）は応答から落とす。
  landed_in:
    - "backend/core/deliberation/identity_links.py"
    - "backend/api/routes/library.py"
    - "backend/api/routes/deliberation.py"
    - "frontend/public/js/deliberation.js"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_share_round25_r6_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面で対の相手を見て確定・見送りを決められるかの再演（第 7 周 te-05）"
related: [IK-0917, IK-1020, IK-0956]
view_of: []
history: []
---

## 課題

症状: 同一性の候補 10 番を［確認］すると、確認の問いが「部品「TOV residual loss at collocation points」と共通部品「TOV residual loss at collocation points」は、同じものですか（一方が他方を使う・満たすだけではありませんか）。」になる。教員が確かめたいのは TeX 版の部品と PDF 版の部品「TOV residual constraint」が同じかだが、PDF 版はこの問いに出ない。確定後に詳細を開いて、結んだのが同じ論文の 2 版だったと気づいた。

原因: 候補づくりがハブのエントリに一方の部品の名前を付け、確認はリンク 1 本ずつ（部品 ⇄ エントリ）で問うため。

## 発見の観点

再現（reproduction）: 確認の問いの文面。不変条項の照合（invariant_audit）: 確定は人間が判断材料を持って行う（IK-0917 の弁）と、問いに渡る材料を突き合わせた。

## 解決の観点

是正済み（第 25 周・面 A 第 6 波・2026-10-27）。

旧案: 候補エントリのリンクの確認の問いに、同じエントリへの他のリンク（対の相手の部品・版・要約）を並べ、「この 2 つは同じものですか」の形で問う。候補エントリの名前を部品名の写しにしない（「…（同一性の候補）」など）かは表示の判断。IK-0917 の弁（使う・満たすだけでないか）は相手が見えて初めて働く。

## 一般化

中継の記録（ハブ）に片方の対象の名前を付けると、ハブと対象の対応を問う画面が同語反復になる。型 same-name-different-referents（同じ名前が別の referent を指す — 部品と、部品名を名乗る概念）。

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: 未評価。te-05 は今周リンクを確定しなかった（見送り 2・取り消し 1）。残った候補はすべて片側が置き換わった行で、対の相手を名指す問いの対象が砂場に無い。
