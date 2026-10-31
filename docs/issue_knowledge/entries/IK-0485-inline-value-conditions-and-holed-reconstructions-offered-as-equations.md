---
id: IK-0485
title: "本文中の値の条件（「z < 2」「wa = 0」「z ≈0」）と、原文が切れ端で復元 LaTeX に「[unknown …]」の穴がある式が埋め込める式として一覧に載り、原文そのままの「N = 15」に復元の注記が付く一方、原文と違う LaTeX（M_max）の項目には復元の印が無かった"
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、トピックの式を参照一覧・content_blocks の写しとして授業用ドラフトの材料に差し出し、AI の復元した式にその事実を添える
  layers: [course_builder]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: 式として読めない候補の判定（IK-0459）は、短い代入を「4文字未満で = のもの」に限り、< / ≈ の条件や空白入りの代入を式として通していた。復元 LaTeX がある式は穴があっても落とさなかった。接続軸 medium: 復元の事実（reconstructed）は reconstructed_equation_ids の別キーにしか載らず、項目自身には「原文より先まで復元」の注記しか無かった — 原文と違うが先まではいかない LaTeX（M_max = 2.062…）は印が無く、別キーが予算で落ちると復元の事実が消える。逆に LaTeX が原文と同じ式にも「原文は短い」の注記が付いていた。
generalization:
  level: repo_pattern
  general_form: 取り込み段の候補の形を粗い基準で確かめ、同じ類の断片を通す。候補の性質（復元）を別の一覧だけで伝え、項目と一緒に運ばない
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: 再生成プロンプトの available_references と content_blocks の式の本体・raw_text・reconstructed を1件ずつ突き合わせた。
resolution:
  perspective: [single_point_fix, carry_through]
  note: >-
    is_junk_equation_candidate に、記号1つと数値1つを関係記号でつないだだけの原文（_is_short_value_condition → axis_ticks「図の目盛り・短い断片」）と、原文が切れ端（_raw_is_fragment）で LaTeX に [unknown …] の穴がある式（split_fragment）を足した。reconstruction_beyond_raw は LaTeX と原文が正規化して一致するなら False。参照一覧の式の項目に、表示する LaTeX が原文と違う復元式なら reconstructed: true を付け（_reference_shows_reconstructed_latex）、topic_reconstructed_equation_ids も同じ条件にした（LaTeX の無い式を「AI が復元した式」と言わない）。新しい理由語は作らず既存の語に寄せた（label_vocab.py は共有ファイルのため触らない）。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0484_0489_course_draft_regen12.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成
      - LaTeX を持つ短い条件（「N = 15」の latex あり）は式として残す — 本文の値の言及かどうかは形だけでは決められない
      - 穴のある復元式を外したことで、そのトピックに式が1つも残らない場合の授業用ドラフトの質
related: [IK-0459, IK-0460, IK-0454, IK-0429]
view_of: []
history: []
---

## 課題

本文の値の条件と穴のある復元式が式として差し出され、復元の印が項目に付いていなかった。

## 発見の観点

参照一覧の式を原文と LaTeX で1件ずつ読んだ。

## 解決の観点

短い値の条件と切れ端の復元を外し、復元の事実を項目自身に付ける。

## 一般化

候補の性質は別の一覧ではなく項目と一緒に運ぶ。
