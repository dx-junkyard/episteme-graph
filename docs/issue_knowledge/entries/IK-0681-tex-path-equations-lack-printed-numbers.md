---
id: IK-0681
title: "TeX 経路の論文の式に印字の式番号が無く、表示名が TeX のラベル（eq:mu・eq:cl_kk）になり、コースビルダーも「内部の識別子しか無い」と答える"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/pipeline/overview.md"
  - "docs/features/display_projection_design.md"
feature_context:
  realizing: "教員・学習者が TeX 経路の論文の式を、原文の式番号で照合する"
  layers: [pipeline_a, course_builder, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 砂場 DB の chunks.formulas[].label は TeX の \label の鍵（eq:cl_kk）か空で印字番号が無いこと（確認済みの事実）から、TeX には番号そのものが書かれておらず（組版で数える）、取り込みが式の出現順から番号を数えていない。構造軸 representation = 印字番号を表す値が TeX 経路で作られない。接続軸 information = 番号が表示名・コースビルダーの文脈へ届かない。TeX の式環境の出現順と PDF の印字番号を突き合わせれば確定する。
generalization:
  level: repo_pattern
  general_form: "入力の形式によっては表示に要る値が原文に書かれておらず、取り込みが導出しないまま内部の鍵が表示名に繰り上がる"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 23 周の砂場で、几帳面な教員が「式番号は内部の識別子しか無いと回答された」、学生が「式の見出しに eq:mu のようなラベル」と書き、DB の式ラベルを読んで確かめた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（A層の入力・core）: TeX の番号付き式環境の出現順から印字番号を数えて式に持たせ、表示名の正本（equation_display_label）が番号を使えるようにする。"
related: [IK-0645]
view_of: []
history: []
---

## 課題

症状: TeX 経路の式に原文の番号が無く、TeX の鍵が表示名に出る。原因（仮説）: 取り込みが番号を数えない。

## 発見の観点

ペルソナの通し受講（reproduction）と DB の読み取り（data_inspection）。

## 解決の観点

未解決。取り込み段で番号を導出する。

## 一般化

表示に要る値が原文に書かれない形式では、取り込みで導出しないと内部の鍵が表示名になる。確かめ方は、番号付き式を含む TeX を取り込み、式の表示名が原文の番号になることを見ること。

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演（面 A）: STILL・新しい面。学生の記号の吹き出し（L_TOV）の定義の式の下に TeX のラベル「eq:tov_residual」がそのまま 1 行で出る（symbol_lookup の definition.equation_label → app.js 5782 が素通しで描く・uxsim/runs/c-astro-r25-share-r7/20261027T172824Z-59013 seq10）。

## 第 7 波の是正（第 25 周・面 A、2026-10-28）

第 7 波の是正（2026-10-28・面 A の学生の記号の吹き出しの面だけ）: symbol_lookup は TeX の label キー（eq:…）を definition.equation_label・式の表示名に出さない（判定の正本 display_projection.is_tex_label_key）。課題の本体（TeX 経路の式に印字番号が無い）は open のまま。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

形を変えて STILL。第 8 周の st-04 は PDF 版の論文のコース（6be229e1）で、吹き出しの equation_label は印字番号「4」（TeX の label キーが出る場面ではない — 頭脳の GONE は文書が替わった見かけ）。新しい面: app.js は equation_label をそのまま 1 行に描くので、式の下に裸の「4」が出て「式 (4)」と言わない（symbol_lookup の _equation_name は使われていない）（uxsim/runs/c-astro-r25-share-r8/20261027T224425Z-99386 seq7・9）。

## 第 9 周の再演（第 25 周・面 A 第 9 周、2026-10-31・judge_share_r9）

定義の経路は GONE、式の中に現れる経路で形を変えて STILL。同じコース 1bad7d9f の L_TOV（第 7 周は定義の式の下に「eq:tov_residual」）は今周、定義文・式・出典だけ。定義の文の無い N_TOV は「この論文には定義の文は見つかりませんでしたが、この記号は式 (eq:tovnet)・式 (eq:tov_resid…)の中に現れます。」と式の見出し「式 (eq:tov_resid…)」。原因（.venv で関数を呼んで確かめた）: ①core/symbol_lookup.py の式のフォールバックは label を _clean(…, limit=12) で切ってから _equation_name → _printed_equation_label で is_tex_label_key を判定するので、「eq:tov_resid…」（… が付く）が TeX のキーの正規表現に当たらず印字番号として通る ②_where_text は labels を判定を通さず「式 (…)」に並べる（9 字の「eq:tovnet」も出る）。是正の見立て: 判定を切り詰めの前に行い、_where_text も同じ判定を通す。run: uxsim/runs/c-astro-r25-share-r9/20261031T020038Z-42487（seq7・8）。

## 第 9 波の是正（第 25 周・面 A、2026-10-31）

第 9 周の「形を変えて STILL」（式の中に現れる経路）を是正。①core/symbol_lookup の式の写しの経路で、印字番号の判定
（_printed_equation_label → display_projection.is_tex_label_key）を 12 字の切り詰めの**前**に行う（「eq:tov_resid…」のすり抜け）。
切り詰め済みの値が来ても末尾「…」を外して判定する。②_where_text も同じ判定を通し、TeX の label キーは「番号の無い式」に数える。
③要素文脈の式の見出し（learner_context_common.equation_focus_label）と MINI 参照（element_context._project_mini_ref）も TeX の
label キーを番号として出さない。印字番号を数えて持たせる本筋（resolution の推奨）は未着手のまま。テスト
backend/tests/test_share_round25_r9_fixes.py::TestTexLabelKeyBeforeTruncation。確かめていない: st-04 × (e) の N_TOV の再演（第 10 周）。

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

定義の経路は GONE 継続（L_TOV の吹き出しに TeX ラベルの行なし・同じコース 1bad7d9f で比較可）。「式の中に現れる」経路は st-04 の材料に N_TOV が無く run では未評価（harness H62）。`core.symbol_lookup._where_text(['eq:tovnet','eq:tov_resid…'], False)` と `_equation_name('eq:tov_resid…')` を直接呼ぶと「番号の無い式」— コードは GONE。run: uxsim/runs/c-astro-r25-share-r10/20261031T053407Z-68377（seq9）。

## 第 11 周の再演（第 25 周・面 A 第 11 周、2026-10-31・judge_share_r11）

本体 STILL（TeX ラベルは出ない — 第 9 波の是正は継続）。st-04 が N_TOV を初めてタップ（harness H62 の是正）:「この論文には定義の文は見つかりませんでしたが、この記号は番号の無い式の中に現れます。」・式の見出し 2 つとも「番号の無い式」。同じ吹き出しの原文の用例は「as written in Eq. (2)」「In the TOV residual loss of Eq. (4)」で、論文には印字番号がある（uxsim/runs/c-astro-r25-share-r11/20261031T101956Z-2094 seq11）。
