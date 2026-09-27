---
id: IK-0363
title: G層の「教材をアップロードする」ルールは本人所有の教材だけを数えるため、公開・共有された教材でコースを作れる教員にも「登録されている教材がまだありません」と案内し、次の一歩（コース作成）を隠す
status: open
recorded_at: 2026-09-27
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 教員が「次にやること」に従って教材からコースを作る
  layers: [guidance_g, auth_visibility]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target, condition]
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
    接続軸: ルール `materials.none` の評価対象は本人所有の教材（target。コースビルダーは可視な教材＝公開・
    グループ共有も使えるので、判定の対象集合が使える集合より狭い）。可視性の条件がルールに運ばれていない
    （condition）。確認: 砂場で教材 4 本を公開にした状態で、所有しない教員ペルソナの next-steps は
    `materials.none:global`「登録されている教材がまだありません」だけ（run 20260927T033148Z ステップ 2・20）。
    所有者側には material.no_course が 4 本分出る。他の軸は関係しない（none）。
generalization:
  level: repo_pattern
  general_form: 案内の判定が「所有」だけを見て「使える（可視）」を見ないため、共有物で作業できる人に誤った空状態を案内する
pattern: entry-scope-mismatch
discovery:
  perspective: [reproduction]
  note: 所有しない教員ペルソナで通し受講を始めた最初の画面（次にやること）で見えた。
resolution:
  perspective: [pending]
  note: >-
    判定を「可視な教材がゼロ」に広げるか、所有ゼロ・可視あり のときは別の事実文（「共有された教材から
    コースを作れます」）と capability course_builder.open を案内する。
  landed_in: []
related: []
view_of: []
history: []
---

## 課題

（2026-09-27 追記）実ペルソナの周回（run 20260927T044913Z）でも再現。教員ペルソナは「登録されている教材が
まだありません」を読んだ直後に教材一覧で公開教材を見つけ、`confused` を記録し、Copilot に矛盾を尋ねた。

公開・共有教材でコースを作れる教員に対し、「教材をアップロードする（登録されている教材がまだありません）」だけが
案内される。所有ゼロ・可視ありの状態が「教材なし」に畳まれている。

## 発見の観点

通し受講の教員段の最初の操作（次にやることを開く）。

## 解決の観点

未解決。ルールの対象集合を可視な教材にし、所有ゼロ・可視ありの案内文を分ける。

## 一般化

判定の対象集合が、実際に操作できる集合より狭い型（entry-scope-mismatch）。
