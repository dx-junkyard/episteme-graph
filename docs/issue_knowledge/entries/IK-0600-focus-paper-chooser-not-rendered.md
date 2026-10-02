---
id: IK-0600
title: "焦点論文が決まらないとき、サーバは候補の論文を返しているのに学習画面のパネルが選ぶ部品を描かず、学習者が先へ進めない"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/features/simulation_bridge_design.md §12.2
feature_context:
  realizing: "学習画面の「動かして確かめる」パネルで、いま学んでいるトピックの論文の理論モジュールを動かす"
  layers: [simulation_bridge_sb, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [assignment]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: トピックの教材がチャンクに裏付けられていないと焦点論文の解決器はトピックの段で論文を決められず、
    サーバは `document_id: null` と候補 `documents` と事実文「動かす論文を選んでください。」を返していた。
    パネル（`frontend/public/js/simulation-lab.js`）は事実文だけを描き、候補を選ぶ部品を描かなかった。
    接続軸 information = サーバが渡した候補の一覧が画面の段で落ちた。統制軸 assignment = 「どの論文か」を
    決める主体はこの場面では学習者だが、その判断の場が用意されていなかった。処理軸は判定・計算の誤りではないので
    none。構造軸は表現・責務の置き場所ではなく描画の欠落なので none。
generalization:
  level: repo_pattern
  general_form: "サーバは利用者に選ばせる前提で候補を返すが、画面が候補を選ぶ部品を描かず、案内文だけが残って利用者が先へ進めない"
pattern: last-mile-missing
discovery:
  perspective: [trace_walk]
  note: "実機の dev スタックでテスト用の学生としてパネルを開き、入口から実行まで歩いたところ、案内文の後に操作できる部品が無く止まった。"
resolution:
  perspective: [carry_through]
  note: >-
    サーバが返す候補を画面まで運んだ。候補があれば「動かす論文」のセレクトを描き（候補が複数のとき、または
    まだ選ばれていないとき）、候補がちょうど一つで未選択なら画面がそれを採ったうえで題名を見せる（選択を隠さない —
    `default-hides-choice` を避けるため）。自動で採る判断はサーバではなく画面に置いた。サーバ側に「どの論文か」の
    新しい優先規則を足すと焦点論文の段順（FD）の外に条件式が増えるため、サーバは候補の提示に留める。
  landed_in:
    - frontend/public/js/simulation-lab.js
    - backend/tests/test_simulation_lab_ui_static.py
    - 0f533d4
    - docs/features/simulation_bridge_design.md §12.2
  verification:
    methods: [manual_ui, guardrail]
    unverified:
      - "複数の論文を束ねたコース（候補が複数のときのセレクトの操作）"
related: [IK-0599]
view_of: []
history: []
---

## 課題

学習画面の「動かして確かめる」パネルが行き止まりになった。トピックの教材がチャンクに裏付けられていないとサーバは焦点論文を
決められず、候補の論文と「動かす論文を選んでください。」という事実文を返す。パネルは事実文だけを描き、選ぶ部品を描かなかった
ので、学習者はそこから何もできなかった。

## 発見の観点

テスト用の学生として実機でパネルを入口から実行まで歩き、案内文の後に操作できる部品が無いところで止まった。

## 解決の観点

サーバが渡している候補を画面の段まで運び、選ぶ部品を描いた。候補が一つだけのときは画面が採るが、題名は見せたままにして
選択が行われたことを隠さない。サーバに自動選択を足す案は、焦点論文の段順の外に「どの論文か」の条件式を増やすので採らなかった。

## 一般化

利用者に判断を委ねる応答は、判断の場（部品）が画面にあって初めて完結する。案内文と候補を返す API を足したら、画面がその候補を
操作可能な形で描いているかを入口から歩いて確かめる。辞書の型は `last-mile-missing`。候補が一つのときの自動採用は
`default-hides-choice` に転びやすく、採った値を見せることで避けた。
