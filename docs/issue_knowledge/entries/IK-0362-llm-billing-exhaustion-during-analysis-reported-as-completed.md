---
id: IK-0362
title: 解析の途中で LLM 提供元の課金残高が尽き数百回の呼び出しが失敗しても、各ステージが縮退・修復失敗として吸収し、run と教材は「解析完了」になるため、教員にも G層にも「提供元の障害で成果が欠けた」事実が届かない
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 教材を投入して解析パイプラインを走らせ、完了した成果からコースを作る
  layers: [pipeline_a, status_notification, guidance_g, usage_metering_u]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning, condition]
    governance: [completion]
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
    統制軸: run の完了判定はステージが例外を投げずに戻ったかで決まり、ステージ内で LLM 呼び出しが失敗した事実
    （U層 `llm_usage_events` に success=false / RateLimitError が数百行）は完了判定に入らない（completion）。
    接続軸: 各ステージは提供元の課金切れを自分の語彙に読み替える — component_assembly は `fallback: true` と
    `fallback_reason` に提供元の生メッセージ（URL 込み）、discuss_opening / landscape_placement は
    `skipped_reason: repair_failed`、equation_semantics は失敗回数を残さず `completed`（meaning。原因が
    「修復失敗」「上限」にすり替わる）。条件（提供元が落ちている）は後段のステージにも G層にも運ばれない
    （condition）。構造軸: run・教材の状態語彙に「提供元の障害で成果が欠けた」を表す値が無い（representation。
    medium: status を増やすか事実文で足りるかは設計判断）。確認: 砂場 run（4 教材）の `llm_usage_events` で
    equation_semantics の chat 265 / vision 265・component_assembly 2・dsl_linking 2・thesis_reconstruction 2・
    discuss_opening 6・landscape_placement 6 が RateLimitError、一方 `document_analysis_runs.status` と
    `stage_outputs.*.status` は全て completed、`MaterialOut.analysis_error` は null。
generalization:
  level: general
  general_form: 外部提供元の障害を各段が自分の縮退語彙で吸収し、完了判定が呼び出しの成否を見ないため、成果が欠けたまま「完了」と表示される
pattern: failure-reported-as-success
discovery:
  perspective: [data_inspection, reproduction]
  note: >-
    ペルソナ通し受講の砂場で解析中に提供元の課金が尽きた。教材一覧は「解析完了」、審判 E が砂場 DB の
    `llm_usage_events` を読んで失敗行の山を見つけ、run の stage_outputs と突き合わせた。
resolution:
  perspective: [first_class_state, explicit_contract]
  note: >-
    run 完了時に U層の失敗行を要約して stage_outputs.llm_failures に事実として残し（数値は run 内部のみ）、G層 material.analysis_llm_failed が採用 run のそれを読んで再実行を案内する。縮退語彙の 3 分割（provider_failure）は未実施。
  landed_in:
    - backend/core/llm_usage/run_failures.py
    - backend/core/document_pipeline/orchestrator.py
    - backend/core/admin_assistant/next_steps.py
    - backend/tests/test_ik0362_llm_failures.py
  verification:
    methods: [guardrail]
    unverified:
      - 提供元の失敗を含む解析 run の砂場での再現（残高ゼロの状態で新規解析を走らせていない）
      - 再試行で回復した一過性の失敗まで点灯する副作用
related: [IK-0365]
view_of: []
history: []
---

## 課題

解析の途中で LLM 提供元の課金残高が尽きると、以後の呼び出しは全て失敗する。しかし各ステージは失敗を縮退
（fallback）・修復失敗（repair_failed）・打ち切りとして吸収し、run は completed、教材は「解析完了」になる。
教員は欠けた成果（式の意味づけ・部品・議論のきっかけ・配置）に気づけず、そのままコースを作る。G層にも
「提供元の障害で成果が欠けた教材」を示すルールが無い。`fallback_reason` には提供元の生メッセージ（課金ページの
URL 込み）がそのまま残る。

## 発見の観点

砂場 DB の `llm_usage_events`（審判 E）と `document_analysis_runs.stage_outputs` を突き合わせた。

## 解決の観点

未解決。完了判定に「呼び出しの成否」を入れるか、少なくとも run の記録と教材の状態に事実文で残す。縮退語彙に
provider_failure を足す。

## 一般化

外部依存の障害が段ごとの縮退で吸収され、上位の完了判定に届かない型。
