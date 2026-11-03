---
id: IK-0720
title: "論文中に再掲された同じ式（同じ解析接続の式が 3 箇所）が別ブロックの別要素として扱われ、それぞれ別々に説明が生成される"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/features/hierarchical_context_explanation_design.md"
  - "docs/features/url_material_upload_design.md"
feature_context:
  realizing: "TeX から取り出した式を要素として登録し、要素ごとに意味役割と文脈的な説明を付ける"
  layers: [pipeline_a, hierarchical_explanation]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [budget]
  axis_confidence:
    processing: medium
    structure: medium
    connection: low
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 式の同一性がブロック位置（eq_tex_bN）で決まり、本文中で再掲された同じ式（eq_tex_b425 / eq_tex_b456 / eq_tex_b570 の
    解析接続の式）を同じものと数える表現が無い。TeX の別名環境（\be 等のマクロ）や再掲を内容で突き合わせていない。
    構造軸 representation = 「同じ式」を表す内容由来の同一性が式の要素に無い。統制軸 budget = 同じ式に説明生成の LLM 呼び出しが
    重複して消費される（確信度 low: 回数の実測はしていない）。接続軸 none（確信度 low: 説明どうしの食い違いは見ていない）。
    処理軸 none。確定には、該当 3 ブロックの TeX 原文を比べて同一の式か（再掲か、記号の違う別式か）と、式の要素化で
    内容の正規化キーを持っているかを確認する。
generalization:
  level: general
  general_form: "同じ内容の対象が出現位置ごとに別物として採番され、処理と生成が重複する"
pattern: id-unique-only-within-inner-scope
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 L0 が contextual_explanation（seq 2016・2022）を応答する際、同じ解析接続の式が別要素として 3 度目に出てきたことに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_L0.md）。"
resolution:
  perspective: [pending]
  note: "未解決。式の正規化した内容から同一性キーを作り、再掲を同一の要素の別の出現として束ねる（出現位置は残す）か、説明生成の段で同じ式の要素を 1 つにまとめる。"
  landed_in:
    - "docs/pipeline/agents.md"
related: []
view_of: []
history: []
---

## 課題

症状: 同じ解析接続の式が eq_tex_b425 / eq_tex_b456 / eq_tex_b570 の 3 要素になり、それぞれ別々に説明される。
原因（仮説）: 式の同一性が出現位置で決まり、再掲や TeX の別名環境を内容で突き合わせていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_L0.md（seq 2016・2022）。

## 発見の観点

実際の説明要求の材料を読んだ（data_inspection）。

## 解決の観点

未解決。式の内容由来の同一性で出現を束ねる。

## 一般化

位置で採番された対象は再掲を別物と数える。辞書の型 id-unique-only-within-inner-scope（局所の位置を同一性に流用）に最も近い。
