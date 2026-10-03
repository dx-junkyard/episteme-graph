---
id: IK-0627
title: "コード生成の資料が式の定義記号のレコードを文字列化して渡すため、記号の意味が辞書の表記のまま切れて LLM に届き、記号表の内部 ID も混ざる"
status: open
recorded_at: 2026-10-03
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "理論モジュールの式と記号の意味を資料にして、LLM に理論プログラムを生成させる"
  layers: [simulation_bridge_sb, pipeline_a]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [contract]
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
    確認: backend/core/simulation_bridge/codegen.py の _str_list は要素を str(v) で文字列化し、defined_symbols の要素が辞書（記号・意味・記号表 ID を持つレコード）だと Python の辞書表記のまま一定字数で切って資料に入れる。製品側の頭脳メモで、意味が None のまま・記号表の内部 ID 入りで渡った例を確認した。接続軸 contract = 前段（記号表）の値の形が文字列の列という後段の想定と合わず、境界で検査されない。処理軸 input_handling = 形の異なる値を文字列化で押し通す。構造軸 none。統制軸 none。
generalization:
  level: general
  general_form: "境界で値の形（文字列か構造化レコードか）を検査せず文字列化で押し通し、構造化レコードが表記のまま切れて下流に届く"
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 21 周の製品側の頭脳（コード生成の LLM 役）が受け取った資料に、記号の意味が辞書表記で切れて入っていた。資料の組み立てを読んで文字列化を確認した。"
resolution:
  perspective: [pending]
  note: "部分着地（第 21 波の後の追補・2026-10-03）: codegen._output_symbols が論文層の定義記号から名前だけを取るようにし、学習者に出る重なりの事実文から辞書表記を消した。LLM へ渡す資料の側で記号レコードを文字列化して素通しする箇所は直していないため open のまま。"
  landed_in:
    - backend/core/simulation_bridge/codegen.py
    - "docs/features/simulation_bridge_design.md §12.4"
related: [IK-0615, IK-0617]
view_of: []
history: []
---

## 課題

症状: 生成の資料で、記号の意味が辞書の表記の断片になり、意味が空のものや記号表の内部 ID が混ざる。生成されるコードの記号の扱いが推測頼みになる。原因: 定義記号の要素がレコードのとき、資料の組み立てが形を見ずに文字列化する。

## 発見の観点

ペルソナの通し受講で製品の LLM 役が受け取った資料を読み（data_inspection）、組み立ての関数を確かめた（reproduction の過程で発見）。

## 解決の観点

部分着地。事実文側では名前だけを取るようにしたが、プロンプトへ渡す資料の文字列化は残る。

## 一般化

文字列化は形の違いを黙って吸収する。境界で形を確かめないと、構造化された値が壊れた表記で届く。型は unchecked-type-contract-at-boundary。確かめ方は、定義記号がレコードの式で資料を組み、資料に辞書表記・内部 ID が現れないことを見ること。
