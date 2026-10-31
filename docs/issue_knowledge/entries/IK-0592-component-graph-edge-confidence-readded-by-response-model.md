---
id: IK-0592
title: "グラフレビューの辺に confidence の生値が戻る（射影の後で応答モデルの既定値 0.5 が入る）・辺の根拠文が英語の定型文で式 ID が残る"
status: resolved
recorded_at: 2026-10-01
resolved_at: 2026-10-01
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.11
feature_context:
  realizing: "教員向けのグラフレビューで理論操作グラフの辺を、数値と内部 ID を落として返す"
  layers: [graph_review, shared_infra]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [contract]
    governance: [ordering]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説（根拠文の部分。confidence の部分は確認済み）: `GET /documents/{id}/component-graph` は `project_teacher_dto` で数値キーを落としたあと
    `ComponentGraphResponse(**…)` に詰め直し、`ComponentGraphEdge.confidence: float = 0.5`（backend/api/schemas.py）が既定値を
    戻す（接続軸 contract・統制軸 ordering — 射影が応答モデルの前にある）。根拠文（evidence.reason）は仮説: A層の定型文
    「Theory flow: … output feeds …（eq_1）」が英語のまま表示文として残り、式 ID の置き換えが括弧内の一部トークンに効いていない
    （「番号なしの式, 番号なしの式, eq_4」）。トークン置き換えの境界を読めば確定する。構造軸は none。
generalization:
  level: general
  general_form: "表示用の射影を通したあと、既定値を持つ型に詰め直して落としたはずの値が戻る"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: "第 18 周 te-03 の頭脳メモ（辺の confidence: 0.5・英語の reason）を、全辺が同じ 0.5 であることから応答モデルの既定値と見当を付け、route と schemas.py を辿って確かめた。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: '`ComponentGraphEdge.confidence` の既定を None にし、None のときは応答からキーごと外す serializer を置いた。射影で落とした値が応答モデルの詰め直しで戻らない。辺の根拠文（英語の定型文・式 ID の残り）はこの是正に含まれない。'
  landed_in:
    - backend/api/schemas.py
    - backend/tests/test_wave18_gate_stuck_advice.py
    - 97994b9
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（te-03）"
      - "辺の根拠文の英語の定型文と式 ID の残り（未着手）"
      - "既定値を持つ他の教員向け応答モデル"
related: [IK-0579, IK-0546]
view_of: []
history:
  - date: '2026-10-01'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '第 19 波で、辺の confidence が射影後に既定値で戻らないようにした（ガードレールで確認・再演は次の周）。残り: 辺の根拠文の英語の定型文と式 ID の残りは未着手。（persona_enactment_testing_design.md §18.12）'
---

## 課題

教員向けのグラフレビュー（component-graph）の辺に `confidence: 0.5` が全辺同じ値で残る。教員向け射影（DP7・PL4）は confidence を禁止キーとして
落としているが、route がその結果を `ComponentGraphResponse` に詰め直すため、`ComponentGraphEdge` の既定値 0.5 が戻る（全辺同値なのは
既定値だから）。辺の根拠文は「Theory flow: Observable construction output feeds Theory basis (eq_1).」の英語の定型文のままで、括弧内の
式 ID が一部だけ「番号なしの式」に置き換わり `eq_4` 等が残る。根拠文は原稿スタジオの辺の詳細（intrinsic_summary）として描かれる。

証拠: run 20261001T053216Z-65555 seq=3、20261001T053550Z-67818 seq=3（te-03）。

## 発見の観点

再演と経路の追跡。全辺が同じ値であることを手掛かりに、射影と応答モデルの順序を読んだ。

## 解決の観点

97994b9。応答モデル側で、値が無いことをキーの不在として表す（既定値で埋めない）見立て。射影の順序は変えていない。
根拠文の部分は未着手で、原因は仮説のまま（classification.basis）。

## 一般化

射影のあとに既定値を持つ型へ通す経路は、落とした値を黙って復活させる。応答モデルを持つ他の教員向け面でも再発しうる。
