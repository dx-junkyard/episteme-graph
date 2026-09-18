---
id: IK-0316
title: 書き込み経路が役割だけを見て、対象の所有条件を確かめていなかった
status: resolved
recorded_at: 2026-07-20
resolved_at: 2026-07-20
sources:
  - docs/architecture/user_assistant_agents_survey_2026-07.md §8
  - docs/features/assistant_common_infra_design.md
feature_context:
  realizing: 教員が自分の担当教材の原稿だけを書き換える
  layers: [lecture_studio, auth_visibility]
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
    原因は「対象（どの教材・どのコースに属するか）という条件が、識別子を直接受け取る
    書き込み経路に伝わっていないこと」。役割の確認は行われ、識別子の解決も行われるが、
    その二つを結ぶ所有の条件が段の間で落ちている。同じファイルの設定系は所有を確認して
    おり、経路ごとに権限モデルが食い違っていた。調査記録 §8 が、識別子の直接指定で
    他教員のものを書き換えられる旨を実装位置つきで確認している。
generalization:
  level: general
  general_form: 役割の確認だけで書き込みを通し、対象の所有・可視性という条件を後段へ運ばない
pattern: condition-not-propagated
discovery:
  perspective: [inventory, boundary_walk]
  note: >-
    支援エージェント全件の横断比較で、経路ごとの権限ゲートを一覧にしたときに、同じ
    ファイル内で権限モデルが食い違う行として現れた。
resolution:
  perspective: [fail_closed, canonical_source]
  note: >-
    対象の編集権限を確かめる共通の関数を 1 つ置き、識別子を直接受ける経路はすべてそこを
    通す。役割の確認だけで通す経路を残さない。実装中に、同種の欠如がもう 1 経路にも
    あることが分かり同時に是正した（1 件見つかったら同型を数える）。
  landed_in:
    - backend/api/routes/lecture_studio/_shared.py
    - docs/features/assistant_common_infra_design.md
related: [IK-0314]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.condition, governance.assignment]
    to: axes=processing=[none]; structure=[none]; connection=[condition]; governance=[assignment]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

原稿の書き換えと手動保存は、教員という役割さえあれば実行できた。対象の識別子を直接指定
すれば、自分が担当していない教材の原稿も書き換えられる状態だった。

原因は、対象の所有・可視性という条件が書き込み経路へ伝わっていないこと。役割の確認は
行われ、識別子から対象を引くこともできるが、両者を結ぶ「この人がこの対象を編集してよいか」
が抜けていた。同じファイルの設定系は所有を確認しており、経路ごとに権限モデルが違っていた。

## 発見の観点

支援エージェントを全件棚卸しし（`inventory`）、経路ごとの権限ゲートを横断比較の表にした。
同じファイル内で権限モデルが食い違う行として出た。境界（識別子を受ける経路 × 権限）を
意図して歩いたことで、症状が出る前に見つかった（`boundary_walk`）。

## 解決の観点

対象の編集権限を確かめる共通の関数を正本として置き（`canonical_source`）、識別子を直接
受ける経路は必ずそこを通す（`fail_closed`）。1 件見つけた時点で同型を数え直したところ、
別経路にも同じ欠如があり同時に是正した。

## 一般化

権限には「誰か（役割）」と「何を（対象）」の二軸があり、役割だけで通る経路は、対象の
識別子を受け取る API が増えるたびに生まれる。対象を受ける入口は、対象側の条件を必ず
引き当てる。辞書の型 `condition-not-propagated` に対応する。
