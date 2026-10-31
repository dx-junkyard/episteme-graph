---
id: IK-0591
title: "学習者が求めると、回答が自分の説明の確かさを「9割以上」「8割程度」の数で項目ごとに出す"
status: resolved
recorded_at: 2026-10-01
resolved_at: 2026-10-01
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.11
feature_context:
  realizing: "学習チャットの回答で、説明の確かさを学習者に伝える"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: low
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: プロンプトの「数値を見せない」規則が検索件数・一致度・網羅率を名指しするだけで、回答者自身の確かさの自己評価の数を
    書いていない（処理軸 wording）。数値非表示は学習者向けの不変条項だが出力側の検査が無く、プロンプトの文言に任せている
    （接続軸 contract・low）。規則の文面と、回答の後処理に数の検査が無いことを確かめれば確定する。
generalization:
  level: repo_pattern
  general_form: "表示しない数の種類を列挙で禁じ、列挙に無い種類の数が出口から出る"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, invariant_audit]
  note: "第 17 周（§18.9 で判定保留）と第 18 周 st-06 で同型が再現した。審判 B（学習者に数値が見えている）が拾った。"
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: 'tutor・casual のテキスト経路のプロンプトに、確からしさを割合・確率の数で示さず根拠の種類で伝える規則を足した。出力側の数の検査は置いていない。'
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_wave18_gate_stuck_advice.py
    - 97994b9
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（st-06 s-misconception-mirror）"
      - "discuss・楽屋のプロンプト"
      - "回答本文の後処理による数の検査（置いていない）"
related: [IK-0582]
view_of: []
history:
  - date: '2026-10-01'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '第 19 波で、確からしさを数で示さない規則をプロンプトに足した（ガードレールで確認・再演は次の周）。残り: 出力側の検査は無くプロンプトの文言に任せたまま。（persona_enactment_testing_design.md §18.12）'
---

## 課題

学習者が「AI の自信の度合いを数字で出して」と求めると、回答が表で項目ごとに「9割以上」「8割程度」を出す。「目安」と断ってはいるが、
学習者に数値を見せない規律（vision §6 の改訂原則 4 の個人向け面）と衝突する。根拠の種類（論文の記述を直接確認・解釈・抜粋が途中まで）は
並んでいるので、数を外しても情報は落ちない。

証拠: run 20261001T054334Z-73002 seq=10（st-06・s-misconception-mirror）。第 17 周 seq=45 と同型。

## 発見の観点

再演と不変条項の監査。第 17 周で判定保留にした 1 件が別ペルソナでも再現した。

## 解決の観点

97994b9。数の種類を列挙で禁じるのではなく、確からしさを伝える形式（根拠の種類）を規則として与える見立て。プロンプト文言の契約で、出口の検査ではない。
原因は起票時の仮説のまま（classification.basis）で、是正後の再演で確かめる。

## 一般化

禁止を列挙で書くと、列挙に無い種類が漏れる型。数値非表示を持つ他の学習者向け応答（discuss・楽屋）でも再発しうる。
