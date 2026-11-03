---
id: IK-0441
title: "同じ学ぶ単位を束ねた 2 つのトピック（「磁場強度の推定」と「確かめられていない点」）の要約が一字一句同じ文になり、コース概要でも授業用ドラフトの材料でも区別されない"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、トピックごとの要約を部品・学ぶ単位から決定論的に組み、コース概要として生成モデルへ渡す
  layers: [course_builder, learning_units]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: トピックの要約（_topic_summary / _units_summary）はトピックが束ねた部品・単位だけから作られ、
    トピックの題名は材料に入らない。2 つのトピックが同じ単位を束ねると、要約は同じ文になる（トピックと単位の
    粒度が 1 対 1 でない。decomposition。medium）。第 9 周の req 00231（t2）と 00233（t4）の前後関係・根拠候補の
    summary が byte 一致した。処理軸は none（medium: 導出規則は仕様どおり動いている）。接続軸は none（medium:
    重複の事実を後段へ運ぶ受け皿が無いとも読める）。
generalization:
  level: general
  general_form: 束ねる側の粒度が束ねられる側と一致しないのに、束ねられる側の材料だけで束ねる側の説明を作る
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: 第 9 周のコース内容の生成プロンプト（req 00231 / 00233）の前後関係を並べた。
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: >-
    _flag_duplicate_topic_summaries が同じ要約を持つトピックの coverage に duplicate_summary_of（相手のトピック ID）と
    事実文 label_vocab.DUPLICATE_TOPIC_SUMMARY_NOTE（件数なし。既に文があれば上書きしない）を残し、現在のセクションに
    summary_shared_with（相手の題名）を渡す。プロンプトには「要約を繰り返さず、題名に沿って書き分ける」を足した。
    要約そのものは書き換えない（決定論の導出のまま）。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0438_0443_course_draft_context.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（t4 の授業用ドラフトが t2 と書き分けられるか・原稿スタジオのカバレッジ欄に事実文が出るか）
      - 要約の文字列が完全一致（空白の正規化のみ）の場合だけで、言い換えの近い要約は判定しない
related: [IK-0388]
view_of: []
history: []
---

## 課題

同じ単位を束ねた 2 つのトピックの要約が同じ文になり、区別されずに渡っていた。

## 発見の観点

生成モデルが受け取った前後関係の要約を並べて比べた。

## 解決の観点

重複を事実として残し、生成モデルに相手の題名を渡して書き分けさせる。

## 一般化

束ねる単位と説明の材料の粒度が違うと、別の項目が同じ説明になる。
