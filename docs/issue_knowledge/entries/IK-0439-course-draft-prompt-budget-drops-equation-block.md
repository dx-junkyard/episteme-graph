---
id: IK-0439
title: "コース内容の生成プロンプトの文字数予算で、根拠候補の content_blocks 末尾の equations ブロックが丸ごと落ち、トピックの式が参照一覧の ID だけになる"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、トピックの根拠候補を文字数予算の中で生成モデルへ渡す
  layers: [course_builder]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [budget]
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
    統制軸: _prompt_json の間引きは「要素数の多いリストの末尾要素を落とす」で、根拠候補の中身の優先度を
    知らない（budget）。処理軸: content_blocks の最後の要素が equations ブロックなので、予算を超えると
    式のブロックが先に丸ごと落ち、重複した content（要約と式の行の再掲）や teaching_takeaways（部品の
    再掲）は残った（logic）。第 9 周の req 00236（t7）は式 6 件が参照一覧にあるのに content_blocks に
    equations ブロックが無く、00240（t11）も同じ。接続軸: 落ちた式は参照一覧の ID だけで後段（生成モデル）に
    本体が届かない（information。medium: 事実文 _omitted は付いていた）。
generalization:
  level: general
  general_form: 予算で要素を落とす順序を並びの位置で決め、中身の重要度と重複を見ない
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: 第 9 周のコース内容の生成プロンプト（req 00236 / 00240）の根拠候補で、式の参照があるのに式のブロックが無いことを見た。
resolution:
  perspective: [order_and_budget, guardrail_fix]
  note: >-
    根拠候補だけ優先順位つきで間引く（_evidence_prompt_json）。先に重複する材料（content / teaching_takeaways /
    source_evidence_ids）→ 原文抜粋を 200 字 → 参照一覧の主張の本文を 60 字 → トピックに結びつかない
    チャンク由来の式、の順で減らし、トピックに結びついた式（linked_equation_ids）・下書きが埋め込む式・部品が
    参照する式とその入れ物は汎用の間引きでも最後まで落とさない（_prompt_json の keep_predicate）。式の本体が
    200 字以内なら参照一覧にも本体を載せるので、ブロックが落ちても ID だけにはならない。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0438_0443_course_draft_context.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（第 9 周の t7 / t11 で equations ブロックがプロンプトに残るか）
      - 200 字を超える式の本体は参照一覧に載せないので、最後の手段で式のブロックまで落ちたときは ID だけになる
related: [IK-0379, IK-0429]
view_of: []
history: []
---

## 課題

予算を超えると、根拠候補の末尾にある式のブロックが丸ごと落ちていた。

## 発見の観点

生成モデルが受け取った根拠候補で、式の参照があるのに式の本体が無いことを見た。

## 解決の観点

重複した材料と長文を先に減らし、トピックに結びついた式は最後まで残す。

## 一般化

落とす順序を並びの位置で決めると、後ろに置いた重要な材料から消える。
