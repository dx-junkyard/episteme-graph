---
id: IK-0903
title: "反実仮想の「観測を仮に倒す」の選択肢がコースの教材全体の観測系主張から作られ、いま開いているグラフの論文に絞られず論文名も付かないため、Cepheus を開いて暗黒エネルギー論文の観測を、暗黒エネルギー論文を開いて Cepheus の観測を倒してしまい、依存範囲は前提を倒したときと変わらない（グラフに無い主張を倒した事実も出ない）"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/architecture/persona_enactment_testing_design.md §22.8"
  - "docs/features/stakes_ledger_design.md"
  - "docs/features/doubt_layer_issues.md"
feature_context:
  realizing: "教員が原稿スタジオのグラフで観測を仮に倒し、その論文の主張がどこまで揺らぐかを見る"
  layers: [stakes_ledger_sl, doubt_d, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 target: doubt-atlas.js::renderObservationToggleForm は GET /api/admin/doubt/courses/{course_id}/observation-targets を呼び、routes/doubt.py はコースの教材の document 集合（IK-0852）で observation_claim_targets
    を返す。反実仮想の状態は cfState.documentId（開いているグラフの論文）を持っているのに、選択肢の取得にその document を渡さず、選択肢に論文名も載せない。compute は document_id のグラフで伝播するので、別の論文の主張を倒しても範囲は変わらない。処理軸・構造軸 none（範囲の正本・グラフの解決は正しい）。
generalization:
  level: general
  general_form: "画面が既に 1 つの対象に絞られているのに、その中の選択肢を親の範囲全体から取り、出所も付けずに並べるため、利用者は対象の外の項目を選んでしまい、結果に何も起きないことにも気づけない"
pattern: scope-widened-silently
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    te-04 が Cepheus のグラフで観測を倒すと『C_{II}^{ij} denotes the intrinsic-alignment autocorrelation.』（暗黒エネルギー論文）、te-01 が暗黒エネルギー論文で倒すと『These spectral lines were observed at a rest frequency of 330.58
    GHz.』（Cepheus）が選ばれた。選択肢の取得の引数を辿った。run: uxsim/runs/c-r25-doubt-r2/20261022T215750Z-73306（seq34）・uxsim/runs/c-r25-doubt-r2/20261022T220507Z-77990（seq30）。
resolution:
  perspective: [carry_through, fail_closed]
  note: >-
    第 2 波で是正（2026-10-23）。observation-targets に document_id（query）を足し、コースの教材に含まれる論文 1 件に絞る（外は 404）。選択肢の各項目に document_id。画面は cfState.documentId を渡す。反実仮想の試算・保存は、範囲の観測系主張に無い観測を伝播に渡さず、warnings に OBSERVATION_OUTSIDE_SCOPE_WARNING_PREFIX の事実文を足す（主張の agent ID は論文をまたいで同じ番号になりうる）。
  landed_in:
    - "backend/api/routes/doubt.py"
    - "backend/core/doubt/observation_targets.py"
    - "backend/core/doubt/counterfactual.py"
    - "frontend/public/js/doubt-atlas.js"
    - "backend/tests/test_stakes_ledger_api.py"
    - "backend/tests/test_doubt_ledger_document_key_round25.py"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "画面の焦点（開いている論文）を選択肢の範囲の引数として渡す。"
  verification:
    methods: [guardrail]
    unverified:
      - "画面での選択肢の絞り込みはブラウザで未確認"
related: [IK-0852, IK-0854, IK-0622]
view_of: []
history: []
---

## 課題

症状: 反実仮想で「観測を仮に倒す」の一覧に、開いている論文ではない論文の観測の文が混ざり、論文名も付かない。
選んで倒しても依存範囲は前提を倒したときと同じで、倒した観測がこのグラフに無いことも示されない。

原因: 選択肢の取得がコース単位の API で、開いているグラフの document で絞っていない。

## 解決の観点

第 2 波で是正（2026-10-23。frontmatter の resolution）。以下は起票時の是正案。是正案:

- `observation-targets` に optional の `document_id` を足し（コースの教材の document 集合に含まれることを確かめてから
  その 1 件に絞る）、`renderObservationToggleForm` は `cfState.documentId` を渡す。
- 論文に絞れないとき（コース全体から倒す場面を残すなら）は、各選択肢に論文の題名を添える。
- compute は、倒した観測がグラフのどのノードからも参照されていないとき warnings に事実文を足す
  （「この観測はこのグラフの主張から参照されていないため、依存範囲は変わりません」）。IK-0854 の warnings の描画にそのまま乗る。

## 発見の観点

2 人の教員が別の論文で同じ操作をし、互いの論文の観測が出たので、選択肢の取得の範囲を辿った。

## 一般化

画面の焦点（1 論文）と選択肢の範囲（コース全体）がずれると、選んだものが焦点の外にある。確かめ方は、焦点を 2 つ用意して、
選択肢がそれぞれの焦点の中だけになることを見ること。
