---
id: IK-0362
title: 解析の途中で LLM 提供元の課金残高が尽き数百回の呼び出しが失敗しても、各ステージが縮退・修復失敗として吸収し、run と教材は「解析完了」になるため、教員にも G層にも「提供元の障害で成果が欠けた」事実が届かない
status: open
recorded_at: 2026-09-27
resolved_at: null
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
  perspective: [pending]
  note: >-
    候補: ①run の完了時に U層の失敗行を集計し、`stage_outputs` に「提供元の障害で欠けた呼び出し」の事実を
    残す（数値は run 内部のみ）②教材の状態に「成果が欠けている」を表す事実文と G層ルール（道案内は再実行）
    ③縮退理由の語彙に provider_failure を足し、repair_failed / fallback と区別する。数値・提供元の生メッセージを
    教員に出さない。
  landed_in: []
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
