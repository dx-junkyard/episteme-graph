---
id: IK-0661
title: "再構成の自己確認が押し間違えても直せず、送信の失敗も握りつぶされ、「もう一度挑戦」で前の答えが消える"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/reconstruction_loop_design.md"
feature_context:
  realizing: "学習者が再構成の問いに答え、出典と並べて自己確認し、必要なら答え直す"
  layers: [reconstruction_r, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [review]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 自己確認を押すと 3 つのボタンが全て無効になり押し直せず、送信の例外は catch で捨てて後続へ進む（reconstruction.js の selfCheck）。「もう一度挑戦」は出題の画面を空の入力欄で描き直し、前の答えも出典も出さない（renderFollowup → renderElicit）。サーバは改訂を revision_of で前の答えに結ぶ。処理軸 logic = 失敗の分岐が無い。接続軸 information = 保存済みの前の答えが改訂の画面へ届かない。統制軸 review = 本人の自己確認を訂正する手続が無い。構造軸 none。
generalization:
  level: repo_pattern
  general_form: "本人の判断を一度きりの押下で確定させ、改訂の画面には保存済みの前の判断を出さない"
pattern: available-but-unwired
discovery:
  perspective: [invariant_audit, trace_walk]
  note: "第 23 周の机上レビューで、再構成ループの設計（自己確認・改訂の閉ループ）と、自己確認から改訂までの画面の経路を読み合わせて見えた。"
resolution:
  perspective: [pending]
  note: "未解決。前の答えは revision_of で辿れるので改訂の画面に読み取り専用で並べ、自己確認は押し直し（最後の値・履歴は残す）と送信の成否の事実文を足せば解ける。"
related: [IK-0651]
view_of: []
history: []
---

## 課題

症状: 自己確認を押し間違えても直せず、「判定がおかしい」を押した結果も返らない。送信の失敗は無言。答え直すと前の答えが見えない。原因: 押下で全ボタンを無効にし例外を捨てる作り、改訂の画面が前の答えを読まない作り。

## 発見の観点

設計書の閉ループ × 画面の経路の読み合わせ（invariant_audit）と、自己確認から改訂までの値の追跡（trace_walk）。

## 解決の観点

未解決。前の答えと出典は保存済みなので画面へ運べば足り、自己確認の訂正は状態の上書きと履歴で表せる見込み。

## 一般化

閉ループの段を一度きりの押下にすると、本人は自分の判断を直せず、改訂は前の判断と比べられない。確かめ方は、自己確認を押し直して最後の値が残ることと、改訂の画面に前の答えが並ぶことを見ること。
