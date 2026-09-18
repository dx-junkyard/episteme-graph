---
id: IK-0018
title: 自由記述の照合が概念名の部分文字列一致で、言い換えを「言及が無い」と断定する
status: open
recorded_at: 2026-09-10
resolved_at: null
sources:
  - docs/architecture/six_lenses_2026-09-10/05_ai.md §2 所見D
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §5 L6
feature_context:
  realizing: 学習者が自分の言葉で言い直したものと出典を並べ、差を事実として返す
  layers: [reconstruction_r]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
    governance: [none]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「同じ意味を別の表記で書いたときに一致とみなせない照合規則」という単一処理の
    不良である。入力も契約も前提も妥当で、照合を直せば周囲に波及しない。確認手段は
    所見D が引く照合箇所が概念名の部分文字列一致であること、および不一致がそのまま断定的な
    事実文になること。
generalization:
  level: general
  general_form: 表層の文字列一致で同一性を判定し、言い換えや表記ゆれを不一致と断定する
pattern: substring-match-false-positive
discovery:
  perspective: [invariant_audit, trace_walk]
  note: >-
    「比較は採点しない・判定を権威に見せない」という条項と、自由記述の照合結果がどう文面に
    なるかを突き合わせた。照合は部分文字列一致で、外れた概念はそのまま「あなたの版には
    見当たりません」という断定の文になって返る。日英混在や記号表記の違いで必ず外れる。
resolution:
  perspective: [pending]
  note: >-
    未解決。照合を改善する道（別名・記号・正規化の語彙を使う）と、文面を仮説の言い方へ
    弱める道の二つがある。後者だけなら小さく、前者を採ると語彙の供給源を決める必要がある。
    どちらにせよ、断定の文面が先に直るべきである点は変わらない。
  landed_in: []
related: [IK-0006, IK-0001]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=local facets=[local.logic, connection.meaning]
    to: axes=processing=[logic]; structure=[none]; connection=[meaning]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は、学習者が出典と同じ内容を別の言い方で書いたときに「その概念への言及が見当たりません」と
返ることである。照合は概念名の部分文字列一致なので、日英が混ざったり、言い換えたり、記号の
表記が違ったりすれば必ず外れる。

原因は照合規則そのものにある。入力も契約も前提も妥当で、照合を直せば周囲には波及しない。
ただし出力の文面が断定形なので、外れた結果が「事実」として提示される点が害を増幅している。

## 発見の観点

「比較は採点しない・判定を権威に見せない」という条項と、照合結果がどう文面になるかを
突き合わせた。照合の弱さだけなら実装の話だが、断定の文面と組み合わさることで、条項が禁じた
「判定を権威に見せる」に実質的に当たっていた。

## 解決の観点

未解決。照合を良くする道と、文面を弱める道がある。文面を仮説の言い方に直すだけなら小さく、
照合に別名や記号の語彙を持ち込むなら供給源を決める必要がある。順序としては文面が先である。
外れたときの言い方が断定でなければ、照合の弱さは害ではなく情報になる。

## 一般化

同一性を表層の文字列で判定するすべての場所で再発する。照合の結果を断定形で提示すると害が
大きくなる。辞書の `substring-match-false-positive` に対応し、処方は「表層一致の結果は
断定ではなく仮説の言い方で返す」。
