---
id: IK-0643
title: "教員の Copilot が同じ capability の KB 本文を素通しして追加の問いに同じ回答を繰り返し、「確定・日時」の語で無関係な分野の地図の凍結へ案内する"
status: open
recorded_at: 2026-10-04
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "教員が台帳候補の判断について Copilot に続けて尋ねる"
  layers: [admin_copilot]
classification:
  axes:
    processing: [logic]
    structure: [unknown]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: Copilot の guidance は capability KB の本文を検索で選んで返すため、追加の問いでも同じ節が当たり同じ本文が返り、語の重なりで「確定」「日時」を含む凍結の節が当たる。処理軸 logic = 選び方が問いの差を見ない。接続軸 meaning = 同じ語が別の操作を指す。構造軸 unknown = KB の節の粒度が問いに足りないのか、選び方の問題かは未確定。knowledge.py の検索の採点と、該当の問いでどの節が上位に来たかを確かめれば確定する。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "語の重なりで知識の節を選ぶ窓口が、続く問いの差を見ずに同じ節を返し、共有する語で別の操作の節へ案内する"
pattern: substring-match-false-positive
discovery:
  perspective: [reproduction]
  note: "第 22 周の教員場面 (g') で、判断の記録の在りかを尋ねた追加の問いに同じ回答が返り、別の問いで地図の凍結が案内された。"
resolution:
  perspective: [pending]
  note: "未解決。どの節が当たったかの実測と、判断の記録の在りか（監査・台帳の GET）を KB に書くかが分かれば解ける。"
related: [IK-0625]
view_of: []
history: []
---

## 課題

症状: 追加の問いに同じ KB 本文が返り、別の問いで無関係な凍結の操作が案内される。原因（仮説）: 語の重なりで節を選び、問いの差や語の多義を見ない。

## 発見の観点

ペルソナの通し受講（reproduction）で教員が同じ回答を受け取った場面から見えた。

## 解決の観点

未解決。検索の上位の実測が先。

## 一般化

語の重なりで節を選ぶ案内は、共通の語で別の操作へ誤誘導する。型は substring-match-false-positive の近縁（語の照合の誤検出）。確かめ方は、共通の語を含む別の操作の問いを送り、案内される節が操作ごとに分かれることを見ること。
