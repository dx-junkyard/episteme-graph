---
id: IK-1015
title: "narrative_annotator の入力で主グラフのノードの teaching_takeaways が主語・目的語の欠けた定型文「Derive from via derive_result」になり、theory_object が演算名（define）、concepts に DSL ノード名（結果側の speed of sound softening）がそのまま入る"
status: open
recorded_at: 2026-10-27
resolved_at: null
sources:
  - "docs/pipeline/theory-graph.md"
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "理論操作グラフの主グラフに、読み手向けの narrative 注釈と教育上の要点を付ける"
  layers: [pipeline_a, graph_review]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 主グラフの要点は「{動詞} {入力} from {出力} via {operation}」型の定型から組み立てられ、入力・出力の名前が引けないと空のまま
    文が出る。theory_object も理論対象が引けないときに operation（define）で埋まる。IK-0753 の是正（内部 ID と件数の機械文の除去）の後に
    残った別の形の機械文。観察: 頭脳 A 4228 で main が 2 ノードのみ・N2 の teaching_takeaways が「Derive from via derive_result」・
    N1 の theory_object が 'define'・concepts に結果側の DSL ノード名。処理軸 wording = 要点の文の組み立て。接続軸 information =
    入力・出力の名前が要点の組み立てまで運ばれない。構造軸 none（確信度 low: 受け皿の不足かは未確認）。統制軸 none。
    確定には、component_graph の主グラフの teaching_takeaway / theory_object の組み立て箇所と、空欄のときの分岐を読む。
generalization:
  level: general
  general_form: "定型文の差し込み欄が空でも文を出し、引けなかった名前の代わりに操作名を対象名に入れるため、人向けの要点が意味の無い文になる"
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [data_inspection]
  note: "第 6 周の前の PINN TeX 再解析で、製品頭脳 A が narrative_annotator の入力（4228）の要点・理論対象を所見に残した（出所: uxsim/runs/round25/BRAIN_NOTES_product_A_r6pre.md）。"
resolution:
  perspective: [pending]
  note: "未解決。差し込み欄が空なら要点を出さない（欠けたことを事実で残す）、theory_object に操作名を入れない。"
  landed_in:
    - "docs/pipeline/theory-graph.md"
related: [IK-0753, IK-0571]
view_of: []
history: []
---

## 課題

症状: narrative_annotator が受け取る主グラフの要点が「Derive from via derive_result」、理論対象が「define」で、注釈の材料にならない。

原因（仮説）: 定型の差し込み欄が空でも文を組み、理論対象を操作名で埋める。

出所: uxsim/runs/round25/BRAIN_NOTES_product_A_r6pre.md（4228）。

## 発見の観点

LLM 入力の要点・理論対象を、ノードの式・主張と突き合わせた（data_inspection）。

## 解決の観点

未解決。空欄の定型を出さない・代替の名前を入れない。

## 一般化

引けなかった値の代わりに別の欄の値を入れると、人向けの文が根拠のない文になる。IK-0753（内部 ID の機械文）の後に残った別の形。
