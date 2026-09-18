---
id: IK-0210
title: 回答の出所表示と信頼度表示が別々の入力から導かれ、同じ回答に矛盾が同時に出る
status: resolved
recorded_at: 2026-07-17
resolved_at: 2026-07-17
sources:
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §2 P2
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §4
  - docs/backend/rag-chat.md
feature_context:
  realizing: 学習者の質問への回答が何に基づいているかを、出所と教員の承認状況の二軸で正直に示す
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition, information]
    governance: [none]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    二つの表示はそれぞれの入力に対して正しく計算される。原因は、トピックの教材本文を回答に
    注入したという前提が一方の集約へ渡らず、その集約が注入を知らないまま検索結果だけで
    動くこと。各段は単体では正しく見えるという接続の定義に当たる。
generalization:
  level: general
  general_form: >-
    同じ問いに答える二つの表示が別々の入力から導かれ、一方に渡らなかった前提のぶん食い違う
discovery:
  perspective: [boundary_walk, symptom_report]
  note: >-
    出所の判定と信頼度の集約という二つの機能の境界を歩いた。教材本文を注入する経路のときだけ、
    一方は「教材に基づく」と言い、もう一方は「裏づけのない参考」と言う組み合わせが生じることが
    分かった。
resolution:
  perspective: [carry_through, doc_correction]
  note: >-
    注入したという前提を集約側へ運び、教材を注入したときは信頼度に下限を置くことにした。
    ただし承認済みへの昇格はしない（承認は人の行為であり、注入は昇格の根拠にならない）。
    判定の対応表を実装と一致するよう文書側も直した。
  landed_in:
    - backend/core/learning_experience.py
    - docs/backend/rag-chat.md
related: [IK-0209]
view_of: []
pattern: condition-not-propagated
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.condition, connection.information]
    to: axes=processing=[none]; structure=[none]; connection=[condition, information];
      governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 同じひとつの回答に「教材から回答」という出所表示と、「裏づけのない参考」という
信頼度表示が同時に出る。利用者から見れば、システムが自分の回答について矛盾したことを
言っている。

**原因**: 出所の判定はトピックの教材本文を注入したかどうかを見るのに対し、信頼度の集約は
検索で引いた断片だけを見る。教材本文を注入したという前提が集約側へ渡らないため、注入された
本文が信頼度の計算に一切参加しない。

## 発見の観点

`boundary_walk`。「出所の判定」と「信頼度の集約」という、同じ回答について別の軸を答える
二機能の境界を歩いた。二軸が独立であること自体は設計どおりなので、原則の監査では出てこない。

## 解決の観点

`carry_through` — 注入したという前提を集約へ運び、下限を置く形にした。承認済みへ昇格させる
案は明確に退けた（承認は人の行為で、機械の注入がその代わりにはならない）。判定表が実装と
食い違っていた文書も同時に直した（`doc_correction`）。

## 一般化

同じ対象について複数の指標を別経路で計算するあらゆる表示で再発する。二つが独立でよい設計
でも、片方だけが知っている前提があると利用者には矛盾として見える。辞書の型は
`condition-not-propagated`。
