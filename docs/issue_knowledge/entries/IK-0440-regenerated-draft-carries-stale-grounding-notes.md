---
id: IK-0440
title: "コース内容の再生成で、前の生成の注意点「このトピックには図・式・主張が紐づいておらず…」や builder の事実文「論文の解析結果のどの要素にも対応付けられていません」が、根拠の付いたいまのトピックに持ち越される"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 教員が「コース内容を生成」でトピックの授業用ドラフトを作り直す
  layers: [course_builder]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [version]
    governance: [resume]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 下書きの注意点は「そのときの根拠」についての文だが、_topic_existing_draft は前の生成の cautions を
    そのまま「現在の下書き」に渡し、生成モデルが写した（version）。第 9 周の req 00229（t1）/ 00232（t3）/
    00242（t13）/ 00244（t15）/ 00248（t19）は根拠候補に式・主張があるのに、下書きの cautions が「図・式・主張が
    紐づいていない」、00226（t0）は「観測装置名は根拠に無い」だった。_enrich_topics も topic = dict(raw_topic) で
    前の grounding_note / coverage（UNLINKED_TOPIC_GROUNDING_NOTE）を引き継ぎ、根拠が付いた後も残した（書き戻しは
    生成結果に無いキーを live のまま残す）。構造軸: 下書きがどの根拠で作られたかを記録する表現が無く、変わったか
    判定できなかった（representation。medium）。統制軸: 再実行が前の実行の出力を入力に戻す（resume。medium）。
generalization:
  level: general
  general_form: 前回の入力に依存する注記を、入力が変わった再実行の入力として持ち越す
pattern: stale-derivative-served
discovery:
  perspective: [data_inspection]
  note: 第 9 周のコース内容の再生成プロンプトで、根拠候補と「現在の下書き」の注意点が食い違うことを見た。
resolution:
  perspective: [representation_change, guardrail_fix]
  note: >-
    下書きを作ったときの根拠候補の指紋 draft_reference_key（kind:id の整列リスト）をトピックに記録し、次の生成で
    指紋が違う（または記録の無い旧い下書き）なら生成された注意点を「現在の下書き」に渡さない（教員が書いた
    下書きの注意点は渡す）。builder の事実文（GENERATED_DRAFT_NOTES = 定数の集合。自由文は判定しない）は
    注意点・本文の行から外し、_enrich_topics は前の生成の grounding_note / coverage が定数のときだけ空にしてから
    付け直す（setdefault は空を上書きしないので、空のときに書く判定へ変えた）。プロンプトに「前の根拠と食い違う
    注意点は引き継がない」を足した。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0438_0443_course_draft_context.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（第 9 周のコースで t1 / t3 / t13 / t15 の注意点から「紐づいていない」が消えるか）
      - "本文の中にモデルが自由文で書いた注記（「> 注: このトピックの根拠は要約文のみ…」）は外していない（プロンプトの指示だけ）"
related: [IK-0427]
view_of: []
history: []
---

## 課題

前の生成の注意点と builder の事実文が、根拠の変わった再生成に持ち越されていた。

## 発見の観点

生成モデルが受け取った根拠候補と「現在の下書き」の注意点を並べ、食い違いを見た。

## 解決の観点

下書きを作ったときの根拠を記録し、変わっていれば前の注意点を渡さない。builder の文は定数で外す。

## 一般化

前回の入力に依存する注記を再実行の入力に戻すと、入力が変わっても注記が残る。
