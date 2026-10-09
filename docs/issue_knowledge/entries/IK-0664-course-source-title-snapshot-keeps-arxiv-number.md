---
id: IK-0664
title: "コースの「登録済み教材」が論文の題名ではなく arXiv 番号のまま出る（作成時の仮の題名の写しを、読み時の差し替えが仮の値と見なさない）"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/backend/rag-chat.md"
feature_context:
  realizing: "学習者がコースを開き、登録済み教材の一覧から目当ての論文を見つける"
  layers: [learner_experience_b, frontend_learning_ui, course_builder]
classification:
  axes:
    processing: [logic]
    structure: [aggregation]
    connection: [version]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（砂場 DB の読み取り）: learning_courses.data.sources[].title は作成時の写しで「2605.26810v1」、material_id は「c6e9d0bc-3f8」、documents.title は題名の書き戻し（IK-0366/0419）後の正式題名。読み時の差し替え _with_resolved_source_titles は「保存値が空か material_id と同じ」ときだけ差し替えるので、arXiv 番号という別の仮の値を仮と見なさない。仮の題名の判定が documents 側（document_title_is_placeholder）とコース側で別々に書かれている。処理軸 logic = 差し替えの条件式。構造軸 aggregation = 「仮の題名」の判定が 2 箇所。接続軸 version = コースの写しが題名の書き戻しより古い世代のまま読まれる。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "元の値を更新しても、それを写した側が「仮の値」を別の規則で判定しているため、写しが古いまま表示され続ける"
pattern: stale-derivative-served
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 23 周の砂場の再演で学生 3 人がそろって「資料の名前が番号だけ」と書き、砂場 DB の写しと documents.title を読み比べて差し替えの条件が効いていないことを確かめた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core）: 読み時の差し替えを documents 側と同じ仮の題名の判定（document_title_is_placeholder）に委譲し、教員が付けた題名だけを守る。"
related: [IK-0366, IK-0419, IK-0381]
view_of: []
history: []
---

## 課題

症状: コース画面の「登録済み教材」に 4 本中 3 本が arXiv 番号で出て、議論の開幕では同じ論文が正式題名で出る（同じ論文が 2 つの名前に見える）。原因: コースの写しの題名が作成時の仮の値のまま残り、読み時の差し替えがその値を仮と見なさない。

## 発見の観点

ペルソナの通し受講（reproduction）と砂場 DB の読み取り（data_inspection）。

## 解決の観点

未解決。仮の題名の判定を 1 箇所に寄せれば、写しの側も同じ規則で差し替わる。

## 一般化

写しを持つ側が元の側と別の規則で「仮の値」を判定すると、元を直しても写しが直らない。確かめ方は、元の題名を更新したあと写しを読む全画面で新しい題名が出ることを見ること。
