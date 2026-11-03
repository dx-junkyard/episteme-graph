---
id: IK-0685
title: "論文との議論の開幕で「中心となる式」のチップが生の TeX（{B_0} \\approx \\sqrt{…}）のまま出て、論文の骨格の各章に内部の種別名（problem_setting 等）が添えられる"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/discuss_opening_authoring_design.md"
  - "docs/features/display_projection_design.md"
feature_context:
  realizing: "学習者が議論の開幕で、この論文の中心の式と章の流れを読む"
  layers: [discuss, frontend_learning_ui]
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
    確認: discuss.js の renderThesisSection は central_equations の label を discussChip で esc() するだけで KaTeX を通さない（discuss.js に数式描画の呼び出しが無い）。renderChapterSkeletonSection は block_type をそのまま副ラベルに描く。審判 B が開幕の応答に生の TeX を検出した（学生 3 人の全 run）。処理軸 wording。
generalization:
  level: repo_pattern
  general_form: "数式・内部語の表示規則が面ごとに書かれ、新しい面の部品がその規則を通らずに生の値を出す"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で審判 B が議論の開幕の応答に生の TeX を検出し、画面の部品（discussChip）が数式を描かないことを確かめた。学生は「英語と記号まじりで長い」と書いた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（frontend）: 中心の式のチップは app.js の数式描画（renderMaterialKatex 相当）を通すか、学習者向けの式の表示名（印字番号・定義する記号）にする。block_type は表示語に置き換えるか外す。"
related: [IK-0478, IK-0546]
view_of: []
history: []
---

## 課題

症状: 開幕の中心の式が生の TeX、章に内部の種別名。原因: 開幕の部品が数式・内部語の表示規則を通らない。

## 発見の観点

審判 B とペルソナの通し受講（reproduction）。

## 解決の観点

未解決。共有の数式描画か表示名を通す。

追記（2026-10-10・第 3 波の後の再演）: 開幕の骨格の内部語は IK-0690 で消えたが、中心となる式は生の TeX（\begin{aligned} … の途中で切れた文字列）のまま、骨格の文にも「\mu > 1」が残る（学生 2 人が STILL と読んだ）。本課題は未解決。

## 一般化

表示規則が面ごとに書かれると、新しい部品が規則を迂回する。確かめ方は、学習者向けの全部品で生の TeX と内部語を走査すること。
