---
id: IK-1036
title: "学習チャットに渡す「現在表示中の教材」で、文の途中に埋め込まれた主張（「半径を ![[claim:…]] と報告」）を外すため、値の抜けた「半径を  と報告」になる"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22"
  - "docs/features/focus_document_design.md"
feature_context:
  realizing: "学生が表示中の教材について学習チャットで尋ね、教材の本文どおりの値で答えてもらう"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
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
    処理軸 logic: material_text_for_prompt は component / claim / source の埋め込みを「本文の説明が中身を既に書いている」前提で
    空文字にするが、埋め込みが文の要素として使われていると文が欠ける。接続軸 information: その結果、教材に書かれた値（主張の本文）が
    プロンプトへ運ばれない。mailbox req-04241 の [現在表示中の教材] で「半径を  と報告」「見出しとして  を押さえて」を確認した。
generalization:
  level: repo_pattern
  general_form: "表示用の参照記法を機械的に取り除くとき、参照が文の要素として使われている場合を区別せず、文の意味を欠かす"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: >-
    製品頭脳 B の 4241・4243 に渡ったプロンプトの教材本文に、値の抜けた空白が同じ 2 箇所で出た（uxsim/runs/round25/BRAIN_NOTES_product_B_r6.md）。
resolution:
  perspective: [representation_change]
  note: >-
    埋め込みが行の途中にあるときだけ、トピックで公開済みの参照（build_topic_evidence_items）から本文を引用で入れる。引けない・内部 ID 形なら
    種別の語（「（主張）」）。行単独のブロック埋め込みは従来どおり外す。IK-1006（目印の除去で引用符だけが残る）と同じ一般形の別の面。
  landed_in:
    - "backend/core/course_content_builder.py"
    - "backend/tests/test_round25_wave6_chat_rag.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場のトピック（主結果）の教材で、プロンプトに主張の本文が入ること"
      - "evidence_links に無い claim を文中で参照する教材（種別の語に縮退する）"
related: [IK-1006, IK-0455]
view_of: []
history: []
---

## 課題

症状: AI に渡る教材本文の値が抜け、AI は教材が報告する半径の値を教材から読めない。原因: 文中の主張の埋め込みを空文字に置換していた。

## 発見の観点

製品頭脳に渡ったプロンプトの点検。

## 解決の観点

行の途中の埋め込みだけを参照先の本文の引用にした（表示用の解決と同じ材料・トピックで公開済みの参照だけ）。

## 一般化

参照記法の除去で文が欠ける型。確かめ方: 文中・行単独の両方の埋め込みを持つ教材で、文中だけが本文に置き換わり、行単独は消えることを見る。
