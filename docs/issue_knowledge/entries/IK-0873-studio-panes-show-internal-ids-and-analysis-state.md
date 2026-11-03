---
id: IK-0873
title: "原稿スタジオの授業用ドラフト画面が、注意点・根拠リンクに内部 ID（eq_blk_003_0065・claim_span_*）、解析の失敗状態の英文（「Equation semantics could not be inferred.」）、英語の主張を固定長で切った見出しをそのまま教員に見せる"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/display_projection_design.md"
feature_context:
  realizing: "教員が授業用ドラフトの注意点と根拠リンクを読み、どの式・主張を教材に使うかを判断する"
  layers: [lecture_studio, frontend_admin_ui, pipeline_a]
classification:
  axes:
    processing: [wording]
    structure: [responsibility]
    connection: [meaning]
    governance: [none]
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
    処理軸 wording: 注意点（cautions）は下書き生成の LLM が根拠候補に載った内部 ID を引いて書き、原稿スタジオはそれを素通しする。
    根拠リンクの数式の見出しは content_blocks に保存された label をそのまま使い、src/episteme_graph/agents/equation_semantics/repair.py:332
    の修復失敗時の要約「Equation semantics could not be inferred.」が式の名前として出る。主張の見出しは英語の主張本文の先頭固定長。
    構造軸 responsibility（medium）: 教員向けの表示投影（display_projection の DP7）は名指しの面だけに掛かり、原稿スタジオ
    （routes/lecture_studio/）は通らない。接続軸 meaning: 解析段の「推定できなかった」という状態の文が、後段で式の名前（意味のある
    ラベル）として読まれる。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "解析段の内部 ID と失敗状態の文が、表示の投影を通らない編集画面で名前・見出しとして人に見える"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員ペルソナが注意点の内部 ID と、根拠リンクの「Equation semantics could not be inferred.」の反復を記した。根拠リンクの見出しの
    材料（content_blocks の label）と修復失敗時の要約を辿った。run: uxsim/runs/c-astro-r25-studio-r1/20261022T123418Z-31804（seq6・14）。
resolution:
  perspective: [canonical_source, explicit_contract]
  note: "解析段の「推定できなかった」要約（A層 equation_semantics/repair.py の固定文）を backend 側の 1 箇所（course_content_builder.is_uninformative_equation_summary・_explanatory_text）で名前・説明の材料から外し（A層は非改変・一致はテストで A層のソースと突き合わせる）、根拠リンクの式の summary からも落とす。DP7 の読み替え（指揮者の判断）: 原稿スタジオの編集欄の本文には投影を掛けず、読み取り専用の区画（evidence_items と注意点のプレビュー cautions_display）だけを project_for_teacher に通し、内部 ID を付録と同じ名前の段へ写す。下書き生成のプロンプトに「注意点・地の文では内部 ID を書かず印字番号・短い原文・名前で書く」を足した。"
  landed_in:
    - "backend/api/routes/lecture_studio/topics.py"
    - "backend/core/course_content_builder.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/features/display_projection_design.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "読み取り専用の区画は 1 つの入口（_topic_studio_view → project_for_teacher）を通し、画面側で内部 ID を個別に塞がない。"
  verification:
    methods: [guardrail]
    unverified:
      - "教員の画面での表示は docker 実機で見ていない（node が無く JS の評価テストも走らない環境）"
      - "保存済みの英語の主張本文の固定長切りは未着手"
related: [IK-0850, IK-0715, IK-0413]
view_of: []
history: []
---

## 課題

症状: 原稿スタジオの注意点に「eq_blk_003_0065 と eq_blk_003_0066 は同じ作用の式が2つの ID で供給されています」「Claim（claim_span_*）は
…見送りました」と内部 ID が並び、根拠リンクの数式が「Equation semantics could not be inferred.」で何度も並ぶ。主張は英語の本文を固定長で
切ったもの（「…Northwest–S」）。原因: 解析段の内部 ID と失敗状態の文が名前の材料になり、原稿スタジオは教員向け投影の外にある。

## 発見の観点

ペルソナの通し受講（reproduction）と見出しの材料の追跡（trace_walk）。

## 解決の観点

解決済み（2026-10-23）。失敗状態の文を名前にしない、読み取り専用区画だけ投影を通す。

## 一般化

IK-0850（分野の地図タブ）と同じく、投影を面ごとに掛ける運用で投影の外の面に内部値が残る型。確かめ方は、原稿スタジオの
注意点・根拠リンクの描画結果を内部 ID の正規表現（display_projection の語彙）と失敗状態の固定文で走査し、当たらないことを見ること。

第 2 周の再演: 一部残存（注意点の表示は cautions_display で直った — 観察に出た内部 ID は投影の遅れ。根拠リンクの式の見出しに英語の要約「Fragment of the Horndeski Lagrangian…」と同じ見出し「前提式」の繰り返しが残る。run: uxsim/runs/c-astro-r25-studio-r2/20261023T020312Z-65722 seq6。書き換えの材料に失敗状態の文が残る件は IK-0909）。

第 2 波で是正（2026-10-23）: 根拠リンクの式の見出しを教員向けの読み取り専用区画でだけ整える（`core/lecture_studio_state.studio_equation_titles`）: A層の英語の要約（`semantic_kind` の第 1 文「Fragment of …」）から来た見出しは役割の訳（「前提式」）か「数式」に戻し、同じ見出しが並ぶときはグループ内で値の分かれる材料（掲載節 → 読み下し）を括弧で添える（材料が無ければそのまま）。教員・A層が付けた読める名前は変えない。学習画面の evidence DTO は不変（英語の生成文の学習者側は IK-0571 の正本）。書き換えの材料に失敗状態の文が残る件は IK-0909 で是正。
