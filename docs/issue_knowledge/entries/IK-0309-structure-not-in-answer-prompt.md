---
id: IK-0309
title: 画面が見せている構造成果と選択箇所が、AI への入力に一度も入っていなかった
status: resolved
recorded_at: 2026-09-06
resolved_at: 2026-09-12
sources:
  - docs/features/assistant_screen_adapter_design.md §1
  - docs/features/assistant_screen_adapter_design.md §11.1
  - docs/architecture/user_assistant_agents_survey_2026-07.md §8
feature_context:
  realizing: 画面で見ているものについて、その構造を踏まえて AI と対話する
  layers: [screen_adapter_sa, graph_review, rag_chat]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [information]
    governance: [none]
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
    処理: 構造の生成も表示も対話も、単体では正しく動いている。

    構造: 同じ対象を扱う二つの機能のうち、どちらが構造を解決して渡すかが決まっていない点が責務に当たる。集約の不足とも読めるため中。

    接続: 画面が描いている構造が対話の入力へ一度も渡らない点が情報に当たる。

    統制: 取得の道具を持たせる案を退けた理由は統制だが、原因側に順序・予算・割り当ての崩れは無い。
generalization:
  level: repo_pattern
  general_form: 生成済みの成果が別の画面からしか使われず、同じ対象を扱う機能に配線されていない
pattern: available-but-unwired
discovery:
  perspective: [boundary_walk, inventory]
  note: >-
    「画面が知っていること」と「AI が知っていること」の境界を意図的に歩き、構造ごとに
    学習者が触れる経路と回答プロンプトへの到達を表にした。表の右列がすべて未到達だった。
resolution:
  perspective: [carry_through, explicit_contract]
  note: >-
    画面は参照だけを渡し、サーバが既存の権限ゲート付き経路で解決して当該ターンの入力に
    足す、というアダプターの規約を置いた。LLM にデータ取得の道具を持たせる案は、呼び出し
    回数と予算の統制が崩れるため採らなかった。画面のテキストをそのまま送る案も、申告と
    根拠が区別できなくなるため採らない。権限外・不在の参照は静かに落とす。
  landed_in:
    - backend/core/assistant_context/registry.py
    - backend/core/assistant_context/resolvers/graph_review.py
    - docs/features/assistant_screen_adapter_design.md §10
    - docs/features/assistant_screen_adapter_design.md §11.15
related: [IK-0304, IK-0312]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.information, structure.responsibility]
    to: axes=processing=[none]; structure=[responsibility]; connection=[information];
      governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は「グラフを見ながら AI に尋ねても、画面の右ペインに出ている章・式番号・逐語引用に
ついて何も知らない」「段落を選んで『ここが分からない』と送っても、どこかを知らされずに
検索結果で答える」。

原因は、構造成果を対話の入力へ渡す経路が存在しなかったこと。対話側は対象の識別子から
作る固定の材料しか持たず、選択テキストは痕跡の記録にだけ使われて入力には入っていなかった。
生成も表示も正しく動いていたので、欠けていたのは受け渡しだけである。

## 発見の観点

「画面が知っていること」と「AI が知っていること」の境界を意図して歩き（`boundary_walk`）、
構造の種類ごとに、学習者が触れる経路と回答プロンプトへの到達を一覧にした（`inventory`）。
到達の列がすべて空だったことが証拠になった。入力の組み立てを 1 本辿って 5 要素しか
入らないことも確かめた。

## 解決の観点

画面が持っている構造を当該ターンの入力まで運び（`carry_through`）、参照だけを渡して解決は
サーバ側の既存ゲート経由で行う、という規約を置いた（`explicit_contract`）。解決結果は当該
ターンの入力にだけ足し、保存はしない。権限外・不在は静かに落とす。LLM に取得の道具を
持たせる案は、呼び出し回数・予算・確定の主体という既存の統制を崩すので恒久的に排除した。

## 一般化

作った構造が「別の画面からしか使われない」状態は、機能が増えるほど起きる。同じ対象を
扱う機能が複数あるとき、片方の成果がもう片方に届いているかを境界として点検する。
辞書の型 `available-but-unwired` に対応する。
