---
id: IK-0761
title: "論文レーダーで取り込んだ論文を次の起点にする連鎖の系譜（元の起点 → トライスペクトル → FoG …）が画面に出ず、第 4 波の「前の起点」1 行も保存されず 1 段だけ"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/features/paper_radar_design.md §21"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "教員が近い論文を取り込んでは次の起点にして探索を続けるとき、いまの起点がどの起点から来たかの系譜を読む"
  layers: [paper_radar, paper_discovery]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
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
    仮説: 「この論文を起点に探す」で開き直したときの前の起点は JS 内の呼び出し履歴だけで保存も送信もしない（§21 D）ため、系譜は 1 段で
    途切れ、モーダルを閉じる・教材行から開き直すと消える。取り込みの事実（どの起点のレーダーから取り込んだか）がサーバ側の起点解決
    （resolve_seed）に渡らない。接続軸 condition = 取り込み時の文脈（起点）が次の起点の画面へ伝わらない。構造軸 none（確信度 low: 系譜を
    持つ列が無いと読むなら representation にも当たる）。処理・統制は none。確定には、取り込みの監査（AUDIT_ENTITY_PAPER_DISCOVERY）の
    metadata に起点の document が記帳されているか、documents.source_url と突き合わせて系譜を辿れるかを確かめる。
generalization:
  level: general
  general_form: "ある操作の結果を次の操作の入力にして連鎖させるとき、前段の文脈をクライアントの一時状態にしか持たないため、連鎖の系譜が 1 段で途切れ、画面を開き直すと消える"
pattern: condition-not-propagated
discovery:
  perspective: [data_inspection]
  note: "浅い段の審判が scan-07 の seed ヘッダと取り込み済みカードを読み、元の起点（尖度の整合関係）から来たこと・前段で取り込んだ運動論の論文がこの起点から取り込まれたことが読めないと記録した（scan-06 仮説 4 の継続。出所: uxsim/runs/radar_loop/judge-scan-07.md §④-5 / judge-deep-06.md）。"
resolution:
  perspective: [pending]
  note: "未解決。候補: 新しい保存を作らず、documents.source_url（取り込みの出所）と取り込みの監査（AUDIT_ENTITY_PAPER_DISCOVERY）から「この論文はどの起点のレーダーから取り込まれたか」を読み時導出し、seed ヘッダに系譜を数段出す（PR1 の非保存・PR4 と両立の可能性）。監査に起点が無ければ記帳を足すかの判断が先。"
  landed_in:
    - "docs/features/paper_radar_design.md §21"
related: [IK-0700]
view_of: []
history: []
---

## 課題

症状: 連鎖 2 回目（起点 = トライスペクトルの full-shape 解析）の seed ヘッダは起点だけを示し、元の起点から来たこと、前段で取り込んだ
論文がこの起点から取り込まれたことが読めない。第 4 波の「前の起点」1 行は JS 内の一時状態で 1 段だけ。
原因（仮説）: 前段の起点を保存も送信もせず、サーバ側の起点解決に取り込みの文脈が渡らない。

出所: uxsim/runs/radar_loop/judge-scan-07.md §④-5。

## 発見の観点

連鎖の周（scan-06 → scan-07）の seed ヘッダと取り込み済みカードを並べ、系譜が辿れないことを確かめた（data_inspection）。

## 解決の観点

未解決。既存の出所（source_url）と監査から読み時導出する案を第一候補とする。

## 一般化

連鎖する操作では、前段の文脈をどこに持つか（一時状態か、既存の記録から導出できるか）を決めておく。確かめ方は、取り込み → 次の起点で
開き直す → モーダルを閉じて教材行から開き直す、の操作列で系譜が保たれるかを見ること。
