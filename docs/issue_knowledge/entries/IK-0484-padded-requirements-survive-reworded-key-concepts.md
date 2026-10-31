---
id: IK-0484
title: "確認問題の要件の水増し（重要概念を全問の末尾に足したもの）が、重要概念の言い回しが次の生成で変わったため完全一致の除去をすり抜け、「現在の下書き」に残っていた"
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の再生成が、前回の下書きの確認問題を材料として渡し、要件を問いの答えの要素だけで書き直させる
  layers: [course_builder]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [version]
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
    接続軸: IK-0462 の除去は「いまの重要概念・学習目標と完全一致する要件」を外すだけだった。水増しに使われたのは前の生成時点の重要概念（topic.key_concepts は生成結果で上書きされる前の値）で、次の生成でモデルが重要概念を言い換える（「M_PISN（対不安定性に関係する質量スケール）」→「M_PISN（対不安定型超新星の質量閾値）」）と一致しない。版が1つずれた派生物を、いまの版の値で照合していた。処理軸: 水増しの形（同じ要件が全問の末尾に並ぶ）を見ていなかった。
generalization:
  level: repo_pattern
  general_form: 前の版の値で作られた派生物を、いまの版の値との一致で見分けようとし、値が変わった派生物を取りこぼす
pattern: stale-derivative-served
discovery:
  perspective: [data_inspection]
  note: 第 13 波 a の再生成プロンプト 20 本の「現在の下書き」の要件を、同じトピックの重要概念と問いごとの模範解答に突き合わせた（2 トピックで同じ要件が全問の末尾に並んでいた）。
resolution:
  perspective: [single_point_fix]
  note: >-
    strip_padded_requirements: ①同じトピックの2問以上に同じ要件がある ②要件の見出し（括弧の前を正規化）が重要概念・学習目標の見出しと一致する、のどちらかで、かつその問いの模範解答・問いに見出しが現れない要件を外す（答えに根ざす要件は残す）。_topic_existing_draft（生成の下書きのとき）と _detailed_check_questions（モデルが下書きから写した場合）の両方で使う。決定論・非LLM。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0484_0489_course_draft_regen12.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成
      - 保存済み（配信中）の確認問題の水増しは、次の再生成まで配信側に残る（routes/learning.py では外していない）
      - 答えに根ざす判定は見出しの部分文字列で、言い換えた答え（「2つのネットワーク」）は根ざしていないと数える
related: [IK-0462, IK-0461, IK-0440]
view_of: []
history: []
---

## 課題

確認問題の要件の水増しが、重要概念の言い換えで除去をすり抜けた。

## 発見の観点

下書きの要件を重要概念と模範解答に突き合わせた。

## 解決の観点

水増しの形（全問に同じ要件・重要概念の見出し）で見分け、答えに根ざすものだけ残す。

## 一般化

前の版の派生物は、いまの版の値との一致ではなく派生物自身の形で見分ける。
