---
id: IK-0691
title: "リリース前の確認で、確認する配置が 0 件のとき、ステップ 2 にもステップ 3 にも「確認するものはありませんでした」の事実文が無く、「次へ」が何を記録したのか（何も記録しなかったのか）が読めない"
status: open
recorded_at: 2026-10-10
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/release_review_flow_design.md"
feature_context:
  realizing: "教員がリリース前の確認で、AI 推定の配置を見て「次へ」を押し、何を確認したかを確かめてから公開する"
  layers: [release_review, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [review]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: admin-release-review.js の acceptPlacements は pending_count が 0 のとき POST せずに advance() で
    ステップ 3 へ進み、recordedSummaryHtml は記録も行の判断も無いと区画ごと空を返す。ステップ 2 の脚注も
    未確認の件数が 0 のときは何も言わない。構造軸 representation = 画面の要約が「記録したもの」しか表せず、
    「確認する対象が無かった」という状態を表す形が無い。統制軸 review = 確認の手続の結果（何も確認しなかった）が
    確認者に告げられない。サーバ側は第 3 波で 0 件の「次へ」も監査に残すが、この経路は POST しないので記録も無い
    （対象が無いので記帳しないこと自体は DC1 に反しない）。処理軸 none。接続軸 none。
generalization:
  level: repo_pattern
  general_form: "結果の要約が「行ったこと」だけを表す形で、対象が 0 件だったときに区画ごと消え、利用者は何も起きなかったのか表示が壊れたのか区別できない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "第 23 周の再演（第 3 波反映後）で、急ぐ教員の自分のコースの配置が別の教員の確認で既に confirmed になっており、「次へ」が POST せずにステップ 3 へ進んだ。頭脳は「次の画面に何も書いていない」と書いた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（frontend + label_vocab）: pending が 0 のとき、ステップ 2 の「次へ」の直上と脚注に「未確認の候補はありません（次へで新たに記録されるものはありません）」、ステップ 3 の要約に「確認する配置はありませんでした（すべて確認済み・却下済み・対象外）」の事実文を固定文の正本から出す。"
related: [IK-0649, IK-0674, IK-0688]
view_of: []
history: []
---

## 課題

症状: 確認する配置が無い状態で「次へ」を押すと、ステップ 3 に何の要約も出ない。原因: 要約が「記録したもの」しか表さず、0 件の状態を言う文が無い。

## 発見の観点

ペルソナの再演（reproduction）。砂場の状態（別の教員が先に確認していた）でこの経路に入った。

## 解決の観点

未解決。0 件の状態を固定の事実文で言えば足りる見込み。

## 一般化

「したこと」を並べる要約は、何もしなかった回に空になり、空と故障の区別がつかない。確かめ方は、対象 0 件の状態で同じ操作をして、0 件であることを言う文が出ることを見ること。
