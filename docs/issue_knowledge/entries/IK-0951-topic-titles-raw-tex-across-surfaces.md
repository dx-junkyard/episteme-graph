---
id: IK-0951
title: "コースビルダーが作るトピック名に区切りの無い TeX（「\\alpha_M=0・\\alpha_T=0 の簡約」「有効音速 c_s^2(k,a)」「z<2 での \\mu>1」）が入り、原稿スタジオの一覧・学習画面のトピック一覧・各所の見出しがどれも題名を素の文字列として描くため、教員にも学生にも書きかけの記号のまま見える"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "教員と学習者がトピックの一覧から目的のトピックを題名で探す"
  layers: [course_builder, lecture_studio, frontend_learning_ui, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 構造軸 representation: トピック名は「素の文字列」として持たれ、数式の区切り（$…$）を持つかどうかの約束が無い。backend/api/routes/admin.py
    _COURSE_BUILDER_SYSTEM_PROMPT（2163 行〜）の形式は "title": "トピック名" だけで、題名に記号を書くときの規則（$ で囲む／言葉にする）を言わない。
    接続軸 contract: 表示側（admin-lecture-studio.js 1092 行・app.js のトピック一覧と見出し）は escHtml で素の文字列として描き、数式を描かない。生成側が
    TeX を書き、表示側が文字列と見なす契約の食い違い。処理軸 none。統制軸 none。確定するには、題名の生成経路（ビルダー・コース内容生成・書き換え）の
    どれが TeX を書いたかを、74ca9b48 の作成時のセッション履歴で確かめる。
generalization:
  level: repo_pattern
  general_form: "表示名に数式を書いてよいかの約束が無いまま、生成側は記号を書き、表示側は素の文字列として描く"
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction]
  note: >-
    te-03 が原稿スタジオで「トピック名に書きかけの数式記号がそのまま出ていて読みにくい」、st-01 が学習画面で「名前に \\alpha_M=0 みたいな記号が
    そのまま出ていて読めません」と書いた（同じコース 74ca9b48）。run: uxsim/runs/c-astro-r25-studio-r3/20261023T071024Z-19157（seq4）/
    uxsim/runs/c-astro-r25-studio-r3/20261023T071646Z-49888（seq5）。
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: "題名に数式の記法を書かない約束をコースビルダーの生成指示に入れ、course_data.title_has_raw_tex / course_titles_with_raw_tex で検出してコース登録の応答と course-structure に title_notice（COURSE_TITLE_RAW_TEX_NOTICE）+ raw_tex_titles を返す（コースビルダー・原稿スタジオの見出しに出す）。表示側でのインライン描画はしない。"
  landed_in:
    - "backend/core/course_data.py"
    - "backend/core/label_vocab.py"
    - "backend/api/routes/admin.py"
    - "backend/api/routes/learning.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "frontend/public/js/admin.js"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/manual/teacher/12-admin-course-builder.md"
  verification:
    methods: [guardrail]
    unverified:
      - "既に作られたコースの題名は直していない（教員が事実文を見て直す）"
      - "実モデルがこの指示で題名から TeX を外すかは見ていない"
related: [IK-0664]
view_of: []
history: []
---

## 課題

症状: トピック名に「\alpha_M=0」「c_s^2(k,a)」が素のまま出る。

原因（仮説）: 題名に数式を書いてよいかの約束が無く、生成側が TeX を書き、表示側は文字列として描く。

## 発見の観点

同じコースを教員（原稿スタジオ）と学生（受講）の両側から読んだ。

## 解決の観点

第 25 周 面 D 第 3 周の是正（2026-10-23）で解決。題名に数式の記法を書かない約束をコースビルダーの生成指示に入れ、course_data.title_has_raw_tex / course_titles_with_raw_tex で検出してコース登録の応答と course-structure に title_notice（COURSE_TITLE_RAW_TEX_NOTICE）+ raw_tex_titles を返す（コースビルダー・原稿スタジオの見出しに出す）。（以下は起票時の見立て）題名の約束を決め、生成指示と保存時の検出で守る。

## 一般化

名前の欄に記法を許すかを決めずに生成させると、記法が名前に混ざる。確かめ方は、数式の多い論文でコースを作り、一覧の題名にバックスラッシュが出ないことを見ること。
