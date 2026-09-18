---
id: IK-0319
title: 対話本文の正本が上書き・削除で消え、後から何が起きたかを再構成できない
status: deferred
recorded_at: 2026-07-20
resolved_at: null
sources:
  - docs/architecture/user_assistant_agents_survey_2026-07.md §8
feature_context:
  realizing: AI との対話を後から検証・説明できる形で残す
  layers: [rag_chat, deliberation_w, admin_copilot]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [completion]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「対話本文の保存が現在の状態のみを持ち、変更・削除の記録を残さないこと」。
    各処理は仕様どおりに動いており、欠けているのは『何が起きたかを後から示せること』の
    設計であるため統制。調査記録 §8 が、履歴は上書き・削除で正本が消え、痕跡は質問側
    しか残らないことを確認している。
generalization:
  level: general
  general_form: 状態変更の記録を持たない書き込み経路があり、後から経緯を再構成できない
pattern: unaudited-write-path
discovery:
  perspective: [inventory, invariant_audit]
  note: >-
    支援エージェント横断調査で、4 者それぞれの説明責任（何が残るか）を並べたときに、
    どれも会話本文の不変の記録を持たないことが分かった。
resolution:
  perspective: [deferred_decision]
  note: >-
    保留。学習者の対話を不変に残すことは「監視にしない」という既存条項と正面から
    緊張するため、導入するなら本人の同意・用途の限定・保存期間といった制度側の設計が
    先に要る。技術的には追記のみの記録で解けるが、決めるべきは技術ではない。
  landed_in: []
related: [IK-0317]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.completion, structure.representation]
    to: axes=processing=[none]; structure=[representation]; connection=[none]; governance=[completion]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

学習チャット・管理アシスタント・コース構築・要素検討の 4 者とも、会話本文の不変な記録を
持たない。履歴は上書きと削除で現在の状態だけが残り、別に取っている痕跡は質問側しか
持たない。何かが起きたときに、何が提示され何が確定されたのかを後から再構成できない。

原因は、書き込み経路に記録の設計が無いこと。処理も表現も正しく動いているが、「後から示せる
こと」が要件として置かれていない。

## 発見の観点

支援エージェントの横断調査で（`inventory`）、4 者の説明責任を並べた。同時に、確定は
再構成可能な手続にのみ与える、という上位の原則と突き合わせた（`invariant_audit`）。

## 解決の観点

保留（`deferred_decision`）。学習者の対話を不変に残すことは「監視にしない」という条項と
正面から緊張する。技術的には追記のみの記録で足りるが、決めるべきは本人の同意・用途の限定・
保存期間といった制度側の設計であり、実装の前に人の判断が要る。

## 一般化

記録を持たない書き込み経路は、事故・異議・説明が必要になった瞬間に初めて問題になる。
ただし記録の追加は常に観察面の拡大でもあるので、何のために残すかを決めずに足さない。
辞書の型 `unaudited-write-path` に対応する。
