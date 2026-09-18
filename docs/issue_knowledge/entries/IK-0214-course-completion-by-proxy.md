---
id: IK-0214
title: コースの完了を到達位置の代理指標で断定し、完了状態の正本がどこにも残らない
status: resolved
recorded_at: 2026-07-16
resolved_at: 2026-07-17
sources:
  - docs/architecture/issue_494_implementation_review_2026-07-16.md
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §1
feature_context:
  realizing: 学習者がコースを学び終えたことを見届け、修了として伝える
  layers: [learner_experience_b, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [completion]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    表示の処理も判定式も書かれたとおりに動く。原因は「完了」の定義を、到達した位置に次が
    無いことという代理指標で置き、実際に学んだかどうかを見ていないこと。さらに判定結果を
    残さないため、完了の正本がどこにも存在しない。完了判定の決め方が崩れているので統制とする。
generalization:
  level: general
  general_form: >-
    完了を代理指標で判定し、判定結果を保存しないため、事実と食い違う断定が繰り返し表示される
discovery:
  perspective: [adversarial_review, trace_walk]
  note: >-
    完了カードの表示条件を読み、条件が「次が無いこと」だけであることと、途中の項目を飛ばして
    最後へ移動できることを突き合わせた。加えて、判定の結果が保存されず再読み込みで復元できない
    ことを経路を辿って確認した。
resolution:
  perspective: [first_class_state, representation_change]
  note: >-
    完了した項目の集合と完了日時を学習状態に保存し、サーバが全項目の完了を判定して返す形に
    した。永続化を見送る場合の代案として「最後の項目の確認を終えた」という確認できる事実だけを
    表示する案も併記されていたが、修了証明や次の案内へ繋ぐ正本が要るため保存する側を採った。
  landed_in:
    - backend/api/routes/learning.py
    - frontend/public/js/app.js
related: [IK-0215]
view_of: []
pattern: completion-defined-by-proxy
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.completion, structure.representation]
    to: axes=processing=[none]; structure=[representation]; connection=[none]; governance=[completion]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 途中の項目を飛ばして最後の項目へ移動し確認に答えると、「全ての項目を学習しました」と
表示される。しかもその状態は保存されないため、再読み込みでは復元できず、同じカードを何度でも
出せる。

**原因**: 完了の判定が「いま見ている項目に次が無い」という到達位置の代理指標だけで行われ、
実際に各項目を学んだかを見ていない。さらに確認の結果も完了状態もサーバに保存されないため、
「完了した」という事実の正本がどこにも存在しない。

## 発見の観点

`adversarial_review` と `trace_walk`。「この表示は何を根拠にしているか」を条件式まで遡り、
その根拠が主張している内容（全項目を学習した）と同じものかを問うと見える。実装レビューの
視点であり、通常の利用では成立してしまうため症状として上がりにくい。

## 解決の観点

`first_class_state`（完了を一級の状態として学習状態に持たせ、正本を作る）+
`representation_change`（完了した項目の集合と日時を保存し、サーバが判定する）。永続化しない場合は断定文を「最後の項目の確認を終えた」へ
弱める案もあり、いずれにせよ**確認できる事実しか言わない**という線は共通していた。

## 一般化

完了・習得・準備完了・既読といった状態を、到達位置や履歴の有無などの代理指標で判定する
あらゆる場面で再発する。代理指標は通常の使い方では一致するため、逸脱した操作でしか
食い違いが露見しない。辞書の型は `completion-defined-by-proxy`。
