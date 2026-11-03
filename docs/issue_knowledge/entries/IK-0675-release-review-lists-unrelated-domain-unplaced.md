---
id: IK-0675
title: "リリース前の確認で、コースに関係の無い分野（素粒子物理）について「この論文は…の地図に配置できませんでした」が論文ごとに毎回出て、脚注にもその骨格版が並ぶ"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/release_review_flow_design.md"
  - "docs/features/knowledge_landscape_design.md"
feature_context:
  realizing: "教員がリリース前の確認で、このコースの論文の位置づけだけを読む"
  layers: [release_review, knowledge_landscape, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: unplaced_domains は配置ステージが見た全凍結ドメインについての信号（LS10）で、コースのウィザードはそれを論文ごとにそのまま描き、脚注の骨格版も domains の全件を並べる（admin-release-review.js renderLandscapeBody）。コースの地図（is_course_map）や配置のある分野に絞る規則が無い。接続軸 target = 分野全体の信号がコースという対象に絞られずに届く。
generalization:
  level: repo_pattern
  general_form: "全体を対象に作った信号を、一部を対象にした画面がそのまま並べ、関係の無い項目が事実文として毎回出る"
pattern: scope-widened-silently
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で教員 2 人が「1 論文のコースでも素粒子物理の地図に置けなかった旨が毎回出るのはノイズ」「無関係な分野の版がなぜ出るか読めない」と書いた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core）: コースの配置ビューは unplaced と骨格版をコースの地図の分野（と配置のある分野）に絞り、他分野の不在は 1 行に畳む。"
related: [IK-0413]
view_of: []
history: []
---

## 課題

症状: 関係の無い分野の不在が論文ごとに並ぶ。原因: 分野全体の信号をコースの画面で絞っていない。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。コースの対象（分野）で信号を絞る。

## 一般化

全体用の信号を部分の画面で絞らないと、無関係な事実文が本当の情報を埋もれさせる。確かめ方は、対象外の分野を含む状態で画面の事実文がコースの分野に限られることを見ること。
