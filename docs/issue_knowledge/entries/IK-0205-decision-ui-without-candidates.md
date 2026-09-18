---
id: IK-0205
title: 確定する画面はあるのに候補を生む経路と入口が無く、人が確定できないまま止まる
status: resolved
recorded_at: 2026-07-17
resolved_at: 2026-07-18
sources:
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §2 P1
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md 追補
feature_context:
  realizing: AI は候補までを作り、人間が確定するという原則を、実際に回る一連の操作として提供する
  layers: [deliberation_w, image_library_l, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals:
    - kind: value
      target_axis: connection
      neighbor_of: [connection.information, connection.contract]
      statement: 前段と後段をつなぐ経路そのものが用意されず、後段が前段の成果を一度も受け取らない
      confidence: medium
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 確定する側も候補を作る側も、単体では設計どおりに動く。
    構造: 確定するという責務だけが実装され、候補を生む責務の置き場所が決まっていない。確定側を
    作り込んでも手前は埋まらない。分割の粒度との境界で迷った。
    接続: 候補を作る側へ材料（実在する識別子の一覧）が渡らず、確定側へ候補が到達しない。落ちて
    いるのは値というより経路そのもので、既存の値との当たりは完全ではない。
    統制: 担当・順序・予算・完了判定のいずれにも崩れは無い。確定の装置を先に置く実装の順番を
    完了判定の問題と読む余地はあるが、実行時の統制ではないため要素は無いと判断した。
generalization:
  level: repo_pattern
  general_form: >-
    確定・保存の装置はあるが、そこへ至る候補の生成と入口が無く、利用者が装置に到達できない
discovery:
  perspective: [trace_walk, invariant_audit]
  note: >-
    「人が確定する」と宣言している原則ごとに、候補が生まれてから確定されるまでの操作列を
    一本ずつ歩いた。確定の画面は存在するのに、その手前で候補がひとつも生まれないことが
    判明した。指示側は実在する識別子を要求するのに、材料を渡す側がそれを供給していなかった。
resolution:
  perspective: [carry_through, responsibility_move]
  note: >-
    材料の供給（同じ分野の既存項目の一覧）を対話の入力へ運び、供給がゼロのときは候補を
    作らせないという約束を同時に置いた（材料が無いと作り話が生まれるため）。併せて一覧の
    詳細に検討の入口を足し、手で結び付ける操作と、候補を確定する操作を画面に用意した。
  landed_in:
    - frontend/public/js/deliberation.js
    - frontend/public/js/admin.js
    - docs/architecture/vision_ux_gap_survey_2026-07-17.md 追補
related: [IK-0204]
view_of: []
pattern: last-mile-missing
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.information, structure.responsibility]
    to: axes=processing=[none]; structure=[responsibility]; connection=[information];
      governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 前年の調査で直したはずの「届いていない機能」が、形を変えて再発した。今度は
**確定装置だけがある**という奇形で、①標準化の評価は画面から開く入口がひとつも無い
②論文をまたぐ同一性のリンクは確定と却下の画面だけがあり、確定すべき候補が生まれない
③説明に紐づく根拠の確定ボタンが無く、候補のまま永久に残る。

**原因**: 候補を作る側へ材料が渡っていない。対話の指示文は実在する識別子を要求するのに、
材料を組み立てる側がその一覧を供給しないため、AI は候補を出せないか、存在しない識別子を
作って確定時に失敗するかの二択になっていた。手で候補を作る入口も無かった。

**軸ごとの再判定（2026-09-19）**: 座標は変えていない。ただし接続軸は「材料が落ちる」より
「候補を運ぶ経路が用意されていない」に近く、既存の値との当たりが完全ではないため確信を中とし、
新しい値の提案を 1 件出した。統制軸は、確定の装置を先に置く実装の順番を完了判定の問題と読む
余地はあるが、実行時の統制ではないので要素は無いと判断した。

## 発見の観点

`trace_walk` と `invariant_audit`。「AI は候補・確定は人間」という原則が要求する操作列を、
候補の生成 → 提示 → 確定の順に一本ずつ歩いた。確定側だけを見ると正常に見えるため、
手前まで遡らないと分からない。

## 解決の観点

`carry_through`（材料を対話の入力まで運ぶ。供給がゼロなら候補を作らせない、という作り話の
防止も同時に置いた）+ `responsibility_move`（手で結び付ける操作と確定の入口を人に開く）。確定側を作り込む案は採らなかった — 不足は確定手段ではなく、その手前だった。

## 一般化

「人が確定する」設計では、確定の画面が原則の象徴なので先に作られ、候補の供給と入口が
後回しになりやすい。承認フロー・レビューキュー・アラート運用など、確定の装置を先に置く
あらゆる設計で同型が出る。辞書の型は `last-mile-missing`。
