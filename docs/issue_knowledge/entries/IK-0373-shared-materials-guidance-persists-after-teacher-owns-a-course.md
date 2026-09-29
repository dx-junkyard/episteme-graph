---
id: IK-0373
title: "G層の新ルール「共有されている教材からコースを作成する」は所有教材ゼロ・共有教材ありだけを条件にしていたため、共有教材からコースを作って公開した後も一番上に残り続け、何をすれば消えるか分からない"
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-27
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 教員が「次にやること」に従って共有教材からコースを作り公開する
  layers: [guidance_g]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [completion]
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
    統制軸: 案内の「済み」を導く条件に、案内が促した行為の結果（コースを作った）が入っていなかった（completion。
    G1 の「実施すれば自動消滅」が成り立たない）。接続軸: 判定に「本人所有のコースの有無」という条件が運ばれていない
    （condition）。確認: 第 5 周（run 20260927T135738Z）で te-01 が公開まで終えた直後の next-steps に同ルールが
    残った観測。IK-0363 の是正で足したルールの取りこぼし。
generalization:
  level: repo_pattern
  general_form: 状態から導く案内の消滅条件に、案内が促した行為の結果を含めていない
pattern: completion-defined-by-proxy
discovery:
  perspective: [reproduction]
  note: 実ペルソナの教員段（公開直後に「次にやること」を開く）で見えた。IK-0363 の是正直後の再演。
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: 所有コースが 1 件以上あれば出さない条件を足した（読めなければ案内を残す側に倒す）。テストで両分岐を固定。
  landed_in:
    - backend/core/admin_assistant/next_steps.py
    - backend/tests/test_ik0363_materials_shared_available.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（第 5 周の学生段が終わった後に再ビルドして確かめる）
related: [IK-0363]
view_of: []
history: []
---

## 課題

IK-0363 で足した案内が、コースを作った後も消えない。

## 発見の観点

是正直後の再演（実ペルソナの教員段）。

## 解決の観点

所有コースありなら出さない（completion の条件を足す）。

## 一般化

案内の消滅条件に、促した行為の結果を含めていない型。
