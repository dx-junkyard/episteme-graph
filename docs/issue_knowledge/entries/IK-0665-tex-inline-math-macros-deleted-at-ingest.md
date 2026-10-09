---
id: IK-0665
title: "TeX から取り込んだ本文・主張・出典の引用で、インライン数式の記号（\\mu・\\alpha 等）が消え「evidence for > 1」「_ M = 0」のような文になる"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "学習者・教員が TeX 経路の論文の主張（⚓）・出典の引用・教材の抜粋を読む"
  layers: [pipeline_a, frontend_learning_ui, rag_chat]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [information]
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
    確認: tex_archive._clean_text が $…$ の区切りだけを外して中身を残した後、引数なしの制御綴り（\mu・\alpha・\Sigma など）を _COMMAND_RE で削除する。砂場 DB の theory_claims_live と chunks に「We interpret the evidence for > 1」「The growth of _ B is driven…」が入っている。処理軸 input_handling = 入力の正規化で記号を落とす。接続軸 information = 落ちた記号が主張・チャンク・出典の引用・LLM の文脈まで後段全部に届かない。構造軸・統制軸 none。
generalization:
  level: repo_pattern
  general_form: "書式を剥がす正規化が、意味を持つ記号まで書式とみなして消し、後段はどこも元に戻せない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 23 周の砂場で学生 3 人が ⚓ の主張と出典の引用に「^2_CMB 1」「> 1」の欠けた文を見つけ、DB の本文と取り込みの正規化を読み合わせて原因を確かめた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core・A層の入力）: インライン数式は $…$ のまま残すか、記号を Unicode（μ・α）に置き換えてから制御綴りを除く。既存教材は再解析が要る。"
related: [IK-0571, IK-0581]
view_of: []
history: []
---

## 課題

症状: TeX 経路の論文の主張・出典の引用・教材の抜粋で μ・α_B などが消え、文が読めない。AI は「抽出で µ が抜けている」と自分から断る。原因: 取り込みの本文正規化がインライン数式の制御綴りを削除する。

## 発見の観点

ペルソナの通し受講（reproduction）で読めない主張が出て、DB の本文（data_inspection）で取り込み段の欠落と特定した。

## 解決の観点

未解決。正規化で記号を落とさないよう取り込み段を直す（下流の読み替えで補わない）。

## 一般化

上流の正規化で失われた記号は下流のどこでも復元できない。確かめ方は、インライン数式を含む TeX を取り込み、主張・チャンク・出典の引用のすべてに記号が残ることを見ること。
