---
id: IK-0347
title: 表本体を body_paragraph として残すようにしたため、役割判定と主張採否の母集合に表のセルが並ぶ
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §7.1
feature_context:
  realizing: 表本体を落とさず保持しつつ、主張の候補には表のセルを並べない
  layers: [pipeline_a]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [condition]
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
    接続軸: 文書構造は表由来であることを raw.in_table として運んでいたが、後段（rhetorical_role / claim_qualification のinput_builder）が block_type だけで対象を選び、その条件を読んでいなかった。処理軸: 入力選別の条件不足。確認手段: rhetorical_role/input_builder.py の _TARGET_BLOCK_TYPES による選別と grobid_parser.py の表本体ブロック。
generalization:
  level: repo_pattern
  general_form: 上流が保持のために情報の種類を広げたとき、下流の選別が種類の印を読まず、広げた分をそのまま処理対象にする
pattern: available-but-unwired
discovery:
  perspective: [adversarial_review, trace_walk]
  note: >-
    第 1 波の文書構造復元（表本体の保持）から役割判定・主張採否まで、block_type の流れを辿った。
resolution:
  perspective: [carry_through, guardrail_fix]
  note: >-
    raw.in_table / from_table を役割判定と主張採否の両 input_builder で除外条件にし、除外した block ID を coverage のdetails に残す（件数は書かない）。
  landed_in:
    - src/episteme_graph/agents/rhetorical_role/input_builder.py
    - src/episteme_graph/agents/claim_qualification/input_builder.py
related: [IK-0334]
view_of: []
history: []
---

## 課題

**症状**: 役割判定と主張採否の母集合に「0.1 | 0.2 | …」のような表本体が入り、LLM 呼び出しも増えた。

**原因**: 表本体を body_paragraph で残す変更に対し、後段の対象選別が provenance（in_table）を読んでいなかった。

## 発見の観点

敵対的レビュー（`adversarial_review`）で block の流れを辿った（`trace_walk`）。

## 解決の観点

上流の印を後段の選別まで運び（`carry_through`）、テストで固定（`guardrail_fix`）。

## 一般化

「落とさない」ための保持は種類の印とセットで行い、下流の選別は印を読む。block_type だけの選別は保持の拡張で壊れる。
