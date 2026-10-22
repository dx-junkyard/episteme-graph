---
id: IK-0763
title: "component_graph の辺修復プロンプトが前回出力を 3,000 字で切って JSON として壊れたまま渡すため前回の辺を参照できず、依存先には存在しない部品 ID（comp_018）が入る"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/theory-graph.md"
feature_context:
  realizing: "理論操作グラフの辺を LLM に推定させ、検証に落ちたら前回出力と誤りを添えて修復させる"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [budget]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定（2026-10-16 にコードで確認・旧記述は仮説）: 修復要求を組み立てるとき、前回出力を文字数で切り詰め（3,000 字）、JSON の構造境界を無視しているため、
    LLM に渡る「前回の辺」が無効な JSON になる。構造軸 representation = 切り詰めた事実と残りの辺を表す受け皿が無く、
    黙って削られる。接続軸 information = 修復の材料（前回の辺の全体）が次の呼び出しに届かない。統制軸 budget =
    材料の欠けた修復で回数を使う（確信度は低い）。あわせて依存先に入力に存在しない部品 ID（comp_018）が現れており、
    出力側で実在 ID を検査していない可能性がある。処理軸 none。確定には修復要求の組み立てコードの切り詰め箇所と、
    依存先 ID の実在検査の有無を確認する。
generalization:
  level: general
  general_form: "修復・再試行に渡す前回の出力を構造を無視して切り詰めるため、修復の材料そのものが壊れ、生成者は前回の結果を参照できないまま作り直す"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 Q2 が component_graph のエッジ修復要求（2 回目・seq 2552・2610.19001・34 辺）を読み、「前回出力」が 3,000 字で切られて無効な JSON になっていること、依存先に存在しない comp_018 が入っていることに気付いた。同じ要求で検証エラーの大半がノード側だった点は IK-0729、inline 値指定の混入は IK-0762。出所: uxsim/runs/radar_loop/BRAIN_NOTES_Q2.md。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: "前回出力は文字数で切らず、収まらないときは辺の配列を先頭から間引いて json.dumps し直し、示した範囲（先頭と末尾の edge_id）と完全な辺の列を返すべきことを注記する（prompt.previous_output_excerpt）。依存先に存在しない部品 ID は辺の validator（unknown_target_component）が修復対象として扱い、部品側の落ち（IK-0765）は別件。"
  landed_in:
    - "src/episteme_graph/agents/component_graph/prompt.py"
    - "src/tests/agents/component_graph/test_repair_scope.py"
    - "docs/pipeline/agents.md"
  verification:
    methods: [guardrail]
    unverified:
      - "再解析での実 run の再演（次の周で確認）"
      - "依存先 ID の欠落の起点（refiner の部品落ち = IK-0765）"
related: [IK-0729, IK-0728, IK-0762, IK-0765]
view_of: []
history:
  - date: '2026-10-16'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '原因確定: prompt.build_repair_messages が json.dumps(previous_output)[:3000] で JSON 文字列の途中から切っていた。構造単位で間引く形に替えた。'
---

## 課題

症状: component_graph の辺修復プロンプトの「前回出力」が 3,000 字で切られ、無効な JSON のまま LLM に渡る。前回の辺を
参照できないまま修復が回る。依存先に入力に存在しない部品 ID（comp_018）が現れる。
原因（仮説）: 修復要求の組み立てで前回出力を文字数で切り詰め、構造境界を保っていない。依存先 ID の実在検査が無い。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_Q2.md（seq 2552）。

追加の出所（頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2940 — 辺修復の「Previous output」が 3000 字で JSON 文字列の途中から切れて提示される）。

追加の出所（頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2939 — Previous output が 3000 字で JSON の途中で切れ、edge 0009 以降が見えず指摘された component_edge_0031 を特定できない）。

## 発見の観点

修復要求に載った前回出力と、出力スキーマ・入力の部品 ID を突き合わせた（data_inspection）。

## 解決の観点

前回出力の表現を「途中で切れた文字列」から「間引いた有効な JSON + 範囲の注記」に変えた（representation_change）。

## 一般化

修復の材料が黙って削られる。辞書の型 information-dropped-as-unrepresentable。IK-0729（辺では直せないノード側の誤りを
返す）とは別の害で、本件は修復に渡す材料自体が壊れている。
