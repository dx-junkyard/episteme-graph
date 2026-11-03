---
id: IK-0404
title: "レクチャーのスライド（core/lecture.build_topic_slides）は、本文に既に [[FORMULA_N]] が入っていると content_blocks の数式を引かず、学習画面では式として見えるトピックがレクチャーでは生のプレースホルダーになっていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者がトピック教材をレクチャー（スライド + 読み上げ）で受講する
  layers: [lecture_player]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: 保存済みコースの本文は [[FORMULA_N]] のまま残り、式の中身は topic.content_blocks の equations にある。
    学習画面（routes/learning.py の _topic_formulas_from_content_blocks + app.js の formulaById）はそこから引くが、
    build_topic_slides は normalize_to_placeholder_format（既にプレースホルダー方式なら何もしない）に formulas=[] を渡すだけで、
    content_blocks を読まなかった。処理軸 logic: 同じトピックの同じ本文が、画面によって式にも生の記号にもなる。確認: 第 5 波 b の
    持ち越し（W6-27）と第 8 周の学生の報告。
generalization:
  level: repo_pattern
  general_form: 同じ保存データを 2 つの画面が別々の解決器で読み、一方だけが参照の解決材料を読むので、画面ごとに見え方が違う
pattern: available-but-unwired
discovery:
  perspective: [trace_walk]
  note: 学習画面とレクチャーの本文解決の経路を並べて読んだ。
resolution:
  perspective: [carry_through]
  note: >-
    resolve_topic_formula_placeholders を足し、本文に元からある [[FORMULA_N]] を content_blocks の数式で解決する（照合は学習画面と
    同じ: id が FORMULA_N と一致する項目を優先し、無ければ出現順の N 番目。読み上げは plain_text を優先して生 TeX を読まない）。
    引けないものは label_vocab.UNRESOLVED_FORMULA_PLACEHOLDER_TEXT の事実文に置き換える。![[equation:…]] の解決番号は、本文に
    元からある番号の最大の次から振る（番号の衝突を防ぐ）。学習画面側の配信時解決（W6-25: get_topic_material で
    replace_unresolved_formula_placeholders を通す）は routes/learning.py の担当で、ここでは触れていない。app.js の formulaById は
    同じ id の項目が位置の項目と衝突したとき後勝ちになるが、この関数は id 一致を優先する（病的な衝突例でだけ差が出る）。
  landed_in:
    - backend/core/lecture.py
    - backend/tests/test_wave6b_mirror_lecture_recon.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのレクチャー再生（スライドと読み上げに生のプレースホルダーが出ないか）
      - トピック音声のキャッシュ（topic_lecture_audio_cache）は再生成するまで旧スライドの読み上げのまま
related: [IK-0389, IK-0391]
view_of: []
history: []
---

## 課題

レクチャーだけが本文の数式プレースホルダーを解決しなかった。

## 発見の観点

学習画面とレクチャーの本文解決の経路を並べた。

## 解決の観点

レクチャー側にも同じ解決材料（content_blocks の数式）を配線する。

## 一般化

同じデータを読む画面は、同じ解決規則を通す。
