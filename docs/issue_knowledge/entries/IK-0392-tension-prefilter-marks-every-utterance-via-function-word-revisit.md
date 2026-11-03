---
id: IK-0392
title: "tension の前段判定（非LLM）が、4 文字の部分文字列の再訪一致で英語の機能語（What / does / this）に毎回当たり、定義の質問や確認問題の依頼まで含めて学習者のほぼ全発話に tension_hint を立てていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者の「理解した上での引っかかり」の候補だけを非同期の LLM 解析に回す
  layers: [learner_experience_b, rag_chat]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [none]
    governance: [budget]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸で、backend/core/tension/prefilter.py の _has_revisit は 4 文字窓の部分文字列一致で、_is_content_like が
    ラテン文字 4 字を「内容語」と見なすため、英語の会話では What / does / this / that が直近発話と毎回一致して
    いた。加えて定義の質問・依頼（「〜とは何ですか」「確認問題を出してください」「What does X mean」）を
    理解の欠落（gap）として区別しておらず、再訪の字面一致だけでヒントが立った。「でも」も「何でも」「いつでも」の
    副助詞に当たっていた。統制軸は medium: ゲートの目的は LLM コストの削減（P6）で、全件にヒントが立つと
    ゲートが働かない。確認: 第 8 周の tension mining 要求で学習者の全ターンが tension_hint=true。req の
    学習者発話を judge_tension_hint に通して再現した（修正前は全発話が真 → 修正後は依頼・gap の発話が偽）。
generalization:
  level: general
  general_form: 語の再出現を短い部分文字列の一致で判定し、機能語・定型の言い回しの一致を内容の再出現と誤認する
pattern: substring-match-false-positive
discovery:
  perspective: [data_inspection, reproduction]
  note: 第 8 周の tension mining 要求で全ターンにヒントが立っていたのを見て、実発話で再現した。
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    英字は語単位（4 字以上・機能語の停止語表を除く）で再訪を照合し、4 文字窓は英数字だけの窓を飛ばす。
    英語の逆接・矛盾・疑いのマーカー（but / however / contradict / doesn't that / aren't these / assum* ほか。
    前提照会の「前提」と対にする）を語境界で足し、曲がった引用符は正規化する。マーカーが無い発話で定義の質問・
    依頼の定型に当たれば、再訪一致だけではヒントを立てない。「でも」は直前が 何/つ/こ/誰 のときは逆接と
    見なさない。worker はこれまでどおり payload.tension_hint = 'true' の行だけを読む（消費側は不変）。
  landed_in:
    - backend/core/tension/prefilter.py
    - backend/tests/test_tension_prefilter.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再観測（第 8 周と同じ学習者で tension mining 要求のヒントの立ち方）
      - 英語の but を含む言い直し・回答（msg_0008/0010 型）は引き続きヒントが立つ（後段 LLM の弁別に任せる設計）
      - 形態素解析なしの日本語の同語再訪（中心語が毎ターン出る会話では引き続き立ちやすい）
related: []
view_of: []
history: []
---

## 課題

tension の前段判定が英語の機能語の部分一致でほぼ全発話にヒントを立てていた。

## 発見の観点

実要求で全ターンにヒントが立っていたのを実発話で再現した。

## 解決の観点

英字は語単位で照合し、逆接・矛盾の英語マーカーを足し、定義質問・依頼の定型では再訪だけで立てない。

## 一般化

短い部分文字列の一致で機能語を内容語の再出現と誤認する。
