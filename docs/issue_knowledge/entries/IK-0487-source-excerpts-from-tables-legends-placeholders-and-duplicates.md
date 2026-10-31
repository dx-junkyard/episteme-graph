---
id: IK-0487
title: "原文抜粋・原文の証拠が図の凡例・表の行・図の説明の続き（「Same as Figure 2 …」）・数式の断片（「4𝜋𝜌𝜎𝑣 [[FORMULA_0]] ≈9.3」）・書誌の途中から始まり、[[FORMULA_N]] / [[eq_eqcand…]] を含み、同じ本文の参照（ev_0042 と ev_0043、証拠と原文抜粋）が重なっていた"
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、トピックの出典チャンクから原文抜粋を作り、原文の証拠と一緒に埋め込める参照として授業用ドラフトの材料に渡す
  layers: [course_builder]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [meaning]
    governance: [budget]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: IK-0464 の判定は所属・書誌・キャプションの見出しと小文字始まりだけで、表・凡例（数字を含む語が多い・同じ語に番号の並び）、キャプションの続き、数字・数式で始まる文の途中、内部参照を見ていなかった。文の切れ目は「. 」の直後すべてで、「et al.」「L. S.」でも切っていた。原文の証拠（evidence_links の source）の本文は抜粋と違い整えていなかった。統制軸 medium: 予算の段が原文の参照を 60 字に切り、同じ段落の続きの証拠が同じ文になった（IK-0463 の主張と同じ形）。
generalization:
  level: repo_pattern
  general_form: 抜粋の元を並びと粗い形で決め、本文として読めるか・他と区別できるかを中身で確かめない
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: 再生成プロンプト 20 本の source_excerpt と kind='source' の参照の書き出しを読み、同じ text の参照の組を数えた。
resolution:
  perspective: [single_point_fix, order_and_budget]
  note: >-
    source_excerpt_rejection_reason に table_or_legend（数字を含む語の割合・同じ語に番号の並び）とキャプションの続きを足し、trim_excerpt_start は数字・数式・内部参照で始まる文の途中も落とす（節番号の見出しは残す。文の切れ目は次が大文字・CJK のところだけで、略語・頭文字では切らない）。抜粋は冒頭に内部参照の無いチャンクを先に選び、残った内部参照は「（数式）」の事実語にする（clean_excerpt_text / scrub_excerpt_placeholders。text_hygiene.scrub_internal_placeholders を使う）。原文の証拠の本文も同じく整える。同じ本文の原文参照は1つにし（下書きが埋め込んでいる方を残す）、予算で短くして同じ文になった参照は区別できる長さまで戻す。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0484_0489_course_draft_regen12.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成
      - 表・凡例の判定は数字の割合の閾値で、数値の多い本文の段落（観測値の列挙）を飛ばすことがある
      - 頭文字の点で切らないため、「5 K. The」のように1文字の単位で終わる文の境目では切れない
      - 学習チャットの RAG のチャンク冒頭は別経路で直していない
related: [IK-0464, IK-0463, IK-0455, IK-0445]
view_of: []
history: []
---

## 課題

原文抜粋・証拠が本文でない区画や数式の断片から始まり、同じ本文の参照が重なっていた。

## 発見の観点

抜粋と原文の参照の書き出しを全トピック分読んだ。

## 解決の観点

本文として読めることを中身で確かめ、内部参照を事実語にし、同じ本文を1つにする。

## 一般化

抜粋は本文として読めることと、他の参照と区別できることを確かめてから渡す。
