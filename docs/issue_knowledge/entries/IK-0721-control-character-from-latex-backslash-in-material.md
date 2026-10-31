---
id: IK-0721
title: "説明生成の材料（記号定義文）に \\vec の \\v が垂直タブ（\\x0b）に化けた制御文字が混入し、LaTeX のバックスラッシュが JSON のエスケープとして解釈されている"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/architecture/trust_boundary_pdf_input.md"
feature_context:
  realizing: "LLM が返した式の意味・記号定義を JSON として受け取り、後段の説明生成の材料として保存・再提示する"
  layers: [pipeline_a, hierarchical_explanation]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [unknown]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: low
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: LaTeX の \vec が JSON 文字列の中で 1 本のバックスラッシュのまま書かれ、JSON パーサが \v を垂直タブとして解釈した
    （JSON 標準では \v は不正だが、寛容なパーサ・修復処理や Python のリテラル経由で \x0b になる）。どこで入ったか —
    頭脳（LLM 役）の応答 JSON か、製品の保存・再シリアライズの段か — は未確定。処理軸 input_handling = LaTeX を含む文字列の
    エスケープの扱いが不良。接続軸 unknown = 段階間（応答 → 保存 → 材料）のどこで化けたかを見ていない。構造軸 none / 統制軸 none。
    確定には、eq_conjugate_momentumExp の equation_semantics の生応答（mailbox の res）と保存された artifact の該当文字列を比べる。
generalization:
  level: general
  general_form: "バックスラッシュを含む記法の文字列が、エスケープの解釈を通る経路で制御文字に化け、黙って後段に残る"
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 L0 が contextual_explanation（seq 2004）の材料を読み、記号定義文の \\vec が制御文字に化けていることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_L0.md と uxsim/runs/radar_loop/brainL0_2004.json）。"
resolution:
  perspective: [pending]
  note: "未解決。single_shot の LaTeX バックスラッシュ修復（opt-in）が equation_semantics の受け取り経路に効いているかを確かめ、保存前に制御文字を検出して（strip_control_sequences で消すのではなく）エスケープ崩れとして扱う。"
  landed_in:
    - "docs/pipeline/agents.md"
related: []
view_of: []
history: []
---

## 課題

症状: contextual_explanation の材料で eq_conjugate_momentumExp の記号定義文に制御文字 `\x0b` が混入（`\vec` の `\v` が垂直タブ化）。
原因（仮説）: LaTeX のバックスラッシュが JSON エスケープとして解釈された。頭脳の応答か製品の保存かは未確定。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_L0.md（seq 2004）。

## 発見の観点

実際の材料の文字列を読んだ（data_inspection）。

## 解決の観点

未解決。化けた段を特定し、境界で検出して止める。

## 一般化

エスケープの意味を持つ文字を含む記法は、文字列の契約を境界で検査しないと制御文字に化けて残る。辞書の型 unchecked-type-contract-at-boundary。
