---
id: IK-0599
title: "存在しない live ビューを読む読み手が SQL 例外を握りつぶして空を返し、ロールバックしないため同じ session の次の SQL が失敗して教員向け概観が 500 になる"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/features/simulation_bridge_design.md §12.2
feature_context:
  realizing: "検証シミュレーションの教員向け概観で、理論モジュールの成員 step が受け取り・生み出す式を知識オブジェクトの live ビューから読む"
  layers: [simulation_bridge_sb, knowledge_objects, migrations_db]
classification:
  axes:
    processing: [resource]
    structure: [none]
    connection: [contract]
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
    確認: 読み手は `knowledge_equations_live` を名指ししていたが、migration 078 が作ったのは表 `knowledge_equations`
    だけで、`_live` ビューは theory_claims / theory_components / knowledge_symbols / knowledge_theory_modules /
    learning_units にしか無かった。読み手は UndefinedTable を捕まえて `[]` を返し、psycopg2 のトランザクションを
    中断状態のまま残したので、同じ session の次の文（`store.list_programs`）が InFailedSqlTransaction で落ちた。
    処理軸 resource = 例外処理の後始末（ロールバック）の不良が症状を別の箇所へ移した。接続軸 contract = 読み手は
    「知識表には live ビューがある」（KO5）という契約で書き、スキーマ側はその契約を全表には満たしていなかった
    （片方だけの前提）。統制軸 review = 読み手が名指す `_live` の実在を確かめる検査が無かった。構造軸は表現・責務の
    置き場所の問題ではないので none。
generalization:
  level: general
  general_form: "失敗を握りつぶす読み手が接続の後始末をしないため、失敗は空の結果に化け、症状は無関係な次の処理の失敗として現れる"
pattern: failure-reported-as-success
discovery:
  perspective: [trace_walk, data_inspection]
  note: "実機の dev スタックで教員として概観を開くと 500 になった。api-server のログに出ていたのは次の文の InFailedSqlTransaction で、そこから同じ session の直前の読み手と、名指ししていたビューの不在まで辿った。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    二つの欠陥を分けて直した。不在のビューは migration で作り、握りつぶす箇所はすべて
    `service.rollback_quietly` を通してから空を返すようにした（空を返す縮退そのものは残す）。
    辞書の型は主に `failure-reported-as-success`（失敗が空の成功に化け別の症状として現れる）で、ビューの不在は
    `referenced-source-does-not-exist`（名指しした正本が実在しない）にも当たる。参照先の実在検査は今回は置いていない。
  landed_in:
    - backend/db/089_simulation_learner.sql
    - backend/core/simulation_bridge/service.py
    - backend/api/routes/simulation_bridge.py
    - backend/api/routes/simulation_lab.py
    - backend/tests/test_simulation_lab_guardrails.py
    - backend/tests/test_simulation_bridge_migration.py
    - docs/features/simulation_bridge_design.md §12.2
  verification:
    methods: [docker_e2e, reproduction_rerun, guardrail]
    unverified:
      - "このリポジトリの他の読み手が、存在しない `_live` ビューを名指ししていないか（backend のコードが名指す `<表>_live` がすべて backend/db/*.sql に存在することを検査するガードレールを後続で置く提案）"
      - "本層以外で、SQL 例外を捕まえてロールバックせずに空を返している読み手"
related: [IK-0597, IK-0598]
view_of: []
history: []
---

## 課題

検証シミュレーションの教員向け概観（`GET /api/admin/simulation/documents/{id}/overview`）が 500 を返した。原因は二段で、
①読み手が存在しない `knowledge_equations_live` ビューを名指ししていた（表はあるがビューは作られていなかった）
②読み手はその SQL 例外を握りつぶして空を返し、ロールバックしなかった。そのため同じ session の次の文が中断状態の
トランザクションで落ち、症状は無関係なプログラム一覧の読み出しの失敗として現れた。

## 発見の観点

実機で概観を開いた操作から、ログに出た次の文の失敗を起点に、同じ session の直前の読み手と名指しされたビューまで経路を
辿った。ログの最初の行は真因ではなく、握りつぶしの後に来た文だった。

## 解決の観点

ビューの不在はスキーマ側の単一の欠落として migration で作った。握りつぶしは縮退（空を返す）を残したまま、接続の後始末
（ロールバック）を共通の関数に寄せて全箇所で通すようにし、失敗時にロールバックすることをテストで固定した。
読み手が名指す live ビューの実在を機械照合する検査は、本層の範囲を超えるので後続の提案に留めた。

## 一般化

fail-soft の読み手は「失敗したら空」で利用者を守るつもりでも、接続を中断状態のまま返すと失敗が次の処理へ伝染し、症状の
場所が真因から離れる。縮退する箇所は後始末をしてから縮退しているかを問う。辞書の型は `failure-reported-as-success`。
名指した正本（ビュー）の不在は `referenced-source-does-not-exist` の側面で、規約（読み手は live ビューを読む）があっても
規約の対象が実在することを検査しなければ同じ不在が再発する。
