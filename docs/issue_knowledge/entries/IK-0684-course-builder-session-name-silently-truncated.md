---
id: IK-0684
title: "コースビルダーのセッション表示名が UTC の日時を前置きし、題名を 20 字で「…」なしに切る（「（第 23 周・岡村」と閉じ括弧が欠ける）"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
feature_context:
  realizing: "教員がコースビルダーのセッション一覧から、自分が付けた題名でセッションを見分ける"
  layers: [course_builder, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
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
    確認: routes/admin.py の _generate_session_display_name が <元ファイル>_<UTC の YYYYMMDD_HHMM>_<題名の先頭 20 字> を作り、切ったことを示さない。処理軸 wording のみ。
generalization:
  level: instance
  general_form: "自動で付ける表示名が、内部の時刻書式を前置きし、題名を切った事実を示さない"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、教員 2 人が「表示名に日時接頭辞が付き、付けた題名と別の名前が並ぶ」「末尾が切れて閉じ括弧が欠ける」と書いた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core）: 表示名は題名を主にし、切るときは「…」を付け、日時は別の欄（JST）にする。"
related: []
view_of: []
history: []
---

## 課題

症状: セッション名が日時接頭辞つき・無言で切れる。原因: 表示名の生成規則。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。局所の是正。

## 一般化

自動の表示名が切った事実を示さないと、利用者は欠けた名前を本物と読む。確かめ方は、長い題名と括弧付きの題名でセッションを作り、表示名を読むこと。
