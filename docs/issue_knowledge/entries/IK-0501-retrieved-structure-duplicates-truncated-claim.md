---
id: IK-0501
title: "学習チャットに渡す「検索で当たった箇所の構造」で、同じ出典に同じ引用文の全文と途中で切れた版が別の主張として並び、同じことを 2 度言う事実文になっていた"
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17.10
  - docs/features/knowledge_transfer_design.md §5
feature_context:
  realizing: "学習者の質問に答えるとき、採用した出典の箇所に結ばれた主張を事実として添える"
  layers: [screen_adapter_sa, knowledge_objects]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [unknown]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: low
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 同じ命題が本文の違う 2 つの live 主張行（全文と途中で切れた版）として保存されている。主張の同一性キーの
    材料は正規化した本文なので、切れた版は別の主張になり、表示面は行をそのまま並べていた（構造軸 representation
    — 何を 1 つの主張と数えるかが本文の完全一致でしか決まっていない）。確認すべきこと: 砂場 DB で当該 chunk の
    主張行を読み、切れた版が span 行か claim object 行か、上流（役割判定の span 切り出しか主張の書き直し）の
    どこで切れたかを見る。接続軸 unknown: 前段が切れた本文を作る経路を見ていない。処理軸 medium: 表示面の
    並べ方自体は仕様どおりで、判定の誤りではないと読んだ。統制軸: 順序・予算・完了判定は関係しない。
generalization:
  level: repo_pattern
  general_form: "同じ内容の全文と切り詰め版が別の対象として保存され、表示面が本文の包含関係を見ずに両方を並べる"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: "ペルソナ通し受講の対話記録で、1 つの出典に同じ引用文の全文と途中で切れた版が並んでいたことを読んだ。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    表示面で畳む: 同じ出典の主張のうち、本文（空白を畳み casefold・末尾の句読点と省略記号を除く）が他の主張に
    丸ごと含まれるものは外し、含む側（長い方）を先に出た位置に残す（resolvers/learning.py の _dedupe_claims）。
    主張の行は消さない。上流で 2 行になる原因は仮説のまま残す。
  landed_in:
    - backend/core/assistant_context/resolvers/learning.py
    - backend/tests/test_retrieved_structure_core.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（修正後に学習チャットを流し直していない）"
      - "2 行になる上流の原因（どの段で本文が切れたか）"
      - "atomic 子主張のように、親の本文に含まれる別の命題も畳まれる。事実文の重複を避ける目的では許容と判断したが、子の方が学習上有用な場面は見ていない"
related: [IK-0500]
view_of: []
history: []
---

## 課題

学習チャットに渡す「検索で当たった箇所の構造」で、同じ出典に同じ引用文の全文と途中で切れた版が別の主張として並び、同じことを 2 度言う事実文になっていた。

## 発見の観点

ペルソナ通し受講の対話記録で、1 つの出典に全文と途中で切れた版が並んでいたことを読んだ。

## 解決の観点

表示面で本文の包含関係を見て、含まれる側を外し長い方を残す。上流の原因は仮説のまま。

## 一般化

同じ内容の全文と切り詰め版が別の対象として保存され、表示面が本文の包含関係を見ずに両方を並べる。
