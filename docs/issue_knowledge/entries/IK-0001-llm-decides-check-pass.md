---
id: IK-0001
title: 確認問題の合否を LLM が返し、そのままトピック完了として永続化される
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-10
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §2 F1
  - docs/architecture/six_lenses_2026-09-10/05_ai.md §2 所見E
  - docs/architecture/six_lenses_2026-09-10/01_learner.md §2 提案2
feature_context:
  realizing: 学習者が自分の言葉で理解を書き、次の単元へ進めるようにする
  layers: [rag_chat, reconstruction_r]
classification:
  primary: governance
  facets: [governance.assignment, governance.completion]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「生成の出力が、そのまま人の状態を確定させる権限まで持っている」こと。
    判定処理そのものは動いており、表現にも段階間の欠落にも問題はない。誰が確定者かの
    割り当てを変えないかぎり、判定精度をいくら上げても同じ構造が残る。確認手段は
    05_ai.md 所見E が引く合否生成から完了永続化までの一続きの経路と、例外時の
    文字数フォールバック。
generalization:
  level: general
  general_form: 候補を出すための仕組みが、そのまま人の状態を確定させる権限まで併せ持つ
pattern: ai-decides-instead-of-human
discovery:
  perspective: [invariant_audit, trace_walk]
  note: >-
    「確定は人間の判断を含む手続にのみ」「比較は採点しない」という不変条項と現行実装を
    照合し、さらに「比べる」実装を横に並べたときに見えた。三つのうち二つは並置に留めて
    いるのに、一つだけが合否を出して進行を止めていた。
resolution:
  perspective: [responsibility_move, explicit_contract]
  note: >-
    判定精度を上げる方向ではなく、確定の主体を本人へ戻す方向で解いた。生成側は要件の
    並置までとし、完了は本人の一操作にする。生成が失敗したときも要件の並置へ縮退させ、
    文字数による代理判定と不合格回答の逐語記帳は経路ごと外した。
  landed_in:
    - backend/core/check_review.py
    - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 1
related: [IK-0003, IK-0004, IK-0005]
view_of: []
history: []
---

## 課題

症状は、学習者が確認問題に自分の言葉で答えると「合格 / 不合格」が返り、合格ならトピック完了・
コース完了が学習状態へ書き込まれることである。不合格の場合は回答の逐語が学習者 ID 付きで
つまずき記録へ流れていた（その記録を読むコードは一つも無かった）。生成が例外になったときの
代替は「一定の文字数以上書けば合格」だった。

症状から遡った原因は、判定の質ではなく**確定の割り当て**にある。候補を出す側（生成）が、
そのまま人の状態を確定する側の権限を持っていた。全経路を見渡しても、生成の出力が人の状態を
そのまま確定させるのはここだけで、他の「比べる」実装は並置に留めていた。

## 発見の観点

不変条項（確定は人間・比較は採点しない・評価に使わない）と現行実装の照合が入口。さらに
「比べる」行為の実装を棚卸しして横に並べたとき、同じ行為に三つの異なる認識論が同居して
いることが見えた。二つは並置で止まり、一つだけが進行のゲートになっていた。入力から
永続化までを一本辿ったことで、例外時の代理判定という第二の穴も出た。

## 解決の観点

「判定を賢くする」案は採らなかった。賢くしても確定者が移らないからである。生成の役割を
要件の並置へ縮め、完了は本人の一操作に戻した。縮退時も同じ役割に留まるようにして、
文字数という代理指標と、読み手のいない逐語記帳を撤去した。

## 一般化

候補生成と確定が同じ経路に載っていると、どの領域でも再発する。推薦がそのまま適用になる、
検出がそのまま削除になる、といった形を取る。辞書の
`ai-decides-instead-of-human` に対応し、確定者の位置を先に決めてから精度の話をする、
という順序が処方になる。
