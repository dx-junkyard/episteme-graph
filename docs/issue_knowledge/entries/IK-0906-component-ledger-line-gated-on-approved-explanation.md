---
id: IK-0906
title: "学生の部品の説明ポップアップで、検証状態の一行（台帳の一行）が承認済みの説明の有無に従属し、承認済みの説明がゼロの部品では早期 return で一行の取得まで進まない — 検証の事実と説明の承認は別の軸なのに、説明が未承認だと台帳に記帳があってもこの入口から見えない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/architecture/persona_enactment_testing_design.md §22.8"
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/personal_map_nearby_design.md"
feature_context:
  realizing: "学生が教材の部品の説明を開き、その部品がどこまで確かめられているかを読む"
  layers: [doubt_d, learner_experience_b, endorsement_c, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 logic: frontend/public/js/app.js::showComponentExplanations は承認済みの説明が無いと『承認済みの説明はまだありません。』を描いて return し、その後ろにある台帳の一行の取得（fetchLearnerLedgerFactLine）に届かない。接続軸 condition:
    説明の承認（C層）という別の層の条件が、検証の事実（D層）の表示の条件として流れ込んでいる。構造軸 none（台帳の一行の解決は IK-0855 の resolve_component_ledger_target で正しく 1 箇所にある）。
generalization:
  level: general
  general_form: "ある情報の表示を、別の軸の情報が存在するときだけ走る分岐の後ろに置いたため、前者の有無にかかわらず後者が無いと前者も出ない"
pattern: gate-position-wrong
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    st-03 / st-09 が『DCF仮定の適用限界』の説明を開くと承認済み説明ゼロの文だけが出て、検証の一行の取得要求が出ていなかった。第 1 周のハーネス是正者 H2 の『製品の事実』を、app.js の分岐で裏取りした。run:
    uxsim/runs/c-r25-doubt-r2/20261022T221137Z-82302（seq11）・uxsim/runs/c-r25-doubt-r2/20261022T221543Z-84621（seq11）。
resolution:
  perspective: [single_point_fix]
  note: >-
    第 2 波で是正（2026-10-23）。showComponentExplanations で、承認済みの説明がゼロのときも appendLearnerLedgerLine を呼んでから return する。学習者の台帳一行の取得は焦点論文（focusQuerySuffix）を添え、キャッシュも論文ごとに分けた（IK-0899）。
  landed_in:
    - "frontend/public/js/app.js"
    - "backend/tests/test_doubt_ledger_document_key_round25.py"
  verification:
    methods: [guardrail]
    unverified:
      - "ブラウザでの表示は未確認"
related: [IK-0855]
view_of: []
history: []
---

## 課題

症状: 部品の説明ポップアップが「承認済みの説明はまだありません。」だけを出し、検証状態の一行が無い。出典タブでは同じ論文の
式に記帳ありの一行が出るのに、部品の側は無言で、学生には「記帳が無い」のか「表示されない」のかが分からない。

原因: 台帳の一行の取得が、承認済み説明がある場合の分岐の後ろにある（ゲートの位置）。

## 解決の観点

第 2 波で是正（2026-10-23。frontmatter の resolution）。以下は起票時の是正案。是正案: 説明の有無の分岐より前に台帳の一行を取得して描く（説明ゼロの文と並べる）。台帳に行が無ければ何も足さない
（fail-closed のまま）。ガードレール: `showComponentExplanations` の本体で、台帳の一行の取得が説明ゼロの return より前にあることを
静的に固定する。

## 発見の観点

学生の操作列で説明ポップアップを開き、台帳の一行の要求が出ないことを確かめ、分岐を読んだ。

## 一般化

別の軸の情報を同じ入口で見せるとき、一方の空で早期 return すると他方も消える。確かめ方は、片方だけがある状態を 2 通り
作り、どちらでもある方が出ることを見ること。

第 4 周の再演と第 4 波（2026-10-25）: 説明ゼロでも一行を出す是正（本エントリ）は、部品にはまず台帳の行が無いので学習者の台帳 GET の 404 で届いていなかった（IK-0971）。第 4 波で学習者の台帳 GET を「行が無くても 200・exists:false・記帳なしの一行」にしたので、部品の説明ポップアップにも一行が出る（画面は未確認）。
