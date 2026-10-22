---
id: IK-0809
title: "equation_semantics の display 式の要求には役割付き span の一覧が無く linked_text_spans が常に空になる（inline 候補の要求にはある）。指示「TeX をそのまま式テキストに」に対応する出力欄もスキーマに無い"
status: resolved
recorded_at: 2026-10-17
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "式の意味役割を判定し、その式を導入・説明する本文の span に結びつける"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information, contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認済み（2026-10-17）: equation_semantics の build_llm_inputs は nearby_span_annotations を式のブロック自身の span からしか組まず、display 式（equation_block には span が無い）では常に空だった。inline 候補は本文の段落そのものが式のブロックなので埋まっていた。span_id はブロックごとに振り直されるので、block_id なしでは前後のブロックの span も区別できない。指示「Use the source-backed TeX equation exactly as the equation text」に対応する出力欄はスキーマに無かった（式本文は入力側が保持し、出力の reconstruction には写さない方針）。 以下は起票時の仮説:
    仮説: equation_semantics の input_builder が Nearby Role-Labeled Spans を inline 候補の経路でだけ組み、display 式の経路では
    近傍ブロック ID しか渡さない。出力スキーマは linked_text_spans を持つので、display 式では常に空になる。接続軸 information =
    役割判定の span が display 式の材料に届かない。接続軸 contract = 指示文（source-backed の TeX をそのまま式テキストに）とスキーマの
    欄が対応しない。処理軸 none / 構造軸 none / 統制軸 none。確定には display と inline の 2 経路の input_builder と、指示文の出所を読む。
generalization:
  level: general
  general_form: "同じ判定を 2 経路で組むとき、片方の経路にだけ材料を添えるため、もう片方では出力欄が常に空になる"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 W0 が equation_semantics の display 式（seq 3246）と inline 候補（seq 3267）の材料を比べ、display 側に span_id が無く linked_text_spans を埋められないことに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md）。頭脳 T1（uxsim/runs/radar_loop/BRAIN_NOTES_T1.md seq 2899）と T2（BRAIN_NOTES_T2.md seq 2900）も同じ非対称を記している。"
resolution:
  perspective: [carry_through, explicit_contract]
  note: "display / inline の両経路で、式のブロックと同じ節の直前・直後の本文ブロック（式ブロックは飛ばす）の役割付き span を candidate_text_spans [{span_id, block_id, text, role_labels}] として渡し、prompt に「linked_text_spans may only contain span_id (with its block_id) listed in candidate_text_spans」を 1 文、出力スキーマの項目に block_id を足した。validator restrict_linked_spans_to_candidates が (block_id, span_id) の組で照合して候補外を warning で外し review flag linked_span_not_in_candidates を残す（block_id が無い項目は span_id が候補内で一意なときだけ補って受ける・neighbor_equations と同じ規律）。役割判定を渡さない経路（None）は制限しない。TeX 式の指示は欄の無い「式テキストにそのまま使え」をやめ「原文の TeX として読む（書き換えない）」に改めた。"
  landed_in:
    - "src/episteme_graph/agents/equation_semantics/input_builder.py"
    - "src/episteme_graph/agents/equation_semantics/schema.py"
    - "src/episteme_graph/agents/equation_semantics/prompt.py"
    - "src/episteme_graph/agents/equation_semantics/validator.py"
    - "src/episteme_graph/agents/equation_semantics/agent.py"
    - "src/tests/agents/equation_semantics/test_candidate_text_spans.py"
    - "docs/pipeline/agents.md"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "seq 3246 の式の再解析で linked_text_spans が埋まることの実測"
      - "linked_text_spans の下流の消費者（現状は validator のみ）"
related: [IK-0757, IK-0793]
view_of: []
history: []
---

## 課題

症状: display 式（eq_tex_b39 ほか）の equation_semantics 要求に span_id が無く、linked_text_spans は常に空。inline 候補の要求には Nearby Role-Labeled Spans があり埋められる。
指示「Use the source-backed TeX equation exactly as the equation text」に対応する式本文の出力欄がスキーマに無い。
原因（仮説）: span の材料が inline の経路にだけ組まれ、指示文とスキーマが別々に書かれている。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md（seq 3246・3267）。

追加の出所（頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3247 — display 式の要求で linked_text_spans を求めるのに span_id が無く空で返した）。

追加の出所（頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3245 — display 式の要求で span_id が与えられず linked_text_spans は空）。

## 発見の観点

display と inline の 2 経路の要求の材料と出力スキーマを突き合わせた（data_inspection）。

## 解決の観点

display / inline の両経路で、式のブロックと同じ節の直前・直後の本文ブロック（式ブロックは飛ばす）の役割付き span を candidate_text_spans [{span_id, block_id, text, role_labels}] として渡し、prompt に「linked_text_spans may only contain span_id (with its block_id) listed in candidate_text_spans」を 1 文、出力スキーマの項目に block_id を足した。validator restrict_linked_spans_to_candidates が (block_id, span_id) の組で照合して候補外を warning で外し review flag linked_span_not_in_candidates を残す（block_id が無い項目は span_id が候補内で一意なときだけ補って受ける・neighbor_equations と同じ規律）。役割判定を渡さない経路（None）は制限しない。TeX 式の指示は欄の無い「式テキストにそのまま使え」をやめ「原文の TeX として読む（書き換えない）」に改めた。

解消の確認（reproduction_rerun・頭脳 Y0・再解析 run 40ecf8df）: uxsim/runs/radar_loop/BRAIN_NOTES_Y0.md（seq 3525 — 要求に candidate_text_spans が入っている）。残る観察: 同じ span_001 が別ブロックで 2 つ並ぶので、(block_id, span_id) の組で照合する前提が要る。linked_text_spans が実際に埋まるかは、応答が放棄されたため確かめていない。

## 一般化

片方の経路にだけ材料を添えると、もう片方で出力欄が常に空になる。辞書の型 available-but-unwired。
