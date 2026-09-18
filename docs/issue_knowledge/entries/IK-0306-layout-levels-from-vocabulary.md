---
id: IK-0306
title: グラフの段を分野語彙と固定の深さ上限から決めたため、ノードが一直線に並び構造が読めない
status: resolved
recorded_at: 2026-09-17
resolved_at: 2026-09-17
sources:
  - docs/features/graph_dialogue_review_design.md §17
feature_context:
  realizing: 理論操作グラフの依存の向きを見取り図として読ませる
  layers: [graph_review, frontend_admin_ui, lecture_studio]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [none]
    governance: [none]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「ノードの段（縦位置）を、構造である辺ではなくラベルの語彙から導いていたこと」。
    上位構成のラベルはほぼ全てが同じ語彙に落ちるため全ノードが同じ段から始まり、さらに
    深さに固定上限があるため段が潰れる。定数を変えるだけでは、語彙が変われば同じ症状に
    戻るので構造。設計書 §17 が、式の詳細層で 30 ノード中 28 個が同じ段に入る実測を記録
    している。
generalization:
  level: repo_pattern
  general_form: 構造から導けるはずの配置や分類を、対象領域の語彙に依存した規則で決める
pattern: domain-vocabulary-hardcoded
discovery:
  perspective: [symptom_report, invariant_audit]
  note: >-
    「ノードが一直線に並んで読めない」という指摘を、レイアウト関数の段決定規則と
    「特定分野・特定論文の用語をハードコードしない」という既存の設計原則に突き合わせた。
    並び順の規則も特定論文の語彙の正規表現だった。
resolution:
  perspective: [representation_change, guardrail_fix]
  note: >-
    段は辺だけから決める（向きが前後関係を表さない辺は段に使わない／後退辺を落として
    最長路で段を取り、深さに上限を置かない／段内はバリセンタ法／連結成分ごとに横に並べ、
    孤立ノードは格子に畳む）。語彙依存の段決定と並び順の関数は撤去した。深さ上限を
    広げるだけの案は、語彙由来の初期段が残るため採らなかった。同じ §17 で、教員が
    ドラッグした位置に保存先が無い（再描画で必ず自動配置へ戻る）問題も、端末内の記憶を
    自動配置に重ねる形で併せて解いている。
  landed_in:
    - frontend/public/js/admin-lecture-studio.js
    - frontend/public/js/admin-graph-review.js
    - docs/features/graph_dialogue_review_design.md §17
related: [IK-0303]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.representation, local.logic]
    to: axes=processing=[logic]; structure=[representation]; connection=[none]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は「グラフを開くとノードが横一列に伸び、どれがどれの前提なのか読めない」。

原因は、段（縦位置）の決定規則が**ラベルの語彙**を起点にしていたこと。上位の理論構成の
ラベルは語彙上ほとんどが同じ分類に落ちるため、全ノードが同じ段から始まる。さらに辺で
伝播させる段に固定の深さ上限が掛かっていたため、深い依存関係も頭打ちになった。段内の
並び順も、特定論文の語彙にあてた正規表現で決まっていた。

## 発見の観点

利用者（教員）の指摘（`symptom_report`）を起点に、レイアウト関数の段決定規則を読み、
「特定分野・特定論文の用語をハードコードしない」という既存の設計原則と突き合わせた
（`invariant_audit`）。原則違反と可読性の症状が同じ行に由来していた。

## 解決の観点

段の根拠を語彙から**構造（辺）**へ移した（`representation_change`）。深さの上限は置かず、
循環があっても止まらない導出にした。上限値だけを広げる案は、語彙由来の初期段が残るので
採らなかった。深さの上限が無いこと・段が構造由来であること・特定語彙で並べないことを
テストで固定した（`guardrail_fix`）。

## 一般化

分類・配置・優先順位を「その分野ならこう呼ぶはず」で決めると、分野が変わった瞬間に全件が
同じ値に落ちて機能が消える。しかも消え方が静か（エラーにならない）なので気づきにくい。
新パターン `domain-vocabulary-hardcoded` として辞書へ提案する。
