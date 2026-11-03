---
id: IK-0638
title: "音声・casual 経路は画面文脈（開いている実行）を受けない設計のため、音声中心の学習者は実行結果の意味を声で尋ねても答えを得られない"
status: open
recorded_at: 2026-10-04
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
  - "docs/features/assistant_screen_adapter_design.md §11.15.4"
feature_context:
  realizing: "音声で学ぶ学習者が、開いている自分の実行の結果の意味を声で尋ねる"
  layers: [screen_adapter_sa, frontend_learning_ui, tts_voice]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: SA層の設計は casual 経路に画面文脈を渡さない（§11.15.4）。音声経路は casual になるため、開いている run の事実文が届かず、AI は手元に無いと正直に縮退した。接続軸 information = 事実はあるが意図的にこの経路へ配線していない。処理軸 none。構造軸 none。統制軸 none。縮退が正直であることは不変条項どおり。
generalization:
  level: instance
  general_form: "会話の様相ごとに文脈の配線を分けた結果、ある様相でしか使わない利用者が手元の対象についての文脈を受け取れない"
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: "第 22 周の学生場面 (k) で、音声の学習者が結果の意味を尋ね、AI が手元に無いと答えた。"
resolution:
  perspective: [pending]
  note: "未解決（意図的な非配線の記録）。開いている run だけは casual にも渡すかは設計判断で、SA層設計書に提案として残す。LC1（推定してよいのは様相だけ）と衝突しない範囲で、明示に開いている対象だけを渡す案が候補。"
related: [IK-0619, IK-0633]
view_of: []
history: []
---

## 課題

症状: 音声で実行結果の意味を聞いても、AI は実行の中身を知らないと答える。原因: casual 経路に画面文脈を渡さない設計で、音声経路が casual になる。

## 発見の観点

ペルソナの通し受講（reproduction）で音声の学習者が詰まった場面から見えた。

## 解決の観点

未解決。意図した非配線であり、正直な縮退は正しい。音声中心の利用者のために開いている対象だけを渡すかは設計判断で、判断材料は casual の短い応答に事実文が載ったときの読みやすさ。

## 一般化

様相ごとに文脈を絞る設計は、その様相だけを使う利用者から手元の対象の文脈を奪う。型は available-but-unwired（意図的）。確かめ方は、各様相で同じ対象を開いて同じ問いを送り、どの様相で対象の事実が届くかを並べること。
