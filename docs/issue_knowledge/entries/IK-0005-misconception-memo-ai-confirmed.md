---
id: IK-0005
title: 誤解メモを AI が即座に確定して書き、本人は撤回できず、古いものは黙って消える
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-10
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §2 F5
  - docs/architecture/six_lenses_2026-09-10/01_learner.md §2 提案3
feature_context:
  realizing: 学習者との対話から引っかかりを拾い、本人が後から見返せるようにする
  layers: [rag_chat, personal_network]
classification:
  primary: governance
  facets: [governance.assignment, governance.review, structure.representation]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「本人についての記述を、本人の確認を経ずに確定として書く割り当てになっている」
    こと。検出処理も保存処理も定義どおり動いており、条件も版も落ちていない。確定者の
    割り当てと、候補という状態の置き場所を作らないかぎり、検出方式を変えても判決文が
    書かれ続ける。確認手段は F5 行が引く検出と保存の箇所、および上限による切り詰め。
generalization:
  level: repo_pattern
  general_form: 本人についての記述を本人の確認なしに確定として書き、取り下げる口を用意しない
pattern: ai-decides-instead-of-human
discovery:
  perspective: [invariant_audit, inventory]
  note: >-
    「AI は候補まで」という条項と、学習者について書かれる記録の棚卸しを突き合わせた。
    他の痕跡はすべて候補から本人の確定へ進む段を持つのに、誤解メモだけが最初から確定として
    保存され、状態語彙にも撤回が無かった。上限を超えた古い行が黙って落ちる点も併せて出た。
resolution:
  perspective: [state_transition, responsibility_move]
  note: >-
    検出方式には触れず、書かれる先の状態を候補へ格下げして、本人の三択（引き受ける・
    違う・取り下げる）を置いた。取り下げは行の削除ではなく状態の遷移にし、件数上限による
    切り詰めは撤廃した。監査の語彙も一つ足して、誰がいつ状態を動かしたかを残す。
  landed_in:
    - backend/api/services.py
    - backend/core/personal_graph/graph_data.py
    - frontend/public/js/app.js
    - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 2
related: [IK-0001, IK-0006, IK-0010]
view_of: []
history: []
---

## 課題

症状は、対話の途中で「あなたはここを誤解しています」という趣旨のメモが本人の確認なしに保存され、
本人はそれを違うと言うことも取り下げることもできず、さらに件数が一定を超えると古いものから
黙って消えることである。

原因は二つ重なっている。ひとつは、**本人についての記述を確定として書く権限が検出側にあった**
こと。もうひとつは、候補・撤回という状態の置き場所が最初から無かったことで、書くか書かないかの
二値しか表現できなかった。判決文の形をした記述が、本人の手の届かない場所に積まれていた。

## 発見の観点

「候補までは AI、確定は人」という条項と、学習者について書かれる記録の棚卸しを突き合わせた。
他の痕跡が候補から本人の確定へ進む段を持っているのに、この記録だけが段を持たない。状態語彙に
撤回が無く、上限超過分が黙って落ちることも同じ棚卸しで出た。

## 解決の観点

検出の精度は今回の争点ではないと見て触れなかった。書かれる先の状態を候補へ格下げし、本人の
三択を置くことで、同じ検出のまま確定者だけが移る。取り下げは削除ではなく状態遷移にして
履歴を残し、件数上限の切り詰めは撤廃した。

## 一般化

利用者本人を主語にした記述を機械が生成するすべての場所で再発する。ラベル付け・分類・
プロファイルの推定がその形を取りやすい。辞書の `ai-decides-instead-of-human` に対応し、
候補という状態と取り下げの口を先に用意しておくことが処方になる。
