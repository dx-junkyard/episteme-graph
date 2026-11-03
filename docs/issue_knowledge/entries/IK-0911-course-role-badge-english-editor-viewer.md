---
id: IK-0911
title: "コース管理の一覧で、自分の役割の印が所有は「所有」なのに共有は英語の「editor」「viewer」のまま、共有先の権限も「（editor）」と英語の内部語で出る"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "教員が自分に共有されたコースと自分のコースを見分け、何ができるかを読む"
  layers: [frontend_admin_ui, auth_visibility]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 wording: frontend/public/js/admin.js 11783〜11800 行の roleBadge は owner を「所有」、viewer / それ以外を英語の "viewer" / "editor" で描き、共有先の行も p.permission（editor / viewer）をそのまま括弧で出す。共有モーダルの選択肢（12235 行）は「editor (編集可)」、別のモーダル（12508 行）は「編集・再解析できる」で、同じ語の表示が画面ごとに違う。
generalization:
  level: instance
  general_form: "権限の内部語を一覧の印にそのまま出し、同じ権限の日本語表記が画面ごとに違う"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: >-
    te-02 がコース管理で共有されたゼミを「〔editor〕」と読んだ。run: uxsim/runs/c-astro-r25-studio-r2/20261023T021041Z-70961（seq2・7・9）。
resolution:
  perspective: [vocabulary_table]
  note: "役割の印と共有の権限語を label_vocab の 2 表（COURSE_ROLE_LABELS / COURSE_SHARE_PERMISSION_LABELS）に置き、コース一覧は role_label・共有の一覧は permission_label をサーバから返して画面が描く。共有モーダルの選択肢は同じ表の逐語ミラーで、一致はテストで固定。editor の印は「共有（編集可・トピックの保存は所有者）」と保存できないことまで言う。"
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/api/schemas.py"
    - "backend/api/routes/admin.py"
    - "frontend/public/js/admin.js"
    - "docs/manual/teacher/13-admin-course-management.md"
  verification:
    methods: [guardrail]
    unverified:
      - "コース管理の一覧を実画面で見ていない"
related: []
view_of: []
history: []
---

## 課題

症状: 共有されたコースの印が英語の「editor」で、何ができるのか（保存できないこと — IK-0908）が読めない。

原因: 内部語の表示。

## 解決の観点

解決（2026-10-23・第 25 周 面 D 第 2 波）: 役割の印と共有の権限語を label_vocab の 2 表（COURSE_ROLE_LABELS / COURSE_SHARE_PERMISSION_LABELS）に置き、コース一覧は role_label・共有の一覧は permission_label をサーバから返して画面が描く。共有モーダルの選択肢は同じ表の逐語ミラーで、一致はテストで固定。editor の印は「共有（編集可・トピックの保存は所有者）」と保存できないことまで言う。

当初の是正案（記録）:

- 印を日本語の事実（「共有（編集可・保存は所有者）」「共有（閲覧のみ）」）にし、共有先の権限表記を 1 つの語彙表から引く（admin.js の 2 箇所の表記を揃える）。

## 発見の観点

第 25 周 面 D 第 2 周（原稿スタジオ → 学生の受講）の通し受講で、ペルソナの観察と HTTP の生応答・製品コードを突き合わせた。

## 一般化

権限の内部語を画面に出さない。確かめ方は、owner / editor / viewer の 3 行を持つ一覧で英語の権限名が出ないことを見ること。
