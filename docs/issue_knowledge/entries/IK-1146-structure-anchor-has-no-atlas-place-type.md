---
id: IK-1146
title: "地図の場所を指す問いの構造帰属に「地図の場所」の型が無く、候補は区画へ縮退するか空になる"
status: deferred
recorded_at: 2026-10-31
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/assistant_screen_adapter_design.md"
feature_context:
  realizing: "学習者が分野の地図の場所について問うた問いを、どこに引っかかったかとして記録する"
  layers: [learner_experience_b, field_atlas_s, screen_adapter_sa]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    structure_anchor の anchor_type は論文の構造（claim / equation / derivation_step / concept / stage / chunk / segment）の語彙で、
    分野の座標（atlas の node）を表せない。地図の場所だけを指す問いは segment（区画の既定値）へ縮退するか unattributed になる。
generalization:
  level: repo_pattern
  general_form: "記録の語彙が 1 つの座標系（論文の構造）だけで、別の座標系（分野の地図）を指す行為を表せない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: >-
    製品頭脳 A 4304 / B 4309: 地図の問い 2 件が seg_0 へ縮退、または候補が claims/equations/concepts すべて空で unattributed。
resolution:
  perspective: [deferred_decision]
  note: >-
    第 25 周 第 10 波（2026-10-31）: v1 は型を足さない（SA層設計書 §11.22）。地図の場所だけを指す問いは帰属の候補が無い事実として扱い、
    区画の既定値を帰属に使わない是正は面 B の IK-1122。型を足すなら骨格の版を跨ぐ node_id の読み替え（K-6 の NodeResolver）と
    教員向け集約（anchor-insights）の行の定義を同時に決める。
  landed_in:
    - "docs/features/assistant_screen_adapter_design.md"
related: [IK-1122, IK-1112]
view_of: []
history: []
---

## 課題

地図の場所を指す問いの構造帰属に「地図の場所」の型が無い。

## 発見の観点

製品頭脳 A 4304 / B 4309。

## 解決の観点

v1 は型を足さず、縮退しない事実として扱う（§11.22）。型の追加は版の読み替えと教員集約の定義と同時に決める。

## 一般化

記録の語彙が 1 つの座標系だけで、別の座標系を指す行為を表せない。型 information-dropped-as-unrepresentable。

## 第 11 周の再演（第 25 周・面 B 第 11 周、2026-10-31・judge_doubt_r11 — 固定コース 1bad7d9f・3 周連続の同じ対象）

第 11 周（judge_doubt_r11）: 見え方は deferred の想定どおり。st-08 の地図の場所の問いは候補が stages だけで unattributed に倒れた（区画への縮退は IK-1122 の是正で消えた）。製品頭脳 B 4325 も「画面文脈の地図ノードが候補に入っていない」と同じ点を挙げた。
