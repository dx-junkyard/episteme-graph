---
id: IK-0875
title: "原稿スタジオの根拠の有無が 3 箇所で別の材料から判定されて食い違う — 何も紐づいていないトピックに「[weak] 根拠候補はあります」、「[missing] どの要素にも対応付けられていません」の直後に数式が 10 件以上、右ペインは「根拠リンク候補はまだありません」"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "教員がトピックの授業用ドラフトを支える根拠があるかを 1 画面で確かめる"
  layers: [lecture_studio, frontend_admin_ui, learning_units]
classification:
  axes:
    processing: [logic]
    structure: [aggregation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 logic: frontend/public/js/admin-lecture-studio.js の lsTopicCoverageStatus（2644〜2650 行）は coverage・content_confidence が
    無く linked_component_ids も無いとき既定で "weak" を返し、lsTopicCoverageMessage が「根拠候補はありますが…」と言う（何も無いときに
    「ある」と言う）。構造軸 aggregation: 中央の見出しは topic.coverage（保存値）→ content_confidence → linked_component_ids の順で、
    その下の一覧は evidence_items（content_blocks の式を含む）、右ペインは evidence_links 由来、書き換えの AI は rewrite の evidence 辞書で
    判定し、「根拠があるか」の正本が 1 つに無い。coverage が missing（学ぶ単位・部品の対応なし）でも content_blocks の式は一覧に並ぶ。
    接続軸 none・統制軸 none。
generalization:
  level: repo_pattern
  general_form: "同じ「根拠があるか」を画面の区画ごとに別の材料から判定し、材料が無いときの既定値が「ある」側に倒れる"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    2 人の教員ペルソナが、同じトピックで中央の根拠表示・右ペイン・AI の注意点の言い分が違うことを記した。3 つの判定の材料を辿った。
    run: uxsim/runs/c-astro-r25-studio-r1/20261022T125145Z-40602（seq12・13・15）・uxsim/runs/c-astro-r25-studio-r1/20261022T123418Z-31804（seq6）。
resolution:
  perspective: [canonical_source, first_class_state]
  note: "根拠の状態の正本を core/lecture_studio_state.topic_evidence_state に置き、course-structure が各トピックに evidence_state（sufficient / weak / missing / none・日本語の短いラベル・事実文・missing で候補が並ぶときの見出し）を返す。画面（lsTopicCoverageStatus / Message・pill・右ペインの空状態）は同じ値を読み、JS の判定と文言は撤去した。何も無いときの既定は none（「根拠候補はありません（コース内容生成の前か…）」）、学ぶ単位だけのときはそのことを言う。トピック概要の source は根拠に数えない。"
  landed_in:
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "frontend/public/js/admin-lecture-studio.js"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "中央・右ペイン・書き換えが別々に判定していたのをやめ、1 つの値を全区画が読む。"
  verification:
    methods: [guardrail]
    unverified:
      - "書き換えの AI 応答が evidence_state と同じことを言うかは LLM を通して見ていない"
related: [IK-0874]
view_of: []
history: []
---

## 課題

症状: 登録直後のトピックの中央に「[weak] 根拠候補はありますが、授業用ドラフトとして使う前に教員確認が必要です」、右ペインは
「このトピックに対応する根拠リンク候補はまだありません」、AI は「根拠候補が1つも紐づいていません」。別のトピックでは
「[missing] この項目は、論文の解析結果のどの要素にも対応付けられていません。」の直後に数式が 10 件以上並ぶ。原因: 根拠の有無の
判定が区画ごとに別の材料を読み、何も無いときの既定が「ある」になる。

## 発見の観点

ペルソナの通し受講（reproduction）と判定の材料の追跡（trace_walk）。

## 解決の観点

解決済み（2026-10-23）。根拠の状態を 1 つの値にして全区画が読む。

## 一般化

同じ事実の判定が面ごとに分かれる型。確かめ方は、根拠ゼロ・式だけ・単位あり の 3 種のトピックで中央・右ペイン・書き換えの
事前判定が同じことを言うのを見ること。
