---
id: IK-0778
title: "同じ構造の論文の照合で、「照合したが相手なし」と「照合の対象外（単一工程のモジュール）」が同じ「見つかっていません」に畳まれる"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §22"
feature_context:
  realizing: "教員が取り込んだ近い論文と起点の論文の理論モジュールの構造を照合し、関係の有無を読む"
  layers: [paper_radar, graph_review]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
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
    確認（related.py の読み）: 照合は identity_eligible の外枠だけで行うのに、checked_module_keys は外枠の全部を列挙し、
    事実文も相手の有無だけで決まっていた。照合の絞り（判定）と「照合した」の集合（表示）が別の述語で、対象外という情報が
    下流へ渡らない。接続軸 information。処理・構造・統制は none（既存の判定結果のブールで足りた）。
generalization:
  level: general
  general_form: "処理を一部の対象に絞りながら、完了・不在の表示は絞る前の母集合で言うため、「処理したが無い」と「処理していない」が区別できない"
pattern: completion-defined-by-proxy
discovery:
  perspective: [data_inspection, trace_walk]
  note: "深い段の審判が deep-06.json の両側 related（両側保存・相手なし）を読み、外枠の多くが単一工程で照合対象外なのに checked_module_keys が全外枠を含むことを related.py で確かめた。"
resolution:
  perspective: [explicit_contract, carry_through]
  note: "checked_module_keys を照合した外枠だけに縮め、対象外は ineligible_module_keys で別に返す。照合の対象が無ければ FACT_RELATED_NO_ELIGIBLE、混在なら対象外をラベルで名指し（件数なし）。グラフレビューは対象外モジュールに専用の 1 文、レーダーの module_match_note は両側保存でも「対象外 / 照合したが一致なし / 一致あり / 旧規則」を言い分け、照合の範囲（記号・概念は見ていない）を添える。"
  landed_in:
    - backend/core/theory_modules/related.py
    - backend/core/library/structural_matches.py
    - backend/core/paper_discovery/radar.py
    - frontend/public/js/admin-graph-review.js
    - "docs/features/paper_radar_design.md §22"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB の両側保存の組（deep-06）での表示（ブラウザ・砂場の再演）"
related: [IK-0751, IK-0750, IK-0779]
view_of: []
history: []
---

## 課題

症状: 両側とも理論モジュールが保存された組で、related は「このコーパスの中では、同じ構造のモジュールを持つ他の論文は見つかっていません。」
の 1 行だけを返し、レーダーの取り込み済みカードは何も言わなかった。実際には両側とも外枠の多くが「定義」だけの単一工程で、照合の対象に
乗っていなかった。原因: 照合は `identity_eligible` で絞るのに、`checked_module_keys` と事実文はその絞りを反映しなかった。

出所: uxsim/runs/radar_loop/judge-deep-06.md §①-b・§④-1。

## 発見の観点

記録の DTO と照合の実装を突き合わせ、照合の述語と「照合した」の集合の述語が違うことを確かめた（data_inspection / trace_walk）。

## 解決の観点

照合の集合を判定と同じ述語で作り、対象外を別キーと別の文で運んだ（explicit_contract / carry_through）。

## 一般化

完了や不在を言う表示は、処理が実際に通った母集合で言う。確かめ方は、処理の絞り込み条件を列挙し、その条件で外れた対象が表示でどう言われるかを
1 件ずつ見ること。
