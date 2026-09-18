---
id: IK-0111
title: 事前確認と確定が同じ入力である保証が無く、一括確定の記帳も無かった
status: resolved
recorded_at: 2026-09-13
resolved_at: 2026-09-13
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §9.2 P4-R4
  - docs/features/knowledge_transfer_design.md §14.1
  - docs/features/decision_context_design.md
feature_context:
  realizing: 知識の束を取り込む前に内容を提示し、教員の確定でだけ書き込む
  layers: [knowledge_transfer, decision_context]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
    governance: [ordering, review]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 検査も適用もそれぞれ書かれたとおり動く。構造: 束の同一性を表す値は是正で導入したが、順序や状態で担保する設計も取り得たため、表現の不在が原因だとは言い切れない。接続:
    事前確認の段と確定の段の間で、対象（どの束か）が同じである保証が無い。別々の要求どうしを段と読むかどうかで判断が割れるため確信は中。統制:
    検査と適用をいつ行うかという順序が崩れており（順序）、実質の一括確定なのに提示と適用を後から再構成できる記帳が無かった（レビュー）。
generalization:
  level: general
  general_form: >-
    検査と適用が別の要求に分かれ、両者が同じ対象であることを確認しないため、提示したものと
    適用したものが食い違い得る
pattern: gate-position-wrong
discovery:
  perspective: [adversarial_review, invariant_audit]
  note: >-
    「確定は再構成可能な手続にのみ」という条項に照らし、確定の入口に何が記録されるかを読んだ。
    そこで、提示と適用の同一性がどこにも担保されていないことと、一括確定なのに記帳が無いことが
    同時に見えた。
resolution:
  perspective: [required_argument, explicit_contract]
  note: >-
    事前確認が入力の指紋を返し、確定はそれを必須引数として受けてサーバ側の再計算値と照合する
    （欠落は 422・不一致は 409・どちらも書き込みゼロ）。あわせて提示と適用の内訳・代替・再審の
    経路を確定の記帳に残した。
  landed_in:
    - backend/core/knowledge_import/bundle.py
    - backend/api/routes/export.py
    - docs/features/knowledge_transfer_design.md §14.1
related: [IK-0110, IK-0115]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.ordering, governance.review]
    to: axes=processing=[none]; structure=[none]; connection=[none]; governance=[ordering,
      review]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: connection=[none]
    to: connection=[target]
    reason: >-
      軸ごとの再判定で、事前確認と確定の間で対象が同じである保証が無い点を接続の要素として置いた
---

## 課題

**症状**: 事前確認で内容を見せたうえで確定する流れなのに、確認した束と確定時の束が同じである
保証が無い。確定は実質の一括確定なのに、何を提示して何が適用されたかが後から再構成できない。

**原因**: 検査と適用が別々の要求で、その間に対象が入れ替わっても気づけない順序になっていた。
また一括の確定は記帳の対象に入っていなかった。

**軸ごとの判断（2026-09-19）**: 接続は、確認の段と確定の段の間で対象が同じである保証が無い点を要素として置いた（別々の要求を段と読むかで割れるため確信は中）。
統制は順序と一括確定の記帳。処理と構造は要素なし。

## 発見の観点

「確定は再構成可能な手続にのみ与える」という条項に照らして確定の入口を読んだ
（`invariant_audit`）。そこから、確認と適用の間に差し替えを差し込む筋道を具体的に考えた
（`adversarial_review`）。

## 解決の観点

順序を変えるのではなく、**同じものであることを必須キーとして検査に載せる**方向を取った
（`required_argument`）。入力の指紋を往復させ、欠けていても一致しなくても書き込みは 0 にする。
記帳は新しい台帳を作らず、既存の確定文脈の枠に載せた（`explicit_contract`）。

## 一般化

「確認 → 実行」の二段を持つ操作は、両段が同じ対象を見ている保証を明示しない限り成立しない。
プレビューのある一括操作・見積りのある課金・差分表示のあるデプロイなど、二段構えの場所は
すべてこの型の候補になる。
