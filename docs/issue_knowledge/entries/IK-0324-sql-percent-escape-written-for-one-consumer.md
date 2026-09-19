---
id: IK-0324
title: migration SQL が一方の実行経路（psycopg2）のエスケープ規約で書かれ、もう一方（psql）の CI で落ちる
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/features/knowledge_objects_design.md §12
  - CLAUDE.md
feature_context:
  realizing: 起動時に全 migration を番号順に冪等再実行し、CI と docker 初期化でも同じファイルで DB を組み立てる
  layers: [migrations_db, tests_guardrails, deployment]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [contract]
    governance: [review]
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
    接続軸: SQL ファイルという 1 つの成果物を、psycopg2 経由のランナーと psql の 2 つの消費者が読む。
    ファイルは前者の補間規約（パーセントを二重に書く）に合わせて書かれ、後者はそれを literal として
    読むため両者の契約が両立していない。処理軸: 各消費者は自身の規約どおりに正しく動いており、単一処理の
    不良ではない。構造軸: 消費者ごとのエンコードが共有成果物の側に置かれ、正本（plain SQL）と
    ドライバ都合の変換が分離されていなかった。分離しなければ消費者が増えるたびに再発する。
    ただし責務の置き場所の問題とも読めるため medium。統制軸: CI は片方の経路しか流しておらず、
    コメントは「ランナーと同じ手順」と述べていた。両経路を検証する手続が無かった。加えて既存の
    ガードレールが誤った側の規約（二重書き）を強制しており、検査が欠陥を固定していた。
generalization:
  level: general
  general_form: 複数の消費者が読む共有成果物を、片方の消費者のエンコード規約で書き、他方の経路が検証されないまま壊れる
pattern: contract-changed-one-side
discovery:
  perspective: [guardrail_failure, trace_walk]
  note: >-
    CI の psql 適用が「too many parameters specified for RAISE」で止まった。RAISE の書式文字列に
    二重のパーセントがある箇所から、なぜ二重なのか（ランナーの psycopg2 補間）と、どの経路がそれを
    前提にしているかを辿り、CI（psql）と docker initdb（psql）が同じファイルを素で流していることに
    行き着いた。既存の lint が二重書きを要求していたことも同時に見つかった。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    共有成果物は最も素朴な消費者（psql）で読める plain SQL を正本とし、ドライバ都合の変換
    （パーセントの二重化）はランナーの 1 箇所に置いた。lint は向きを反転し、ファイル側の二重書きを
    禁じる。CI は psql 経路とランナー経路の両方で適用し、後者は 2 回目の適用（冪等再実行）の検証を兼ねる。
    あわせて CI に含まれていなかった src の agent テストを足した。
  landed_in:
    - backend/core/migrations.py
    - backend/tests/test_migrations_runner.py
    - .github/workflows/test.yml
    - backend/db/078_knowledge_objects.sql
related: [IK-0315, IK-0212]
view_of: []
history: []
---

## 課題

**症状**: CI の schema 初期化ステップで `psql` が 078 の DO ブロックを「too many parameters
specified for RAISE」で拒否し、ジョブが終了コード 3 で止まる。

**原因**: 起動時ランナーは SQL 本文を psycopg2 の `exec_driver_sql` に空のパラメータ付きで渡すため、
本文のパーセントが補間記号として解釈される。これを避けるため、ファイル側にパーセントを二重に
書く規約が置かれ、lint がそれを要求していた。一方 CI と docker の initdb は同じファイルを psql で
素のまま流す。psql では二重のパーセントが literal になり、RAISE の書式に対して引数が余る。
2 つの消費者の契約が両立しておらず、CI は片方（psql）しか流していなかった。

軸ごとの判断: 接続軸は 2 消費者の契約不一致。構造軸は消費者都合のエンコードが共有成果物側に
あり正本と変換が分かれていなかったこと。統制軸は片経路しか検証しない CI と、誤った規約を
固定していた lint。処理軸には要素が無い。

## 発見の観点

CI の失敗（`guardrail_failure`）が起点。二重のパーセントの由来を遡る経路追跡（`trace_walk`）で、
「ランナーの補間 → ファイル側の二重書き規約 → lint による固定 → psql 経路の未検証」という連鎖が
見えた。ローカルの全テストは 16,000 件以上通っていたが、SQL を実際に流すテストは CI にしか無く、
その CI が片方の経路だけだった。

## 解決の観点

**正本の一本化**が主。共有成果物は最も素朴な消費者で読める形（plain SQL）を正本とし、消費者固有の
変換は消費者側の境界（ランナー）1 箇所に置く。**ガードレールの是正**が従。lint の向きを反転し、
CI は両経路で流す。

解消方法として残す発見:
- 共有成果物に消費者固有のエンコードを書くと、消費者が増えたときに必ず壊れる。**変換は消費者の
  境界に置き、成果物は最も素朴な読み手で読める形にする**。
- 検査は片方の消費者の契約を固定しうる。**ガードレール自身が「どの消費者の契約か」を明記して
  いなければ、欠陥を守る側に回る**。今回の lint は反転して初めて守る側になった。
- 「本番と同じ手順」と書かれた CI ステップが本番と違う経路だった。**複数の実行経路があるなら、
  CI は全経路を流す**。ランナー経路を足すと冪等再実行の検証も同時に得られる。

## 一般化

「契約を片側だけ変えた」型（`contract-changed-one-side`）の一例。成果物を複数の経路が読む場面
（SQL ファイル、設定ファイル、テンプレート）で、片方の経路の都合に成果物を合わせたときに出る。
関連として IK-0315（毎起動全件再実行と履歴の両立）と IK-0212（検査が名前列挙・字面に依り新実装を
覆えない）がある。
