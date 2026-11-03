---
id: IK-0765
title: "ComponentRefiner が LLM の返した 23 部品のうち 017〜023（結果と診断の群）を component_ids から落とし、依存先に存在しない部品 ID が残る"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/theory-graph.md"
feature_context:
  realizing: "component_assembly の refiner が LLM の部品分割を受け取り、部品の一覧（component_ids）と依存関係として後段のグラフへ渡す"
  layers: [pipeline_a]
classification:
  axes:
    processing: [resource]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認済み（2026-10-17・頭脳 W1 seq 3283/3289 で再発・2610.08751）: 部品を落としていたのは refiner ではなく
    component_graph の入力組立（input_builder の max_components=32 による先頭からの切り詰め）。refiner が前半の親を
    __opN / __rN の子に割って 32 個を埋め、分割されない後半の結果・診断・限界の部品（式を持たない ClaimBundle /
    Diagnostic / Uncertainty 型）が辺候補（component_ids）から黙って外れていた。ノードとしては残るが辺を張れず、
    落とした事実の受け皿も無かった。処理軸は resource（上限）に確定。以下は起票時の仮説:
    仮説: refiner が LLM の返した部品を component_ids に取り込む段で、後半の部品（017〜023 = 降着からカウンター項までの結果と
    診断の群）を黙って落としている（件数上限・検証不合格の除外・切り詰めのいずれか）。落とした事実の受け皿（coverage /
    review_required）が無く（構造軸 representation）、後段のグラフには部品も、落とした事実も届かない（接続軸 information）。
    一方で残った部品の依存先には落とした部品 ID（comp_012 → comp_018）が残り、存在しない ID を指す。処理軸は除外の原因が
    上限か検証かで変わるため確信度は低い。確定には refiner が component_ids を組む箇所の上限・除外条件を確認する。
generalization:
  level: general
  general_form: "生成者が返した要素の一部を取り込み段で黙って捨て、捨てた事実を残さないため、後段では要素が欠け、残った要素は存在しない相手を参照する"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 Q0 が component_graph の辺修復要求（seq 2553・2610.19001）を読み、前段の refiner が部品 017〜023 を component_ids から落としていること（comp_012 の依存先 comp_018 が不在）に気付いた。同じ要求で前回出力の 3,000 字切断（IK-0763）・検証エラーが全てノード側（IK-0729）・同一の巨大な導出ノードの 7 親への複製（IK-0728）も見えた。出所: uxsim/runs/radar_loop/BRAIN_NOTES_Q0.md。"
resolution:
  perspective: [order_and_budget, single_point_fix]
  note: "component_graph の上限内の選び方を、元の親（__ の前）ごとの round-robin に変えた（select_components_within_limit。各親に最低 1 枠・選んだ部品は元の順）。入り切らなかった部品は coverage 形式（reasons=max_components・details.dropped_component_ids）で残し warning を出す。あわせて refiner の同一 step の複製をやめ（IK-0728）、子の数そのものを減らした。式を持たない結果・診断型は refiner の分割対象外のまま残ることをテストで固定。"
  landed_in:
    - "src/episteme_graph/agents/component_graph/input_builder.py"
    - "src/episteme_graph/agents/component_assembly/component_refiner.py"
    - "src/tests/agents/component_assembly/test_refiner_radar_w1_regressions.py"
    - "docs/pipeline/agents.md"
  verification:
    methods: [guardrail]
    unverified:
      - "2610.08751 の再解析で component_graph の component_ids に comp_013〜018 が載り、辺が張られることの実測"
      - "親の数そのものが上限を超える論文（各親 1 枠でも入り切らない場合）の辺の質"
related: [IK-0728, IK-0733, IK-0763]
view_of: []
history:
  - date: '2026-10-17'
    field: classification
    from: processing=[none] cause_status=hypothesis
    to: processing=[resource] cause_status=confirmed
    reason: 原因が refiner の除外ではなく component_graph 入力の件数上限（先頭からの切り詰め）と確定したため
---

## 課題

症状: ComponentRefiner が LLM の返した 23 部品のうち 017〜023（結果と診断の群）を component_ids から落とす。後段のグラフに
これらの部品が無く、残った部品の依存先には存在しない comp_018 が残る。
原因（仮説）: refiner の取り込みで後半の部品が上限・除外条件で黙って捨てられ、捨てた事実を残す受け皿が無い。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_Q0.md（seq 2553）。

同根の再発: uxsim/runs/radar_loop/BRAIN_NOTES_Q1.md（seq 2551）— comp_012 の依存先 comp_018 が component_ids に無い。

追加の出所（2610.08751・頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3289 — component_assembly（seq 3283）が返した 18 部品のうち comp_013〜018（主結果・比較・h 診断・形状パラメータ・限界・残差診断 = ClaimBundle / Diagnostic / Uncertainty 型・式なし）が component_graph の component_ids から消え、見出し主張を直接支える結果の層がグラフに乗らない。応答は置かれていない（後任が先に回答）が、材料の読みは同じ）。

## 発見の観点

refiner の出力（component_ids）と、残った部品の依存先 ID を突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

捨てた部品も捨てた事実も後段に届かない。辞書の型 information-dropped-as-unrepresentable。IK-0728（子部品の不整合・重複）・
IK-0733（headline の主張 ID）と同じ refiner の段の問題で、本件は部品そのものの欠落。
