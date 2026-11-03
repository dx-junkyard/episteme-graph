---
id: IK-0767
title: "分野の地図の cosmology 領域の概念枠が 0（concept_slots_remaining=0）で、landscape_placement がハロー力学の概念ギャップを正しい親の下に置けず、別の領域の下に出す"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "論文を分野の地図に配置し、置けなかった主題をカテゴリギャップ候補として正しい親領域の下に提案する"
  layers: [pipeline_a, field_atlas_s]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: low
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 骨格の領域ごとの概念数に上限（6）があり、満杯の領域（cosmology）は concept_slots_remaining=0 として LLM に
    提示される。プロンプトは満杯の領域に概念ギャップを出す余地を与えないため、LLM は本来の親ではなく枠の残る領域
    （galaxies）の下にギャップを置く。構造軸 representation = 「満杯の領域に属する新概念」を表す受け皿が無く、
    正しい親という情報が落ちる。接続軸 meaning = 親領域の意味がずれた候補として教員レビューに届く（確信度は低い）。
    レビュー側には満杯領域への取り込みを非活性にする規則があるが、生成側で親をすり替えるとその事実が見えない。
    確定には landscape_placement のプロンプトの concept_slots_remaining の扱いと、gap の parent_region_id の検査を確認する。
generalization:
  level: general
  general_form: "置き場の容量が尽きたとき、尽きた事実を残さずに近い別の置き場へ入れさせるため、正しい所属の情報が落ちたまま候補が提示される"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 Q1 が landscape_placement（seq 2569・2610.19001）を読み、cosmology 領域の概念枠が 0 でハロー概念の gap を正しい親に置けず、galaxies の下に置いたことに気付いた。同じ枠 0 は IK-0730（seq 2130）でも観察されている。出所: uxsim/runs/radar_loop/BRAIN_NOTES_Q1.md。"
resolution:
  perspective: [pending]
  note: "未解決。満杯の領域にも親として gap を出せるようにし（取り込みは教員側で非活性のまま）、満杯である事実を候補に残す。親のすり替えは validator で検出する。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0730]
view_of: []
history: []
---

## 課題

症状: 分野の地図の cosmology 領域の概念枠が 0 で、landscape_placement がハロー力学の概念ギャップを本来の親（cosmology）ではなく
galaxies の下に置く。
原因（仮説）: 満杯の領域に gap を出す受け皿が無く、LLM が枠の残る領域へ親をすり替える。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_Q1.md（seq 2569）、uxsim/runs/radar_loop/BRAIN_NOTES_M0.md（seq 2130）。

追加の出所（2609.35757・頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2960 — cosmology の concept_slots_remaining=0 で、cosmology 配下の概念ギャップ「赤方偏移空間歪み」を出しても取り込めない）。

## 発見の観点

landscape_placement の材料の骨格（領域ごとの概念枠）と、出力された gap の親を突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

容量の尽きた置き場の事実が落ち、候補の所属がずれる。辞書の型 information-dropped-as-unrepresentable。IK-0730 は配置の材料の
打ち切り、本件は地図側の構造。
