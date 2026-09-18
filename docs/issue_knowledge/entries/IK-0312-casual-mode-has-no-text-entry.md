---
id: IK-0312
title: 会話の調子を利用者に選ばせる設計のまま、テキスト側にその入口が用意されていなかった
status: resolved
recorded_at: 2026-07-20
resolved_at: 2026-09-12
sources:
  - docs/architecture/user_assistant_agents_survey_2026-07.md §8
  - docs/features/learning_chat_entry_unification_design.md §1
feature_context:
  realizing: 学習者が気軽な問いかけも含めて、ひとつの入口から対話する
  layers: [rag_chat, frontend_learning_ui]
classification:
  primary: structure
  facets: [structure.responsibility, connection.condition]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「会話の調子という判断を利用者側に置き、その選択肢を UI の語彙として並べる
    設計にしたこと」。サーバは調子を受け取って完全に処理できるのに、テキストからは
    渡せず、選べない学習者の発話は拒否文に落ちる。入口を 1 つ足すだけでは、次に調子が
    増えるたび同じ分岐が UI に現れるため構造（判断の置き場所）。設計書 §1 が
    「サーバは 4 値の意図分類器を既に持つのに、casual と discuss はそれを丸ごと
    バイパスする明示スイッチとして上に積まれている」と確認している。
generalization:
  level: repo_pattern
  general_form: 内部の分岐が利用者の選択肢として並び、選べない経路の利用者には機能が存在しないのと同じになる
pattern: available-but-unwired
discovery:
  perspective: [inventory, doc_code_diff]
  note: >-
    対ユーザー支援エージェントを全件棚卸しし、各機能の対応モードと実際の入口を突き合わせた。
    サーバの対応表には在るのに画面の入口が無い行として出た。
resolution:
  perspective: [responsibility_move, explicit_contract]
  note: >-
    入口を 1 つにして、調子は当該発話からサーバが読む形へ判断を移した。推定してよいのは
    調子だけで、検索範囲・出題モード・記録の私有化は明示のまま（推定の対象を契約として
    限定）。推定した事実は本人に見せ 1 タップで覆せる。判定不能は既定の調子へ倒す。
  landed_in:
    - backend/core/learning_stance/schema.py
    - backend/core/learning_stance/heuristic.py
    - frontend/public/js/app.js
    - docs/features/learning_chat_entry_unification_design.md §13
related: [IK-0309]
view_of: []
history: []
---

## 課題

症状は「キーボードで学習している人は、気軽な調子で話しかけると定型の拒否文を返される」。

原因は、会話の調子を利用者が選ぶ設計にしたうえで、その選択肢をハンズフリー音声の経路に
しか用意しなかったこと。サーバ側は調子を受け取って完全に処理できるにもかかわらず、
テキストからは渡す手段が無い。加えて、調子を選べない利用者の雑談めいた発話は、意図分類が
雑談と判定して拒否文へ落ちていた。

## 発見の観点

対ユーザー支援エージェントの全件棚卸し（`inventory`）で、機能ごとの対応モードと実際の
入口を表にしたところ、サーバの対応表には在るのに画面の入口が無い行として現れた
（`doc_code_diff`）。

## 解決の観点

「どう話すか」を選ばせるのをやめ、調子の判断をサーバへ移した（`responsibility_move`）。
ただし推定してよいのは調子だけで、検索範囲・出題モード・記録の私有化は明示のままにする
契約を置いた（`explicit_contract`）。分類不能・例外は既定の調子へ倒す。
テキスト用のモード切り替えボタンを足すだけの案は、UI に内部語彙を増やすので採らなかった。

## 一般化

内部の分岐をそのまま選択肢として画面に出すと、①利用者は内部語彙を学ばされ、②その選択肢が
届かない経路では機能が存在しないのと同じになる。分岐が内部の都合なら、判断は内側に置く。
辞書の型 `available-but-unwired` に対応する。
