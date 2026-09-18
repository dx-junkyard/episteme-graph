---
id: IK-0007
title: 承認の操作が解析時の警告を空で上書きし、見た証拠も見なかった証拠も残らない
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-10
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §2 F6
  - docs/architecture/six_lenses_2026-09-10/05_ai.md §2 所見B
feature_context:
  realizing: 解析が出した構造を教員が確かめて承認し、学習者に届く状態にする
  layers: [graph_review, deliberation_w]
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
    原因は「解析時点の警告と、承認後の現在の警告を区別して置く場所が無い」こと。承認処理は
    行を書き戻しているだけで、権限も版も落ちていない。二つを別の場所に持たないかぎり、
    上書きの書き方を変えても片方が消える。確認手段は F6 行が引く承認時の代入と、承認画面の
    どこにも警告が並んでいないこと。
generalization:
  level: general
  general_form: 状態を進める操作が、その判断の材料になった記録を同じ場所ごと上書きする
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [adversarial_review, invariant_audit]
  note: >-
    承認という弁が形だけにならないかという観点で、承認ボタンの周りに何が提示されているかを
    数えた。否定側の検査が一つも無く、しかも承認の瞬間に解析時の警告が空になる。見て承認した
    のか見ずに承認したのかを、後から区別する材料が残らない。
resolution:
  perspective: [representation_change, carry_through]
  note: >-
    消去をやめて退避させ、承認画面に並置した。解析時点の警告は判断の材料であって現在の状態
    ではないので、同じ場所に置かないことで両立する。承認の是非を機械が言うのではなく、
    材料を確定者の目の前に運ぶ方向で解いた。
  landed_in:
    - backend/api/routes/theory_components.py
    - frontend/public/js/admin-graph-review.js
    - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 5
related: [IK-0008, IK-0010]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.representation, governance.review]
    to: axes=processing=[none]; structure=[representation]; connection=[none]; governance=[review]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は、承認済みの構造を後から見ても、解析が出していた警告が一つも残っていないことである。
承認の操作が警告の列を空の配列で上書きしていた。承認画面にもその警告は並んでいなかったので、
教員が警告を見たうえで承認したのか、そもそも見なかったのかを、あとから区別できない。

原因は上書き処理そのものではなく、**解析時点の警告と現在の警告を別々に置く場所が無い**ことに
ある。列が一つしかないので、状態を進めるたびに判断の材料が消える構造になっていた。

## 発見の観点

承認という弁がゴム印にならないかという観点で、承認ボタンの周囲に提示されているものを数えた。
否定側の検査は一つも無く、警告は承認の瞬間に消える。確定の記録に「既知の不確実性」を残す
という条項に照らすと、その材料が構造的に落ちていた。

## 解決の観点

上書きを丁寧にする案ではなく、置き場所を分ける案を採った。解析時点の警告は判断の材料であって
現在の状態ではないので、退避先を用意すれば上書きと両立する。あわせて承認画面に並置し、
確定者の目の前に材料が出るようにした。

## 一般化

「状態を進める」操作が、進める前の材料と同じ場所を使っている場所すべてで再発する。下書きと
確定、候補と採用、警告と現状が一つの列を共有しているときに起こる。辞書の
`information-dropped-as-unrepresentable` に対応する。
