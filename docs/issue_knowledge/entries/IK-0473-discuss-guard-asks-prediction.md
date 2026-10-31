---
id: IK-0473
title: "discuss で検索が0件のとき、未踏ガードの「予想を先に引き出す」が discuss の規則1（質問には即答・出し惜しみ禁止）と衝突した"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "議論モードで論文の範囲外の問いにも、出所を正直に言ったうえで即答する"
  layers: [discuss, rag_chat]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [contract]
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
    処理軸: ガード文は全モード共通の1つで、「予想を一度引き出す問いを投げる（すぐに答えを与えない）」を含む（wording）。接続軸: discuss の system
    プロンプトの規則1と、後から足すガードの規則が互いを知らずに同じプロンプトに並んだ（contract）。
generalization:
  level: general
  general_form: "あとから足す共通の指示が、モードごとの応答規則と矛盾する段を含む"
pattern: contract-changed-one-side
discovery:
  perspective: [data_inspection]
  note: "odd 側の頭脳のメモ（seq 59 / 67 / 75）で、discuss の空検索の往復の system プロンプトを読んだ。"
resolution:
  perspective: [single_point_fix]
  note: >-
    discuss では予想の段を持たない _DISCUSS_OUT_OF_SOURCE_GUARD を使う（「この論文の抜粋では確認できない」の明示・参考情報の区別・推測を混ぜない、は残す。DM1 の事実行の
    context_block は不変）。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0466_0478_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演"
related: [IK-0378, IK-0471]
view_of: []
history: []
---

## 課題

discuss で検索が0件のとき、未踏ガードの「予想を先に引き出す」が discuss の規則1（質問には即答・出し惜しみ禁止）と衝突した

## 発見の観点

odd 側の頭脳のメモ（seq 59 / 67 / 75）で、discuss の空検索の往復の system プロンプトを読んだ。

## 解決の観点

discuss では予想の段を持たない _DISCUSS_OUT_OF_SOURCE_GUARD を使う（「この論文の抜粋では確認できない」の明示・参考情報の区別・推測を混ぜない、は残す。DM1 の事実行の context_block は不変）。

## 一般化

あとから足す共通の指示が、モードごとの応答規則と矛盾する段を含む。
