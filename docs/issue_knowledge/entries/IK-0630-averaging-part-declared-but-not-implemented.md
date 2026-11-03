---
id: IK-0630
title: "実験機の部品「平均化（N 回）」は目録で分散を縮めると宣言しながら、合成器がその縮小を実装しておらず、N を足してもばらつきが縮まなかった"
status: resolved
recorded_at: 2026-10-04
resolved_at: 2026-10-04
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "学習者が実験機の部品を配線し、平均化で模擬観測のばらつきを縮めて理論の出力と見分ける"
  layers: [simulation_bridge_sb, image_library_l]
classification:
  axes:
    processing: [logic]
    structure: [responsibility]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 同梱部品の目録で平均化の部品は transfer が恒等（x のまま）で、summary は「分散のスケーリングは配線の合成器が行う」と述べる。backend/core/simulation_bridge/instrument.py の compose_instrument にはその処理が無く、部品は何もしていなかった。接続軸 contract = 部品の宣言（前段）と合成器の実装（後段）が両立していない。構造軸 responsibility = 縮小の責務を合成器に預けたが、合成器が引き受けていない。処理軸 logic。統制軸 none。
generalization:
  level: general
  general_form: "部品が「処理は組み立て側が行う」と宣言して自分では何もしない一方、組み立て側がその処理を実装しておらず、部品が無効のまま通る"
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 22 周の学生場面 (d') で、平均化を足してもばらつきが縮まず増えた。部品の目録の宣言と合成器の実装を突き合わせて未実装と分かった。"
resolution:
  perspective: [responsibility_move, explicit_contract]
  note: "合成器が宣言の形（恒等 transfer・回数 n・ランダム雑音を除く）で平均化の部品を見分け、上流の段を雑音の引き直しを含めて n 回やり直した平均に置き換える（分散は 1/n、offset / drift は不変、上限 MAX_AVERAGING）。"
  landed_in:
    - "backend/core/simulation_bridge/instrument.py"
    - "backend/tests/test_simulation_bridge_instrument.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演は未"
      - "平均化の部品を複数重ねた配線・平均化の上流に drift がある配線"
related: [IK-0621]
view_of: []
history:
  - date: 2026-10-04
    field: resolution.verification
    from: 記録時の範囲
    to: 第 22 周の再演で更新
    reason: "TestAveragingPart を追加"
---

## 課題

症状: 平均化の部品を配線しても模擬観測のばらつきが縮まない。原因: 部品は恒等変換で、縮小は合成器が行うと目録が宣言していたが、合成器にその処理が無かった。

## 発見の観点

ペルソナの通し受講（reproduction）で効かない部品を観測し、目録の部品宣言と合成器のコードを突き合わせた（data_inspection）。純関数の実行（煙テスト）で縮小が起きないことも確かめた。

## 解決の観点

責務を宣言どおり合成器に置き、部品の宣言の形から平均化を見分けて上流を n 回やり直す。部品の transfer に平均化を書く案は、部品が上流の雑音を引き直せないため採らなかった。

## 一般化

「組み立て側がやる」と宣言する部品は、組み立て側の実装が無いと無言で無効になる。型は contract-changed-one-side。確かめ方は、宣言どおりの効果（分散の縮小）を純関数で実測し、宣言と実測が一致することを見ること。
