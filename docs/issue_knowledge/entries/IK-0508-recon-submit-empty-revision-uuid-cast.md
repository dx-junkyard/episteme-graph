---
id: IK-0508
title: "再構成の初回提出が全件 500 になっていた（改訂元なしの空文字を CASE で NULL に逃がしてから uuid へ CAST する SQL を、PostgreSQL が計画時に定数畳み込みして失敗する）"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
  - docs/features/reconstruction_loop_design.md
feature_context:
  realizing: "学習者の再構成の応答を保存して照合結果を返す"
  layers: [reconstruction_r]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: 条件式の誤り。``CASE WHEN :rev = '' THEN NULL ELSE CAST(:rev AS uuid) END`` は、束縛値 '' が
    リテラルとして展開されるため ``CAST('' AS uuid)`` が計画時に評価され、分岐に関係なく
    invalid input syntax で落ちる。入力・契約（revision_of は省略可・空文字を NULL にする意図）は妥当で、
    単一の SQL 式の書き方だけが誤り。構造・統制には原因が無い。接続 medium: 出題ゼロの間はこの経路に
    誰も到達しなかった（第 11 周まで item が無かった）ことは発見の遅れであって原因ではない。
generalization:
  level: general
  general_form: "条件分岐で無効値を避けたつもりの型変換が、処理系の定数畳み込みで分岐より先に評価されて失敗する"
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 13 周で審判 A が POST /reconstruction/{item}/submit の 5xx を 2 件出し、api-server のログの SQL と束縛値（rev=''）を読んだ。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "``CAST(NULLIF(:rev, '') AS uuid)`` に書き換え、同型の SQL を backend 全体で禁じるガードレールを置いた。"
  landed_in:
    - backend/api/routes/reconstruction.py
    - backend/tests/test_sql_empty_string_cast_guardrail.py
  verification:
    methods: [docker_e2e, reproduction_rerun, guardrail]
    unverified:
      - "改訂（revision_of あり）の経路は砂場で送っていない"
related: [IK-0483]
view_of: []
history: []
---

## 課題

学習者が再構成の問いに初めて答える（改訂元なし）と、保存の INSERT が
``invalid input syntax for type uuid: ""`` で失敗し、500「Failed to submit reconstruction」が返っていた。
自己確認に進めず、ループが閉じない。原因は ``CASE WHEN :rev = '' THEN NULL ELSE CAST(:rev AS uuid) END`` の
``CAST('' AS uuid)`` が計画時に評価されること。

## 発見の観点

第 13 周（R層 item が初めて配信された周）で、審判 A の 5xx 2 件から api-server のログの SQL と束縛値を読んだ。

## 解決の観点

``NULLIF`` で空文字を NULL にしてから CAST する。同型の SQL をガードレールで禁じる。砂場で同じ item に
同じ学習者で再提出し、200 と照合結果が返ることを確かめた。

## 一般化

条件分岐で無効値を避けたつもりの型変換が、処理系の定数畳み込みで分岐より先に評価されて失敗する。
