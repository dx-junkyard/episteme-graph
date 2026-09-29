---
id: IK-0377
title: "コース内容の生成が式・根拠・出典の抜粋を文書内でしか一意でない ID（eq_N 等）でコース全体から引くため、複数論文のコースでは先に読まれた論文の式が別の論文のトピックに付く"
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 複数の論文からコースを作り、トピックごとの授業用ドラフトを生成する
  layers: [course_builder, knowledge_objects]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 式 ID（eq_N）は印字番号由来で文書内でしか一意でない（知識オブジェクト層 V-6 の既知事実）のに、
    コース内容の生成はコースの全文書を 1 つの索引に畳んで ID で引くため、参照先が別の文書の式にずれる（target）。
    確認: 第 5 周で製品側 LLM の頭脳が読んだ授業用ドラフト作成プロンプトで、中性子星論文の章の全トピックに
    Cep B 論文の式（偏波率・ΔV_NT・N(H2)/W・DCF 変形）と Cep B の節ラベル・source_excerpt が付いていた。
    コード上の箇所は第 3 波（fix-F）が特定する。
generalization:
  level: general
  general_form: 内側の文脈でしか一意でない ID を、複数の文脈を畳んだ索引で引き、別文脈の対象に解決する
pattern: id-namespace-conflated
discovery:
  perspective: [data_inspection, reproduction]
  note: 製品側 LLM を肩代わりした頭脳がプロンプトの中身を読んで気づいた（プロンプト＝製品が組んだ文脈の実物）。
resolution:
  perspective: [representation_change, fail_closed]
  note: >-
    構造化成果の索引を (document_id, local_id) で持ち（_BundleScope）、トピックの文書を単位・部品から決めて同じ文書内でだけ解決する。文書が決められない単位には何も付けず事実文を残す（fail_closed）。砂場でコース内容を再生成し、製品側 LLM を肩代わりした頭脳 2 体が 20 トピックの文脈を読んで「各トピックの式・主張・抜粋が自分の論文に閉じた」ことを確認した。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0377_course_content_document_scope.py
  verification:
    methods: [reproduction_rerun, docker_e2e, guardrail]
    unverified:
      - 1 トピックが同じ ID を持つ 2 論文にまたがる場合（保存参照が裸 ID のまま）
      - routes / src 側の同型の裸 ID 参照（未調査）
      - 再生成していない既存コースの古い内容
related: [IK-0371]
view_of: []
history: []
---

## 課題

複数論文のコースで、トピックに別論文の式・抜粋が付く。

## 発見の観点

製品のプロンプトを読む（肩代わり頭脳の副産物）。

## 解決の観点

未解決。文書スコープの索引にする。

## 一般化

ID の名前空間の畳み込み（id-namespace-conflated）。
