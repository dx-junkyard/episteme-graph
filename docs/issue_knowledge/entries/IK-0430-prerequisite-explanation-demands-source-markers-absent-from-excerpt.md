---
id: IK-0430
title: "前提知識の説明プロンプトが [出典N] マーカーの挿入を求めるのに、コース内トピックの教材の抜粋には番号が付いておらず、抜粋には ![[figure:UUID]] ・ ![[source:topic_summary]] の埋め込み記法と生成時の付録がそのまま入る"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が前提知識の説明を求め、同コースのトピックの教材を根拠に説明を受ける
  layers: [rag_chat]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract]
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
    接続軸: backend/api/routes/learning.py の _resolve_prerequisite_context（① 同コースの topic）は
    「[コース内トピック『…』の教材]」の見出しで _topic_student_material の本文（最大 3000 字）を
    番号なしで積むが、その後の source_block の指示（同ファイルの「付された番号付き出典マーカー
    [出典1] [出典2] … を本文に自然に挿入すること」）は ② チャンク検索の「[出典N]」付きブロック
    だけを前提にしている。① だけで解決したとき、指示が求める番号が抜粋に存在しない（contract）。
    加えて教材本文は埋め込み記法（![[figure:UUID]] / ![[source:topic_summary]]）と生成時の付録
    （「### この節で参照する図」）を含んだまま渡る。第 9 周の req 00194 で確認した。処理・構造・統制は none。
generalization:
  level: general
  general_form: 資料の出し方を後から増やしたのに、資料を参照する指示は元の出し方の形式を前提にしたまま
pattern: contract-changed-one-side
discovery:
  perspective: [data_inspection]
  note: 第 9 周の前提知識の説明プロンプト（mailbox req 00194）を読んだ。
resolution:
  perspective: [explicit_contract, single_point_fix]
  note: >-
    backend/api/routes/learning.py の _generate_learning_advice_response は、source_context に番号付き出典（[出典N]）が
    実際にあるときだけ出典マーカーの挿入を指示し、無ければ「出典マーカーは書かないこと」と指示する（指示の形式を
    抜粋の実際の形に揃えた）。前提説明の往復の回答は _reconcile_citation_markers で根拠の無い番号を除く。
    抜粋（① コース内トピックの教材・② チャンク本文）は _scrub_excerpt_embeds を通す: 生成時の付録を
    course_content_builder.strip_generated_reference_appendix で外し、![[figure:…]] → 「（図）」・![[equation:…]] /
    [[FORMULA_N]] → 「（数式）」（core.text_hygiene.scrub_internal_placeholders）、![[source|component|claim:…]] は除去。
    ② の番号は会話の中で固定した採番（IK-0432）を使う。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0432_0437_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（mailbox req 00194 と同じ往復を修正後に流していない）
      - 本体 RAG の「[現在表示中の教材]」ブロック（トピック教材の注入）には埋め込み記法の除去を掛けていない
related: [IK-0427]
view_of: []
history: []
---

## 課題

前提知識の説明プロンプトが、抜粋に無い出典番号の挿入を求めている。抜粋には埋め込み記法が生のまま入る。

## 発見の観点

生成モデルが受け取った前提知識の説明プロンプトを読んだ。

## 解決の観点

抜粋を参照させる指示を抜粋の実際の形（番号の有無）に揃え、抜粋から埋め込み記法と付録を外した。

## 一般化

資料の出し方を増やしたら、資料を参照させる指示も同じ形式に揃えないと、存在しない番号を求める。
