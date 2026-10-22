---
id: IK-0787
title: "近い構造の対が共有する型の数とファイル内の順だけで並び、どの論文にもある工程の共有と題名がファイル名のままの相手が先頭に来る"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §22.4"
feature_context:
  realizing: "理論モジュールの工程の型が近い他論文のモジュールを、上限付きで列挙して教員に読ませる"
  layers: [graph_review, paper_radar]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（related.py の読み・deep-07.json）: 並びのキーが (共有する型の数, 起点の順, 相手の題名) で、どの論文にも現れる型（定義・結果を導出）だけの
    共有と珍しい型の共有を区別せず、上限 5 で切るため珍しい対が落ちた（処理軸 logic）。題名は documents.title をそのまま使い、レーダーが
    既に持つ「ファイル名なら arXiv の写しの題名」の規則が届いていなかった（接続軸 information）。構造・統制は none。
generalization:
  level: general
  general_form: "上限付きの列挙の並びに、項目の情報量（どれだけ珍しい共通点か）を使わず、件数と既定の順だけで切るため、ありふれた一致が先頭を占める"
pattern: completion-defined-by-proxy
discovery:
  perspective: [data_inspection, symptom_report]
  note: "深い段の審判が deep-07.json の候補側 near_structure の先頭に、比較で共通点なしと判定済みの論文（題名 arXiv-2610.20589v1）との対があることを見つけた。"
resolution:
  perspective: [explicit_contract, carry_through]
  note: "並びを 共有する汎用でない型の数 → 型の珍しさ（型を外枠に持つ論文数の逆数の和・並べ替えにだけ使い出さない）→ 題名 → 起点の順 の決定論ソートにした。題名はレーダーの display_title_for を正本に、arXiv メタデータの写し（API は呼ばない）から引き、引けなければ「題名未取得」を title_note で言う。"
  landed_in:
    - backend/core/theory_modules/related.py
    - backend/core/library/structural_matches.py
    - backend/core/paper_discovery/radar.py
    - frontend/public/js/admin-graph-review.js
    - "docs/features/paper_radar_design.md §22.4"
  verification:
    methods: [guardrail]
    unverified:
      - "実コーパス（deep-07 の組）での並びの再演"
      - "珍しさの重みが主題の近さを表すか（工程の型だけで意味は見ていない）"
related: [IK-0779]
view_of: []
history: []
---

## 課題

症状: 候補側の near_structure の先頭が、主題の離れた論文（題名がファイル名のまま）との対で、共有する工程は「定義・結果を導出」のような
どこにでもある型だった。上限で切られ、起点側から見えた対（T_det ↔ ⟨⋯⟩_FoG）が候補側からは見えなかった。
原因: 並びが共有する型の数と起点の順だけで、型の珍しさを見ない。題名はファイル名形を判定せず documents.title を出していた。

出所: uxsim/runs/radar_loop/judge-deep-07.md ①・④-1。

## 発見の観点

記録の near_structure と比較（compare）の判定を突き合わせた（data_inspection / symptom_report）。

## 解決の観点

並びの規則を明示し、題名の表示規則をレーダーの正本から運んだ（explicit_contract / carry_through）。

## 一般化

上限で切る列挙は、何を先に見せるかの規則に項目の情報量を入れる。ありふれた一致は数が多いほど上位を占める。
