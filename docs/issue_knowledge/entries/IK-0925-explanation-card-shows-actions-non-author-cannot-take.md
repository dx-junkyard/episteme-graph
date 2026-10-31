---
id: IK-0925
title: "原稿スタジオの説明カードは、著者でない教員にも他人の説明の［共有OFF］［査読承認］を同じ形で出すが、サーバは著者以外の編集を 403 で拒むため、押せるように見えて押すと失敗し、押してよいのかも画面から分からない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が他の教員の説明を読み、自分にできる操作（支持・引用）だけを行う"
  layers: [endorsement_c, lecture_studio, frontend_admin_ui, auth_visibility]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [assignment]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 condition: routes/theory_components.py:4866-4879 patch_explanation は著者か SYSTEM_ADMIN 以外を 403
    にする。説明一覧の応答に「この利用者が編集できるか」が無く、admin-lecture-studio.js:7333-7342 は全カードに同じボタンを描く。統制軸 assignment: 権限の判定（サーバ）と操作の見え方（画面）が別々。
generalization:
  level: repo_pattern
  general_form: "操作の可否をサーバだけが知っていて画面に運ばず、できない操作ができるように見える"
pattern: permission-and-affordance-asymmetric
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-05 が te-02
    の説明カードに［共有OFF］［査読承認］が並ぶのを見て、押せる権限があるのか・押すと著者の共有を切ってしまうのか分からないと記した（uxsim/runs/c-astro-r25-share-r3/20261023T033729Z-59479
    seq3）。サーバの 403 の分岐と描画を突き合わせた。
resolution:
  perspective: [explicit_contract]
  note: >-
    説明一覧の各行に can_edit（著者か SYSTEM_ADMIN = PATCH の権限判定と同じ）と edit_note を返し、admin-lecture-studio.js は can_edit が偽の行で［公開の査読］［共有ON/OFF］を出さず、サーバの文「この説明の公開・共有の設定は著者だけが変えられます。」をそのまま描く。支持・引用は誰でも。
  landed_in:
    - "backend/api/routes/theory_components.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "backend/tests/test_share_round25_explanations.py"
    - "backend/tests/test_share_round25_r4_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面で著者以外の教員が見たときのカード"
related: [IK-0924]
view_of: []
history: []
---

## 課題

症状: 他人の説明に押せない操作が並ぶ。原因: 権限の判定が応答に運ばれない。

## 発見の観点

教員ペルソナの通し受講（reproduction）と権限の分岐の確認（boundary 相当の trace_walk）。

## 解決の観点

是正済み（第 3 波）。行ごとに編集できるかをサーバが返し、画面は押せない操作を出さずに理由の 1 文を出す。

## 一般化

権限と見え方の非対称の型。確かめ方は、著者・他の教員・管理者の 3 人で同じカードを開き、押せる操作だけが見えることを画面で見ること。

## 第 4 周の再演（第 25 周・面 A 第 4 周、2026-10-25）

第 4 周の再演: 一部残存（他人の説明で公開・共有の操作は消え、edit_note「この説明の公開・共有の設定は著者だけが変えられます。」が出た。［支持を取り消す］は支持していない教員にも常に描かれる — admin-lecture-studio.js:7423。run: uxsim/runs/c-astro-r25-share-r4/20261024T233620Z-65949 seq3）

## 第 4 波の是正（第 25 周・面 A、2026-10-25）

［支持を取り消す］は本人の有効な支持があるときだけ出す。説明一覧の応答に my_support（component_endorsements の本人・未取り消しの行の有無）を足し、カードは exp.my_support のときだけボタンを描く。

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 消滅（［支持を取り消す］は本人が支持した後にだけ出た。公開・共有の操作は著者のカードにだけ出た）。残る対応ずれ（査読済みに査読ボタン・共有の知らせの方向）は IK-0985、標準の説明の査読者が居ないことは IK-0984。run: uxsim/runs/c-astro-r25-share-r5/20261027T070623Z-23499 seq2〜6
