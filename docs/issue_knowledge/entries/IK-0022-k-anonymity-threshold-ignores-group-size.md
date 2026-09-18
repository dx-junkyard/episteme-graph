---
id: IK-0022
title: 匿名であることを件数の閾値だけで判定し、小さな集団では匿名にならない
status: deferred
recorded_at: 2026-09-10
resolved_at: null
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §6 D6
  - docs/architecture/six_lenses_2026-09-10/known_issues_architecture.md A-27
feature_context:
  realizing: 学習者の痕跡を教員に集約で見せつつ、個人が特定されないようにする
  layers: [learner_experience_b, shared_infra, indicator_catalog]
classification:
  primary: structure
  facets: [structure.representation, governance.review]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「匿名であることを、母集団の規模と無関係な件数の閾値だけで表している」こと。
    集計処理は閾値どおりに動いており、条件も版も落ちていない。判定の表現に母集団の文脈を
    入れないかぎり、閾値の数を上げ下げしても小さな集団では同じ結果になる。確認手段は、
    同学年が二人の研究室では閾値を満たすセル自体がその二人を指すという構成上の事実。
generalization:
  level: general
  general_form: 安全であることを、文脈と無関係な固定の代理指標だけで判定する
pattern: completion-defined-by-proxy
discovery:
  perspective: [adversarial_review, external_constraint]
  note: >-
    実際に想定される規模（同学年が二人の研究室）を当てて集約を読み直した。閾値を満たした
    セルが、そもそもその集団の全員を指す。閾値の設計は正しいのに、適用される場の規模が
    小さいと保護として働かない。
resolution:
  perspective: [deferred_decision]
  note: >-
    保留。推奨は、閾値そのものは変えず、人数が閾値に満たない場では機能単位で「表示しません」と
    宣言する形。閾値を上げると集約が機能しなくなり、下げると保護が弱まるので、どちらも採らない。
    実際の運用規模を観測してから再判断する。
  landed_in: []
related: [IK-0021, IK-0004]
view_of: []
history: []
---

## 課題

症状は、集約を見せているつもりで個人が特定できてしまうことである。閾値は「同じ区分に一定件数
以上あること」で、その条件を満たしたセルだけを表示する。ところが同学年が二人しかいない
研究室では、条件を満たしたセルがそのままその集団の全員を指す。

原因は、匿名であることを母集団の規模と無関係な件数の閾値だけで表していることにある。集計処理は
閾値どおりに正しく動く。閾値が保護の代理として働くのは、母集団が閾値より十分大きいときだけで、
その前提が表現のどこにも書かれていない。

## 発見の観点

想定される実際の規模を当てて集約を読み直した。数式としての閾値は正しく、規模を入れて初めて
崩れる。外部の制約（研究室の規模という現実）から逆算しなければ出てこない種類の課題である。

## 解決の観点

保留。閾値を上げると集約自体が機能せず、下げると保護が弱まる。推奨は、閾値は変えずに、
人数が閾値に満たない場では機能単位で「表示しません」と宣言する形である。どの機能を無効に
するかは運用の規模に依存するので、観測してから判断する。

## 一般化

閾値・比率・件数で安全性や達成を代理判定するすべての場所で再発する。代理が成立する前提
（母集団の規模、分布の形）が表現に現れていないときに起こる。辞書の
`completion-defined-by-proxy` に対応し、処方は「代理指標が成立する前提を、判定と同じ場所に書く」。
