---
id: IK-0486
title: "「重要な数式」の行が「- 27: 」のように本体が空のまま並び、本体の長い式（200 字超）は参照一覧の text が空、本体がどこにも無い式も参照一覧に載っていた"
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、トピックの式を「重要な数式」の行と参照一覧の text として授業用ドラフトの材料に書く
  layers: [course_builder]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
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
    処理軸・接続軸: _compose_topic_content は「重要な数式」の値に latex だけを書き、LaTeX の無い式（原文だけの式）は値が空の行になった。_reference_text は本体が 200 字を超えると text を空にし（LaTeX を途中で切れないため）、読み（plain_text）への代替が無かった。本体がどこにも無い式も一覧に載り、「埋め込む前に text で確かめる」ができなかった。表せる情報（原文・読み）が表せないもの（長い LaTeX）と一緒に落ちていた。
generalization:
  level: repo_pattern
  general_form: 表示に使う一番目の値が無い・長すぎるとき、次の値へ代替せずに空を書く
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: 再生成プロンプトの content の「重要な数式」と、参照一覧で text の無い式を数え、content_blocks の同じ式の項目と突き合わせた。
resolution:
  perspective: [single_point_fix]
  note: >-
    _compose_topic_content は latex → plain_text → raw_text の順に本体を書き、本体の無い式は行にしない（行が1つも無ければ見出しも書かない）。プロンプトの写し（_drop_equation_lines）は保存済みの空の行を content_blocks の本体で埋め、埋められなければ外す。_reference_text は長い式に読み（plain_text、無ければリンクの summary）を短く切って渡す。本体も summary も無い式は参照一覧に載せない。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0484_0489_course_draft_regen12.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成
      - 保存済みの topic.content（配信側）の空の行は再生成まで残る
      - 本体の無い式が配信側（chunks.formulas）では解決できる場合でも、生成の一覧からは外れる
related: [IK-0459, IK-0438, IK-0429]
view_of: []
history: []
---

## 課題

式の行と参照の本体が空のまま渡っていた。

## 発見の観点

「重要な数式」と参照一覧の空の値を content_blocks と突き合わせた。

## 解決の観点

本体を latex → 読み → 原文の順に代替し、何も無いものは載せない。

## 一般化

表示の一番目の値が使えないときは次の値へ代替し、空を書かない。
