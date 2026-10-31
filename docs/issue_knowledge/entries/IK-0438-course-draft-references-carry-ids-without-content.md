---
id: IK-0438
title: "コース内容の生成プロンプトの available_references が ID だけで、主張の本文・図のキャプション・原文の中身が読めず、原文抜粋の「FIG. 1.」には対応する図の参照が無い"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、トピックの授業用ドラフトを作るときに埋め込んでよい根拠を生成モデルへ渡す
  layers: [course_builder]
classification:
  axes:
    processing: [none]
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
    接続軸: _topic_evidence_for_prompt は topic.evidence_links から (kind, id) だけを取り出し、同じ行が持つ
    summary（主張の normalized_text・図のキャプション・原文の evidence_text）を捨てていた（information）。
    第 9 周の req 00229〜00248 の全ドラフトで claim の参照が ID だけ、00236 / 00242 / 00246 の図は ID だけで、
    00248 は原文抜粋に「FIG. 1. Solutions for µ …」があるのに kind='figure' の参照が無かった。生成モデル自身が
    cautions に「埋め込んだ主張の本文はこの根拠候補からは読めない」と書いている（00234 / 00236 / 00238 / 00240）。
    構造軸は none（medium: 参照一覧を「埋め込みの閉世界」だけに使う表現とも読めるが、素材は同じ行にあった）。
generalization:
  level: general
  general_form: 選択肢の一覧から中身を落とし、選ぶ側が ID だけで選ぶことになる
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: 第 9 周のコース内容の生成プロンプト（mailbox req 00229〜00248）の根拠候補を読んだ。
resolution:
  perspective: [carry_through, guardrail_fix]
  note: >-
    available_references の各項目に短い本文 text を添えた（主張・原文 = summary の 120 字、図 = キャプションの
    120 字、式 = 本体が 200 字以内のときだけ本体、原文抜粋 topic_summary = 冒頭 120 字。長い TeX は切らずに
    載せない）。原文抜粋に図の見出しがあるのに図が供給されていないときは figures_note に事実文
    （SOURCE_FIGURE_NOT_PROVIDED_NOTE）を置き、プロンプトに text / figures_note の読み方を足した。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0438_0443_course_draft_context.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（第 9 周のコースで、次のプロンプトの参照一覧に本文が載るか・生成モデルの「主張の本文が読めない」注意点が消えるか）
      - 原文抜粋に現れる図を供給する経路（図の ID を原文抜粋から引く）は作っていない。供給されていない事実を書くだけ
related: [IK-0379]
view_of: []
history: []
---

## 課題

埋め込んでよい根拠の一覧が ID だけで、生成モデルは中身を知らずに埋め込んでいた。

## 発見の観点

生成モデルが受け取った根拠候補を読み、同じ行に本文があるのに一覧へ渡っていないことを見た。

## 解決の観点

一覧の各項目に短い本文を運び、供給されていない図は事実として書く。

## 一般化

選択肢の一覧から中身を落とすと、選ぶ側は ID の字面だけで選ぶ。
