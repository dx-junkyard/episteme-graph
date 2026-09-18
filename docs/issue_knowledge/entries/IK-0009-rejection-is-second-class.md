---
id: IK-0009
title: 却下・撤回が理由も帰属も残さず、主張の側には却下する口すら無い
status: open
recorded_at: 2026-09-10
resolved_at: null
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §2 F8
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §5 T1
  - docs/architecture/six_lenses_2026-09-10/01_learner.md §2 提案6
  - docs/architecture/six_lenses_2026-09-10/known_issues_architecture.md A-03
feature_context:
  realizing: 人が「これは採らない」と判断したことを、後から辿れる記録として残す
  layers: [graph_review, deliberation_w, doubt_d]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [review]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「否定側の判断を、理由と対象範囲つきで表せる形が用意されていない」こと。却下の
    処理自体は状態を動かせており、権限も版も落ちていない。表現に理由と範囲の居場所を作らない
    かぎり、画面に理由欄を足しても保存先が無い。確認手段は F8 行が引く却下の受け口の一覧で、
    理由を必須にしているのは地図系の一部だけであること。
generalization:
  level: repo_pattern
  general_form: 肯定の判断は理由と帰属を伴って残るのに、否定の判断は痕跡がほとんど残らない
pattern: negative-decision-second-class
discovery:
  perspective: [inventory, invariant_audit]
  note: >-
    確定の受け口を全件棚卸しし、肯定側と否定側で残るものを並べた。承認には帰属も段階も
    残るのに、却下は理由なしで通り、主張の粒度には却下の口すら無い。学習者の側には
    「自分の確定を取り下げる」語彙が存在しない。三つのレンズが別の入口から同じ非対称に着いた。
resolution:
  perspective: [pending]
  note: >-
    未解決。何が分かれば解けるかは見えている。却下の理由を選べる語彙（種別）と、その却下が
    どこまで効くのかという範囲の二つを表現に足せば、画面と免疫記憶はその上に乗る。範囲を
    近傍で持つか対象単位で持つかの選択が残っている。
  landed_in: []
related: [IK-0005, IK-0007]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.representation, governance.review]
    to: axes=processing=[none]; structure=[representation]; connection=[none]; governance=[review]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は、あとから「なぜこれは採らなかったのか」を辿れないことである。知識オブジェクト側の
却下はほとんどが理由なしで通り、主張の粒度にはそもそも却下の受け口が無い。学習者の側には、
一度引き受けた自分の確定を取り下げる語彙が存在しない。

原因は、**否定の判断を理由と範囲つきで表せる形が用意されていない**ことにある。状態を動かす
処理はあるので、単体では機能している。ただ、肯定側には帰属も段階も残るのに、否定側は
「消えた」以上のことを言えない構造になっている。

## 発見の観点

確定の受け口を全件棚卸しして、肯定側と否定側に何が残るかを並べた。非対称が一目で出る。
学習者・教員・知識オブジェクトの三つの観点が、別の入口から同じ非対称に到達した。これは
一箇所の実装漏れではなく、表現の欠落であることの傍証になった。

## 解決の観点

未解決。画面に理由欄を足す案は、保存先が無いので成立しない。却下の理由を選べる語彙と、
その却下がどこまで効くかという範囲の二つを表現へ足すのが先で、免疫記憶（同じ候補を
再提案しない）も範囲の上に乗る。範囲を近傍で持つか対象単位で持つかは未決である。

## 一般化

候補を出して人が採否を決める仕組みすべてで再発する。採用の設計に手間をかけ、却下は
単なる非採用として実装した瞬間に起こる。辞書の `negative-decision-second-class` に対応し、
処方は「否定の判断も、理由・帰属・範囲を持つ一級の記録として設計する」。
