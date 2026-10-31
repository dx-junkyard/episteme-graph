---
id: IK-0389
title: "トピックの本文に [[FORMULA_0]] [[FORMULA_1]] [[FORMULA_2]] が生のまま残り、配信する formulas が空なので学習者に文字列のまま表示される"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コースのトピック本文（授業用教材）を生成し、学習画面で数式として描く
  layers: [course_builder, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 学習画面（app.js renderMaterialChunk）は [[FORMULA_N]] を配信された formulas（= content_blocks の式）の
    id か位置で引く。C-10 / P0-4 で content_blocks の式を「実際に参照する式」に絞った一方、本文側は生成モデルが
    出典抜粋から写したプレースホルダーを持ち得たままで、両者が両立しなくなっていた（contract）。処理軸: 生成後に
    引けないプレースホルダーを検査する段が無かった（logic。medium: 第 8 周のプレースホルダーがモデルの写しだったか
    どうかは本文の生成ログを見ていないので確かめていない）。確認: 主結果トピックの本文に [[FORMULA_0..2]] があり
    formulas が空だった（IK-0388 で式が1つも結びつかなかったことと重なる）。
generalization:
  level: repo_pattern
  general_form: 参照先の集合を絞った側と、参照を書く側の片方だけが変わり、宙に浮いた参照が配信される
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction]
  note: 学生が主結果トピックで生のプレースホルダーを見た。
resolution:
  perspective: [single_point_fix]
  note: >-
    コース内容生成（_generate_course_topic_drafts）で、生成成功・決定論フォールバックの両方の後に
    _sanitize_topic_formula_placeholders を通す。学習画面と同じ引き方（id / 正規化 id / 位置）で引けない
    [[FORMULA_N]] だけを label_vocab.UNRESOLVED_FORMULA_PLACEHOLDER_TEXT「（数式は出典の区画を参照）」に置き換え
    （式を捏造しない）、grounding_note と coverage（既存の値は上書きしない）に事実文を残す。![[equation:ID]] と
    [[FIGURE_N]] には触れない。対象は student_material と spoken_script。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0388_section_block_evidence.py
    - backend/api/routes/learning.py
    - backend/tests/test_learning_chat_wave6_turns.py
  verification:
    methods: [guardrail]
    unverified:
      - 受講レクチャー経路（core/lecture.build_topic_slides）は本文にプレースホルダーがあると content_blocks の式を使わず空の formulas を返す（別の食い違い。未変更）
      - 砂場でのコース再生成
related: [IK-0388, IK-0377]
view_of: []
history:
  - date: 2026-09-28
    field: resolution.note
    from: "引けない FORMULA_N プレースホルダー = formulas に id / 位置が無いもの"
    to: "latex / plain_text が空の数式（inline 式候補）も引けないに数える"
    reason: "第 9 周の砂場で t0 の本文に FORMULA_0 が残っていた。画面に描けない数式は引けないのと同じ"
  - date: 2026-09-28
    field: landed_in
    from: 生成時の是正のみ
    to: 生成時 + 配信時
    reason: >-
      保存済みのコース（生成時の是正より前のスナップショット）にも効くよう、routes/learning.py get_topic_material が
      区画の本文（resolved_text と segments）に同じ正本 replace_unresolved_formula_placeholders を通すようにした
      （引けるプレースホルダーには触れない・保存データは書き換えない。テスト
      test_learning_chat_wave6_turns.py::TestTopicMaterialUnresolvedFormulaPlaceholders）
---

## 課題

本文に引けない数式プレースホルダーが残り、学習者に生のまま出る。

## 発見の観点

実ペルソナの受講（第 8 周）。

## 解決の観点

生成後に、学習画面と同じ規則で引けないプレースホルダーだけを事実の文に置き換える。

## 一般化

参照先を絞る変更は、参照を書く側の検査と組にする。
