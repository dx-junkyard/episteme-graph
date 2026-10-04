---
id: IK-0627
title: "コード生成の資料が式の定義記号のレコードを文字列化して渡すため、記号の意味が辞書の表記のまま切れて LLM に届き、記号表の内部 ID も混ざる"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-04
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
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
  perspective: [explicit_contract, representation_change]
  note: "第 22 波: codegen._prompt_material で資料を整形した — 記号は名前だけ（dict は symbol キー）・英語要約は別キー summary・記号表はこの式群に現れる記号だけに絞り kind / unit が不明のキーを落とす・出力の式が無いモジュールには output_note・契約に io_contract.cannot_compute を足して計算できない記号を名指しさせる。service.symbol_rows は名前と normalize_symbol で照合し、一致ゼロで教材全体へ落とさない。手続の式（損失など）は suitability.py が判定する。"
  landed_in:
    - "backend/core/simulation_bridge/codegen.py"
    - "backend/core/simulation_bridge/service.py"
    - "backend/core/simulation_bridge/suitability.py"
    - "docs/features/simulation_bridge_design.md §12.5"
  verification:
    methods: [guardrail, scratch_db, reproduction_rerun]
    unverified:
      - "LLM が cannot_compute を使うかは第 22 周の製品頭脳でだけ見た"
related: [IK-0615, IK-0617]
view_of: []
history:
  - date: 2026-10-04
    field: status
    from: open
    to: resolved
    reason: "第 22 波で資料の組み立てを整形した"
  - date: 2026-10-04
    field: resolution.verification
    from: 記録時の範囲
    to: 第 22 周の再演で更新
    reason: "第 22 周 (b)(c) の再演で GONE"
---

## 課題

症状: 生成の資料で、記号の意味が辞書の表記の断片になり、意味が空のものや記号表の内部 ID が混ざる。生成されるコードの記号の扱いが推測頼みになる。原因: 定義記号の要素がレコードのとき、資料の組み立てが形を見ずに文字列化する。

## 発見の観点

ペルソナの通し受講で製品の LLM 役が受け取った資料を読み（data_inspection）、組み立ての関数を確かめた（reproduction の過程で発見）。

## 解決の観点

部分着地。事実文側では名前だけを取るようにしたが、プロンプトへ渡す資料の文字列化は残る。

第 22 波で解決。資料の側で記号レコードを名前に落とし、記号表を絞り、計算できない記号を契約で名指しさせた。記号表そのものの kind / unit の欠落（A層）は IK-0637 の範囲。

第 22 周の再演: (b)(c) で role= と辞書の表記が消え、記号を名指しした計算不能の文が出た（GONE）。

## 一般化

文字列化は形の違いを黙って吸収する。境界で形を確かめないと、構造化された値が壊れた表記で届く。型は unchecked-type-contract-at-boundary。確かめ方は、定義記号がレコードの式で資料を組み、資料に辞書表記・内部 ID が現れないことを見ること。
