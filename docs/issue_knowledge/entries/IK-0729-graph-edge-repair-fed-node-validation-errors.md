---
id: IK-0729
title: "component_graph の辺推定の修復要求に、辺では直せないノード側の検証エラーが渡り、修復の繰り返しが無意味に回る"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/theory-graph.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "理論操作グラフのノードを決定論で作り、辺だけを LLM に推定させ、検証に落ちた辺を修復させる"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [contract]
    governance: [budget]
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
    確定（2026-10-16 にコードで確認・旧記述は仮説）: 修復要求に添える検証エラーを、辺の出力が原因のものに絞らず、グラフ全体の validator の結果（ノードの
    linked_claim_ids 欠落・汎用 operation「limit / parameterize / couple / verify」・derive ノードの式リンク欠落）を
    そのまま渡している。構造軸 responsibility = ノード側の欠陥を直す責務は決定論の normalizer にあるのに、辺だけを返す
    LLM に修復を求めている。接続軸 contract = 修復の出力契約（辺の列）と、要求が指す誤り（ノードの属性）が両立しない。
    統制軸 budget = 直せない誤りで修復回数を使い切る。処理軸 none（validator の判定自体は正しい）。確定には修復要求を
    組み立てるコードで、エラーを辺由来とノード由来に分けているかを確認する。
generalization:
  level: general
  general_form: "検証の誤りを、それを直す権限の無い生成者にまとめて返すため、修復の繰り返しが予算を使うだけで収束しない"
pattern: gate-position-wrong
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 M0 が component_graph の repair 要求（seq 2094・AdS/dS 2PN・26 部品）を読み、検証エラーが全てノード側だったことに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_M0.md）。同じ要求で refiner の重複子と claim_span_001_40_sub01 が全部品の linked_claim に入る事実も見えた（IK-0728）。"
resolution:
  perspective: [responsibility_move, order_and_budget]
  note: "修復ループと修復要求を辺の出力で直せる検証種別（repair.EDGE_REPAIRABLE_RULE_IDS）だけに絞り、ノード側の誤りは修復に回さず normalizer / 降格と最終検証に任せる。直せる誤りが無ければ修復コールを出さない（agent.needs_edge_repair）。旧挙動はノード側の誤りで修復を使い切り、fallback で LLM の辺ごと捨てていた。"
  landed_in:
    - "src/episteme_graph/agents/component_graph/repair.py"
    - "src/episteme_graph/agents/component_graph/agent.py"
    - "src/tests/agents/component_graph/test_repair_scope.py"
    - "docs/pipeline/agents.md"
  verification:
    methods: [guardrail]
    unverified:
      - "再解析での実 run の再演（次の周で確認）"
      - "許可リスト外の新しい辺側の検証種別が足されたときの追随（許可リストの更新が要る）"
related: [IK-0727, IK-0728]
view_of: []
history:
  - date: '2026-10-16'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '原因確定: agent.run がグラフ全体の検証結果の error を修復の契機にし、repair.py もその全件を修復要求に渡していた。辺の誤りに絞った（IK-0763 と同日）。'
---

## 課題

症状: component_graph の repair 要求に載る検証エラーが全てノード側（linked_claim_ids 欠落・汎用 operation・derive ノードの
式リンク欠落）で、辺を返す LLM にはどれも直せない。修復を何度回しても同じエラーが残る。
原因（仮説）: 修復要求がグラフ全体の検証結果をそのまま渡し、辺由来の誤りに絞っていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_M0.md（seq 2094）。

追加の出所（頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2940 — repair 2 回目の validation errors が main_graph_generic_operation・output_node_missing_linked_equations・derive_node_missing_derivation_link・missing_claim_link のノード側だけで、辺を返す LLM には直せない）。

追加の出所（頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2939 — repair 1 回目の指摘 74 行の大半が main_graph_generic_operation・missing_linked_equations・missing_claim_link のノード側）。

## 発見の観点

修復要求の検証エラーと、LLM の出力契約（辺の列）を突き合わせた（data_inspection）。

## 解決の観点

修復の責務を辺の出力で直せる誤りに限定し、ノード側は決定論の後段へ（responsibility_move）。直せる誤りが無いときはコールを出さない（order_and_budget）。

## 一般化

直せない相手に誤りを返す修復は収束しない。辞書の型 gate-position-wrong（検証を回す位置と相手の誤り）。
fallback-fabricates-missing-link とは違い、偽の対応は積まれず予算だけが減る。
