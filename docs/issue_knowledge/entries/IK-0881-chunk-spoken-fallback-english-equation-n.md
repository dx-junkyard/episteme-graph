---
id: IK-0881
title: "チャンク経路の読み上げ原稿で、読み方の無い式が英語の「equation 5」に置き換わり、括弧も残って「[[equation 5]]」として日本語の講義に読まれる（同じチャンクの表示には「$eq_eqcand_inline_blk_…$」の内部 ID が残る）"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "トピック教材の無いトピックで、PDF 由来のチャンクを表示と読み上げのスライドにする"
  layers: [lecture_studio, lecture_player, tts_voice, pipeline_a]
classification:
  axes:
    processing: [logic, wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: low
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 logic: backend/core/document_pipeline/persistence.py の _spoken_text_from_formulas（1170〜1180 行）は placeholder を
    formula.get("id")（括弧なしの式 ID）で置換するため、本文側が「[[eq_…]]」なら括弧が残る。処理軸 wording: spoken が無いときの代替は
    言語を問わず英語の「equation {label}」。接続軸 none（low）: 表示側の「$eq_eqcand_inline_…$」は、式本文を ID で置き換える前段
    （同ファイル 1150〜1155 行の placeholder 置換）が `$…$` の内側に ID を入れた可能性があるが、この run の材料だけでは置換の入力を
    確かめていない（仮説として本文に残す）。構造・統制 none。
generalization:
  level: repo_pattern
  general_form: "置換の代替文を言語を問わず固定の英語で作り、置換対象の表記（括弧の有無）と置換キーの表記がずれて記号が残る"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員ペルソナがスライドタブで、表示に内部 ID、読み上げに「[[equation 5]]」が入ったチャンクを見た（開いたチャンクがトピックと無関係
    だったのはハーネスの選び方 = harness_fixes_studio.md）。読み上げ原稿の置換を辿った。run: uxsim/runs/c-astro-r25-studio-r1/20261022T123418Z-31804（seq7）。
resolution:
  perspective: [single_point_fix]
  note: "_spoken_text_from_formulas は [[id]] を先に、裸の id は語境界でだけ同じ読み上げ語へ置き換える（括弧残りと eq_1 が eq_10 に当たる誤置換を防ぐ）。読み上げ文の無い式の代替は講義の言語の語（既定 ja: 印字番号があれば「式 (N)」、無ければ「数式」／en: equation (N) / the equation）。表示側の ID 残り（仮説）は砂場で入力を確かめていないので未着手。"
  landed_in:
    - "backend/core/document_pipeline/persistence.py"
    - "backend/tests/test_lecture_studio_round25.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "根拠の状態・トピックの論文・音声の有無をサーバの 1 箇所（core/lecture_studio_state と course-structure）で決め、画面は返った値と文を描くだけにした。"
  verification:
    methods: [guardrail]
    unverified:
      - "保存済みチャンクの spoken_text は再解析まで旧い文のまま"
      - "表示側の $eq_eqcand_inline_…$ の原因は未確認"
related: [IK-0873]
view_of: []
history: []
---

## 課題

症状: チャンクのスライドの読み上げに「if [[equation 5]] = 0, we have」が入り、表示には「$eq_eqcand_inline_blk_004_0145_200_f43d961d$」が
残る。原因（確定した部分）: 読み上げの式の置換が括弧の表記を揃えず、代替文が固定の英語。

## 発見の観点

ペルソナの通し受講（reproduction）と読み上げ原稿の置換の追跡（trace_walk）。

## 解決の観点

解決済み（2026-10-23）。置換キーの表記を揃え、代替文を講義の言語に合わせる。

## 一般化

固定言語の代替文と表記ずれの型。確かめ方は、spoken の無い式を含むチャンクを ja で生成し、読み上げに英語と括弧が残らないことを見ること。
