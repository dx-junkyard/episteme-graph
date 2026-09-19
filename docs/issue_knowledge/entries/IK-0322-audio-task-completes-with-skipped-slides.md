---
id: IK-0322
title: 前提が揃わないまま一括生成を始められ、飛ばされた分があっても完了として扱われる
status: resolved
recorded_at: 2026-07-14
resolved_at: 2026-09-03
sources:
  - docs/features/lecture_audio_generation_readiness.md §1
  - docs/features/lecture_audio_generation_readiness.md §4
feature_context:
  realizing: 読み上げ原稿が揃っているコースに対して、音声をまとめて生成する
  layers: [lecture_studio, lecture_player, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [none]
    governance: [completion, ordering]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 各対象の生成処理は正しく、前提の無い対象を飛ばす振る舞いも仕様どおりである。代理の指標での判定を条件式の誤りと読む余地は残るため中。

    構造: 準備完了の判定が画面とサーバに分かれ、正本が無い点が集約に当たる。条件の受け渡しとも読めるため中。

    接続: 画面とサーバの判定がずれるのは正本が分散していることの結果で、段の間で条件が落ちているわけではないと判断した。

    統制: 開始条件を代理の指標で判定する点が順序に、飛ばした対象があっても全体を成功と扱う点が「済み」の定義に当たる。
generalization:
  level: general
  general_form: 開始条件を代理指標で判定し、飛ばされた対象があっても処理全体を成功として扱う
pattern: completion-defined-by-proxy
discovery:
  perspective: [boundary_walk, symptom_report]
  note: >-
    「コースを選ぶ前に言語選択が開く」という症状から操作の状態遷移を書き起こし、
    生成開始の条件・ワーカーのスキップ規則・完了の扱いを並べたときに、完了の定義が
    実態と合っていないことが分かった。
resolution:
  perspective: [canonical_source, fail_closed]
  note: >-
    ①操作を状態起点にする（コースを選ぶ → 読み上げ可能を確認 → 言語を選ぶ → 生成）
    ②開始条件を画面とサーバの両方で強制し、不足があれば理由と次の操作を示して拒否する
    ③準備完了の判定はサーバ側の 1 つの関数を正本にし、案内とボタンの活性が食い違わない
    ようにする。ワーカーのスキップを黙認したまま完了と呼ぶ扱いはやめた。
  landed_in:
    - backend/core/lecture.py
    - backend/api/routes/lecture_studio/scripts.py
    - frontend/public/js/admin-lecture-studio.js
    - docs/features/lecture_audio_generation_readiness.md
related: [IK-0321, IK-0317]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.completion, governance.ordering]
    to: axes=processing=[none]; structure=[none]; connection=[none]; governance=[completion,
      ordering]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: axes=processing=[none]; structure=[none]; connection=[none]; governance=[completion, ordering]
    to: axes=processing=[none]; structure=[aggregation]; connection=[none]; governance=[completion, ordering]
    reason: 軸ごとの再判定で、準備完了の判定に正本が無く画面とサーバに分散していた点を構造軸の要素として認めた
---

## 課題

症状は二つ。①原稿スタジオを開いた直後、コースを選ぶ前に音声の言語選択が開き得る（対象が
決まっていないので選ぶ意味が定まらない）。②読み上げ原稿が未生成の対象を含んだまま一括生成を
開始でき、ワーカーはそれを飛ばすため、**完了と表示されても一部に音声が無い**。

原因は、生成の可否を代理の指標（コース内容が生成済みか・チャンクがあるか）で判定し、実際に
必要な前提（対象それぞれに読み上げ原稿があるか）を見ていないこと。飛ばした事実も完了の
判定に反映されない。

4 軸で見直すと、構造軸にも要素がある。準備完了の判定が画面とサーバに分かれて正本が無かったため、案内とボタンの活性と実行の可否が別々の根拠を見ていた。

## 発見の観点

「コースを選ぶ前にモーダルが開く」という症状（`symptom_report`）から操作の状態遷移を書き
起こし、開始条件・スキップ規則・完了の扱いという境界を並べた（`boundary_walk`）。完了の定義が
実態と合っていないことがそこで見えた。

## 解決の観点

準備完了の判定はサーバ側の 1 関数を正本にして、案内・ボタンの活性・実行の可否が同じ根拠を
見るようにした（`canonical_source`）。そのうえで操作を状態起点の流れに直し、開始条件を
画面とサーバの両方で強制して、不足があれば理由を示して拒否する（`fail_closed`）。画面側
だけを直す案は、UI を経由しない呼び出しで同じ状態に戻るため採らなかった。

## 一般化

一括処理は「始められたか」と「全部できたか」を別々に定義しないと、スキップが成功に化ける。
開始条件は対象ごとの実体で確かめ、完了は飛ばした分を含めて定義する。
辞書の型 `completion-defined-by-proxy` に対応する。
