---
id: IK-0708
title: "論文レーダーの AI 比較で、起点と無関係な候補でも共通点が「理論と観測を結ぶ」のような一般論で毎回埋まり、近くないと言う出口が無い"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §18"
  - "docs/features/paper_radar_design.md §19"
feature_context:
  realizing: "起点と候補の要旨を AI に比べさせ、共通点と差分を仮説として示す"
  layers: [paper_radar, paper_discovery]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
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
    確認（比較の structured output の契約と応答）: 共通点 1 文が事実上必須で、「要旨から目立った共通点は読み取れない」と書く選択肢が
    無かった。接続軸 meaning = 一般論で埋めた共通点が後段（画面・読者）では「共通点あり」と読まれ、代替であることが伝わらない。
    処理軸 logic = 出力の検証が共通点の具体性を見ておらず、一般論を通していた。構造軸 none = 関係の種類を足すのは既存の DTO への
    additive な追加。統制軸 none = 仮説文体と注記は守られており、手続の問題ではない。
generalization:
  level: general
  general_form: "対応が無いときに必須欄を一般的な文で埋め、代替であることが後段に伝わらず根拠のある対応として読まれる"
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [data_inspection]
  note: "B 中間子崩壊・ホログラフィーなど起点と無関係な候補でも共通点が毎回埋まり、重なりは全件空だったことを比較結果で読んだ（出所: uxsim/runs/radar_loop/judge-scan-01.md §4 仮説 5・judge-scan-02.md §4 の 5）。"
resolution:
  perspective: [fail_closed, explicit_contract]
  note: "各候補に関係の種類（同じ対象・同じ方法・隣り合う話題・目立った共通点なし）を足し、共通点が無ければ空にする出口を作る。共通点が部品ラベルにも要旨の具体語にも当たらなければ落として隣り合う話題へ降格する（項目は保持）。画面は関係のラベルを素通しする。"
  landed_in:
    - backend/core/paper_discovery/compare.py
    - frontend/public/js/admin-paper-radar.js
    - "docs/features/paper_radar_design.md §18"
  verification:
    methods: [guardrail]
    unverified:
      - "実機の比較で無関係な候補が「目立った共通点なし」になること"
      - "漢字だけで書かれた具体的な共通点の誤降格"
related: [IK-0709]
view_of: []
history: []
---

## 課題

症状: 起点と主題が無関係な候補でも「共通点」が一般論で毎回埋まり、論文に書かれていない近さを書いているように読まれる。
原因: 比較の出力契約が共通点 1 文を事実上必須とし、空を空と言う選択肢が無く、検証も共通点の具体性を見ていなかった。

出所: uxsim/runs/radar_loop/judge-scan-01.md §4 仮説 5・judge-scan-02.md §4 の 5。

## 発見の観点

実データの比較結果を候補の主題と並べて読んだ（data_inspection）。

## 解決の観点

埋めない出口を作り（fail_closed）、関係の種類を契約として持たせた（explicit_contract）。LLM の呼び出し回数・注記・数値非表示は不変。

## 一般化

LLM の構造化出力で「必ず何か書く」欄は、対応が無いときに一般論で埋まる。型 fallback-fabricates-missing-link。
