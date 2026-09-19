---
id: IK-0327
title: CI でしか走らないテスト（node・DB 依存）がローカルでは静かにスキップされ、CI が前段で止まっていたため失敗が見えなかった
status: resolved
resolved_at: 2026-09-19
recorded_at: 2026-09-19
sources:
  - docs/issue_knowledge/entries/IK-0324-sql-percent-escape-written-for-one-consumer.md
  - docs/features/knowledge_objects_design.md §12
feature_context:
  realizing: グラフレビューの参照解決（JS）と revision 受理の DB 統合を、実行環境の道具（node・Postgres）を要する検査で固定する
  layers: [tests_guardrails, graph_review, knowledge_objects]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [version]
    governance: [review, completion]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    統制軸: node と Postgres を要するテストは無ければスキップする設計で、開発機には両方無い。CI は
    migration 適用ステップで止まっていたため、テストステップ自体が 1 週間走らず、「ローカル全件通過」
    が完了の判定になっていた。接続軸: JS 側の実装が新しい補助関数（数式区切りの除去）を呼ぶように
    変わり、artifact の格納が列から表へ移ったが、検査側の抽出リストと生 SQL の読み方が古い版のまま
    残った。構造軸: node ハーネスは実装の呼び出し関係を手書きのリストで写しており、実装が変わる
    たびに手で追随する二重管理になっている（medium — 自動抽出にすれば消える要素かは未確認）。
    処理軸: 実装の関数自体は正しく動く。
generalization:
  level: general
  general_form: 環境の道具が無いと黙ってスキップする検査は、その道具のある場所（CI）が別の理由で止まると、実装の変更に追随していない事実が誰にも見えない
pattern: guardrail-does-not-cover-new-path
discovery:
  perspective: [guardrail_failure, trace_walk]
  note: >-
    migration の修正で CI のテストステップが初めて走り、node の ReferenceError（未定義の補助関数）と
    DB 統合テストの KeyError（列に無くなった artifact）が出た。ローカルで再現するため pip 配布の
    node バイナリを隔離環境に入れ、ハーネスが抽出する関数リストを実装の呼び出し関係と突き合わせた。
resolution:
  perspective: [carry_through, guardrail_fix]
  note: >-
    ハーネスの抽出リストに新しい補助関数を足し、統合テストは artifact を列の生読みではなく
    hydrate する getter（読み手の契約）で読むように直した。CI には agent 群のテスト（src）も
    追加し、走らない検査を減らした。node が無い開発機では pip 配布の node で走らせられる。
  landed_in:
    - backend/tests/test_graph_reference_resolution_ui_static.py
    - backend/tests/test_issue_448_450_detail_panel.py
    - backend/tests/test_revision_pg_integration.py
    - .github/workflows/test.yml
related: [IK-0324, IK-0326, IK-0105]
view_of: []
history: []
---

## 課題

**症状**: CI のテストステップが走った初回に、node で動く JS 検査 3 件が `lsGraphStripMathDelimiters is
not defined` で、DB 統合テスト 1 件が `KeyError: '_artifacts'` で落ちた。ローカルでは 4 件とも
スキップされていた。

**原因**: JS 検査は実装ファイルから関数を名前のリストで抽出して node で評価する。実装が数式区切りを
除く補助関数を呼ぶように変わったが、リストに足されなかった。統合テストは artifact を
`stage_outputs` 列から生 SQL で読んでいたが、artifact は表へ移り列には残らなくなった。どちらも
実装の変更に検査が追随していない。それが見えなかったのは、検査が node と Postgres を要し、無い環境では
スキップし、ある環境（CI）は migration 適用の段で止まっていたからである。

軸ごとの判断: 統制軸はスキップ設計と CI の前段停止が重なって完了判定が空になったこと。接続軸は
実装側の版（呼び出し関係・格納場所）が検査側に運ばれなかったこと。構造軸は抽出リストという手書きの
写し。

## 発見の観点

CI の赤が起点。ローカルに node が無いので、pip 配布の node バイナリを隔離環境に入れて同じ検査を
走らせ、失敗を再現した。抽出リストと実装の呼び出し関係を機械的に突き合わせて、欠けている関数を
特定した。

## 解決の観点

**後段まで運ぶ**が主。実装の変更（新しい呼び出し・新しい格納場所）を検査側に反映する。統合テストは
生 SQL ではなく読み手の契約である getter を使い、格納場所の変更に強くした。**ガードレールの是正**が従。
CI に走っていなかった agent 群のテストを足した。

解消方法として残す発見:
- **スキップは失敗ではないが、成功でもない**。道具が無い環境でスキップされる検査は、道具のある
  環境で必ず走ることを別に保証しないと、無いのと同じになる。CI の前段が止まると全部止まる。
- 実装の呼び出し関係を手書きで写すハーネスは、実装が変わるたびに壊れる。**呼び出し関係を実装から
  機械的に抽出する**か、実装を module 化して直接 import できる形にすれば写しが消える。
- 開発機に無い道具は、pip 配布のバイナリなど**隔離環境に一時的に入れて再現する**のが最短。

## 一般化

「ガードレールが新しい経路・表現を覆わない」型の一例で、覆わない理由が「検査が走る場所が
無かった」という点で IK-0326（版差で空振り）と隣り合う。環境依存の検査を持つ場面（ブラウザ・
DB・外部バイナリ）で一般に出る。
