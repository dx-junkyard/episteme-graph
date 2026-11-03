---
id: IK-0635
title: "改訂案が無いとき「作れませんでした」とだけ返し、AI が変更を挙げなかったのか挙げた変更を全て外したのかが分からなかった"
status: resolved
recorded_at: 2026-10-04
resolved_at: 2026-10-04
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "学習者が実験機の配線について改訂案を求め、案が無いときにその理由を読む"
  layers: [simulation_bridge_sb]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: backend/core/simulation_bridge/revision.py は提案が空のとき、AI が何も挙げなかった場合と、挙げた変更を目録外・範囲外・規約違反で全て外した場合を同じ FACT_NO_PROPOSAL 1 文で返していた（外した理由は個別に持っていた）。構造軸 representation = 2 つの理由を区別する居場所が応答に無い。接続軸 information = 理由がサーバ内にあるのに利用者へ渡らない。処理軸 wording = 文が理由を言わない。統制軸 none。
generalization:
  level: general
  general_form: "空の結果を 1 つの文で返し、空になった理由（何も無かった／全て除外した）が利用者に伝わらない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "第 22 周の学生場面 (d') で、改訂案が無いという文だけが出て、ペルソナが次に何を変えればよいか分からなかった。"
resolution:
  perspective: [representation_change, single_point_fix]
  note: "FACT_NO_PROPOSAL に理由の 1 文（FACT_NO_PROPOSAL_NONE / FACT_NO_PROPOSAL_ALL_DROPPED）を添える。"
  landed_in:
    - "backend/core/simulation_bridge/revision.py"
    - "backend/core/simulation_bridge/schema.py"
    - "backend/tests/test_simulation_bridge_revision.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の再演で GONE を確認していない"
related: [IK-0629]
view_of: []
history: []
---

## 課題

症状: 改訂案が無いとき理由が無く、学習者が次の手を決められない。原因: 2 つの異なる理由の空を同じ文で返していた。

## 発見の観点

ペルソナの通し受講（reproduction）で、理由の無い空の応答に詰まった場面から見えた。

## 解決の観点

理由ごとの固定文を添える。failure-reported-as-success（失敗を成功として返す）ではなく、空であることは正直に返しており理由だけが落ちていたため、情報を落とす型として扱った。

## 一般化

空の結果は理由が複数ありうるのに 1 文で返されやすい。型は information-dropped-as-unrepresentable。確かめ方は、何も挙がらない場合と全て除外される場合を作り、別々の理由文が出ることを見ること。
