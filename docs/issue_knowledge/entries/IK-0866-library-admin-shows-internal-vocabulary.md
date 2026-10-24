---
id: IK-0866
title: "ナレッジライブラリと教材の共有画面が内部の値をそのまま教員に見せる — エントリ一覧の「更新:」に作成者の UUID（aae21fb9-…）か「bundled_import」、詳細に分野の英字キー（astrophysics）と「版 0 / revision 1」、凍結していない版 0 と「いまの扱い: 確定」が説明なしに並び、共有画面は viewer / editor / Claim を英語のまま出す"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/concept_registry_design.md"
  - "docs/features/display_projection_design.md"
feature_context:
  realizing: "教員がライブラリのエントリと教材の共有設定を読み、誰がいつ何を確定したかと共有の意味を判断する"
  layers: [concept_registry, image_library_l, frontend_admin_ui, auth_visibility]
classification:
  axes:
    processing: [wording]
    structure: [responsibility]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 wording: frontend/public/js/admin.js:2473-2474 は一覧に「版 ' + latest_version_no」と updated_by の生値
    （ユーザー UUID または同梱シードの bundled_import）を、:2536-2537 は詳細に domain_key の生値と
    「版 N / revision N」を出す。:12219 / :12228 の共有画面は「viewer=閲覧・引用／editor=編集・再解析」「Claim」と
    英語の権限語を出す。構造軸 responsibility: 帰属（user_id → 表示名）と分野キー → 分野名の写像を route が返さず、
    画面にも写像が無い（分野の地図タブの同型 IK-0850 と同じ面ごとの欠落）。版 0（未凍結）と review_status=confirmed
    （概念として確定）の 2 軸が説明なしに並ぶため、教員は下書きのつもりの登録を「確定」と読む。接続軸 none（値は
    正しく届いている）。
generalization:
  level: repo_pattern
  general_form: "教員向けの管理画面が帰属・分野・版・権限の内部値を表示語へ写さずに描き、同じ欠落が画面ごとに残る"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction]
  note: >-
    教員 te-03・te-01 が「更新: bundled_import」「UUID」「astrophysics」「版 0 / revision 1」「viewer / editor / Claim」を
    読めないと記した。run: uxsim/runs/c-astro-r25-share-r1/20261022T124804Z-38413 seq2-3・20261022T125310Z-41522 seq4・seq7-8。
resolution:
  perspective: [vocabulary_table]
  note: >-
    library の一覧・詳細・作成・更新・分野一覧・同一性の候補が route 層で updated_by_label（シードは「同梱の初期データ」・帰属の表示名は atlas_review_display.fetch_actor_labels を共有）/ domain_label / version_line（凍結版 N・まだ凍結していません）/ review_status_note（確定と凍結は別）を返し、admin.js は revision・UUID・英字キーを出さない。教材の解析成果のグループ共有の権限語を label_vocab.DOCUMENT_SHARE_PERMISSION_LABELS（DTO の permission_label）と日本語の選択肢にした。
  landed_in:
    - "backend/api/routes/library.py"
    - "backend/core/label_vocab.py"
    - "backend/api/schemas.py"
    - "frontend/public/js/admin.js"
    - "docs/manual/teacher/19-admin-knowledge-library.md"
    - "docs/manual/teacher/11-admin-materials.md"
    - "backend/tests/test_share_round25_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "コースのグループ共有の画面の viewer / editor 表記は今回触っていない"
      - "名前が登録されていない分野は「名前が登録されていない分野」と出る"
related: [IK-0850]
view_of: []
history: []
---

## 課題

症状: 一覧の更新者が UUID か bundled_import、詳細の分野が英字キー、版が「版 0 / revision 1」、共有の権限が英語。教員は
誰が登録したか・凍結済みか・確定とは何かを読めない。原因: 管理画面が内部値を表示語へ写さずに描き、写像が route にも無い。

## 発見の観点

教員ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。帰属・分野名の解決を route 層の 1 関数にし、地図タブ（IK-0850）と共有する。

## 一般化

教員向けの面は学習者向けの表示投影の外にあり、面ごとに内部値が残る型。確かめ方は、ライブラリ・共有・地図の各画面の
DTO と描画を走査し、UUID・シード名・英字キー・revision が表示文に出ないことを見ること。

## 第 2 周の再演（第 25 周・面 A 第 2 周、2026-10-23）

残存（一部）。ライブラリの一覧・詳細の生応答には `domain_label`（宇宙物理）・`version_line`・`updated_by_label` が載り、画面の
描画（`admin.js` renderLibraryDetail）も表示名を使う — 詳細の「astrophysics」はハーネスの投影の遅れ（`harness_fixes_share.md`）。
残っているのは「地図との対応を導出」の確認ダイアログで、`admin.js` deriveLibraryAtlasLinks が `_libraryTabState.selectedDomain`
（分野の英字キー）をそのまま「分野「astrophysics」の…」に埋め込んでいる。分野の選択肢の表示名（ライブラリの分野一覧の
`domain_label`）を引いて文に使う。

第 2 波（2026-10-23・面 A 第 2 周の是正）で是正: 導出の確認ダイアログの分野名は、分野一覧のサーバの domain_label を `_libraryTabState.domainLabels` に控えて使い、英字キーを出さない（表示名が無ければ「選んだ分野」）。
