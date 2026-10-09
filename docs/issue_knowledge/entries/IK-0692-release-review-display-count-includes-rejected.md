---
id: IK-0692
title: "リリース前の確認のステップ 3 が「位置づけ N 件を受講者に表示します」の N に、教員が却下した配置を含めて数える"
status: open
recorded_at: 2026-10-10
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/release_review_flow_design.md"
  - "docs/features/knowledge_landscape_design.md"
feature_context:
  realizing: "教員が公開の直前に、受講者に何が見えるかを要約で確かめる"
  layers: [release_review, knowledge_landscape]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
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
    確認: routes/landscape.py の course 単位の配置一覧は landscape_schema.LIVE_STATUSES（rejected を含む）で行を
    引いて placement_count = len(rows) を返し、admin-release-review.js の renderPublishStep はその数を「受講者に
    表示します」の文に入れる。学習者向けに出るのは LEARNER_VISIBLE_STATUSES（confirmed / inferred /
    review_required）だけ。処理軸 logic = 数える母集合の取り違え。接続軸 meaning = 教員のレビュー用の数を、
    次の段（要約の文）が受講者に見える数の意味で読む。構造軸 none（学習者表示の母集合は語彙として既にある）。
    統制軸 none。
generalization:
  level: repo_pattern
  general_form: "手元にある一覧の件数を、別の読み手に届く件数として文に入れ、片方にしか含まれない行（見送り・却下）の分だけ数がずれる"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction]
  note: "第 23 周の再演で、急ぐ教員がステップ 2 で［却下］と［再検討中］を含む 8 行を見たあと、ステップ 3 が「8件を受講者に表示します」と言い、却下した行も受講者に出るのかと読んだ。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core + frontend）: サーバが学習者表示の母集合（LEARNER_VISIBLE_STATUSES）で数えた件数を別キーで返し、ステップ 3 はそれを使う。却下が 1 件以上あるときは「却下した N 件は受講者に表示しません」の事実文を添える。"
related: [IK-0649, IK-0691]
view_of: []
history: []
---

## 課題

症状: 却下した配置まで「受講者に表示します」の件数に入る。原因: 教員のレビュー用の一覧の件数を、受講者に見える件数として文に使っている。

## 発見の観点

ペルソナの再演（reproduction）。ステップ 2 の行の状態とステップ 3 の数を突き合わせて見えた。

## 解決の観点

未解決。受講者に見える母集合で数えた件数をサーバから返せば足りる見込み。

## 一般化

同じ「件数」でも、誰に届く件数かで母集合が違う。確かめ方は、却下した行を含む状態で、要約の数が受講者向けの一覧の行数と一致することを見ること。
