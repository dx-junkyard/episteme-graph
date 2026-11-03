---
id: IK-0368
title: "分野の地図の検証行は status が verified なら「検証: 原文N本に裏付け」と書くため、根拠 0 本で verified になったノードで「原文0本に裏付け」という自己矛盾の文が学習者に出る"
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が分野の地図でノードの検証状態を読む
  layers: [field_atlas_s, doubt_d]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [condition]
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
    処理軸: `core/atlas_state.py` の verified 分岐は evidence_count を検査せず「原文{n}本に裏付け」を組む（wording。
    n=0 でも同じ文型）。接続軸: verified の判定と evidence_count の導出が別の条件から来ており、両者が矛盾する組み合わせ
    （verified かつ 0 本）を弾く条件が無い（condition。medium: verified が何から立つかは本エントリでは追っていない）。
    確認: 実ペルソナ run 20260927T044913Z 以降の学生段の atlas 観測（ledger_status: verified と「検証: 原文0本に裏付け」が
    同じノードに並ぶ）。
generalization:
  level: repo_pattern
  general_form: 状態ラベルと根拠の件数を別々に導き、矛盾する組み合わせのまま 1 つの文に組む
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: 実ペルソナ（学生）が分野の地図を開いた観測を頭脳エージェントが読み、矛盾として報告した。
resolution:
  perspective: [explicit_contract]
  note: >-
    根拠 0 本の verified は「裏付け」と書かず、骨格（教員レビュー済）の初期表示である事実文にした。DTO の ledger_status（verified）と文の食い違いは懐疑派ペルソナがなお指摘（表示の pill ラベルは別ファイルで未変更）。
  landed_in:
    - backend/core/atlas_state.py
    - backend/tests/test_ik0368_atlas_verified_zero_evidence.py
  verification:
    methods: [reproduction_rerun, docker_e2e, guardrail]
    unverified:
      - atlas_view.py の pill ラベル「原文に裏付け」（seed-verified でも同じ）
      - verified が 0 本で立つ経路そのものの妥当性
related: []
view_of: []
history: []
---

## 課題

verified のノードで「検証: 原文0本に裏付け」が出る。ラベルと件数が矛盾したまま 1 文になる。

## 発見の観点

実ペルソナの学生段（分野の地図）の観測。

## 解決の観点

未解決。0 本の分岐を分け、verified の由来を事実で書く。

## 一般化

ラベルと根拠を別に導いて矛盾したまま組む型（wording-mismatch）。
