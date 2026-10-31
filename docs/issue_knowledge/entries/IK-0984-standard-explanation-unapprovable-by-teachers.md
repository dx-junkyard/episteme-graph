---
id: IK-0984
title: "AI 由来の標準の説明（著者なし）は、学生への公開の査読・共有を管理者しか設定できず、コースの所有者にも「この説明の公開・共有の設定は著者だけが変えられます」と言う — 著者のいない説明の査読者が決まっておらず、標準の説明は教員の操作で学生に届かない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が部品の標準の説明を査読して、自分のコースの学生に届ける"
  layers: [endorsement_c, lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [assignment]
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
    統制軸 assignment: 編集可否は routes/theory_components.py _explanation_can_edit（SYSTEM_ADMIN か author_id 一致）で、標準の説明は author_id が空なので教員は誰も一致しない。PATCH の 403 も同条件（IK-0925 の是正で画面の出し分けを揃えた）。結果、標準の説明の査読は管理者だけに割り当たり、edit_note は存在しない著者を指す（core/explanation_governance.py EXPLANATION_AUTHOR_ONLY_NOTE）。処理・接続は正しい（条件どおりに動いている）。
generalization:
  level: repo_pattern
  general_form: "著者に権限を結び付けた判断で、著者のいない（機械が作った）対象の判断者を決めておらず、誰も判断できない"
pattern: permission-and-affordance-asymmetric
discovery:
  perspective: [reproduction, invariant_audit]
  note: >-
    コースの所有者 te-04 の画面で標準の説明のカードに［学生に公開してよいと査読する］が無く「著者だけが変えられます」と出た（uxsim/runs/c-astro-r25-share-r5/20261027T070623Z-23499 seq2〜6）。_explanation_can_edit と標準の説明の author_id を突き合わせた。
resolution:
  perspective: [responsibility_move, deferred_decision]
  note: >-
    オーナー判断（推奨どおり）: 著者のいない標準の説明は、その部品の論文を編集できる教員（_can_review_standard = 管理者 or _ensure_component_editable）が査読でき、査読者は監査の changed_by に残る。1 人の査読ではその教員のコース（learning_courses の所有者）の学生にだけ届き、ほかのコースへ広げるには別の教員がもう一人の査読者として査読する（STANDARD_REVIEWERS_SQL / STANDARD_DELIVERY_SQL が LEARNER_EXPLANATION_SCOPE_SQL に入る・2 人目は action=additional_review の記帳・同じ人は 409）。本文・題名・根拠の主張の書き換えは管理者だけ（403）。説明カードは can_review / second_review_available で出し分け、査読できない教員には EXPLANATION_STANDARD_REVIEWER_NOTE。説明カードの査読の記帳が無い承認（部品の承認から引き継いだもの・旧規則）は従来どおり部品を使うコースへ届く — これを規則の対象にするかは次の判断として設計書の残した課題に置いた。
  landed_in:
    - "backend/core/explanation_governance.py"
    - "backend/api/routes/theory_components.py"
    - "backend/core/label_vocab.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "backend/core/help_kb/admin_ui_anchors.py"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
    - "docs/features/concept_registry_design.md"
    - "backend/tests/test_share_round25_r5_fixes.py"
    - "backend/tests/test_share_round25_explanations.py"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "届く範囲の条件を学習者の読み口の SQL の内側（LEARNER_EXPLANATION_SCOPE_SQL）で強制し、画面の可否と PATCH の 403 を同じ判定にした。固有の差分: 判定材料は新しい列ではなく既存の監査行（changed_by）。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB での STANDARD_DELIVERY_SQL（2 人目の査読・所有コースの判定）"
      - "部品の承認から生まれた標準の説明の扱い（次の判断）"
related: [IK-0925, IK-0926, IK-0960]
view_of: []
history: []
---

## 課題

症状: 標準の説明は 5 周とも「査読待ち」のままで、学生の部品の文脈には教員が書いた独自解釈だけが出る。コースの所有者でも査読できず、画面の文は誰が著者かを言わない。原因: 説明の編集・査読の権限を著者に結んだ規則が、著者のいない標準の説明の査読者を決めていない。

## 発見の観点

再現（te-04 (b1)）と不変条項の点検（C層「確定は教員」）。

## 解決の観点

オーナー判断の候補（人の権利・制度）: 著者のいない説明を誰が学生への公開を査読できるか。推奨: その部品の document の編集権限を持つ教員（`user_can_edit_document`）が査読・共有を設定でき、記帳は既存の監査（AUDIT_ENTITY explanation）で査読者を残す。自己査読の扱い（IK-0926 の届く範囲の規則）は「著者なし」を著者以外として扱う。画面の文は標準の説明では「AI が作った標準の説明です。学生への公開は、この論文を編集できる教員が査読します。」に。判断の前の是正として、edit_note を標準の説明では事実どおり（管理者だけが変えられる）に直す。

## 一般化

作者に権限を結ぶ規則が機械生成物で宙に浮く型。確かめ方は、標準の説明を論文の編集者が査読でき、学生の画面に標準の説明が届くことを見ること。

## 第 5 波の是正（第 25 周・面 A、2026-10-27）

是正済み。オーナー判断（推奨どおり）: 著者のいない標準の説明は、その部品の論文を編集できる教員（_can_review_standard = 管理者 or _ensure_component_editable）が査読でき、査読者は監査の changed_by に残る。1 人の査読ではその教員のコース（learning_courses の所有者）の学生にだけ届き、ほかのコースへ広げるには別の教員がもう一人の査読者として査読する（STANDARD_REVIEWERS_SQL / STANDARD_DELIVERY_SQL が LEARNER_EXPLANATION_SCOPE_SQL に入る・2 人目は action=additional_review の記帳・同じ人は 409）。本文・題名・根拠の主張の書き換えは管理者だけ（403）。説明カードは can_review / second_review_available で出し分け、査読できない教員には EXPLANATION_STANDARD_REVIEWER_NOTE。説明カードの査読の記帳が無い承認（部品の承認から引き継いだもの・旧規則）は従来どおり部品を使うコースへ届く — これを規則の対象にするかは次の判断として設計書の残した課題に置いた。

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: GONE（入口）。コース 1bad7d9f の editor の te-01（著者ではない）に、標準の説明の［学生に公開してよいと査読する］が出た（第 5 周は「著者だけが変えられます」）。押されず、査読後の届く先の知らせは未評価。教員の説明に著者以外が査読する入口が無いのは別の欠け → IK-1023。run: uxsim/runs/c-astro-r25-share-r6/20261027T123545Z-11458 seq3
