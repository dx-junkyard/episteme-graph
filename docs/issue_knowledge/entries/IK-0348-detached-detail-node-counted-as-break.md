---
id: IK-0348
title: 式の詳細ノードの親欠落を参照の破断に数えたため、旧 run のグラフを持つ既存教材が一斉に broken へ振れうる
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §7.1
feature_context:
  realizing: 参照の健全性を教員に事実として見せる
  layers: [knowledge_transfer, theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [version]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: 「参照先が無い（破断）」と「まだ結ばれていない（欠落）」を同じ集計区画（DETAIL_KINDS → status broken）に入れていた。接続軸: 親付けが claim 交差に広がったのは今回の run からで、旧 run のグラフは親が空のまま正常に存在する（版の違い）。確認手段: reference_health.py の DETAIL_KINDS と status 導出。
generalization:
  level: general
  general_form: 新しく足した検査が「まだ無い」状態を「壊れている」に分類し、改訂前に作られた既存データが一斉に失敗扱いになる
pattern: threshold-reused-across-regimes
discovery:
  perspective: [adversarial_review, data_inspection]
  note: >-
    第 1 波で detail_node_parent を検査種別に足した直後に、実データ（ccb39a23: 0 → 155 detail に親）と照らし、再解析前の教材で何件が broken になるかを見た。
resolution:
  perspective: [representation_change, guardrail_fix]
  note: >-
    detail_node_parent を破断（DETAIL_KINDS）から欠落（GAP_KINDS）へ移し、status は変えず事実文だけ出す。
  landed_in:
    - backend/core/reference_health.py
    - backend/tests/test_reference_health_gaps.py
  verification:
    methods: [guardrail]
    unverified:
      - docker で組み上げた実機での E2E
related: [IK-0338]
view_of: []
history: []
---

## 課題

**症状**: 親の無い式の詳細ノードが 1 つでもあると status が broken になり、再解析していない既存教材の教材行チップが全件赤くなりうる。

**原因**: 欠落と破断を同じ集計区画に入れ、旧 run の正常な形を考慮しなかった。

## 発見の観点

敵対的レビュー（`adversarial_review`）と実データの照合（`data_inspection`）。

## 解決の観点

区画の表現を変え（`representation_change`）、テストで固定（`guardrail_fix`）。

## 一般化

計器に検査を足すときは、改訂前のデータでその検査がどう出るかを先に見る。「無い」と「壊れている」を同じ色にしない。
