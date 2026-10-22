---
id: IK-0704
title: "論文レーダーで近さの根拠（着地・重なり・新しい面）が全候補で空でも、空である理由（分野の地図が無い・索引が無い・承認済み部品が無い）が画面に出ない"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §19"
  - "docs/features/paper_radar_design.md §19.1"
feature_context:
  realizing: "近い論文の候補ごとに、なぜ近いと言えるかの根拠を教員に示す"
  layers: [paper_radar, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（検索応答の DTO と描画コード）: 縮退の事実は DTO の relation_context.available や空のキーフレーズ候補から分かったが、
    それを利用者向けの事実文にして画面へ届ける経路が無かった。接続軸 information = サーバ側に在る縮退の情報が画面の段で落ちる。
    処理軸 none = 根拠チップの算出は正しく空を返していた。構造軸 none = 事実文の置き場（facts キー）は additive に足せた。
    統制軸 none = 判断の割り当てや完了の定義の問題ではない（黙って縮退しないという原則は既にあり、適用の配線漏れ）。
generalization:
  level: general
  general_form: "縮退の事実が内部の状態として分かっているのに、利用者に届く文への配線が無く、空欄が「該当なし」と読まれる"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection, invariant_audit]
  note: "自律周回の検索記録で全候補の根拠欄が空なのに理由が無いことを読み、黙って縮退しないという原則と突き合わせた（出所: uxsim/runs/radar_loop/judge-scan-01.md §4・judge-scan-02.md §4）。"
resolution:
  perspective: [carry_through, guardrail_fix]
  note: "検索結果に facts（事実文・数値なし）を足し、地図が無い・ベクトル索引が無い・承認済み部品が無いの 3 条件ごとに文を出す。何も欠けていなければキーを付けない。画面は候補一覧の上にサーバ文言を素通しする。"
  landed_in:
    - backend/core/paper_discovery/radar.py
    - frontend/public/js/admin-paper-radar.js
    - "docs/features/paper_radar_design.md §19"
  verification:
    methods: [guardrail]
    unverified:
      - "ブラウザでの事実文の表示"
      - "実機の検索で各条件の文が出ること"
related: [IK-0703]
view_of: []
history: []
---

## 課題

症状: 一致キーフレーズ・重なり・着地・新しい面が全候補で空なのに、画面は空欄のままで、教員は「近い根拠が無い」のか
「根拠を測れなかった」のかを区別できない。
原因: 起点の論文に分野の地図が無い・凍結版のベクトル索引が無い・承認済み部品が無いという縮退の事実は応答から分かるが、
事実文として画面へ届ける経路が無かった。

出所: uxsim/runs/radar_loop/judge-scan-01.md §4 仮説 3・judge-scan-02.md §4 の 2。

## 発見の観点

実データの応答を読み（data_inspection）、黙って縮退しないという原則と照合した（invariant_audit）。

## 解決の観点

縮退の条件ごとの事実文をサーバで組み、画面まで運んで素通しする（carry_through）。条件ごとに出る・出ないをテストで固定した。

## 一般化

計算結果が空になる理由を持っているのに文にしない構造は、根拠・候補を並べる他の画面でも起きる。型 available-but-unwired。
