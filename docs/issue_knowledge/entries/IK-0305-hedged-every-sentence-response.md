---
id: IK-0305
title: 不確かさを文ごとの留保で表す契約にしたため、応答が読めず聞けないものになった
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-10
sources:
  - docs/features/graph_dialogue_review_design.md §15
feature_context:
  realizing: AI の読みであることを明示したまま、グラフや要素について対話する
  layers: [tts_voice]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [none]
    governance: [none]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「不確かさを『どこに付けるか』の設計が、文ごとの言い回しに置かれていたこと」。
    文言を個別に直しても、契約が文ごとの留保を要求する限り同じ応答が再生産されるため
    構造。設計書 §15 が、全文が「〜の可能性があります」で終わる症状と、その出所
    （プロンプト契約の該当文）を対応づけている。
generalization:
  level: general
  general_form: 断定を避けるという要請を文ごとの言い回しで満たし、内容が読み取れなくなる
pattern: wording-mismatch
discovery:
  perspective: [symptom_report, invariant_audit]
  note: >-
    利用者からの3点の不具合報告（生の数式記法・書き言葉のままの読み上げ・全文が留保）を、
    「AI に確定させない」という不変条項が要求しているのは文体か構造かという問いに
    突き合わせた。
resolution:
  perspective: [representation_change, canonical_source]
  note: >-
    留保を返答全体に付く固定ラベルへ移し、本文は簡潔な断定調にした。読み上げ用の文は
    同じ 1 回の呼び出しで別フィールドとして受け取る（呼び出し回数を増やさない）。
    ラベル文言の正本は共有の語彙モジュール 1 箇所に置き、重複定義を検出させる。
    承認判断の非代行・根拠の捏造禁止・数値の非表示という条項は一切変えていない。
  landed_in:
    - backend/core/label_vocab.py
    - backend/core/deliberation/graph_dialogue.py
    - docs/features/graph_dialogue_review_design.md §15
related: [IK-0302]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.representation, local.wording]
    to: axes=processing=[wording]; structure=[representation]; connection=[none]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は三つ。①応答に生の数式記法と制御文字の残骸がそのまま出る。②音声モードが書き言葉の
応答をそのまま読み上げる。③すべての文が「〜の可能性があります」で終わり、何を言っている
のか読み取れない。

③の原因は、プロンプト契約が**文ごとの留保**を要求していたこと。「AI に確定させない」と
いう不変条項を、文体の水準で満たそうとした結果だった。①は数式の区切りを契約に書いて
いなかったこと、②は伝達形式（読む／聞く）が応答の表現に反映されていなかったことによる。

## 発見の観点

利用者の症状報告（`symptom_report`）を、不変条項が本当に要求しているものと突き合わせた
（`invariant_audit`）。条項が守っているのは「AI が確定に関与しない構造」であって、
文体ではない、という切り分けが解決の起点になった。

## 解決の観点

不確かさの置き場所を、文から**返答全体のラベル**へ移した（`representation_change`）。
伝達形式は同じ 1 コールの出力に別フィールドを足して分けた。ラベル文言は共有語彙の
1 箇所を正本にして重複を禁じた（`canonical_source`）。新旧の契約文が原文で存在・不在で
あることもテストで固定した。

## 一般化

「断定させない」「押し付けない」といった要請を、文ごとの言い回しで満たそうとすると、
出力の可読性が壊れる。要請は構造（どこに何のラベルが付くか）で満たし、文体は読み手に
合わせる。辞書の型 `wording-mismatch` に対応する。
