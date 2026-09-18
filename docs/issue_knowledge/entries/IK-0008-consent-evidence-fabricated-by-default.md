---
id: IK-0008
title: 確認の理由欄に既定文が入り、根拠を見た事実をサーバが定数で断言する
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-10
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §2 F7
  - docs/architecture/six_lenses_2026-09-10/05_ai.md §2 所見B
  - docs/features/decision_context_design.md
feature_context:
  realizing: 一括の確定を、あとから再構成できる手続として記帳する
  layers: [decision_context, release_review, doubt_d]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [review, assignment]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「確認したという事実を、確定者の行為ではなく既定値と定数が作っている」こと。
    記帳処理は所定の値を書いているだけで、情報も条件も落ちていない。何をもって確認と
    みなすかの手続を変えないかぎり、文面を変えても同じ記録が積まれる。確認手段は F7 行が
    引く理由欄の初期値、サーバ側の固定値、およびクライアント自己申告の扱い。
generalization:
  level: general
  general_form: 人が確認した事実を、既定値や定数が代わりに作り、記録上は本人がしたことになる
pattern: default-hides-choice
discovery:
  perspective: [adversarial_review, invariant_audit]
  note: >-
    「確定は再構成できる手続にのみ」という改訂原則に照らして、確定の記録に何が残るかを
    逆から読んだ。理由欄は初期値が入っていて空欄のまま押せる。根拠を見たという値はサーバが
    無条件に真を書いている。記録だけを見ると、どちらも人が確認したように読める。
resolution:
  perspective: [fail_closed, representation_change]
  note: >-
    理由欄の初期値を空にして、書かなければ書かれないようにした。根拠を見たという値は
    サーバが断言せず、実際の展開操作に基づく申告として隔離し、申告であることが分かる形で
    記帳する。提示と適用は別の鍵で持ち、一致は導出で判定する。
  landed_in:
    - backend/core/decision_context.py
    - backend/api/routes/landscape.py
    - frontend/public/js/admin-release-review.js
    - docs/features/decision_context_design.md
    - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 5
related: [IK-0007, IK-0014]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.review, governance.assignment]
    to: axes=processing=[none]; structure=[none]; connection=[none]; governance=[review,
      assignment]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は、確定の監査記録を読んでも「本当に確認したのか」が分からないことである。理由欄には
あらかじめ確認した旨の文が入っており、そのまま押せば理由を書いたことになる。根拠を見たか
どうかを表す値は、サーバが無条件に真を書いていた。別の画面ではクライアントの自己申告を
そのまま事実として記帳していた。

原因は、**確認という事実を人の行為ではなく既定値と定数が作っていた**ことにある。処理は所定の
値を書いているだけなので、単体では何も壊れていない。手続として見たときにだけ崩れている。

## 発見の観点

「確定は再構成できる手続にのみ」という改訂原則を逆から読み、確定の記録に何が残るかを見た。
既定値・固定値・自己申告という三つの供給源が、いずれも人の行為と区別できない形で同じ欄に
入っていた。

## 解決の観点

文面を強くする案は採らなかった。既定値がある限り、書かなくても書いたことになるからである。
初期値を空にして、書かなければ残らないようにした。見たという事実はサーバが断言せず、
実際の操作に基づく申告として、申告と分かる場所に隔離した。

## 一般化

同意・確認・レビュー済みを記録するすべての場所で再発する。既定でチェックが入っている、
定型文が入っている、クライアントの申告をそのまま保存する、という形を取る。辞書の
`default-hides-choice` に対応し、処方は「確認の記録は、確認しなければ空のまま残す」。
