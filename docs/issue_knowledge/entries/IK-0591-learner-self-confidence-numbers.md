---
id: IK-0591
title: "学習者が求めると、回答が自分の説明の確かさを「9割以上」「8割程度」の数で項目ごとに出す"
status: open
recorded_at: 2026-10-01
resolved_at: null
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
  perspective: [pending]
  note: "未解決。確かさは段階ラベル（confidence の 2 表・label_vocab）か、根拠の種類（論文の記述を直接確認 / 一般知識の補足 / 推測）の事実で返し、割合・確率の数を出さない規則を足す案。"
  landed_in: []
related: [IK-0582]
view_of: []
history: []
---

## 課題

学習者が「AI の自信の度合いを数字で出して」と求めると、回答が表で項目ごとに「9割以上」「8割程度」を出す。「目安」と断ってはいるが、
学習者に数値を見せない規律（vision §6 の改訂原則 4 の個人向け面）と衝突する。根拠の種類（論文の記述を直接確認・解釈・抜粋が途中まで）は
並んでいるので、数を外しても情報は落ちない。

証拠: run 20261001T054334Z-73002 seq=10（st-06・s-misconception-mirror）。第 17 周 seq=45 と同型。

## 発見の観点

再演と不変条項の監査。第 17 周で判定保留にした 1 件が別ペルソナでも再現した。

## 解決の観点

未解決。数の種類を列挙で禁じるのではなく、確かさを伝える形式（段階ラベル・根拠の種類）を規則として与える。
原因は仮説で、確かめる点は classification.basis に書いた。

## 一般化

禁止を列挙で書くと、列挙に無い種類が漏れる型。数値非表示を持つ他の学習者向け応答（discuss・楽屋）でも再発しうる。
