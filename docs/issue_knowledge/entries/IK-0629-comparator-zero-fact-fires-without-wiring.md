---
id: IK-0629
title: "比較器が相対差を測れなかった理由（配線なしで測らない／理論の出力がゼロ）を同じ None に畳み、配線なしの実行で全記号に「理論の出力がゼロ」の事実文が出た"
status: resolved
recorded_at: 2026-10-04
resolved_at: 2026-10-04
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "学習者が理論プログラムを実行し、比較器の事実文で理論の出力と模擬観測の関係を読む"
  layers: [simulation_bridge_sb]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [meaning]
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
    確認: backend/core/simulation_bridge/comparator.py の _rel_diff は理論の出力が EPSILON 未満のとき None を返し、配線なしの run では相対差をそもそも測らないので同じく None になる。ゼロの名指しは rel is None だけで判定していたため、配線なしの run では理論の出力が大きな値の記号にも「ゼロ」と出た。構造軸 representation = None という 1 つの値が「測らない」と「ゼロで測れない」の 2 つの理由を区別できない。接続軸 meaning = 後段の事実文が None を「ゼロ」の意味に読んだ。処理軸 logic = 判定条件が理由を見ない。統制軸 none。
generalization:
  level: general
  general_form: "欠測を表す 1 つの値に異なる理由を畳み、後段がその値を片方の理由として読んで事実と食い違う文を出す"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, trace_walk]
  note: "第 22 周の学生場面 (i)(k) で、明らかに大きな値の記号に「理論の出力がゼロ」と出た。事実文の生成から _rel_diff の戻り値まで辿り、None の理由が 2 つあると分かった。"
resolution:
  perspective: [single_point_fix, explicit_contract]
  note: "ゼロの名指しは配線ありで |理論| < EPSILON のときだけにし、配線なしは測らない事実として扱う。記載値がゼロのときの None を max() に混ぜないガードも足した。"
  landed_in:
    - "backend/core/simulation_bridge/comparator.py"
    - "backend/tests/test_simulation_bridge_comparator.py"
  verification:
    methods: [guardrail]
    unverified:
      - "差し替え後の配線なしの実行での再演は未（第 22 周 (b) の再演は出力がゼロで判定できなかった）"
      - "配線ありで理論の出力が本当にゼロになる教材での表示"
related: [IK-0613, IK-0614]
view_of: []
history:
  - date: 2026-10-04
    field: resolution.verification
    from: 記録時の範囲
    to: 第 22 周の再演で更新
    reason: "修正は砂場に差し替え済み・再演は未"
---

## 課題

症状: 配線なしの実行の結果に、理論の出力が大きい記号まで「理論の出力がゼロの記号」と並ぶ。原因: 比較器は相対差を測れないとき None を返すが、その理由（配線なしなので測らない／理論の出力がゼロで割れない）を値に残さず、事実文は None をゼロと読んでいた。

## 発見の観点

ペルソナの通し受講（reproduction）で事実と食い違う文を観測し、事実文の条件から相対差の関数まで経路を辿った（trace_walk）。

## 解決の観点

None の理由を判定側で区別する（配線の有無と理論値の大きさを直接見る）。None を理由付きの列挙に置き換える案は、表示側の読み替え（store.display_metrics）と保存形を変えずに済む方を採って見送った。

## 一般化

欠測を 1 つの値で表す表現は、欠測の理由を後段に渡せない。型は information-dropped-as-unrepresentable。確かめ方は、理由の異なる欠測（測らない・測れない）をそれぞれ作り、表示が理由どおりに分かれることを見ること。
