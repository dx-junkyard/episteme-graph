---
id: IK-0709
title: "論文レーダーの AI 比較のプロンプトが「下の部品リスト」に言及するが、承認済み部品ゼロの起点ではリストが本文に無い"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §18"
  - "docs/features/paper_radar_design.md §12"
feature_context:
  realizing: "起点の承認済み部品ラベルを閉世界のリストとして示し、AI に重なりを答えさせる"
  layers: [paper_discovery, paper_radar]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（LLM に渡された比較の入力）: 指示文は部品リストがある前提で固定され、リストの区画は部品があるときだけ組まれる。部品ゼロの
    起点では指示文だけが残り、存在しない区画を指す。接続軸 contract = 指示文（前段）と入力区画（後段）の片方だけが条件で変わり、
    両者が両立しない。処理軸 none = 重なり 0 件の応答自体は妥当。構造軸 none = 文言の出し分けで足りる。統制軸 none = 手続の問題ではない。
generalization:
  level: general
  general_form: "条件で省かれる入力区画を、固定の指示文が常に参照し、入力が無いときに存在しない区画を指す"
pattern: contract-changed-one-side
discovery:
  perspective: [data_inspection]
  note: "自律周回で LLM を肩代わりした頭脳が、比較の入力に部品リストへの言及があるのにリストが無いことに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_C1.md の 721 番の応答）。"
resolution:
  perspective: [explicit_contract]
  note: "部品ラベルが無いときは指示文から部品リストへの言及を外し、「承認済みの理論部品はまだ無いので overlaps は空配列・component_label を捏造しない」と明示する（compare.build_prompt）。題名がファイル名形なら写しの arXiv 題名を渡す（seed_prompt_title）。LLM の呼び出し回数は増やさない。"
  landed_in:
    - backend/core/paper_discovery/compare.py
    - backend/tests/test_paper_radar_compare.py
  verification:
    methods: [guardrail]
    unverified: ["実機での比較の再実行（部品ゼロの起点）", "ブラウザ表示"]
related: [IK-0708, IK-0703]
view_of: []
history: []
---

## 課題

症状: 承認済み部品ゼロの起点で比較を走らせると、プロンプトが「下の部品リスト」に言及するのに本文にリストが無い。応答は重なり
を空で返したが、存在しない区画への言及は LLM の解釈を不安定にする。
原因: 指示文は固定で、部品リストの区画だけが部品の有無で出し分けられていた。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_C1.md（721 番の discovery:compare の応答）。

## 発見の観点

LLM に渡った実際の入力を読んだ（data_inspection）。

## 解決の観点

指示文を入力区画と同じ条件で出し分けた（部品ゼロなら言及を外し、空配列を明示）。LLM 呼び出しは増えない。

## 一般化

条件付きで省かれる入力を固定の指示文が参照する形は、grounding の区画を出し分ける他の AI 対話でも起きる。型 contract-changed-one-side。
