---
id: IK-0010
title: 分野を選ぶ口が入口に無く、既定の分野が全ての解析に当たる
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-10
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §2 F9
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §5 C1
  - docs/architecture/six_lenses_2026-09-10/06_coldstart.md
feature_context:
  realizing: 新しい研究室が最初の教材を取り込み、その分野として解析が走るようにする
  layers: [pipeline_a, cartridges, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [assignment]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「分野を決める判断が、入口の人の操作ではなく環境の既定値に割り当てられている」
    こと。解析処理は与えられた分野で正しく動いており、表現にも欠けが無い。誰がいつ分野を
    決めるかの割り当てを変えないかぎり、既定値を差し替えても別の分野が黙って当たる。確認
    手段は F9 行が引く既定値と、取り込みの受け口に分野の引数が無かったこと。
generalization:
  level: general
  general_form: 選ぶべき前提が入口に現れず、環境の既定値が全ての処理に黙って当たる
pattern: default-hides-choice
discovery:
  perspective: [invariant_audit, boundary_walk]
  note: >-
    新しい研究室が最初の一本を取り込むところから歩いた。分野を選ぶ画面も引数も無く、
    別分野の論文でも既定の分野として立ち上がる。入口の正直さと、出所を偽らないという
    条項に照らすと、選んでいないものを選んだことにしていた。
resolution:
  perspective: [responsibility_move, fail_closed]
  note: >-
    既定値を空にし、取り込みの三経路すべてに分野の引数を通した。「指定しない」を一級の
    選択肢として入口に出し、指定が無いときは分野の語彙を読まない中立の経路が走る。
    分野を当てにいくのではなく、選ばれていないことをそのまま扱う方向で解いた。
  landed_in:
    - backend/api/routes/admin.py
    - backend/core/document_pipeline/orchestrator.py
    - frontend/public/js/admin.js
    - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 7
related: [IK-0011]
view_of: [IK-0011]
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.assignment, connection.condition]
    to: axes=processing=[none]; structure=[none]; connection=[condition]; governance=[assignment]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は、どの分野の論文を取り込んでも特定の一分野として解析が走ることである。取り込みの
受け口には分野を渡す引数が無く、環境の既定値がそのまま全ての実行に当たっていた。新しい
研究室は、選んでいない分野として立ち上がる。

原因は、**分野を決める判断が入口の人から環境の既定値へ移っていた**ことにある。解析自体は
与えられた分野で正しく動く。誰がいつ決めるかだけが抜けていた。

## 発見の観点

立ち上がりの観点で、最初の一本を取り込むところから順に歩いた。分野を選ぶ画面も引数も
見つからない。入口の正直さという観点に照らすと、選ばれていない前提を選ばれたものとして
扱っていた。

## 解決の観点

既定値をもっと無難なものに替える案は採らなかった。何を既定にしても「選んでいないのに
選んだことになる」性質は残るからである。分野を決める主体を入口の教員へ戻し、既定を空にして選択を出し、「指定しない」を
一級の選択肢として扱った。指定が無いときは分野の語彙を読まない経路が走る。

## 一般化

環境変数や設定ファイルの既定値が、利用者の選択の代わりに働いている場所すべてで再発する。
既定が無難であるほど発見が遅れる。辞書の `default-hides-choice` に対応し、処方は
「選ばれていないことを表せる値を用意し、入口で選べるようにする」。
